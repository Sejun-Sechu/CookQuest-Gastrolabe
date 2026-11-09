"""Fridge product extraction: photos -> validated, de-duplicated item list.

Pipeline (server-side only):
  1. decode_image()      base64 -> PIL image (EXIF-rotated, RGB), kept at full resolution
  2. run_detection()     ONE Gemini request with the full photos (max 2576 px long edge, high
                         media resolution) returns the structured JSON result. Only if the model
                         lists unreadable labels in `crop_requests` is ONE follow-up request
                         sent with those full-resolution crops. Never more than two requests.
  3. normalize_result()  validates/cleans the model JSON into the public format:
                         clamps confidence, drops guessed quantities, turns vague names into
                         "unknown items", marks low-confidence items for confirmation
  4. merge_items()       merges duplicate detections (within a response and across extra
                         photos) and folds new detections into the player's edited list

The public result format (what the frontend gets):
{
  "image_quality": "good | acceptable | poor",
  "needs_another_photo": bool,
  "photo_advice": str,
  "detected_items": [{id, name, detected_name, brand, category, quantity,
                      quantity_is_certain, confidence, needs_confirmation, evidence,
                      user_confirmed}],
  "unknown_items": [{id, description, confidence}]
}
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import re
import time
import uuid

from PIL import Image, ImageOps, ImageStat

log = logging.getLogger("fridge")

# --- tunables ----------------------------------------------------------------
MODEL_MAX_EDGE = 2576          # long edge sent to the model (keeps 4 photos + crops < 20 MB)
ZOOM_MAX_EDGE = 1568           # close-up crops are sent at most this large
ZOOM_MIN_EDGE = 768            # tiny crops are upscaled to at least this, for legibility
MAX_PHOTOS_PER_REQUEST = 4
MAX_CROP_REGIONS = 3           # close-ups the model may ask for (sent together in ONE follow-up)
DETECT_BUDGET_SECONDS = 90     # hard limit for one detection request, all Gemini calls included
CROP_PASS_MIN_SECONDS = 30     # skip the optional crop follow-up with less time than this left
MIN_PHOTO_EDGE = 300
CONFIRM_THRESHOLD = 0.75       # below this an item needs the player's confirmation
UNKNOWN_THRESHOLD = 0.35       # below this an item is reported as an unknown item instead
MAX_ITEMS = 80
MAX_UNKNOWN = 15

CATEGORIES = [
    "dairy", "eggs", "meat", "fish_seafood", "vegetables", "fruit", "herbs",
    "sauces_condiments", "spreads", "drinks", "bakery", "grains_pasta",
    "prepared_food", "frozen", "snacks", "other",
]

# Names that describe a container, not a product -> unknown item.
VAGUE_NAMES = {
    "unknown", "unknown item", "item", "items", "food", "product", "container", "jar",
    "bottle", "box", "package", "packet", "pack", "tub", "bag", "carton", "can", "tin",
    "pot", "tray", "wrap", "foil", "leftovers", "leftover", "tupperware", "packaged food",
    "unidentified item", "misc", "something",
}
NO_BRAND = {"", "none", "null", "unknown", "n/a", "na", "generic", "no brand", "unbranded", "-"}

# Spelling variants folded together when matching duplicates.
SYNONYMS = {
    "yoghurt": "yogurt", "chilli": "chili", "chile": "chili", "aubergine": "eggplant",
    "courgette": "zucchini", "capsicum": "bell pepper", "coriander": "cilantro",
    "spring onion": "green onion", "scallion": "green onion", "rocket": "arugula",
    "minced beef": "ground beef", "beef mince": "ground beef",
}


class DetectionError(Exception):
    """code: bad_image | too_small | malformed | too_many_steps"""

    def __init__(self, message: str, status: int = 400, code: str = "bad_image"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


# =============================================================================
# 1. Images
# =============================================================================

def decode_image(b64: str) -> Image.Image:
    if not isinstance(b64, str) or not b64:
        raise DetectionError("No image received.")
    if "," in b64[:100]:  # tolerate a data: URL prefix
        b64 = b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(b64, validate=False)
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
    except (binascii.Error, OSError, ValueError, Image.DecompressionBombError):
        raise DetectionError("That file couldn't be read as a photo. Try a JPEG or PNG.")
    if min(img.size) < MIN_PHOTO_EDGE:
        raise DetectionError("That photo is too small to read. Use the full-size camera photo.", 400, "too_small")
    return img


def fit(img: Image.Image, max_edge: int) -> Image.Image:
    w, h = img.size
    scale = max_edge / max(w, h)
    if scale >= 1:
        return img
    return img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)


def jpeg_bytes(img: Image.Image, quality: int = 88) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def too_dark(img: Image.Image) -> bool:
    """Cheap pre-check so a pitch-black photo doesn't cost an API call."""
    return ImageStat.Stat(img.convert("L").resize((64, 64))).mean[0] < 22


# =============================================================================
# 2. Model call (one structured-output request, optional single crop follow-up)
# =============================================================================

MODEL_SCHEMA = {
    "type": "object",
    "properties": {
        "image_quality": {"type": "string", "enum": ["good", "acceptable", "poor"]},
        "needs_another_photo": {"type": "boolean"},
        "photo_advice": {"type": "string"},
        "detected_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "brand": {"type": ["string", "null"]},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "quantity": {"type": ["integer", "null"]},
                    "quantity_is_certain": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "evidence": {"type": "string"},
                },
                "required": ["name", "brand", "category", "quantity",
                             "quantity_is_certain", "confidence", "evidence"],
                "additionalProperties": False,
            },
        },
        "unknown_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["description", "confidence"],
                "additionalProperties": False,
            },
        },
        "crop_requests": {
            "type": "array",
            "description": "Usually empty. Regions to see in close-up, only when an item's identity depends on a label you cannot read.",
            "items": {
                "type": "object",
                "properties": {
                    "photo": {"type": "integer"},
                    "x0": {"type": "integer"}, "y0": {"type": "integer"},
                    "x1": {"type": "integer"}, "y1": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["photo", "x0", "y0", "x1", "y1", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["image_quality", "needs_another_photo", "photo_advice",
                 "detected_items", "unknown_items", "crop_requests"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You extract the food products visible in photos of a fridge (shelves, drawers, door) for a cooking app. The player will check your list, and a recipe will be built from it, so an invented item is worse than a missed one, but missing a clearly visible item is also an error.

How to work:
1. Scan every photo systematically: each shelf top to bottom, left to right, then door shelves and drawers. Look at the back of shelves and behind items in front.
2. Read labels and brand names before naming a packaged product. Look closely at small or distant items and partly hidden items, and count units carefully.
3. Decide each item from what you can actually see, and return the complete result in this answer.
4. crop_requests: leave it EMPTY in almost all cases. Only if an item's identity depends on a label that is too small to read, list up to 3 tight regions (photo number, x0/y0/x1/y1 normalized 0-1000, reason). You will then get those close-ups once. Still report your best current result now (uncertain items as low confidence or unknown).

Rules for each detected item:
- name: the generic ingredient a cook would use, lowercase (e.g. "milk", "greek yogurt", "cheddar cheese", "chicken breast", "ketchup", "red bell pepper"). Be exactly as specific as the evidence allows: "cheese" when the type isn't visible, "cheddar cheese" only if a label or the look makes it clear. Never put the brand in the name.
- brand: only text you can actually read on the pack (or an unmistakable logo). If you can't read it, use null. Never infer a brand from colour or packaging shape.
- category: the best fit from the allowed list.
- One entry per distinct product. Several identical units of the same product (three of the same yogurt pot, a dozen eggs) are ONE entry with a quantity. The same physical item seen in two photos is ONE entry: photos may overlap, so don't count it twice. Different brands or varieties of the same thing are separate entries.
- quantity: an integer ONLY when every unit can be seen and counted individually (eggs in an open carton, separate cans, separate pots, whole vegetables). Then quantity_is_certain is true. If units are hidden, stacked out of view, or the item is loose or bulk (a bag of spinach, a bunch of grapes), set quantity to null and quantity_is_certain to false. Never estimate weight, volume or how full a container is.
- confidence, between 0 and 1, calibrated honestly:
  0.90-1.00 label read, or the item is unmistakable and fully visible
  0.75-0.89 clearly visible and identifiable, but no readable label
  0.50-0.74 partly hidden, blurry, or could be one of two similar things
  below 0.50 you are mostly guessing; report it as an unknown item instead
- evidence: a few words on where it is and what you saw (e.g. "top shelf left, label reads 'Arla Mellanmjölk'", "crisper drawer, 4 loose tomatoes").

Unknown items: anything that's food but can't be identified reliably goes in unknown_items with a short neutral description of what you see and where (e.g. "opaque white tub with no visible label, middle shelf right", "foil-covered bowl, bottom shelf"). Don't guess the contents of opaque, unlabelled or covered containers, leftovers or jars whose label you can't read: those are unknown items. Never list items that are hidden, implied, or typical for a fridge but not actually visible.

Ignore non-food items (ice packs, medicine, cosmetics, batteries) and the fridge itself.

Photo quality:
- image_quality: "good" when nearly everything is legible; "acceptable" when usable but parts are dark, blurred, cut off or blocked; "poor" when you can't reliably identify most of the contents.
- needs_another_photo: true when quality is poor, or when a significant area (a shelf, the door, a drawer) is cut off, too dark or blocked.
- photo_advice: one short, specific sentence telling the player what to photograph or how to retake it (e.g. "The door shelves are cut off - take a separate photo of the door."). If nothing more is needed, briefly say what you found."""


def _crop(images_full, inp):
    """Crop a requested region (coordinates normalized 0-1000) from the full-resolution photo."""
    try:
        idx = int(inp.get("photo", 0)) - 1
    except (TypeError, ValueError):
        idx = -1
    if not 0 <= idx < len(images_full):
        return None, f"There is no photo {inp.get('photo')}. Photos are numbered 1 to {len(images_full)}."
    full = images_full[idx]
    try:
        x0, x1 = sorted((float(inp["x0"]), float(inp["x1"])))
        y0, y1 = sorted((float(inp["y0"]), float(inp["y1"])))
    except (KeyError, TypeError, ValueError):
        return None, "Invalid coordinates. Use x0, y0, x1, y1 between 0 and 1000."
    x0, y0 = max(0.0, x0), max(0.0, y0)
    x1, y1 = min(1000.0, x1), min(1000.0, y1)
    if x1 - x0 < 5 or y1 - y0 < 5:
        return None, "That region is empty or too small. Coordinates are 0-1000 across the photo."
    w, h = full.size
    box = (round(x0 / 1000 * w), round(y0 / 1000 * h), round(x1 / 1000 * w), round(y1 / 1000 * h))
    crop = full.crop(box)
    long_edge = max(crop.size)
    if long_edge > ZOOM_MAX_EDGE:
        crop = fit(crop, ZOOM_MAX_EDGE)
    elif long_edge < ZOOM_MIN_EDGE:
        s = ZOOM_MIN_EDGE / long_edge
        crop = crop.resize((round(crop.size[0] * s), round(crop.size[1] * s)), Image.LANCZOS)
    return crop, f"Photo {idx + 1}, region x {x0:.0f}-{x1:.0f}, y {y0:.0f}-{y1:.0f} (of 1000)."


def run_detection(generate, images_full: list[Image.Image], *, deadline: float | None = None,
                  tag: str = "", allow_crop: bool = True, stats: dict | None = None) -> tuple[dict, dict]:
    """Analyse the photos with as few Gemini requests as possible.

    Request 1 (always the only "normal" request): the full photos -> the complete structured result.
    The model may list up to MAX_CROP_REGIONS regions in `crop_requests` when an item's identity
    depends on a label it can't read. Only then is ONE follow-up request sent with those crops, to
    correct the result; no further rounds. If the follow-up fails, the first result is kept.

    `generate(contents=..., system=..., schema=..., thinking=..., deadline=..., stats=..., tag=...)`
    sends one generate_content call. Returns (raw_json, stats) where stats["requests"] is the
    number of requests actually sent to Gemini for this scan.
    """
    from google.genai import types

    deadline = deadline or (time.monotonic() + DETECT_BUDGET_SECONDS)
    stats = stats if stats is not None else {}     # caller's dict, so counts survive an exception
    stats.setdefault("requests", 0)
    stats.setdefault("crops", 0)
    photo_parts = []
    for i, img in enumerate(images_full, 1):
        sent = fit(img, MODEL_MAX_EDGE)
        photo_parts.append(types.Part.from_text(text=f"Photo {i}:"))
        photo_parts.append(types.Part.from_bytes(data=jpeg_bytes(sent), mime_type="image/jpeg"))
    ask = types.Part.from_text(text=(
        f"{len(images_full)} photo(s) of the same fridge. Extract every clearly visible food product "
        "following the rules, in this single pass."))

    first = _ask_json(generate, [types.Content(role="user", parts=photo_parts + [ask])],
                      deadline, stats, f"{tag} pass=1")

    regions = first.get("crop_requests") if isinstance(first.get("crop_requests"), list) else []
    regions = [r for r in regions if isinstance(r, dict)][:MAX_CROP_REGIONS]
    if not (allow_crop and regions):
        return first, stats
    if deadline - time.monotonic() < CROP_PASS_MIN_SECONDS:
        log.info("%s crop pass skipped: not enough time left", tag)
        return first, stats

    crop_parts = []
    for r in regions:
        crop, note = _crop(images_full, r)
        if crop is not None:
            crop_parts.append(types.Part.from_text(text=f"Close-up - {note} Reason: {_clean_text(r.get('reason'), 120)}"))
            crop_parts.append(types.Part.from_bytes(data=jpeg_bytes(crop), mime_type="image/jpeg"))
    if not crop_parts:
        return first, stats
    stats["crops"] = len(crop_parts) // 2
    first_json = json.dumps({k: v for k, v in first.items() if k != "crop_requests"}, ensure_ascii=False)
    follow_up = [types.Content(role="user", parts=photo_parts + crop_parts + [types.Part.from_text(text=(
        "These close-ups are the regions you asked to see. Here is your first result:\n" + first_json +
        "\nReturn the complete corrected result for all photos. crop_requests must be an empty list."))])]
    try:
        second = _ask_json(generate, follow_up, deadline, stats, f"{tag} pass=2")
    except Exception as e:  # keep the first, already valid, result
        log.warning("%s crop pass failed (%s) - keeping the first result", tag, getattr(e, "code", type(e).__name__))
        return first, stats
    return second, stats


def _ask_json(generate, contents, deadline, stats, tag) -> dict:
    resp = generate(contents=contents, system=SYSTEM_PROMPT, schema=MODEL_SCHEMA, thinking="medium",
                    deadline=deadline, stats=stats, tag=tag)
    text = _final_text(resp)
    if not text:
        raise DetectionError("Gemini returned no detection result. Retry, or use demo mode.", 502, "malformed")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise DetectionError("Gemini returned malformed data (not valid JSON). Retry, or use demo mode.",
                             502, "malformed")
    if not isinstance(data, dict):
        raise DetectionError("Gemini returned an unexpected result. Retry, or use demo mode.", 502, "malformed")
    return data


def _final_text(resp) -> str:
    content = resp.candidates[0].content if resp.candidates else None
    parts = (content.parts if content else None) or []
    return "".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False))


# =============================================================================
# 3. Validation / normalization
# =============================================================================

def _clean_text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _confidence(value) -> float:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return 0.0
    if c != c:  # NaN
        return 0.0
    if 2 <= c <= 100:  # tolerate a percentage ("85"); 1 < c < 2 is just over-confident -> clamp
        c /= 100
    return round(min(1.0, max(0.0, c)), 2)


def match_key(name: str) -> str:
    """Normalized key used to recognise duplicates ("Tomatoes" == "tomato")."""
    n = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    n = " ".join(n.split())
    for k, v in SYNONYMS.items():
        n = re.sub(rf"\b{k}\b", v, n)
    words = n.split()
    if words:
        w = words[-1]
        if w.endswith("ies") and len(w) > 4:
            w = w[:-3] + "y"
        elif w.endswith("oes") and len(w) > 4:
            w = w[:-2]
        elif w.endswith("s") and not w.endswith(("ss", "us", "is")) and len(w) > 3:
            w = w[:-1]
        words[-1] = w
    return " ".join(words)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def normalize_result(raw) -> dict:
    """Validate the model JSON and convert it to the public format. Never trusts the model:
    every field is type-checked, clamped or defaulted."""
    if not isinstance(raw, dict):
        raise DetectionError("Gemini returned an unexpected result. Retry, or use demo mode.", 502, "malformed")

    quality = raw.get("image_quality")
    if quality not in ("good", "acceptable", "poor"):
        quality = "acceptable"

    items, unknown = [], []
    for it in raw.get("detected_items") or []:
        if not isinstance(it, dict):
            continue
        name = _clean_text(it.get("name"), 60).lower()
        conf = _confidence(it.get("confidence"))
        evidence = _clean_text(it.get("evidence"), 160)
        if not name or name in VAGUE_NAMES or name.startswith("unknown"):
            unknown.append({"id": _new_id("unk"), "confidence": conf,
                            "description": evidence or "Unidentified item"})
            continue
        if conf < UNKNOWN_THRESHOLD:
            desc = f"Possibly {name}" + (f" ({evidence})" if evidence else "")
            unknown.append({"id": _new_id("unk"), "confidence": conf, "description": desc[:160]})
            continue

        brand = _clean_text(it.get("brand"), 40) or None
        if brand and brand.lower() in NO_BRAND:
            brand = None
        if brand and match_key(brand) == match_key(name):
            brand = None
        # strip a brand the model put into the name anyway
        if brand and name.startswith(brand.lower() + " "):
            name = name[len(brand) + 1:]

        category = it.get("category") if it.get("category") in CATEGORIES else "other"

        qty, certain = it.get("quantity"), bool(it.get("quantity_is_certain"))
        if isinstance(qty, bool) or not isinstance(qty, (int, float)) or qty != int(qty) or not 1 <= qty <= 99:
            qty = None
        if qty is None or not certain:  # only report counts the model says it actually counted
            qty, certain = None, False

        if not evidence:  # no grounding given -> treat as less certain
            conf = min(conf, CONFIRM_THRESHOLD - 0.01)

        items.append({
            "id": _new_id("itm"),
            "name": name,
            "detected_name": name,
            "brand": brand,
            "category": category,
            "quantity": int(qty) if qty is not None else None,
            "quantity_is_certain": certain,
            "confidence": conf,
            "needs_confirmation": conf < CONFIRM_THRESHOLD,
            "evidence": evidence,
            "user_confirmed": False,
        })

    for u in raw.get("unknown_items") or []:
        if not isinstance(u, dict):
            continue
        desc = _clean_text(u.get("description"), 160)
        if desc:
            unknown.append({"id": _new_id("unk"), "confidence": _confidence(u.get("confidence")),
                            "description": desc})

    merged = merge_items([], items)
    needs_more = bool(raw.get("needs_another_photo")) or quality == "poor"
    advice = _clean_text(raw.get("photo_advice"), 240)
    if needs_more and not advice:
        advice = "Take another photo with the light on, holding the phone steady."

    return {
        "image_quality": quality,
        "needs_another_photo": needs_more,
        "photo_advice": advice,
        "detected_items": merged[:MAX_ITEMS],
        "unknown_items": _dedupe_unknown(unknown)[:MAX_UNKNOWN],
    }


def _dedupe_unknown(unknown: list[dict]) -> list[dict]:
    seen, out = set(), []
    for u in unknown:
        k = match_key(u["description"])
        if k not in seen:
            seen.add(k)
            out.append(u)
    return out


# =============================================================================
# 4. Merging
# =============================================================================

def _combine(target: dict, other: dict) -> None:
    """Fold `other` (a duplicate detection) into `target` in place."""
    if not target.get("brand") and other.get("brand"):
        target["brand"] = other["brand"]
    if target.get("category") == "other" and other.get("category") not in (None, "other"):
        target["category"] = other["category"]
    # Quantities: the same units may appear in two photos, so never add them up.
    # Keep the larger count; it's only "certain" if both detections agree exactly.
    q1, q2 = target.get("quantity"), other.get("quantity")
    if q1 is not None and q2 is not None:
        target["quantity"] = max(q1, q2)
        target["quantity_is_certain"] = bool(
            q1 == q2 and target.get("quantity_is_certain") and other.get("quantity_is_certain"))
    elif q2 is not None and not target.get("user_confirmed"):
        target["quantity"], target["quantity_is_certain"] = q2, bool(other.get("quantity_is_certain"))
    target["confidence"] = max(target.get("confidence", 0), other.get("confidence", 0))
    if other.get("user_confirmed"):
        target["user_confirmed"] = True
    target["needs_confirmation"] = (not target.get("user_confirmed")
                                    and target["confidence"] < CONFIRM_THRESHOLD)
    ev = [e for e in (target.get("evidence"), other.get("evidence")) if e]
    target["evidence"] = "; ".join(dict.fromkeys(ev))[:240]


def _keys(item: dict) -> set[str]:
    """An item matches by its current name and by the name the AI first gave it,
    so a player's rename ("milk" -> "whole milk") still absorbs later sightings."""
    return {match_key(n) for n in (item.get("name"), item.get("detected_name")) if n}


def merge_items(existing: list[dict], new: list[dict]) -> list[dict]:
    """Merge `new` detections into `existing` (which may contain the player's edits).

    Same generic name + same brand -> one item. An unbranded detection merges into the
    item of the same name if exactly one branded/unbranded match exists; two different
    brands of the same product stay separate. Player-edited names are kept.
    """
    out = [dict(i) for i in existing]
    for item in new:
        key = match_key(item["name"])
        brand = (item.get("brand") or "").lower()
        same_name = [o for o in out if key in _keys(o)]
        exact = [o for o in same_name if (o.get("brand") or "").lower() == brand]
        target = None
        if exact:
            target = exact[0]
        elif same_name:
            if not brand:
                # unbranded sighting of a product we already have: fold into the closest match
                target = max(same_name, key=lambda o: o.get("confidence", 0))
            elif not any(o.get("brand") for o in same_name):
                target = same_name[0]              # first time we read this item's brand
        if target is not None:
            _combine(target, item)
        else:
            out.append(dict(item))
    return out


def sanitize_existing(items) -> list[dict]:
    """Validate the item list the browser sends back (player edits) before merging."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        name = _clean_text(it.get("name"), 60).lower()
        if not name:
            continue
        brand = _clean_text(it.get("brand"), 40) or None
        qty = it.get("quantity")
        if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= 99:
            qty = None
        confirmed = bool(it.get("user_confirmed"))
        conf = _confidence(it.get("confidence")) if it.get("confidence") is not None else 1.0
        out.append({
            "id": _clean_text(it.get("id"), 20) or _new_id("itm"),
            "name": name,
            "detected_name": _clean_text(it.get("detected_name"), 60).lower() or name,
            "brand": brand,
            "category": it.get("category") if it.get("category") in CATEGORIES else "other",
            "quantity": qty,
            "quantity_is_certain": bool(it.get("quantity_is_certain")) and qty is not None,
            "confidence": conf,
            "needs_confirmation": not confirmed and conf < CONFIRM_THRESHOLD,
            "evidence": _clean_text(it.get("evidence"), 240),
            "user_confirmed": confirmed,
        })
    return out[:MAX_ITEMS]


def sanitize_unknown(items) -> list[dict]:
    out = []
    for u in items or []:
        if isinstance(u, dict) and _clean_text(u.get("description"), 160):
            out.append({"id": _clean_text(u.get("id"), 20) or _new_id("unk"),
                        "description": _clean_text(u.get("description"), 160),
                        "confidence": _confidence(u.get("confidence"))})
    return out[:MAX_UNKNOWN]


def combine_results(previous: dict | None, new: dict) -> dict:
    """Merge a new photo's result into the result the player already has."""
    if not previous:
        return new
    items = merge_items(sanitize_existing(previous.get("detected_items")), new["detected_items"])
    unknown = _dedupe_unknown(sanitize_unknown(previous.get("unknown_items")) + new["unknown_items"])
    return {
        "image_quality": new["image_quality"],
        "needs_another_photo": new["needs_another_photo"],
        "photo_advice": new["photo_advice"],
        "detected_items": items[:MAX_ITEMS],
        "unknown_items": unknown[:MAX_UNKNOWN],
    }
