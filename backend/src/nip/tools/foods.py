"""Food table access: region-filtered search and deterministic nutrition arithmetic."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from nip.data.taxonomy import JAIN_EXCLUDED, alias_terms
from nip.domain.models import DietPattern, FoodPortion, NutritionTotals, Region, UserProfile

DEFAULT_FOODS_CSV = Path(__file__).resolve().parents[4] / "data" / "processed" / "foods.csv"
NUTRIENTS = list(NutritionTotals.model_fields)


@dataclass(frozen=True)
class Food:
    code: str
    region: str
    source: str
    name: str
    group: str
    allergens: frozenset[str]
    meats: frozenset[str]
    animal_flesh: bool
    alcohol: bool
    per100g: dict[str, float] = field(hash=False, compare=False)

    def summary(self) -> dict:
        p = self.per100g
        return {
            "code": self.code, "name": self.name, "group": self.group,
            "per_100g": {"kcal": round(p["kcal"]), "protein_g": round(p["protein_g"], 1),
                         "carbs_g": round(p["carbs_g"], 1), "fat_g": round(p["fat_g"], 1),
                         "fiber_g": round(p["fiber_g"], 1), "sodium_mg": round(p["sodium_mg"])},
            "allergens": sorted(self.allergens),
        }


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class FoodDB:
    def __init__(self, foods: list[Food]):
        self.foods = foods
        self.by_code = {f.code: f for f in foods}
        self._tok = {f.code: set(_tokens(f.name)) for f in foods}

    @classmethod
    def from_csv(cls, path: Path = DEFAULT_FOODS_CSV) -> FoodDB:
        foods = []
        with path.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                foods.append(Food(
                    code=r["code"], region=r["region"], source=r["source"], name=r["name"], group=r["group"],
                    allergens=frozenset(a for a in r["allergens"].split("|") if a),
                    meats=frozenset(m for m in r["meats"].split("|") if m),
                    animal_flesh=r["animal_flesh"] == "1", alcohol=r["alcohol"] == "1",
                    per100g={k: float(r[k]) for k in NUTRIENTS},
                ))
        return cls(foods)

    def get(self, code: str) -> Food | None:
        return self.by_code.get(code)

    def search(self, query: str, region: Region | str | None = None, limit: int = 10,
               profile: UserProfile | None = None) -> list[Food]:
        """Token-overlap search over food names, filtered by region and by the profile's hard limits."""
        q = set(_tokens(query))
        if not q:
            return []
        region_value = region.value if isinstance(region, Region) else region
        scored: list[tuple[float, Food]] = []
        for f in self.foods:
            if region_value and f.region != region_value:
                continue
            toks = self._tok[f.code]
            hit = len(q & toks) + 0.5 * sum(1 for t in q for w in toks if w.startswith(t) and w != t)
            if hit <= 0:
                continue
            if profile and not is_allowed(f, profile)[0]:
                continue
            # Prefer short, generic names ("Rice, raw" over "Rice, ... with butter, frozen").
            score = hit / len(q) - 0.01 * len(toks)
            scored.append((score, f))
        scored.sort(key=lambda s: -s[0])
        return [f for _, f in scored[:limit]]


def is_allowed(food: Food, p: UserProfile) -> tuple[bool, str | None]:
    """Hard constraints for one food and one person. Returns (allowed, reason_if_not)."""
    blocked: set[str] = set()
    for a in [*p.allergies, *p.intolerances]:
        blocked |= alias_terms(a)
    hit = food.allergens & blocked
    if hit:
        return False, f"contains {', '.join(sorted(hit))}"
    name = food.name.lower()
    for a in p.allergies:  # free-text allergies we have no tag for ("kiwi", "mango")
        if alias_terms(a) == {a} and re.search(rf"(?<![a-z]){re.escape(a)}", name):
            return False, f"matches allergy '{a}'"
    for d in p.dislikes:
        if re.search(rf"(?<![a-z]){re.escape(d)}", name):
            return False, f"disliked ({d})"

    dp = p.diet_pattern
    if dp != DietPattern.omnivore:
        if food.animal_flesh and not (dp == DietPattern.pescatarian and not food.meats):
            return False, f"not {dp.value}"
        if dp in (DietPattern.vegetarian, DietPattern.vegan, DietPattern.jain) and "egg" in food.allergens:
            return False, f"egg not {dp.value}"
        if dp == DietPattern.vegan and "dairy" in food.allergens:
            return False, "dairy not vegan"
        if dp == DietPattern.vegan and re.search(r"(?<![a-z])(honey|gelatin|ghee)", name):
            return False, "animal product not vegan"
        if dp == DietPattern.jain and any(re.search(rf"(?<![a-z]){re.escape(k)}", name) for k in JAIN_EXCLUDED):
            return False, "root/bulb vegetable not Jain"
    rules = set(p.religious_rules)
    if rules & {"halal", "kosher", "no_pork"} and "pork" in food.meats:
        return False, "pork"
    if "no_beef" in rules and "beef" in food.meats:
        return False, "beef"
    if rules & {"halal", "kosher"} and food.alcohol:
        return False, "alcohol"
    if "kosher" in rules and "shellfish" in food.allergens:
        return False, "shellfish not kosher"
    return True, None


def portion_totals(food: Food, grams: float) -> NutritionTotals:
    f = grams / 100.0
    return NutritionTotals(**{k: food.per100g[k] * f for k in NUTRIENTS})


def compute_totals(db: FoodDB, items: list[FoodPortion]) -> tuple[NutritionTotals, list[str]]:
    """Sum nutrients for a list of portions. Unknown codes are reported, never guessed."""
    total = NutritionTotals()
    unknown: list[str] = []
    for it in items:
        food = db.get(it.food_code)
        if food is None:
            unknown.append(it.food_code)
            continue
        total = total + portion_totals(food, it.grams)
    return total.rounded(), unknown


@lru_cache
def get_food_db() -> FoodDB:
    return FoodDB.from_csv()
