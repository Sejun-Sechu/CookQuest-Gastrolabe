"""Recipe photos from TheMealDB (free, no key), with a local placeholder fallback.

A missing image NEVER removes a recipe: every lookup failure (no match, timeout, network
error, bad JSON) returns the local placeholder. Lookups run in parallel with a short timeout
and are cached, so they add little time and no Gemini quota. Only the short, generic
`image_search_term` (e.g. "frittata") is sent to TheMealDB.
"""

from __future__ import annotations

import json
import logging
import os
import time
from threading import Lock
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import httpx
from app.recipes.ingredients import canonical_name

log = logging.getLogger("fridge")

PLACEHOLDER = "assets/recipe-placeholder.svg"          # served from app/frontend/
SEARCH_URL = "https://www.themealdb.com/api/json/v1/1/search.php?s={}"
TIMEOUT_SECONDS = 4
ENABLED = os.environ.get("MEALDB_ENABLED", "1") != "0"

_cache: dict[str, str | None] = {}


def _fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "fridge-to-table-prototype"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def lookup(term: str) -> str | None:
    """Image URL for a search term, or None. Never raises."""
    term = " ".join(str(term or "").split())[:60]
    if not term or not ENABLED:
        return None
    key = term.lower()
    from app.config import get_settings
    if get_settings().cache_enabled and key in _cache:
        return _cache[key]
    url = None
    try:
        data = _fetch_json(SEARCH_URL.format(urllib.parse.quote(term)))
        meals = data.get("meals") if isinstance(data, dict) else None
        if isinstance(meals, list) and meals and isinstance(meals[0], dict):
            thumb = meals[0].get("strMealThumb")
            if isinstance(thumb, str) and thumb.startswith("https://"):
                url = thumb
    except Exception as e:  # timeouts, DNS, HTTP errors, bad JSON: fall back, never fail
        log.info("mealdb lookup failed for %r: %s", term, type(e).__name__)
        return None                                   # don't cache transient failures
    if get_settings().cache_enabled:
        _cache[key] = url
    return url


def attach_images(recipes: list[dict]) -> None:
    """Set image_url / image_source on every recipe in place (placeholder when not found)."""
    terms = [r.get("image_search_term") or r.get("dish_name") or "" for r in recipes]
    if recipes:
        with ThreadPoolExecutor(max_workers=min(5, len(recipes))) as pool:
            urls = list(pool.map(lookup, terms))
    else:
        urls = []
    for r, url in zip(recipes, urls):
        r["image_url"] = url or PLACEHOLDER
        r["image_source"] = "themealdb" if url else "placeholder"


# Ingredient vocabulary uses the same external recipe provider.
SOURCE_URL = "https://www.themealdb.com/api/json/v1/1/list.php?i=list"
_lock = Lock()
_names: list[str] = []
_expires = 0.0


def _fetch_names():
    response = httpx.get(SOURCE_URL, timeout=3, follow_redirects=True)
    response.raise_for_status()
    rows = response.json().get("meals")
    if not isinstance(rows, list):
        raise ValueError("Invalid ingredient catalogue")
    names = sorted({canonical_name(row["strIngredient"]) for row in rows
                    if isinstance(row, dict) and isinstance(row.get("strIngredient"), str)
                    and 0 < len(row["strIngredient"].strip()) <= 60})
    if not names:
        raise ValueError("Empty ingredient catalogue")
    return names


def suggestions(base):
    global _names, _expires
    if os.environ.get("MEALDB_ENABLED", "1") == "0":
        return sorted(set(base))
    from app.config import get_settings
    if not get_settings().cache_enabled:
        try:
            return sorted(set(base) | set(_fetch_names()))
        except (httpx.HTTPError, ValueError, TypeError):
            return sorted(set(base))
    with _lock:
        if time.monotonic() >= _expires:
            try:
                _names = _fetch_names()
                _expires = time.monotonic() + 86400
            except (httpx.HTTPError, ValueError, TypeError):
                # Retain any previous successful catalogue during an outage.
                _expires = time.monotonic() + 300
        return sorted(set(base) | set(_names))
