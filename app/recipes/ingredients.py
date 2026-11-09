"""Ingredient identity and roles: allow at most two missing essentials.

Missing supporting/optional items are shown, never assumed owned. Missing items
with unresolved roles are excluded conservatively. Aliases deduplicate counts.
"""
import re
from fractions import Fraction

POLICY_VERSION = "ingredients.v4"
MAX_MISSING_ESSENTIAL = 2

# Deliberate aliases only: no fuzzy matching of different ingredient varieties.
GROUPS = {
    "eggs": ["egg"], "salt": ["sea salt", "table salt"],
    "sugar": ["white sugar", "granulated sugar"], "brown sugar": [],
    "black pepper": ["pepper", "ground black pepper"], "white pepper": [],
    "vegetable oil": ["cooking oil"], "sunflower oil": [],
    "olive oil": ["extra virgin olive oil", "extra-virgin olive oil"],
    "sesame oil": [], "butter": [], "milk": [],
    "plain flour": ["flour", "all purpose flour", "all-purpose flour"],
    "self raising flour": ["self-raising flour"], "bread flour": [],
    "soy sauce": ["soya sauce"], "vinegar": [], "rice vinegar": [],
    "garlic": ["garlic cloves", "garlic clove"], "onion": ["onions"],
    "spring onion": ["spring onions", "scallion", "scallions"],
    "ginger": [], "potato": ["potatoes"], "tomato": ["tomatoes"],
    "carrot": ["carrots"], "chicken breast": ["chicken breasts"],
    "tofu": [], "rice": [], "long grain rice": ["long-grain rice", "longgrain rice"],
    "cooked rice": [], "spaghetti": [], "water": [],
    "paprika": [], "cumin": ["ground cumin"],
    "coriander seeds": ["ground coriander", "coriander powder"],
    "cinnamon": ["ground cinnamon"], "turmeric": ["ground turmeric"],
    "chilli powder": ["chili powder"], "chilli flakes": ["chili flakes"],
    "curry powder": [], "dried oregano": [], "dried thyme": [],
    "bay leaf": ["bay leaves"], "parsley": [], "basil": [],
    "honey": [], "baking powder": [], "baking soda": [],
    "cornflour": ["corn starch", "cornstarch"], "yeast": [],
    "lemon": ["lemons"], "lemon juice": [], "sesame seeds": [],
    "chicken stock": ["chicken broth"], "cream": [], "cheese": [],
}


def basic(value):
    return re.sub(r"[\s_-]+", " ", value.strip().casefold())


ALIASES = {basic(alias): name for name, values in GROUPS.items() for alias in [name, *values]}


def canonical_name(name):
    clean = re.sub(r"\s*\((?:optional|to garnish|to serve)\)", "", basic(name)).strip()
    prepared = re.sub(r"\b(finely|roughly|chopped|minced|grated|sliced|crushed|peeled|diced|small|large)\b", "", clean)
    prepared = re.sub(r"\s+", " ", prepared).strip()
    if prepared in ALIASES:
        return ALIASES[prepared]
    return ALIASES.get(clean, clean)


def normalize_inventory(names):
    return sorted({canonical_name(name) for name in names if name.strip()})


def is_owned(name, names):
    return canonical_name(name) in set(normalize_inventory(names))


# Source lines that name two ingredients at once; both must be confirmed.
COMPOUNDS = {"salt and pepper": ("salt", "black pepper"), "salt & pepper": ("salt", "black pepper")}


def required_names(name):
    canonical = canonical_name(name)
    return COMPOUNDS.get(canonical, (canonical,))


SEASONINGS = {"salt", "salt and pepper", "salt & pepper", "black pepper", "white pepper", "vegetable oil", "olive oil", "sunflower oil",
              "water", "vinegar", "rice vinegar", "soy sauce", "sesame oil", "paprika", "cumin",
              "coriander seeds", "cinnamon", "turmeric", "chilli powder", "chilli flakes",
              "dried oregano", "dried thyme", "bay leaf", "curry powder"}
STRUCTURAL_DISHES = re.compile(r"\b(cake|bread|cookie|cookies|biscuit|pastry|pancake|waffle|dough|shortbread|pie|tart|muffin|scone|brownie|meringue|souffle|caramel|jam|pesto|mayonnaise|hollandaise)\b")
STRUCTURAL = {"vegetable oil", "olive oil", "sunflower oil", "water", "vinegar", "rice vinegar", "salt"}
GARNISHES = {"parsley", "spring onion", "sesame seeds", "lemon", "basil", "mint", "coriander"}
IDENTITY_FAMILIES = (
    (r"\baglio\b.*\bolio\b", {"olive oil", "garlic", "spaghetti"}),
    (r"\bvinaigrette\b", {"olive oil", "vegetable oil", "vinegar", "rice vinegar", "lemon juice"}),
    (r"\b(fried|fritter|fritters|tempura|pakora)\b", {"vegetable oil", "sunflower oil", "olive oil", "sesame oil"}),
)


def classify_ingredient(dish_name, ingredient):
    name = canonical_name(ingredient["name"])
    title = set(re.findall(r"\w+", basic(dish_name)))
    words = set(re.findall(r"\w+", name)) - {"ground", "dried", "fresh", "sauce", "oil", "seeds", "powder"}
    # Title/structural rules override even a source's 'optional' annotation.
    if words and words <= title:
        return "essential", "dish_identity"
    if any(re.search(pattern, basic(dish_name)) and name in core for pattern, core in IDENTITY_FAMILIES):
        return "essential", "dish_family_identity"
    if STRUCTURAL_DISHES.search(basic(dish_name)) and name in STRUCTURAL:
        return "essential", "structural_recipe_family"
    baking = STRUCTURAL_DISHES.search(basic(dish_name))
    if baking and name in {"butter", "plain flour", "self raising flour", "bread flour", "sugar", "brown sugar", "eggs", "yeast", "baking powder"}:
        return "essential", "baking_structure"
    quantity = basic(ingredient.get("quantity", ""))
    amount = re.search(r"(\d+(?:\.\d+)?(?:\s+\d+/\d+|/\d+)?)\s*(ml|g|tbsp|tablespoons?)\b", quantity)
    if amount and name in SEASONINGS | {"butter", "garlic", "ginger"}:
        try:
            value = float(sum(Fraction(part) for part in amount[1].split()))
        except (ValueError, ZeroDivisionError):
            value = 0
        unit = amount[2]
        if value >= (4 if unit.startswith("t") else 60):
            return "essential", "substantial_quantity"
    source_label = basic(ingredient["name"] + " " + quantity)
    if re.search(r"\boptional\b", source_label):
        return "optional", "source_explicit_optional"
    if name in GARNISHES and re.search(r"\b(garnish|garnishing|to serve|for serving)\b", quantity):
        return "optional", "source_garnish"
    if name in SEASONINGS:
        return "supporting", "seasoning_allowlist"
    if name == "butter" and re.search(r"\b(fried rice|stir fry|saute|sauté|risotto)\b", basic(dish_name)):
        return "supporting", "cooking_fat_context"
    if name in {"garlic", "ginger"} and re.search(r"\b(rice|stir fry|soup)\b", basic(dish_name)):
        return "supporting", "aromatic_context"
    if re.search(r"\b(allspice|nutmeg|cloves|peppercorns|sumac|garam masala|fennel seeds|mustard seeds)\b", name):
        return "supporting", "spice_context"
    if name in {"eggs", "plain flour", "self raising flour", "bread flour", "rice", "long grain rice", "cooked rice", "spaghetti", "potato", "carrot", "tomato", "onion", "tofu", "milk", "cheese", "cream"} or re.search(r"\b(chicken|beef|pork|lamb|fish|salmon|prawns|shrimp|lentils|chickpeas|beans|pasta|noodles)\b", name):
        return "essential", "core_food_structure"
    return "uncertain", "context_needed"


def assess_ingredients(dish_name, ingredients, inventory, role_resolutions=None):
    owned = set(normalize_inventory(inventory))
    rows = []
    for index, ingredient in enumerate(ingredients):
        role, reason = classify_ingredient(dish_name, ingredient)
        resolution = (role_resolutions or {}).get(str(index), {})
        if role == "uncertain" and resolution.get("role") in {"essential", "supporting", "optional"} and resolution.get("confidence", 0) >= .85 and resolution.get("basis"):
            role, reason = resolution["role"], "ai_context_estimate"
        canonical = canonical_name(ingredient["name"])
        rows.append({"name": ingredient["name"], "quantity": ingredient.get("quantity", ""),
                     "canonical_name": canonical, "role": role, "role_reason": reason,
                     "available": all(part in owned for part in required_names(ingredient["name"]))})
    # Duplicate lines cannot inflate counts or downgrade a mandatory ingredient.
    essential = {row["canonical_name"] for row in rows if row["role"] == "essential"}
    for row in rows:
        if row["canonical_name"] in essential and row["role"] != "essential":
            row.update(role="essential", role_reason="duplicate_essential_override")
    missing_essential = sorted({row["canonical_name"] for row in rows if row["role"] == "essential" and not row["available"]})
    missing_supporting = sorted({row["canonical_name"] for row in rows if row["role"] in {"supporting", "optional"} and not row["available"]})
    missing_uncertain = sorted({row["canonical_name"] for row in rows if row["role"] == "uncertain" and not row["available"]})
    # Source names (first occurrence) of every ingredient the player has not confirmed.
    missing = {}
    for row in rows:
        if not row["available"]:
            missing.setdefault(row["canonical_name"], row["name"])
    missing = list(missing.values())
    return {"ingredients": rows, "missing_essential_count": len(missing_essential),
            "missing_supporting_count": len(missing_supporting), "missing_essential": missing_essential,
            "missing_supporting": missing_supporting, "missing_uncertain": missing_uncertain,
            "missing_uncertain_count": len(missing_uncertain),
            "missing_ingredients": missing, "missing_ingredient_count": len(missing),
            # Unknown missing roles cannot bypass the essential ingredient budget.
            "eligible": bool(rows) and len(missing_essential) <= MAX_MISSING_ESSENTIAL and not missing_uncertain,
            "classification_version": POLICY_VERSION}


def inventory_options():
    return {"version": POLICY_VERSION, "suggestions": list(GROUPS), "aliases": ALIASES,
            "search_categories": {"spices": ["paprika", "cumin", "coriander seeds", "cinnamon", "turmeric", "chilli powder", "chilli flakes", "curry powder", "dried oregano", "dried thyme", "bay leaf"]},
            "max_missing_essential": MAX_MISSING_ESSENTIAL}
