"""Built-in sample data for DEMO MODE (no API key / AI service unavailable).

The main-photo detection matches app/frontend/assets/demo-fridge.svg.
Demo recipes go through the same server-side validator as live recipes.
"""

PANTRY_STAPLES = [
    "salt", "black pepper", "olive oil", "vegetable oil", "water", "garlic",
    "flour", "sugar", "dried pasta", "rice", "dried herbs", "chili flakes",
    "soy sauce", "vinegar", "stock cube",
]

# Raw model-format output for the sample photos. It goes through the same
# validation/merging as live results (fridge_detection.normalize_result), so the
# demo also shows duplicate merging, unknown items and confirmation flags.
_DEMO_MAIN_PHOTO = {
    "image_quality": "good",
    "needs_another_photo": True,
    "photo_advice": "Clear shot of the shelves. The door isn't visible - add a photo of the door shelves.",
    "detected_items": [
        {"name": "milk", "brand": "Arla", "category": "dairy", "quantity": 1, "quantity_is_certain": True,
         "confidence": 0.95, "evidence": "top shelf left, carton label reads 'MILK'"},
        {"name": "eggs", "brand": None, "category": "eggs", "quantity": 4, "quantity_is_certain": True,
         "confidence": 0.93, "evidence": "middle shelf, open carton, 4 eggs visible"},
        {"name": "red bell pepper", "brand": None, "category": "vegetables", "quantity": 1,
         "quantity_is_certain": True, "confidence": 0.9, "evidence": "middle shelf right, one whole pepper"},
        {"name": "cherry tomatoes", "brand": None, "category": "vegetables", "quantity": None,
         "quantity_is_certain": False, "confidence": 0.88, "evidence": "bottom shelf, punnet, some hidden"},
        {"name": "cherry tomato", "brand": None, "category": "vegetables", "quantity": None,
         "quantity_is_certain": False, "confidence": 0.8, "evidence": "bottom shelf, top of punnet"},
        {"name": "chicken breast", "brand": None, "category": "meat", "quantity": 2, "quantity_is_certain": True,
         "confidence": 0.91, "evidence": "bottom shelf left, clear tray with 2 fillets, label reads 'Chicken Breast'"},
        {"name": "onion", "brand": None, "category": "vegetables", "quantity": 1, "quantity_is_certain": True,
         "confidence": 0.86, "evidence": "bottom shelf right, one loose onion"},
        {"name": "spinach", "brand": None, "category": "vegetables", "quantity": None,
         "quantity_is_certain": False, "confidence": 0.92, "evidence": "crisper drawer, bag labelled 'SPINACH'"},
        {"name": "cheddar cheese", "brand": None, "category": "dairy", "quantity": 1, "quantity_is_certain": True,
         "confidence": 0.68, "evidence": "middle shelf, yellow wedge, no readable label - could be another hard cheese"},
        {"name": "butter", "brand": None, "category": "dairy", "quantity": 1, "quantity_is_certain": True,
         "confidence": 0.62, "evidence": "top shelf right, yellow block, partly hidden"},
        {"name": "yoghurt", "brand": None, "category": "dairy", "quantity": None, "quantity_is_certain": False,
         "confidence": 0.3, "evidence": "white tub, top shelf middle, no label"},
    ],
    "unknown_items": [
        {"description": "Small foil-covered bowl at the back of the bottom shelf", "confidence": 0.2},
    ],
}

_DEMO_DOOR_PHOTO = {
    "image_quality": "acceptable",
    "needs_another_photo": False,
    "photo_advice": "Got the door shelves. The bottom door shelf is a little dark but readable.",
    "detected_items": [
        {"name": "ketchup", "brand": "Heinz", "category": "sauces_condiments", "quantity": 1,
         "quantity_is_certain": True, "confidence": 0.97, "evidence": "door top shelf, label reads 'HEINZ Tomato Ketchup'"},
        {"name": "dijon mustard", "brand": "Maille", "category": "sauces_condiments", "quantity": 1,
         "quantity_is_certain": True, "confidence": 0.9, "evidence": "door top shelf, small jar, label 'Maille Dijon'"},
        {"name": "milk", "brand": "Arla", "category": "dairy", "quantity": 1, "quantity_is_certain": True,
         "confidence": 0.9, "evidence": "same Arla carton visible at the door edge"},
        {"name": "orange juice", "brand": None, "category": "drinks", "quantity": 1, "quantity_is_certain": True,
         "confidence": 0.78, "evidence": "door bottom shelf, orange carton, brand not readable"},
    ],
    "unknown_items": [
        {"description": "Dark glass bottle with a torn label, door bottom shelf", "confidence": 0.25},
    ],
}


def demo_detection_raw(extra_photo: bool = False) -> dict:
    import copy
    return copy.deepcopy(_DEMO_DOOR_PHOTO if extra_photo else _DEMO_MAIN_PHOTO)


DEMO_PANTRY = ["salt", "black pepper", "olive oil"]

DEMO_ALL_ALLOWED = [i["name"] for i in _DEMO_MAIN_PHOTO["detected_items"]] + DEMO_PANTRY

_BASE_INGREDIENTS = {
    "beginner": [
        {"name": "eggs", "quantity": "4 large", "source": "fridge"},
        {"name": "red bell pepper", "quantity": "1/2 pepper", "source": "fridge"},
        {"name": "onion", "quantity": "1/2 small", "source": "fridge"},
        {"name": "spinach", "quantity": "2 handfuls (about 60 g)", "source": "fridge"},
        {"name": "cheddar cheese", "quantity": "40 g, grated", "source": "fridge"},
        {"name": "butter", "quantity": "1 tbsp", "source": "fridge"},
        {"name": "salt", "quantity": "2 pinches", "source": "pantry"},
        {"name": "black pepper", "quantity": "1 pinch", "source": "pantry"},
    ],
    "intermediate": [
        {"name": "eggs", "quantity": "6 large", "source": "fridge"},
        {"name": "milk", "quantity": "3 tbsp", "source": "fridge"},
        {"name": "red bell pepper", "quantity": "1 pepper", "source": "fridge"},
        {"name": "onion", "quantity": "1 medium", "source": "fridge"},
        {"name": "spinach", "quantity": "3 handfuls (about 90 g)", "source": "fridge"},
        {"name": "cherry tomatoes", "quantity": "8", "source": "fridge"},
        {"name": "cheddar cheese", "quantity": "60 g, grated", "source": "fridge"},
        {"name": "olive oil", "quantity": "2 tbsp", "source": "pantry"},
        {"name": "salt", "quantity": "1/2 tsp", "source": "pantry"},
        {"name": "black pepper", "quantity": "1/4 tsp", "source": "pantry"},
    ],
    "expert": [
        {"name": "eggs", "quantity": "3 large", "source": "fridge"},
        {"name": "butter", "quantity": "20 g", "source": "fridge"},
        {"name": "red bell pepper", "quantity": "1/4 pepper", "source": "fridge"},
        {"name": "onion", "quantity": "1/4 small", "source": "fridge"},
        {"name": "cherry tomatoes", "quantity": "6", "source": "fridge"},
        {"name": "spinach", "quantity": "1 handful (about 30 g)", "source": "fridge"},
        {"name": "cheddar cheese", "quantity": "20 g, finely grated", "source": "fridge"},
        {"name": "olive oil", "quantity": "1 tsp", "source": "pantry"},
        {"name": "salt", "quantity": "to taste", "source": "pantry"},
        {"name": "black pepper", "quantity": "to taste", "source": "pantry"},
    ],
}

_RECIPES = {
    "beginner": {
        "dish_name": "Cheesy Veggie Scramble",
        "description": "Soft scrambled eggs with sautéed pepper, onion and wilted spinach, finished with cheddar.",
        "servings": 2, "prep_minutes": 10, "cook_minutes": 10,
        "tasks": [
            {"skill": "Basic slicing",
             "instruction": "Cut the half pepper into thin strips, then cut the strips in half. Peel the onion and slice it thinly. Keep your fingertips curled under, like a claw, and let the knife do the work.",
             "proof_photo": "The pepper strips and onion slices spread out on the cutting board, photographed from directly above."},
            {"skill": "Cracking & whisking eggs",
             "instruction": "Crack the 4 eggs into a bowl one at a time, tapping each on a flat surface (not the bowl edge) so shell doesn't break in. Add a pinch of salt and whisk with a fork for about 30 seconds until the yolks and whites are fully mixed and no clear streaks remain.",
             "proof_photo": "The bowl of whisked eggs from above, showing an even yellow color with no shell pieces."},
            {"skill": "Gentle sauté",
             "instruction": "Melt the butter in a non-stick pan over medium heat. Add the pepper and onion and stir every 30 seconds for about 5 minutes until soft. Add the spinach and stir until it shrinks and turns dark green (about 1 minute).",
             "proof_photo": "The pan from above showing soft, glossy vegetables and fully wilted, dark-green spinach."},
            {"skill": "Soft scrambling",
             "instruction": "Turn the heat to low. Pour in the eggs, wait 20 seconds, then slowly push them from the edges to the middle with a spatula. Repeat until the eggs form soft folds but still look slightly shiny. Take the pan off the heat - they keep cooking.",
             "proof_photo": "A close-up of the eggs in the pan showing large, soft curds that are still slightly glossy, not dry or browned."},
            {"skill": "Finishing & plating",
             "instruction": "Sprinkle the grated cheddar over the eggs and fold once so it melts. Spoon onto two plates and finish with a pinch of black pepper.",
             "proof_photo": "One plated portion from a 45-degree angle, showing melted cheese and the vegetables mixed through."},
        ],
    },
    "intermediate": {
        "dish_name": "Spinach & Pepper Frittata with Blistered Tomatoes",
        "description": "A thick, evenly set frittata packed with vegetables, topped with blistered cherry tomatoes.",
        "servings": 3, "prep_minutes": 15, "cook_minutes": 20,
        "tasks": [
            {"skill": "Even dicing",
             "instruction": "Dice the pepper and onion into even 1 cm pieces. Square off the pepper first, cut it into strips, then cross-cut.",
             "proof_photo": "The diced pepper and onion in two separate piles spread out in one layer on the board, shot from directly above."},
            {"skill": "Golden sauté",
             "instruction": "Heat 1 tbsp olive oil in an oven-safe pan over medium-high heat. Sauté the onion and pepper for 6-7 minutes until the edges are golden, then wilt in the spinach.",
             "proof_photo": "The pan from above showing vegetables with visible golden edges and fully wilted spinach."},
            {"skill": "Egg custard base",
             "instruction": "Whisk the eggs with the milk, salt, pepper and half the cheese until smooth and slightly frothy. Strain out any bits of chalaza if you like.",
             "proof_photo": "The bowl of egg mixture from above showing a uniform color with fine bubbles on the surface."},
            {"skill": "Even set",
             "instruction": "Spread the vegetables in an even layer, pour over the eggs and cook on low for 8 minutes until the edges set. Top with the remaining cheese and finish under a hot grill for 3-4 minutes until puffed and just set in the center.",
             "proof_photo": "A slice cut from the frittata, held up or laid on its side, showing an evenly set, moist interior with no runny center."},
            {"skill": "Blistering tomatoes",
             "instruction": "Heat the remaining 1 tbsp oil in a small pan on high. Add the whole cherry tomatoes and shake the pan for 3-4 minutes until the skins blister and split. Spoon over the frittata.",
             "proof_photo": "A close-up of the tomatoes on the frittata showing wrinkled, split skins with some charred spots."},
        ],
    },
    "expert": {
        "dish_name": "French Omelette with Brunoise Vegetables & Tomato Concassé",
        "description": "A pale, rolled, custardy omelette filled with fine brunoise, served with a quick concassé.",
        "servings": 1, "prep_minutes": 15, "cook_minutes": 8,
        "tasks": [
            {"skill": "Brunoise",
             "instruction": "Cut the pepper and onion into 2-3 mm brunoise. Fillet the pepper flesh flat before cutting the julienne.",
             "proof_photo": "The brunoise next to a fork for scale, spread out in one layer, shot directly from above in good light."},
            {"skill": "Tomato concassé",
             "instruction": "Quarter the cherry tomatoes, remove the seeds and cut the flesh into a small, neat dice. Season lightly with salt.",
             "proof_photo": "The diced tomato flesh on the board from above, showing seedless, evenly sized cubes."},
            {"skill": "Sweating aromatics",
             "instruction": "Sweat the brunoise in the olive oil over low heat with a pinch of salt for 3-4 minutes until translucent with no color. Add the spinach until just wilted. Set aside.",
             "proof_photo": "The pan from above showing translucent, glossy vegetables with no browning."},
            {"skill": "French omelette technique",
             "instruction": "Beat the eggs with salt until fully homogenous. Foam the butter over medium heat, add the eggs and agitate continuously with a spatula while shaking the pan to form small curds. Stop while still baveuse, spread the filling and cheese down the center, then roll.",
             "proof_photo": "The rolled omelette on the plate seam-side down, showing a smooth, pale yellow surface with no browning."},
            {"skill": "Precision plating",
             "instruction": "Cut the omelette open lengthwise to show the custardy interior. Spoon the concassé along one side and finish with black pepper.",
             "proof_photo": "A 45-degree shot of the opened omelette showing a soft, just-set interior and a neat line of concassé."},
        ],
    },
}


def demo_recipe(level: str) -> dict:
    r = dict(_RECIPES[level])
    r["ingredients"] = _BASE_INGREDIENTS[level]
    r["status"] = "ok"
    r["skill_level"] = level
    r["follow_up_question"] = ""
    r["suggested_additions"] = []
    return r


# Two extra options so the demo shows dietary variety (vegan + meat) next to the level's
# vegetarian recipe. Their dietary tags are NOT written here: the server computes them
# (dietary.py), using ingredient origins for non-religious categories.
_VEGAN_OPTION = {
    "dish_name": "Blistered Tomato, Pepper & Spinach Sauté",
    "description": "A quick, colourful vegetable sauté finished with blistered cherry tomatoes.",
    "servings": 2, "prep_minutes": 10, "cook_minutes": 12,
    "ingredients": [
        {"name": "red bell pepper", "quantity": "1 pepper", "source": "fridge", "origin": "plant"},
        {"name": "onion", "quantity": "1 small", "source": "fridge", "origin": "plant"},
        {"name": "spinach", "quantity": "3 handfuls (about 90 g)", "source": "fridge", "origin": "plant"},
        {"name": "cherry tomatoes", "quantity": "10", "source": "fridge", "origin": "plant"},
        {"name": "olive oil", "quantity": "2 tbsp", "source": "pantry", "origin": "plant"},
        {"name": "salt", "quantity": "2 pinches", "source": "pantry", "origin": "plant"},
        {"name": "black pepper", "quantity": "1 pinch", "source": "pantry", "origin": "plant"},
    ],
    "tasks": [
        {"skill": "Even slicing",
         "instruction": "Cut the pepper into strips about 1 cm wide and slice the onion into thin half-moons of the same thickness.",
         "proof_photo": "The pepper strips and onion slices spread in one layer on the board, shot from directly above."},
        {"skill": "Golden sauté",
         "instruction": "Heat 1 tbsp oil over medium-high heat and cook the onion and pepper for 6-7 minutes, stirring now and then, until the edges turn golden.",
         "proof_photo": "The pan from above, showing vegetables with clearly golden edges and no burnt spots."},
        {"skill": "Wilting greens",
         "instruction": "Add the spinach a handful at a time and stir until it has just collapsed and turned dark green (about 1 minute).",
         "proof_photo": "A close-up of the pan showing glossy, fully wilted dark-green spinach mixed through."},
        {"skill": "Blistering tomatoes",
         "instruction": "In a second hot pan with the remaining oil, shake the whole tomatoes for 3-4 minutes until the skins blister and split.",
         "proof_photo": "A close-up of the tomatoes showing wrinkled, split skins with a few charred spots."},
        {"skill": "Seasoning & plating",
         "instruction": "Season the vegetables with salt and pepper, spoon onto plates and top with the blistered tomatoes.",
         "proof_photo": "One plated portion from a 45-degree angle, with the tomatoes on top of the greens."},
    ],
}

_CHICKEN_OPTION = {
    "dish_name": "Pan-Seared Chicken with Wilted Spinach & Tomatoes",
    "description": "Golden seared chicken breast served on wilted spinach with burst tomatoes.",
    "servings": 2, "prep_minutes": 10, "cook_minutes": 18,
    "ingredients": [
        {"name": "chicken breast", "quantity": "2 fillets", "source": "fridge", "origin": "meat"},
        {"name": "spinach", "quantity": "3 handfuls (about 90 g)", "source": "fridge", "origin": "plant"},
        {"name": "cherry tomatoes", "quantity": "8", "source": "fridge", "origin": "plant"},
        {"name": "onion", "quantity": "1/2 small", "source": "fridge", "origin": "plant"},
        {"name": "olive oil", "quantity": "2 tbsp", "source": "pantry", "origin": "plant"},
        {"name": "salt", "quantity": "1/2 tsp", "source": "pantry", "origin": "plant"},
        {"name": "black pepper", "quantity": "1/4 tsp", "source": "pantry", "origin": "plant"},
    ],
    "tasks": [
        {"skill": "Even thickness",
         "instruction": "Place each chicken breast between two sheets of baking paper and flatten the thick end so the whole fillet is an even 2 cm thick. Season both sides.",
         "proof_photo": "Both fillets side by side on the board, photographed from the side so their even thickness is visible."},
        {"skill": "Hard sear",
         "instruction": "Heat 1 tbsp oil in a pan until shimmering. Lay the chicken away from you and leave it untouched for 5-6 minutes until a deep golden crust forms, then turn.",
         "proof_photo": "The seared side of one fillet facing up in the pan, showing an even deep-golden crust."},
        {"skill": "Cooking to temperature",
         "instruction": "Lower the heat and cook the second side for 5-6 minutes until the thickest part is white all the way through with clear juices. Rest it on a plate for 5 minutes.",
         "proof_photo": "A fillet cut through the thickest part, showing white, juicy meat with no pink."},
        {"skill": "Pan vegetables",
         "instruction": "In the same pan, soften the sliced onion for 3 minutes, add the tomatoes until they burst, then stir in the spinach until just wilted.",
         "proof_photo": "The pan from above showing burst tomatoes and wilted spinach coated in the pan juices."},
        {"skill": "Slicing & plating",
         "instruction": "Slice the rested chicken across the grain into 1 cm slices and fan them over the vegetables.",
         "proof_photo": "A plated portion from above with evenly sliced chicken fanned over the vegetables."},
    ],
}


_SOUP_OPTION = {
    "dish_name": "Creamy Roasted Pepper & Tomato Soup",
    "description": "A smooth, comforting soup of softened peppers and tomatoes enriched with milk and butter.",
    "image_search_term": "tomato soup", "difficulty": 2,
    "servings": 2, "prep_minutes": 10, "cook_minutes": 20,
    "ingredients": [
        {"name": "red bell pepper", "quantity": "1 pepper", "source": "fridge", "origin": "plant"},
        {"name": "cherry tomatoes", "quantity": "15", "source": "fridge", "origin": "plant"},
        {"name": "onion", "quantity": "1 small", "source": "fridge", "origin": "plant"},
        {"name": "milk", "quantity": "300 ml", "source": "fridge", "origin": "dairy"},
        {"name": "butter", "quantity": "1 tbsp", "source": "fridge", "origin": "dairy"},
        {"name": "salt", "quantity": "1/2 tsp", "source": "pantry", "origin": "plant"},
        {"name": "black pepper", "quantity": "1 pinch", "source": "pantry", "origin": "plant"},
    ],
    "optional_ingredients": ["fresh basil"],
    "tasks": [
        {"skill": "Rough chopping",
         "instruction": "Chop the pepper and onion into rough 2 cm pieces and halve the tomatoes.",
         "proof_photo": "The chopped vegetables in three piles on the board, shot from directly above."},
        {"skill": "Sweating vegetables",
         "instruction": "Melt the butter over medium-low heat and cook the onion and pepper for 8 minutes until soft but not browned.",
         "proof_photo": "The pan from above showing soft, glossy vegetables with no browning."},
        {"skill": "Simmering",
         "instruction": "Add the tomatoes and milk, bring to a gentle simmer (small bubbles only) and cook for 10 minutes.",
         "proof_photo": "The pot from above showing a gentle simmer with small bubbles, not a rolling boil."},
        {"skill": "Smooth blending",
         "instruction": "Blend until completely smooth, then season with salt and pepper.",
         "proof_photo": "A spoon lifted from the soup showing a smooth texture with no lumps."},
        {"skill": "Plating",
         "instruction": "Ladle into warm bowls and wipe the rims clean.",
         "proof_photo": "One bowl from above with clean rims and an even surface."},
    ],
}

_STIR_FRY_OPTION = {
    "dish_name": "Chicken & Pepper Stir-Fry",
    "description": "Thin strips of chicken stir-fried hot and fast with peppers, onion and spinach.",
    "image_search_term": "chicken stir fry", "difficulty": 3,
    "servings": 2, "prep_minutes": 15, "cook_minutes": 10,
    "ingredients": [
        {"name": "chicken breast", "quantity": "2 fillets", "source": "fridge", "origin": "meat"},
        {"name": "red bell pepper", "quantity": "1 pepper", "source": "fridge", "origin": "plant"},
        {"name": "onion", "quantity": "1 medium", "source": "fridge", "origin": "plant"},
        {"name": "spinach", "quantity": "2 handfuls", "source": "fridge", "origin": "plant"},
        {"name": "olive oil", "quantity": "2 tbsp", "source": "pantry", "origin": "plant"},
        {"name": "salt", "quantity": "1/2 tsp", "source": "pantry", "origin": "plant"},
    ],
    "optional_ingredients": ["soy sauce", "spring onions"],
    "tasks": [
        {"skill": "Cutting even strips",
         "instruction": "Slice the chicken across the grain into strips about 1 cm thick and the pepper into strips of the same width.",
         "proof_photo": "The chicken and pepper strips laid out side by side on the board, shot from above."},
        {"skill": "High-heat searing",
         "instruction": "Heat the oil until shimmering and spread the chicken in one layer. Leave it for 2 minutes to brown before stirring.",
         "proof_photo": "The pan showing chicken strips in a single layer with browned edges."},
        {"skill": "Stir-frying",
         "instruction": "Add the onion and pepper and toss constantly for 3 minutes so the vegetables stay crisp.",
         "proof_photo": "A close-up of the pan with brightly coloured, still-crisp vegetables."},
        {"skill": "Cooking to temperature",
         "instruction": "Check the thickest strip: it must be white all the way through. Then wilt in the spinach and season.",
         "proof_photo": "One chicken strip cut open showing white meat with no pink."},
        {"skill": "Plating",
         "instruction": "Pile the stir-fry neatly in the centre of each plate.",
         "proof_photo": "A plated portion from a 45-degree angle showing chicken, peppers and spinach."},
    ],
}

_DEMO_IMAGE_TERMS = {"beginner": "scrambled eggs", "intermediate": "frittata", "expert": "omelette"}


def demo_recipe_options(level: str) -> list[dict]:
    """Five demo recipes: vegetarian (level-specific), vegan, two with chicken, vegetarian soup."""
    import copy
    main = dict(_RECIPES[level])
    main["ingredients"] = _BASE_INGREDIENTS[level]
    main["image_search_term"] = _DEMO_IMAGE_TERMS[level]
    main["difficulty"] = {"beginner": 2, "intermediate": 3, "expert": 4}[level]
    vegan = {**_VEGAN_OPTION, "image_search_term": "vegetable saute", "difficulty": 2}
    chicken = {**_CHICKEN_OPTION, "image_search_term": "chicken breast", "difficulty": 3}
    return [copy.deepcopy(r) for r in (main, vegan, chicken, _SOUP_OPTION, _STIR_FRY_OPTION)]
