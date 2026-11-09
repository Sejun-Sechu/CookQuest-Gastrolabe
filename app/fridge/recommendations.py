"""Recipe generation: confirmed ingredients -> exactly five recipe objects.

Flow (at most TWO Gemini requests per generation):
  1. One request asks for RECIPE_COUNT recipes (structured JSON).
  2. Every recipe is normalised; recoverable problems are fixed, not rejected:
       id generated when missing, skill_points forced to 2, difficulty clamped to 1-5,
       ingredient_match_percent clamped to 0-100, optional_ingredients defaults to [],
       ingredients the player didn't confirm are moved to optional_ingredients,
       dietary tags are always decided by dietary.py (fallback "No specific dietary category").
     A recipe is rejected only when essential information is unusable: no title, fewer than
     MIN_CONFIRMED_INGREDIENTS confirmed ingredients, or not exactly five usable tasks.
  3. If fewer than RECIPE_COUNT are valid, ONE repair request sends the validation errors and asks
     only for the missing number. No further retries (protects the free quota).
  4. Images come from TheMealDB (recipe_images.py); a missing image uses a local placeholder and
     never removes a recipe.

Errors are returned with one of: RATE_LIMITED, INVALID_API_KEY, MODEL_UNAVAILABLE,
INVALID_RECIPE_RESPONSE, NETWORK_TIMEOUT, SERVER_ERROR.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from app.fridge import dietary as dietary

log = logging.getLogger("fridge")

RECIPE_COUNT = 5
SKILL_POINTS = 2
MIN_CONFIRMED_INGREDIENTS = 2
MAX_OPTIONAL = 5
RECIPE_BUDGET_SECONDS = 100
DEFAULT_DIFFICULTY = {"beginner": 2, "intermediate": 3, "expert": 4}

# --- error types ---------------------------------------------------------------
ERROR_TYPES = {
    "rate_limit_minute": "RATE_LIMITED", "quota_daily": "RATE_LIMITED",
    "invalid_key": "INVALID_API_KEY", "permission": "INVALID_API_KEY",
    "model_unavailable": "MODEL_UNAVAILABLE", "no_free_quota": "MODEL_UNAVAILABLE",
    "billing_required": "MODEL_UNAVAILABLE",
    "malformed": "INVALID_RECIPE_RESPONSE", "truncated": "INVALID_RECIPE_RESPONSE",
    "blocked": "INVALID_RECIPE_RESPONSE", "invalid_recipes": "INVALID_RECIPE_RESPONSE",
    "timeout": "NETWORK_TIMEOUT", "network": "NETWORK_TIMEOUT",
}


def error_type(code: str | None) -> str:
    return ERROR_TYPES.get(code or "", "SERVER_ERROR")


class RecipeError(Exception):
    def __init__(self, message, status=502, code="invalid_recipes", details=None):
        super().__init__(message)
        self.message, self.status, self.code, self.details = message, status, code, details or []
        self.quota_type = self.retry_after = None


# --- schema + prompt --------------------------------------------------------------
_RECIPE = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "dish_name": {"type": "string"},
        "description": {"type": "string"},
        "image_search_term": {"type": "string",
                              "description": "1-3 word generic English dish name for a photo search, e.g. 'frittata'."},
        "difficulty": {"type": "integer", "description": "1 (very easy) to 5 (very hard)"},
        "ingredient_match_percent": {"type": "integer",
                                     "description": "Share of this recipe's ingredients the player already has, 0-100"},
        "servings": {"type": "integer"},
        "prep_minutes": {"type": "integer"},
        "cook_minutes": {"type": "integer"},
        "ingredients": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": "string"},
                    "source": {"type": "string", "enum": ["fridge", "pantry"]},
                    "origin": {"type": "string", "enum": dietary.ORIGINS},
                },
                "required": ["name", "quantity", "source", "origin"],
            },
        },
        "optional_ingredients": {"type": "array", "items": {"type": "string"}},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string"},
                    "instruction": {"type": "string"},
                    "proof_photo": {"type": "string"},
                },
                "required": ["skill", "instruction", "proof_photo"],
            },
        },
    },
    "required": ["dish_name", "description", "image_search_term", "difficulty", "ingredient_match_percent",
                 "servings", "prep_minutes", "cook_minutes", "ingredients", "optional_ingredients", "tasks"],
}

RECIPE_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok", "need_more_ingredients"]},
        "follow_up_question": {"type": "string"},
        "suggested_additions": {"type": "array", "items": {"type": "string"}},
        "recipes": {"type": "array", "items": _RECIPE},
    },
    "required": ["status", "follow_up_question", "suggested_additions", "recipes"],
}

LEVEL_GUIDE = {
    "beginner": (
        "BEGINNER: choose forgiving dishes. Techniques: basic knife work (rough chop, slicing), boiling, "
        "simple sauté, mixing a basic dressing. Instructions are detailed and reassuring: explain how to "
        "hold the knife, what heat level to use, and what 'done' looks like."
    ),
    "intermediate": (
        "INTERMEDIATE: techniques such as an even dice, sautéing to golden brown, cooking pasta/grains "
        "to the right texture, a pan sauce or emulsified dressing, cooking eggs to a specific doneness. "
        "Instructions are clear but concise; assume the player knows basic terms."
    ),
    "expert": (
        "EXPERT: demanding techniques such as brunoise or julienne, a hard sear with fond, reducing and "
        "mounting a sauce with butter, precise doneness, emulsions, tempering. Instructions are brief "
        "and use proper culinary terminology."
    ),
}

SYSTEM_PROMPT = f"""You design home-cooking recipes for a cooking-skills game.

Return EXACTLY {RECIPE_COUNT} genuinely different recipes in "recipes".
- When the ingredients allow it, vary the dietary profile: for example with meat or fish, vegetarian, and
  vegan (no egg, dairy, honey, meat or fish), and include options without pork or alcohol. Never add an
  ingredient just to reach a profile.

Ingredient rules (strict, for every recipe):
- "ingredients" may contain ONLY items from the player's confirmed fridge list and the pantry staples they
  confirmed - not even salt, oil or water unless listed. Use at least {MIN_CONFIRMED_INGREDIENTS} of them.
- "optional_ingredients": up to 3 extras the player does NOT have that would improve the dish (the recipe
  must work without them), or [] - usually [].
- ingredient_match_percent: how much of the full recipe (ingredients + optional_ingredients) the player
  already has, 0-100.
- Give the quantity the recipe needs (e.g. "1 medium", "200 g", "2 tbsp"). You cannot see how much the
  player has, so never assume the amounts available.
- For every ingredient give "origin": plant, dairy, egg, honey, fish, shellfish, meat, pork (any pork
  product), alcohol (wine, beer, mirin, spirits...), gelatin, or uncertain (contents unknown).
- Do not write dietary labels: the app decides dietary tags itself.
- If no sensible dish is possible, set status to "need_more_ingredients", ask one short follow_up_question,
  list 3-6 suggested_additions, and return an empty recipes list.

Each recipe: dish_name, a one-sentence description, image_search_term (1-3 word generic English dish
name such as "frittata" or "chicken stir fry"), difficulty 1-5, servings, prep_minutes, cook_minutes.

Task rules (strict, for every recipe):
- EXACTLY FIVE tasks, in cooking order, together covering the whole recipe from prep to plating.
- Each task practices one distinct, visually checkable technique. "skill" is a short technique name
  (e.g. "Even dicing", "Golden sauté", "Sauce consistency", "Plating").
- "instruction" says what to do, matched to the skill level.
- "proof_photo" says exactly what a phone photo should show so a judge can assess the technique
  (e.g. "The diced pepper spread in one layer on the board, shot from directly above.")."""


# --- normalisation -----------------------------------------------------------------
def _int(v, default):
    if isinstance(v, bool):
        return default
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def _text(v, limit=400) -> str:
    return " ".join(str(v or "").split())[:limit]


PREP_WORDS = {"chopped", "diced", "sliced", "grated", "shredded", "fresh", "large", "small", "medium",
              "minced", "crushed", "whole", "ripe", "cooked", "raw", "boneless", "skinless", "beaten",
              "softened", "melted", "cold", "warm", "halved", "peeled", "of", "a", "the", "some"}


def _words(name: str) -> list[str]:
    from app.fridge.detection import match_key       # singular/synonym normalisation ("tomatoes" -> "tomato")
    return [w for w in match_key(name).split() if w]


def _in_list(name: str, allowed: set[str]) -> bool:
    """Is a recipe ingredient one of the confirmed items?

    Same item ("tomatoes" = "tomato"), a more generic name ("cheese" for confirmed "cheddar cheese"),
    or the same item with preparation words ("grated cheddar cheese"). A more specific item is NOT a
    match: confirmed "onion" does not cover "spring onions", "milk" does not cover "coconut milk"."""
    rw = [w for w in _words(name) if w not in PREP_WORDS]
    if not rw:
        return False
    for a in allowed:
        aw = _words(a)
        if not aw:
            continue
        if rw == aw or set(rw) <= set(aw):
            return True
    return False


def normalize_recipe(raw, allowed: set[str], level: str,
                     used_ids: set[str]) -> tuple[dict | None, list[str]]:
    """Return (recipe, []) or (None, [reasons]) - rejecting only on unusable essentials."""
    if not isinstance(raw, dict):
        return None, ["not an object"]
    errors = []
    title = _text(raw.get("dish_name") or raw.get("title"), 120)
    if not title:
        errors.append("missing title")

    ingredients, optional = [], []
    for ing in raw.get("ingredients") if isinstance(raw.get("ingredients"), list) else []:
        if isinstance(ing, str):
            ing = {"name": ing}
        if not isinstance(ing, dict):
            continue
        name = _text(ing.get("name"), 60)
        if not name:
            continue
        if not _in_list(name, allowed):
            optional.append(name)          # not confirmed by the player -> optional, never silently "had"
            continue
        ingredients.append({
            "name": name,
            "quantity": _text(ing.get("quantity"), 40) or "as needed",
            "source": ing.get("source") if ing.get("source") in ("fridge", "pantry") else "fridge",
            "origin": ing.get("origin") if ing.get("origin") in dietary.ORIGINS else None,
        })
    if len(ingredients) < MIN_CONFIRMED_INGREDIENTS:
        errors.append(f"only {len(ingredients)} confirmed ingredient(s); needs at least {MIN_CONFIRMED_INGREDIENTS}"
                      + (f" (unconfirmed: {', '.join(optional[:5])})" if optional else ""))

    tasks = []
    raw_tasks = raw.get("tasks") if isinstance(raw.get("tasks"), list) else []
    for t in raw_tasks:
        if isinstance(t, dict):
            task = {k: _text(t.get(k), 600) for k in ("skill", "instruction", "proof_photo")}
            if all(task.values()):
                tasks.append(task)
    if len(tasks) != 5:
        errors.append(f"needs exactly 5 complete tasks, got {len(tasks)}")

    if errors:
        return None, errors

    for extra in raw.get("optional_ingredients") if isinstance(raw.get("optional_ingredients"), list) else []:
        name = _text(extra, 60)
        if name and not _in_list(name, allowed) and name.lower() not in (o.lower() for o in optional):
            optional.append(name)
    optional = optional[:MAX_OPTIONAL]

    rid = _text(raw.get("id"), 40)
    if not rid or rid in used_ids:
        rid = f"rcp_{uuid.uuid4().hex[:8]}"
    used_ids.add(rid)

    computed_match = round(100 * len(ingredients) / (len(ingredients) + len(optional)))
    recipe = {
        "id": rid,
        "dish_name": title,
        "description": _text(raw.get("description"), 300),
        "image_search_term": _text(raw.get("image_search_term"), 60) or title,
        "difficulty": min(5, max(1, _int(raw.get("difficulty"), DEFAULT_DIFFICULTY.get(level, 3)))),
        "ingredient_match_percent": min(100, max(0, _int(raw.get("ingredient_match_percent"), computed_match))),
        "skill_points": SKILL_POINTS,
        "servings": _int(raw.get("servings"), 2) if 1 <= _int(raw.get("servings"), 2) <= 50 else 2,
        "prep_minutes": max(0, _int(raw.get("prep_minutes"), 0)),
        "cook_minutes": max(0, _int(raw.get("cook_minutes"), 0)),
        "ingredients": ingredients,
        "optional_ingredients": optional,
        "tasks": tasks,
        "skill_level": level,
    }
    if optional:
        # the model can't claim 100 % when some listed items are missing
        recipe["ingredient_match_percent"] = min(recipe["ingredient_match_percent"], computed_match)
    diet = dietary.classify(ingredients)
    if not diet.get("dietary_tags"):
        diet = {"dietary_tags": ["No specific dietary category"], "dietary_note": "",
                "dietary_badges": [{"tag": "No specific dietary category", "detail": ""}]}
    recipe.update(diet)
    return recipe, []


# --- generation ---------------------------------------------------------------------
def _parse(text: str) -> dict:
    if not text:
        raise RecipeError("Gemini returned an empty recipe response.", 502, "malformed")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise RecipeError("Gemini returned malformed recipe data (not valid JSON).", 502, "malformed")
    if not isinstance(data, dict):
        raise RecipeError("Gemini returned recipe data in an unexpected shape.", 502, "malformed")
    return data


def generate(call_json, *, level, ingredients, staples, tag=""):
    """call_json(text, stats) -> response text from one Gemini request.

    Returns {"status": "ok", "recipes": [...], "gemini_requests": n, "rejected": [...]} or a
    need_more_ingredients dict. Raises RecipeError / AIError."""
    allowed = {i.lower() for i in ingredients + staples}
    base = (f"Skill level: {level}\n{LEVEL_GUIDE[level]}\n\n"
            f"Confirmed fridge ingredients: {', '.join(ingredients)}\n"
            f"Confirmed pantry staples: {', '.join(staples) if staples else '(none - do not assume any)'}")
    stats = {"requests": 0}
    deadline = time.monotonic() + RECIPE_BUDGET_SECONDS
    used_ids: set[str] = set()
    valid, rejected = [], []

    def run(text, label):
        log.info("%s %s gemini request started", tag, label)
        data = _parse(call_json(text, stats, deadline))
        raw = data.get("recipes") if isinstance(data.get("recipes"), list) else []
        log.info("%s %s gemini response received: status=%s raw_recipe_count=%d",
                 tag, label, data.get("status"), len(raw))
        return data, raw

    def absorb(raw_list, label, limit):
        for n, raw in enumerate(raw_list, 1):
            if len(valid) >= limit:
                break
            recipe, errors = normalize_recipe(raw, allowed, level, used_ids)
            if recipe and recipe["dish_name"].lower() in (v["dish_name"].lower() for v in valid):
                recipe, errors = None, ["duplicate title"]
            if recipe:
                valid.append(recipe)
            else:
                title = _text(raw.get("dish_name"), 60) if isinstance(raw, dict) else "?"
                rejected.append({"recipe": title or f"#{n}", "errors": errors})
                log.warning("%s %s recipe %d rejected (%s): %s", tag, label, n, title or "untitled", "; ".join(errors))

    data, raw = run(base, "pass=1")
    if data.get("status") == "need_more_ingredients" and not raw:
        return {"status": "need_more_ingredients",
                "follow_up_question": _text(data.get("follow_up_question"), 200) or "What else do you have?",
                "suggested_additions": [s for s in (data.get("suggested_additions") or [])
                                        if isinstance(s, str) and s.lower() not in allowed][:6],
                "gemini_requests": stats["requests"]}
    if len(raw) != RECIPE_COUNT:
        log.warning("%s pass=1 returned %d recipes instead of %d", tag, len(raw), RECIPE_COUNT)
    absorb(raw, "pass=1", RECIPE_COUNT)

    missing = RECIPE_COUNT - len(valid)
    if missing > 0:                                   # ONE repair request, only for what's missing
        problems = "; ".join(f"'{r['recipe']}': {', '.join(r['errors'])}" for r in rejected) or \
                   f"only {len(raw)} recipe(s) were returned"
        have = ", ".join(v["dish_name"] for v in valid) or "none"
        repair = (f"{base}\n\nYour previous answer was missing {missing} usable recipe(s). Problems: {problems}.\n"
                  f"Already accepted (do not repeat): {have}.\n"
                  f"Return EXACTLY {missing} NEW recipe(s) in \"recipes\" that follow every rule.")
        try:
            _, raw2 = run(repair, "repair")
            absorb(raw2, "repair", RECIPE_COUNT)
        except Exception as e:                        # keep the valid ones from pass 1
            if not valid:
                raise
            log.warning("%s repair request failed (%s); keeping %d valid recipe(s)",
                        tag, getattr(e, "code", type(e).__name__), len(valid))

    log.info("%s final valid recipe count=%d (requested %d, rejected %d, gemini_requests=%d)",
             tag, len(valid), RECIPE_COUNT, len(rejected), stats["requests"])
    if not valid:
        raise RecipeError("Gemini didn't return any usable recipe. Retry, or use demo mode.", 502,
                          "invalid_recipes", rejected)
    return {"status": "ok", "recipes": valid, "gemini_requests": stats["requests"], "rejected": rejected}
