"""Keyword taxonomy that tags foods with allergens, animal source and diet attributes.

Used at ingestion time (so every food row carries explicit tags) and again by the rule engine,
which re-derives tags from the food name as a second, independent check. Keyword lists favour
recall: a false "contains" only removes a food from the candidate list; a miss could hurt someone.
"""

from __future__ import annotations

import re

ALLERGEN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "dairy": ("milk", "paneer", "ghee", "butter", "cheese", "curd", "yogurt", "yoghurt", "dahi", "whey",
              "cream", "khoa", "khoya", "casein", "lactose", "buttermilk", "lassi", "kefir", "ice cream"),
    "egg": ("egg", "albumen", "mayonnaise"),
    "fish": ("fish", "salmon", "tuna", "cod", "sardine", "mackerel", "pomfret", "hilsa", "rohu", "catla",
             "anchovy", "herring", "tilapia", "trout", "seer", "bombay duck", "pollock", "haddock", "bass"),
    "shellfish": ("shrimp", "prawn", "crab", "lobster", "shellfish", "crayfish", "oyster", "clam", "mussel",
                  "scallop", "squid", "octopus", "cuttlefish", "mollus", "krill"),
    "peanut": ("peanut", "groundnut"),
    "tree_nut": ("almond", "cashew", "walnut", "pistachio", "hazelnut", "pecan", "macadamia", "brazil nut",
                 "pine nut", "chilgoza", "nut,", "nuts", "praline", "marzipan"),
    "soy": ("soy", "soya", "tofu", "edamame", "tempeh", "miso"),
    "gluten": ("wheat", "atta", "maida", "semolina", "suji", "sooji", "rava", "barley", "rye", "bulgur",
               "couscous", "seitan", "spelt", "bread", "pasta", "noodle", "macaroni", "spaghetti", "oat",
               "cracker", "biscuit", "flour, wheat", "triticale", "dalia"),
    "sesame": ("sesame", "gingelly", "til ", "tahini"),
    "mustard": ("mustard",),
}

# Words that look like an allergen keyword but are not (e.g. "coconut milk" is not dairy).
ALLERGEN_EXCEPTIONS: dict[str, tuple[str, ...]] = {
    "dairy": ("coconut milk", "almond milk", "soy milk", "soymilk", "oat milk", "rice milk", "butternut",
              "cocoa butter", "peanut butter", "milk thistle", "butterfish", "butterbur", "buttermilk biscuit"),
    "tree_nut": ("coconut", "nutmeg", "butternut", "doughnut", "nutrition", "water chestnut", "nut oil"),
    "egg": ("eggplant", "egg plant", "nutmeg"),
    "fish": ("fish sauce substitute", "shellfish"),
    "gluten": ("buckwheat", "gluten-free", "gluten free", "breadfruit", "goat"),
}

MEAT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "beef": ("beef", "veal", "buffalo", "carabeef", "bison", "ox ", "cow meat"),
    "pork": ("pork", "ham", "bacon", "lard", "pig", "sausage, pork", "prosciutto", "salami", "pepperoni"),
    "poultry": ("chicken", "turkey", "duck", "goose", "quail", "poultry", "hen"),
    "goat_lamb": ("mutton", "goat", "lamb", "sheep"),
    "game": ("rabbit", "venison", "deer", "elk", "boar", "squirrel", "beaver", "moose", "caribou", "frog"),
}

ALCOHOL_KEYWORDS = ("wine", "beer", "rum", "vodka", "whiskey", "whisky", "liqueur", "brandy", "gin,",
                    "alcohol", "sake", "cocktail")

# Jain diet excludes underground plant parts and a few others.
JAIN_EXCLUDED = ("potato", "onion", "garlic", "carrot", "beetroot", "beet", "radish", "turnip", "ginger",
                 "yam", "sweet potato", "colocasia", "arbi", "tapioca", "cassava", "elephant foot", "leek",
                 "shallot", "scallion", "spring onion", "mushroom", "turmeric root", "suran", "kand")

HIGH_TYRAMINE = ("aged cheese", "cheddar", "parmesan", "blue cheese", "salami", "pepperoni", "soy sauce",
                 "miso", "sauerkraut", "kimchi", "tap beer", "fermented", "cured", "fava", "broad bean")
GRAPEFRUIT = ("grapefruit", "pomelo", "seville orange")


def _contains(name: str, keywords: tuple[str, ...]) -> bool:
    n = f" {name.lower()} "
    for k in keywords:
        k = k.lower()
        if k.endswith(" ") or k.endswith(","):
            if k in n:
                return True
        elif re.search(rf"(?<![a-z]){re.escape(k)}", n):
            return True
    return False


PLANT_OR_DAIRY_GROUP_TERMS = ("cereal", "grain", "legume", "vegetable", "fruit", "root", "tuber", "spice",
                              "condiment", "herb", "nut", "seed", "sugar", "sweet", "mushroom", "milk",
                              "dairy", "egg", "oils and fats", "fats and oils", "beverage")
MEAT_GROUP_TERMS = ("poultry", "pork", "beef", "lamb", "veal", "game", "animal meat", "sausage", "luncheon")


def allergens_for(name: str, group: str = "") -> set[str]:
    """Allergens from the food name, plus a few unambiguous food-group rules."""
    text = name.lower()
    found: set[str] = set()
    for allergen, kws in ALLERGEN_KEYWORDS.items():
        if not _contains(text, kws):
            continue
        stripped = text
        for e in ALLERGEN_EXCEPTIONS.get(allergen, ()):
            stripped = stripped.replace(e, " ")
        if _contains(stripped, kws):
            found.add(allergen)
    g = group.lower()
    if ("milk" in g or "dairy" in g) and not (g.startswith("dairy and egg") and "egg" in found):
        found.add("dairy")  # recall over precision: untagged items in dairy groups count as dairy
    if g.startswith("egg"):
        found.add("egg")
    if "mollus" in g or g == "marine shellfish":
        found.add("shellfish")
    elif "fish" in g and "shellfish" not in found:
        found.add("fish")
    return found


def meats_for(name: str, group: str = "") -> set[str]:
    g = group.lower()
    if any(t in g for t in PLANT_OR_DAIRY_GROUP_TERMS) and not any(t in g for t in MEAT_GROUP_TERMS):
        return set()
    found = {m for m, kws in MEAT_KEYWORDS.items() if _contains(name, kws)}
    if "poultry" in g:
        found.add("poultry")
    if "pork" in g:
        found.add("pork")
    if "beef" in g:
        found.add("beef")
    if "lamb" in g or "veal" in g or "game" in g:
        found.add("goat_lamb")
    if "animal meat" in g and not found:
        found.add("meat_unspecified")
    return found


def is_animal_flesh(allergens: set[str], meats: set[str]) -> bool:
    return bool(meats) or "fish" in allergens or "shellfish" in allergens


def alias_terms(allergy: str) -> set[str]:
    """Map what users type ("nuts", "lactose", "prawns") onto canonical allergen keys."""
    a = allergy.strip().lower()
    aliases = {
        "milk": "dairy", "lactose": "dairy", "dairy": "dairy", "casein": "dairy", "whey": "dairy",
        "eggs": "egg", "egg": "egg",
        "fish": "fish", "seafood": "fish|shellfish",
        "shellfish": "shellfish", "prawn": "shellfish", "prawns": "shellfish", "shrimp": "shellfish",
        "crab": "shellfish", "crustacean": "shellfish",
        "peanut": "peanut", "peanuts": "peanut", "groundnut": "peanut",
        "nuts": "tree_nut|peanut", "tree nut": "tree_nut", "tree nuts": "tree_nut", "tree_nut": "tree_nut",
        "almond": "tree_nut", "cashew": "tree_nut", "walnut": "tree_nut",
        "soy": "soy", "soya": "soy", "soybean": "soy",
        "gluten": "gluten", "wheat": "gluten", "celiac": "gluten", "coeliac": "gluten",
        "sesame": "sesame", "til": "sesame", "mustard": "mustard",
    }
    if a in aliases:
        return set(aliases[a].split("|"))
    return {a}


# Everyday names -> the names used in each region's food table, so a planner can search "moong dal"
# or "palak" and find IFCT's "Green gram, dal" or "Spinach". Word-level and deterministic.
FOOD_SYNONYMS = {
    "IN": {
        "moong": "green gram", "mung": "green gram", "chana": "bengal gram", "chickpea": "bengal gram",
        "chickpeas": "bengal gram", "besan": "bengal gram flour", "urad": "black gram", "masoor": "lentil",
        "toor": "red gram", "arhar": "red gram", "tur": "red gram", "rajma": "rajmah", "matar": "peas",
        "palak": "spinach", "methi": "fenugreek", "aloo": "potato", "gobi": "cauliflower",
        "baingan": "brinjal", "eggplant": "brinjal", "bhindi": "ladies finger", "okra": "ladies finger",
        "lauki": "bottle gourd", "doodhi": "bottle gourd", "kheera": "cucumber", "pyaz": "onion",
        "tamatar": "tomato", "gajar": "carrot", "poha": "rice flakes", "suji": "semolina", "sooji": "semolina",
        "rava": "semolina", "atta": "wheat flour atta", "maida": "wheat flour refined",
        "roti": "wheat flour atta", "chapati": "wheat flour atta", "dalia": "wheat bulgur", "nachni": "ragi",
        "peanut": "groundnut", "peanuts": "groundnut", "mungphali": "groundnut", "badam": "almond",
        "kela": "banana", "seb": "apple", "doodh": "milk",
    },
    "US": {"garbanzo": "chickpeas", "chickpea": "chickpeas", "yoghurt": "yogurt", "curd": "yogurt",
           "eggs": "egg", "oatmeal": "oats"},
}


def expand_food_query(query: str, region: str | None) -> str:
    syn = FOOD_SYNONYMS.get(region or "", {})
    return " ".join(syn.get(w, w) for w in re.findall(r"[a-z0-9]+", query.lower()))
