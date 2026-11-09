"""Ingredient-origin dietary categories: vegan, vegetarian, pescatarian and meat.
No religious certification or suitability assessment is performed.
"""

from __future__ import annotations

import re

TAGS = ["Vegan", "Vegetarian", "Pescatarian", "Contains meat",
        "No specific dietary category"]          # also the display order
ORIGINS = ["plant", "dairy", "egg", "honey", "fish", "shellfish", "meat", "pork", "alcohol",
           "gelatin", "uncertain"]

PORK_TERMS = ["pork", "bacon", "ham", "prosciutto", "pancetta", "chorizo", "pepperoni", "salami",
              "lard", "guanciale", "gammon", "speck", "mortadella", "spam", "pork belly", "hot dog"]
MEAT_TERMS = ["chicken", "beef", "lamb", "mutton", "veal", "turkey", "duck", "goat", "venison",
              "mince", "steak", "sausage", "meatball", "burger", "liver", "kebab", "pastrami",
              "brisket", "rabbit", "quail", "goose", "jerky", "meat"] + PORK_TERMS
FISH_TERMS = ["fish", "salmon", "tuna", "cod", "haddock", "mackerel", "sardine", "anchovy", "trout",
              "tilapia", "herring", "sea bass", "pollock", "halibut", "hake", "fish sauce",
              "worcestershire"]
SHELLFISH_TERMS = ["shrimp", "prawn", "crab", "lobster", "mussel", "clam", "oyster", "scallop",
                   "squid", "calamari", "octopus", "crayfish"]
DAIRY_TERMS = ["milk", "cheese", "butter", "cream", "yogurt", "yoghurt", "ghee", "kefir", "whey",
               "cheddar", "mozzarella", "parmesan", "feta", "halloumi", "ricotta", "mascarpone",
               "creme fraiche", "crème fraîche", "custard", "buttermilk", "paneer", "quark", "skyr",
               "brie", "gouda", "philadelphia"]
EGG_TERMS = ["egg", "mayonnaise", "mayo", "aioli", "meringue"]
HONEY_TERMS = ["honey"]
ALCOHOL_TERMS = ["wine", "beer", "rum", "vodka", "gin", "whisky", "whiskey", "brandy", "sherry",
                 "sake", "mirin", "liqueur", "cider", "prosecco", "champagne", "cognac", "bourbon",
                 "vermouth", "marsala", "ale", "lager", "stout"]
GELATIN_TERMS = ["gelatin", "gelatine", "marshmallow"]
UNCERTAIN_TERMS = ["stock", "broth", "bouillon", "gravy", "stock cube"]

# Checked before everything else: these contain a "meat/dairy/egg" word but are plant-based.
PLANT_PHRASES = ["peanut butter", "almond butter", "cashew butter", "nut butter", "cocoa butter",
                 "butternut", "butter bean", "butter lettuce", "coconut milk", "oat milk",
                 "almond milk", "soy milk", "soya milk", "rice milk", "oat drink", "coconut cream",
                 "eggplant", "cream of tartar", "vegetable stock", "vegetable broth", "veggie stock",
                 "plant-based", "plant based", "vegan", "veggie burger", "veggie sausage",
                 "vegetarian sausage", "ginger ale", "ginger beer", "root beer", "wine vinegar",
                 "cider vinegar", "rice vinegar", "fish-free", "jackfruit", "mushroom stock",
                 "hamburger bun", "graham"]

_WORDS = {
    "pork": PORK_TERMS, "meat": MEAT_TERMS, "fish": FISH_TERMS, "shellfish": SHELLFISH_TERMS,
    "dairy": DAIRY_TERMS, "egg": EGG_TERMS, "honey": HONEY_TERMS, "alcohol": ALCOHOL_TERMS,
    "gelatin": GELATIN_TERMS, "uncertain": UNCERTAIN_TERMS,
}


def _has(name: str, term: str) -> bool:
    # whole words, tolerant of plurals ("eggs", "sausages", "anchovies")
    pattern = rf"\b{re.escape(term)}(s|es)?\b"
    if term.endswith("y"):
        pattern += rf"|\b{re.escape(term[:-1])}ies\b"
    return re.search(pattern, name) is not None


PLANT_QUALIFIERS = ["vegan", "plant-based", "plant based", "dairy-free", "dairy free", "meat-free", "meatless"]


def lexicon_origins(name: str) -> set[str]:
    n = " ".join(str(name).lower().split())
    if any(q in n for q in PLANT_QUALIFIERS):      # "vegan cheese", "plant-based mince"
        return {"plant"}
    plant_phrase = any(p in n for p in PLANT_PHRASES)
    if plant_phrase:
        # strip the plant phrase(s) and see if anything animal is left ("vegan chicken" stays plant)
        for p in PLANT_PHRASES:
            n = n.replace(p, " ")
    found = {origin for origin, terms in _WORDS.items() if any(_has(n, t) for t in terms)}
    if "pork" in found:
        found.add("meat")
    if found:
        return found
    return {"plant"} if plant_phrase else set()


# Basic plant foods and pantry staples. The model sometimes answers "uncertain" for these
# (it once called plain salt uncertain); a known item overrides the model's "I don't know".
KNOWN_PLANT_TERMS = ["salt", "pepper", "black pepper", "sugar", "flour", "rice", "pasta", "noodle", "water",
                     "oil", "olive oil", "vegetable oil", "vinegar", "herbs", "dried herbs", "spice",
                     "chili flakes", "paprika", "cumin", "garlic", "onion", "tomato", "potato", "spinach",
                     "bell pepper", "carrot", "lettuce", "cucumber", "lemon", "lime", "mushroom", "bean",
                     "lentil", "chickpea", "tofu", "soy sauce", "ginger", "basil", "parsley", "coriander",
                     "cilantro", "broccoli", "zucchini", "courgette", "cabbage", "kale", "apple", "banana",
                     "orange", "avocado", "corn", "pea", "bread", "oats", "baking powder", "yeast"]


def _known_plant(name: str) -> bool:
    n = " ".join(str(name).lower().split())
    return any(_has(n, t) for t in KNOWN_PLANT_TERMS)


def origins_for(name: str, model_origin: str | None = None) -> set[str]:
    """Union of lexicon and model classification; unknown words fall back to the model, then plant.

    The model may only make a result STRICTER with a real class (pork, alcohol, dairy, ...).
    Its "uncertain" is ignored when the item is already identified by the lexicon."""
    found = lexicon_origins(name)
    mo = model_origin if model_origin in ORIGINS else None
    if mo == "uncertain" and (found or _known_plant(name)):
        mo = None
    if mo and mo != "plant":
        found.add(mo)
        if mo == "pork":
            found.add("meat")
    if not found:
        found = {mo or "plant"}
    if len(found) > 1:
        found.discard("plant")
    return found


def classify(ingredients: list[dict]) -> dict:
    """ingredients: [{"name": ..., "origin": <model's classification, optional>}]

    Returns {"dietary_tags": [...], "dietary_note": str, "dietary_badges": [{"tag", "detail"}]}.
    """
    per = []
    for ing in ingredients or []:
        name = " ".join(str(ing.get("name", "")).split())
        if name:
            per.append((name, origins_for(name, ing.get("origin"))))

    def items(*kinds):
        return [n for n, o in per if o & set(kinds)]

    pork, gelatin = items("pork"), items("gelatin")
    meat = [n for n, o in per if "meat" in o and "pork" not in o]
    fish, shellfish = items("fish"), items("shellfish")
    animal = items("dairy", "egg", "honey")
    uncertain = items("uncertain")

    tags, notes, badge_detail = [], [], {}

    # --- meat / fish / vegetarian / vegan -----------------------------------
    if meat or pork:
        tags.append("Contains meat")
    elif fish or shellfish:
        if not (gelatin or uncertain):
            tags.append("Pescatarian")
    elif not (gelatin or uncertain):
        if not animal:
            tags += ["Vegan", "Vegetarian"]
        else:
            tags.append("Vegetarian")

    if uncertain and not (meat or pork):
        notes.append(f"Vegetarian status depends on the {_join(uncertain)}: check its label.")
    if gelatin and not (meat or pork):
        notes.append(f"The {_join(gelatin)} is usually animal-derived, so this isn't marked vegetarian.")

    if not tags:
        tags = ["No specific dietary category"]
    tags = [t for t in TAGS if t in tags]          # stable display order
    return {
        "dietary_tags": tags,
        "dietary_note": " ".join(notes),
        "dietary_badges": [{"tag": t, "detail": badge_detail.get(t, "")} for t in tags],
    }


def _join(names: list[str]) -> str:
    names = list(dict.fromkeys(n.lower() for n in names))
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " and " + names[-1]
