"""Server-side Google Gemini wrapper (google-genai SDK).

The API key is read from GEMINI_API_KEY on the server and never sent to the browser.

Request policy (free tier):
  - One generate_content call per `generate()`; no fallback model, no automatic model switching.
  - A 429 is classified from the error's QuotaFailure / RetryInfo metadata:
      per_minute  -> exponential backoff, but only for a short wait (<= BACKOFF_MAX_WAIT s), at most
                     MAX_RATE_RETRIES times and within the deadline; otherwise returned to the
                     browser with retry_after so the player can retry when the window resets.
      per_day     -> never retried automatically.
      zero_quota  -> the model has no free-tier quota: never retried; the model must be changed.
  - Billing-required errors are never retried.
  - A temporary 5xx is retried once after a short pause, only while time remains.
  - Every call is counted in `stats["requests"]` and logged with model, status, quota type and
    retry delay. The API key and image data are never logged.
"""

from __future__ import annotations

import logging
import os
import re
import time
from contextvars import ContextVar

integrated_request = ContextVar("integrated_fridge_request", default=False)

log = logging.getLogger("fridge")

# gemini-3.8-flash and gemini-3.6-flash allow only 20 free requests/day per project; Flash-Lite
# is the free multimodal model that this project can use for regular scanning.
MODEL = os.environ.get("FRIDGE_GEMINI_MODEL") or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
MAX_CALL_SECONDS = 60          # no single Gemini call may take longer than this
MIN_CALL_SECONDS = 5           # don't start a call with less time than this left
BACKOFF_MAX_WAIT = 10          # only wait server-side for per-minute limits that reset this soon
MAX_RATE_RETRIES = 2           # per-minute backoff retries (2 s, 4 s, ... or the server's delay)
SERVER_ERROR_RETRY_DELAY = 2   # one retry for a temporary 5xx

_client = None
_integrated_client = None


def _sleep(seconds):
    """Backoff pause (module-level so tests can replace it without touching time.sleep)."""
    time.sleep(seconds)


class AIError(Exception):
    """code: invalid_key | permission | rate_limit_minute | quota_daily | no_free_quota |
    billing_required | model_unavailable | bad_request | overloaded | timeout | network |
    blocked | truncated | malformed | server_error"""

    def __init__(self, message, status=502, code="server_error", *, quota_type=None,
                 retry_after=None, model=None, limit=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.quota_type = quota_type
        self.retry_after = retry_after
        self.model = model
        self.limit = limit


def api_key() -> str:
    if integrated_request.get():
        return os.environ.get("FRIDGE_GEMINI_API_KEY", "").strip()
    return os.environ.get("GEMINI_API_KEY", "").strip()


def live_available() -> bool:
    return bool(api_key())


def client():
    global _integrated_client
    if integrated_request.get():
        if _integrated_client is None:
            _integrated_client = _new_client()
        return _integrated_client
    global _client
    if _client is None:
        _client = _new_client()
    return _client


def _new_client():
    from google import genai
    from google.genai import types
    # Match the cooking provider: do not inherit stale editor/agent proxy settings.
    # Networks that require their configured proxy can explicitly opt in.
    trust_env = os.environ.get("FRIDGE_GEMINI_TRUST_ENV", "false").lower() == "true"
    return genai.Client(api_key=api_key(), http_options=types.HttpOptions(
        client_args={"trust_env": trust_env}, async_client_args={"trust_env": trust_env}))


BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY",
           "IMAGE_PROHIBITED_CONTENT", "RECITATION"}


def timeout_error() -> AIError:
    return AIError("The analysis took too long and was stopped. Retry in a moment, try one photo at a "
                   "time, or use demo mode.", 504, "timeout")


# =============================================================================
# Quota metadata
# =============================================================================

def _duration_seconds(text) -> float | None:
    """'52052s', '13.5s', '14h27m32.36s', '1m5s' -> seconds."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = re.fullmatch(r"\s*(?:(\d+(?:\.\d+)?)h)?\s*(?:(\d+(?:\.\d+)?)m(?!s))?\s*(?:(\d+(?:\.\d+)?)s)?\s*", str(text))
    if not m or not any(m.groups()):
        return None
    h, mi, s = (float(g) if g else 0.0 for g in m.groups())
    return h * 3600 + mi * 60 + s


def _error_body(e) -> dict:
    details = getattr(e, "details", None)
    if isinstance(details, dict):
        return details.get("error", details) if isinstance(details.get("error", details), dict) else {}
    return {}


def quota_info(e) -> dict:
    """Classify a 429 from its metadata.

    Returns {type, limit, retry_after, metric, quota_id}; type is one of
    per_minute | per_day | zero_quota | unknown.
    """
    body = _error_body(e)
    message = body.get("message") or getattr(e, "message", "") or ""
    violations, retry = [], None
    for d in body.get("details") or []:
        if not isinstance(d, dict):
            continue
        t = d.get("@type", "")
        if t.endswith("QuotaFailure"):
            violations += [v for v in d.get("violations") or [] if isinstance(v, dict)]
        elif t.endswith("RetryInfo"):
            retry = _duration_seconds(d.get("retryDelay"))
    if retry is None:
        m = re.search(r"retry in ([0-9hms.]+)", message)
        retry = _duration_seconds(m.group(1)) if m else None

    v = violations[0] if violations else {}
    quota_id = str(v.get("quotaId", ""))
    metric = str(v.get("quotaMetric", ""))
    try:
        limit = int(float(v.get("quotaValue"))) if v.get("quotaValue") is not None else None
    except (TypeError, ValueError):
        limit = None
    if limit is None:
        m = re.search(r"limit: (\d+)", message)
        limit = int(m.group(1)) if m else None

    text = f"{quota_id} {metric} {message}".lower()
    if limit == 0:
        qtype = "zero_quota"
    elif "perday" in text or "per day" in text:
        qtype = "per_day"
    elif "perminute" in text or "per minute" in text:
        qtype = "per_minute"
    else:
        qtype = "unknown"
    resource = "tokens" if "token" in metric.lower() else "requests"
    return {"type": qtype, "limit": limit, "retry_after": retry, "metric": metric,
            "quota_id": quota_id, "resource": resource}


def _fmt_wait(seconds) -> str:
    if seconds is None:
        return "a while"
    seconds = int(round(seconds))
    if seconds < 90:
        return f"{seconds} s"
    h, m = divmod(seconds // 60, 60)
    return f"{h} h {m} min" if h else f"{m} min"


def quota_error(e, model) -> AIError:
    q = quota_info(e)
    limit = q["limit"]
    if q["type"] == "zero_quota":
        return AIError(f"The configured model {model} has no free-tier quota for this project. Change "
                       f"GEMINI_MODEL in .env to a free-tier model and restart the server.",
                       503, "no_free_quota", quota_type="zero_quota", model=model, limit=0)
    if q["type"] == "per_day":
        lim = f" ({limit} requests per day)" if limit else ""
        return AIError(f"Today's free Gemini quota for {model} is used up{lim}. It resets in about "
                       f"{_fmt_wait(q['retry_after'])}. Use demo mode for now, or set GEMINI_MODEL to "
                       f"another free model.", 429, "quota_daily", quota_type="per_day",
                       retry_after=q["retry_after"], model=model, limit=limit)
    # per_minute (or an unlabelled 429, which Google uses for short-term limits)
    wait = q["retry_after"] if q["retry_after"] is not None else 30.0
    what = "tokens" if q["resource"] == "tokens" else "requests"
    lim = f" ({limit} {what} per minute)" if limit else ""
    return AIError(f"Gemini's per-minute free limit was reached{lim}. You can retry in {_fmt_wait(wait)}.",
                   429, "rate_limit_minute", quota_type="per_minute", retry_after=wait, model=model,
                   limit=limit)


def _billing_required(e) -> bool:
    msg = (getattr(e, "message", None) or str(e)).lower()
    status = str(getattr(e, "status", "") or "").upper()
    return ("billing" in msg and ("enable" in msg or "required" in msg or "free tier" in msg)) or \
        (status == "FAILED_PRECONDITION" and "free tier" in msg)


def friendly(e, model) -> AIError:
    """Map a google.genai APIError to a message the player can act on (never includes the key)."""
    code = getattr(e, "code", None) or 0
    msg = (getattr(e, "message", None) or str(e) or "").lower()
    if code == 429:
        return quota_error(e, model)
    if _billing_required(e):
        return AIError(f"{model} isn't available on the free tier (billing required). Change GEMINI_MODEL "
                       f"in .env to a free-tier model, or enable billing.", 503, "billing_required", model=model)
    if code == 400 and ("api key" in msg or "api_key" in msg):
        return AIError("The server's Gemini API key is invalid. Fix GEMINI_API_KEY in .env and restart, "
                       "or use demo mode.", 503, "invalid_key")
    if code in (401, 403):
        return AIError("The server's Gemini API key isn't allowed to use this model. Check GEMINI_API_KEY "
                       "and that the Gemini API is enabled for its project, or use demo mode.", 503, "permission")
    if code == 404:
        return AIError(f"The configured Gemini model {model} isn't available to this key. Change GEMINI_MODEL "
                       f"in .env.", 503, "model_unavailable", model=model)
    if code == 400:
        return AIError(f"Gemini rejected the request: {getattr(e, 'message', e)}", 400, "bad_request")
    if code in (500, 502, 503, 504):
        return AIError("Gemini is overloaded right now. Retry in a moment, or use demo mode.", 503, "overloaded")
    return AIError(f"Gemini returned an error ({code}). Retry, or use demo mode.", 503, "server_error")


# =============================================================================
# The call
# =============================================================================

def _class_names(e) -> set[str]:
    return {c.__name__.lower() for c in type(e).__mro__}


def _is_timeout(e) -> bool:
    # google-genai raises httpx2.ReadTimeout / ConnectTimeout (subclasses of TimeoutException)
    return any("timeout" in n for n in _class_names(e)) or isinstance(e, TimeoutError)


def _is_network(e) -> bool:
    return bool(_class_names(e) & {"transporterror", "networkerror", "connecterror", "remoteprotocolerror"}) \
        or isinstance(e, ConnectionError)


def generate(*, contents, system, schema, thinking="medium", max_output_tokens=16000,
             deadline=None, stats=None, tag=""):
    """One structured-output generate_content call (plus only the narrow retries described in
    the module docstring). Returns the SDK response; raises AIError.

    deadline: time.monotonic() value the call must finish by (default now + MAX_CALL_SECONDS).
    stats:    dict; "requests" is incremented for every request actually sent to Gemini.
    """
    from google.genai import errors, types

    deadline = deadline or (time.monotonic() + MAX_CALL_SECONDS)
    stats = stats if stats is not None else {}
    stats.setdefault("requests", 0)
    stats["model"] = MODEL
    rate_retries = server_retries = 0

    while True:
        remaining = deadline - time.monotonic()
        if remaining < MIN_CALL_SECONDS:
            raise timeout_error()
        call_timeout = min(MAX_CALL_SECONDS, remaining)
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            max_output_tokens=max_output_tokens,
            thinking_config=types.ThinkingConfig(thinking_level=thinking),
            media_resolution="MEDIA_RESOLUTION_HIGH",
            http_options=types.HttpOptions(timeout=int(call_timeout * 1000)),
        )
        stats["requests"] += 1
        n = stats["requests"]
        t = time.monotonic()
        log.info("%s gemini request #%d started: model=%s timeout=%.0fs", tag, n, MODEL, call_timeout)
        try:
            response = client().models.generate_content(model=MODEL, contents=contents, config=config)
        except errors.APIError as e:
            q = quota_info(e) if e.code == 429 else {}
            log.warning("%s gemini request #%d failed: model=%s status=%s %s quota_type=%s limit=%s "
                        "retry_delay=%s after %.1fs", tag, n, MODEL, e.code, getattr(e, "status", ""),
                        q.get("type", "-"), q.get("limit", "-"),
                        f"{q['retry_after']:.0f}s" if q.get("retry_after") is not None else "-",
                        time.monotonic() - t)
            stats["last_status"] = e.code
            stats["quota_type"] = q.get("type")
            stats["retry_delay"] = q.get("retry_after")
            if e.code == 429 and q.get("type") in ("per_minute", "unknown") and rate_retries < MAX_RATE_RETRIES:
                wait = max(q.get("retry_after") or 0, 2 ** (rate_retries + 1))   # 2 s, 4 s, or server delay
                if wait <= BACKOFF_MAX_WAIT and deadline - time.monotonic() > wait + MIN_CALL_SECONDS:
                    rate_retries += 1
                    log.info("%s per-minute limit: backing off %.0fs (retry %d/%d)",
                             tag, wait, rate_retries, MAX_RATE_RETRIES)
                    _sleep(wait)
                    continue
            if e.code in (500, 502, 503, 504) and server_retries < 1 \
                    and deadline - time.monotonic() > SERVER_ERROR_RETRY_DELAY + MIN_CALL_SECONDS:
                server_retries += 1
                _sleep(SERVER_ERROR_RETRY_DELAY)
                continue
            raise friendly(e, MODEL)               # per-day, zero-quota, billing: never retried
        except Exception as e:                     # httpx2 timeouts / connection errors
            log.warning("%s gemini request #%d transport error: model=%s %s after %.1fs",
                        tag, n, MODEL, type(e).__name__, time.monotonic() - t)
            if _is_timeout(e):
                raise timeout_error()
            if _is_network(e):
                raise AIError("The server couldn't reach Gemini. Check the server internet/proxy settings and retry.",
                              503, "network")
            raise
        finish = _finish(response)
        usage = getattr(response, "usage_metadata", None)
        stats["last_status"] = 200
        log.info("%s gemini request #%d ok: model=%s status=200 %.1fs finish=%s tokens_in=%s tokens_out=%s",
                 tag, n, MODEL, time.monotonic() - t, finish,
                 getattr(usage, "prompt_token_count", "?"), getattr(usage, "candidates_token_count", "?"))
        return _check(response, finish)


def _finish(response) -> str:
    if not getattr(response, "candidates", None):
        return ""
    fr = response.candidates[0].finish_reason
    return str(getattr(fr, "value", fr) or "")


def _check(response, finish):
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        raise AIError("Gemini declined to analyse this photo. Try a different photo or demo mode.", 422, "blocked")
    if not response.candidates:
        raise AIError("Gemini returned no result. Retry, or use demo mode.", 502, "malformed")
    if finish in BLOCKED:
        raise AIError("Gemini declined to analyse this photo. Try a different photo or demo mode.", 422, "blocked")
    if finish == "MAX_TOKENS":
        raise AIError("Gemini's answer was cut off. Retry with fewer photos.", 502, "truncated")
    if finish == "MALFORMED_RESPONSE":
        raise AIError("Gemini returned a malformed answer. Retry, or use demo mode.", 502, "malformed")
    return response


def response_text(response) -> str:
    """Final text, ignoring thought parts."""
    parts = response.candidates[0].content.parts if response.candidates[0].content else []
    return "".join(p.text for p in (parts or []) if getattr(p, "text", None) and not getattr(p, "thought", False))
