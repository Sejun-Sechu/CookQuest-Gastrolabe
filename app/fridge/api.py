"""WHACK cooking game - fridge-to-recipe prototype server.

Serves the mobile web app from ./static and exposes three JSON endpoints:
  GET  /api/status   -> is live AI available (API key set server-side)?
  POST /api/detect   -> fridge photo(s) -> validated, merged product list (structured JSON)
  POST /api/recipe   -> confirmed ingredients + skill level -> {recipes: [5 recipes]}, each with
                        exactly 5 tasks, server-decided dietary tags and an image (recipes.py)

The Google Gemini API key (GEMINI_API_KEY) is read from the environment or a
local .env file and never leaves this process; the browser only talks to /api/*. Every request can also run in demo mode, which
returns built-in sample data and never calls the API.
"""

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from app.fridge import demo_data as demo_data
from app.fridge import detection as fd
from app.fridge import images as recipe_images
from app.fridge import recommendations as recipes

BASE_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = BASE_DIR / "app" / "frontend"

# --- minimal .env loader (so judges don't need python-dotenv) ---------------
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from app.fridge import ai as ai  # noqa: E402  (imported after .env is loaded: it reads GEMINI_* vars)

AIError = ai.AIError
SKILL_LEVELS = ("beginner", "intermediate", "expert")
PANTRY_STAPLES = demo_data.PANTRY_STAPLES
MIN_INGREDIENTS = 3            # below this we ask the player what else they have
MAX_IMAGE_BYTES = 10 * 1024 * 1024   # per photo, decoded

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 48 * 1024 * 1024   # up to 4 high-res photos per request


def live_available() -> bool:
    return ai.live_available()


def generate(**kwargs):
    """One Gemini call (module-level so tests can replace it with a fake)."""
    return ai.generate(**kwargs)


def call_ai_json(system: str, text: str, schema: dict, thinking: str = "medium") -> dict:
    """Single-turn structured-output call. Returns the parsed JSON object."""
    response = generate(contents=text, system=system, schema=schema, thinking=thinking)
    out = ai.response_text(response)
    if not out:
        raise AIError("The AI returned an empty response. Please try again.", 502)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        raise AIError("The AI returned malformed JSON. Please try again.", 502)


# =============================================================================
# Fridge product detection  (logic lives in fridge_detection.py)
# =============================================================================

log = logging.getLogger("fridge")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False


def _error(message, status, code, demo_available=True, **extra):
    """Every failure response has the same shape, so the browser can always leave the loading screen.
    extra: quota_type, retry_after (seconds), model, limit, requests - when known."""
    body = {"error": message, "code": code, "demo_available": demo_available}
    body.update({k: v for k, v in extra.items() if v is not None})
    return jsonify(body), status


@app.errorhandler(413)
def too_large(_e):
    return _error("The photos are too large to upload together. Try one photo at a time.", 413, "too_large", False)


@app.errorhandler(500)
def internal_error(_e):
    return _error("Something went wrong on the server. Retry, or use demo mode.", 500, "server_error")


# Duplicate-scan guard: the same photos can't be analysed twice at the same time
# (double clicks, two tabs), so one click never turns into parallel Gemini requests.
_inflight: set[str] = set()
_inflight_lock = threading.Lock()


def _scan_key(images_b64: list) -> str:
    h = hashlib.sha256()
    for b in images_b64:
        s = b if isinstance(b, str) else ""
        h.update(str(len(s)).encode())
        h.update(s[:65536].encode("ascii", "ignore"))
        h.update(s[-4096:].encode("ascii", "ignore"))
    return h.hexdigest()


@app.post("/api/detect")
def detect():
    """Body: {images: [base64, ...], previous: <earlier result, optional>, demo: bool}
    `previous` is the player's current (possibly edited) list; new detections merge into it.

    One scan sends one Gemini request (two only if the model needs close-ups of unreadable
    labels). Logs each stage with a request id; never logs the API key or image data."""
    rid = f"[detect {uuid.uuid4().hex[:6]}]"
    t0 = time.monotonic()
    body = request.get_json(silent=True) or {}
    previous = body.get("previous") if isinstance(body.get("previous"), dict) else None

    images_b64 = body.get("images")
    if images_b64 is None and body.get("image"):      # single-image form
        images_b64 = [body["image"]]

    if body.get("demo") or not live_available():
        log.info("%s demo mode (%s) - no Gemini request", rid, "requested" if body.get("demo") else "no GEMINI_API_KEY")
        new = fd.normalize_result(demo_data.demo_detection_raw(extra_photo=bool(previous)))
        return jsonify({**fd.combine_results(previous, new), "demo": True})

    stats: dict = {"requests": 0}

    def summary(outcome, status, err=None):
        log.info("%s scan summary: outcome=%s model=%s gemini_requests=%d status=%s quota_type=%s "
                 "retry_delay=%s time=%.1fs", rid, outcome, ai.MODEL, stats.get("requests", 0), status,
                 getattr(err, "quota_type", None) or stats.get("quota_type") or "-",
                 f"{err.retry_after:.0f}s" if getattr(err, "retry_after", None) is not None else "-",
                 time.monotonic() - t0)

    def fail(message, status, code, demo_available=True, err=None):
        log.warning("%s 6/6 detection failed: code=%s status=%d", rid, code, status)
        summary("failed", status, err)
        return _error(message, status, code, demo_available,
                      quota_type=getattr(err, "quota_type", None),
                      retry_after=round(err.retry_after) if getattr(err, "retry_after", None) is not None else None,
                      model=getattr(err, "model", None), limit=getattr(err, "limit", None),
                      requests=stats.get("requests"))

    if not isinstance(images_b64, list) or not images_b64:
        return fail("No photo received. Take or upload a photo of your fridge.", 400, "no_image", False)
    sizes = [len(b) * 3 // 4 if isinstance(b, str) else 0 for b in images_b64]
    log.info("%s 1/6 image received: %d photo(s), %s KB", rid, len(images_b64), "+".join(str(s // 1024) for s in sizes))
    if len(images_b64) > fd.MAX_PHOTOS_PER_REQUEST:
        return fail(f"Send at most {fd.MAX_PHOTOS_PER_REQUEST} photos at a time.", 400, "too_many_photos", False)

    key = _scan_key(images_b64)
    with _inflight_lock:
        if key in _inflight:
            log.warning("%s duplicate scan rejected (same photos already being analysed)", rid)
            return _error("These photos are already being analysed. Wait for that result.", 409,
                          "duplicate_scan", False)
        _inflight.add(key)
    try:
        return _detect_live(rid, t0, images_b64, sizes, previous, stats, fail, summary)
    finally:
        with _inflight_lock:
            _inflight.discard(key)


def _detect_live(rid, t0, images_b64, sizes, previous, stats, fail, summary):
    try:
        images = []
        for b64, size in zip(images_b64, sizes):
            if not isinstance(b64, str) or size > MAX_IMAGE_BYTES:
                return fail("A photo is too large. Try a smaller one.", 413, "too_large", False)
            images.append(fd.decode_image(b64))
    except fd.DetectionError as e:
        return fail(e.message, e.status, e.code, False)
    log.info("%s 2/6 image validated: %s (model gets <= %d px)", rid,
             ", ".join(f"{i.size[0]}x{i.size[1]}" for i in images), fd.MODEL_MAX_EDGE)

    # Don't spend an API call on photos that are essentially black.
    if all(fd.too_dark(img) for img in images):
        log.info("%s photo too dark - skipped Gemini", rid)
        new = fd.normalize_result({
            "image_quality": "poor", "needs_another_photo": True,
            "photo_advice": "The photo is too dark to see anything. Turn on the kitchen light and keep the fridge door wide open.",
            "detected_items": [], "unknown_items": [],
        })
        log.info("%s 6/6 detection completed (dark photo)", rid)
        summary("dark_photo", 200)
        return jsonify({**fd.combine_results(previous, new), "demo": False})

    log.info("%s 3/6 gemini analysis started: model=%s budget=%ds", rid, ai.MODEL, fd.DETECT_BUDGET_SECONDS)
    try:
        raw, _ = fd.run_detection(generate, images, deadline=t0 + fd.DETECT_BUDGET_SECONDS, tag=rid, stats=stats)
        log.info("%s 4/6 gemini response received: requests=%d crops=%d",
                 rid, stats.get("requests", 0), stats.get("crops", 0))
        new = fd.normalize_result(raw)
    except AIError as e:
        return fail(e.message, e.status, e.code, err=e)
    except fd.DetectionError as e:
        return fail(e.message, e.status, e.code)
    except Exception:
        log.exception("%s unexpected error", rid)
        return fail("Something went wrong while analysing the photo. Retry, or use demo mode.", 500, "server_error")

    log.info("%s 5/6 response validated: %d items, %d unknown, quality=%s",
             rid, len(new["detected_items"]), len(new["unknown_items"]), new["image_quality"])
    result = fd.combine_results(previous, new)
    log.info("%s 6/6 detection completed", rid)
    summary("ok", 200)
    return jsonify({**result, "demo": False})


# =============================================================================
# Recipe generation
# =============================================================================

def _recipe_error(message, status, code, *, quota_type=None, retry_after=None, details=None):
    """Recipe failures always carry error_type (RATE_LIMITED, INVALID_API_KEY, MODEL_UNAVAILABLE,
    INVALID_RECIPE_RESPONSE, NETWORK_TIMEOUT, SERVER_ERROR) plus the detailed code."""
    body = {"error": message, "error_type": recipes.error_type(code), "code": code, "demo_available": True}
    if quota_type:
        body["quota_type"] = quota_type
    if retry_after is not None:
        body["retry_after"] = round(retry_after)
    if details:
        body["details"] = details
    return jsonify(body), status


@app.post("/api/recipe")
def recipe():
    """Body: {skill_level, ingredients: [names], pantry_staples: [names], demo}
    Returns {status: "ok", recipes: [5 recipe objects], ...} or {status: "need_more_ingredients", ...}.
    Uses only the confirmed names, never photos. At most two Gemini requests (one repair)."""
    rid = f"[recipe {uuid.uuid4().hex[:6]}]"
    t0 = time.monotonic()
    body = request.get_json(silent=True) or {}
    level = str(body.get("skill_level", "")).lower()
    ingredients = clean_list(body.get("ingredients"))
    log.info("%s recipe request received: level=%s confirmed_ingredients=%d demo=%s",
             rid, level or "-", len(ingredients), bool(body.get("demo")) or not live_available())
    if level not in SKILL_LEVELS:
        return _recipe_error("Choose Beginner, Intermediate or Expert.", 400, "bad_request")

    staples = [s for s in clean_list(body.get("pantry_staples")) if s in PANTRY_STAPLES]
    def sent(payload, status=200):
        log.info("%s response sent: status=%d recipes=%d gemini_requests=%s time=%.1fs", rid, status,
                 len(payload.get("recipes", [])), payload.get("gemini_requests", 0), time.monotonic() - t0)
        return jsonify(payload), status

    # Too few ingredients: ask before spending an API call.
    if len(ingredients) < MIN_INGREDIENTS:
        return sent({
            "status": "need_more_ingredients",
            "follow_up_question": (
                f"You've confirmed {len(ingredients)} ingredient{'s' if len(ingredients) != 1 else ''}. "
                "What else do you have? A protein, a vegetable or a carb would help a lot."
            ),
            "suggested_additions": [
                s for s in ["eggs", "pasta or rice", "an onion", "a tin of tomatoes", "cheese", "bread", "chicken", "potatoes"]
                if s.lower() not in {i.lower() for i in ingredients}
            ][:6],
            "demo": bool(body.get("demo")) or not live_available(),
        })

    if body.get("demo") or not live_available():
        allowed = {i.lower() for i in demo_data.DEMO_ALL_ALLOWED}
        used: set[str] = set()
        out = []
        for raw in demo_data.demo_recipe_options(level):
            r, errors = recipes.normalize_recipe(raw, allowed, level, used)
            if not r:  # should never happen; keeps demo data honest
                return _recipe_error("Demo recipe failed validation: " + "; ".join(errors), 500, "server_error")
            out.append(r)
        recipe_images.attach_images(out)
        return sent({"status": "ok", "skill_level": level, "recipes": out, "demo": True, "gemini_requests": 0})

    def call_json(text, stats, deadline):
        resp = generate(contents=text, system=recipes.SYSTEM_PROMPT, schema=recipes.RECIPE_SCHEMA,
                        thinking="medium", max_output_tokens=24000, deadline=deadline, stats=stats, tag=rid)
        return ai.response_text(resp)

    try:
        result = recipes.generate(call_json, level=level, ingredients=ingredients, staples=staples,
                                  tag=rid)
    except AIError as e:
        log.warning("%s recipe generation failed: code=%s error_type=%s", rid, e.code, recipes.error_type(e.code))
        return _recipe_error(e.message, e.status, e.code, quota_type=e.quota_type, retry_after=e.retry_after)
    except recipes.RecipeError as e:
        log.warning("%s recipe generation failed: code=%s error_type=%s", rid, e.code, recipes.error_type(e.code))
        return _recipe_error(e.message, e.status, e.code, details=e.details)
    except Exception:
        log.exception("%s unexpected recipe error", rid)
        return _recipe_error("Something went wrong while creating recipes. Retry, or use demo mode.", 500, "server_error")

    if result["status"] == "need_more_ingredients":
        return sent({**result, "demo": False})
    recipe_images.attach_images(result["recipes"])
    log.info("%s images: %s", rid, ", ".join(r["image_source"] for r in result["recipes"]))
    return sent({"status": "ok", "skill_level": level, "recipes": result["recipes"], "demo": False,
                 "requested": recipes.RECIPE_COUNT, "gemini_requests": result["gemini_requests"]})


def clean_list(values, limit=40) -> list[str]:
    out, seen = [], set()
    for v in values or []:
        s = " ".join(str(v).split())[:60]
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out[:limit]


# =============================================================================
# Status + static files
# =============================================================================

# Bumped whenever the API contract changes; the frontend refuses to run against another
# version (e.g. an old server process still running after an update) and says to restart.
API_VERSION = 3


@app.get("/api/status")
def status():
    return jsonify({
        "api_version": API_VERSION,
        "live_ai": live_available(),
        "model": ai.MODEL if live_available() else None,
        "pantry_staples": PANTRY_STAPLES,
    })


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/<path:path>")
def static_files(path):
    return send_from_directory(STATIC_DIR, path)


def port_in_use(port: int) -> bool:
    """True if something already accepts connections on this port. On Windows a second server can
    bind the same port and requests may keep going to the OLD one, so we refuse to start instead."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


if __name__ == "__main__":
    import sys
    port = int(os.environ.get("PORT", "5000"))
    if port_in_use(port):
        print(f"\n  Port {port} is already used by another server - probably an older copy of this app")
        print("  still running in another terminal window. Stop that one first (Ctrl+C in its window,")
        print(f"  or in PowerShell: Get-NetTCPConnection -LocalPort {port} -State Listen | "
              "ForEach-Object { Stop-Process -Id $_.OwningProcess }),")
        print("  or start this copy on another port:  $env:PORT=5001; python server.py\n")
        sys.exit(1)
    mode = (f"LIVE AI (Gemini {ai.MODEL})" if live_available()
            else "DEMO ONLY (no GEMINI_API_KEY set)")
    print(f"\n  Fridge-to-Recipe running in {mode}")
    print(f"  Open http://localhost:{port} on this computer")
    print(f"  On your phone (same Wi-Fi): http://<this-computer's-IP>:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
