"""Food table access: region-filtered search and deterministic nutrition arithmetic."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from nip.data.taxonomy import JAIN_EXCLUDED, alias_terms, expand_food_query
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


# Words marking a processed or derived product; such foods rank lower unless the query names them.
PROCESSED_TERMS = frozenset({"oil", "soup", "flour", "juice", "candy", "candie", "sauce", "fried", "canned",
                             "sweetened", "powder", "mix", "babyfood", "beverage", "fast", "snack", "dessert",
                             "mushroom", "chip", "cracker", "bar", "dressing", "sandwich", "pie", "cake"})


def _singular(w: str) -> str:
    if len(w) > 4 and w.endswith(("oes", "ches", "shes")):
        return w[:-2]  # potatoes, peaches
    if len(w) > 3 and w.endswith("ies"):
        return w[:-3] + "y"  # berries
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us")):
        return w[:-1]  # bananas, apples, lentils (not "hummus", "grass")
    return w


def _tokens(text: str) -> list[str]:
    return [_singular(w) for w in re.findall(r"[a-z0-9]+", text.lower())]


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
        region_value = region.value if isinstance(region, Region) else region
        q = set(_tokens(expand_food_query(query, region_value)))
        if not q:
            return []
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
            # Prefer the food itself: names that start with a queried word ("Almonds" over "Oil, almond"),
            # unprocessed forms unless asked for, then short generic names.
            head = _tokens(f.name.split(",")[0])
            score = hit / len(q) - 0.01 * len(toks)
            score += 0.3 if head and head[0] in q else 0
            score -= 0.4 * len((toks & PROCESSED_TERMS) - q)
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


def _solve(m: list[list[float]], r: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting for the small (2x2 / 3x3) systems below."""
    n = len(r)
    a = [row[:] + [r[i]] for i, row in enumerate(m)]
    for c in range(n):
        p = max(range(c, n), key=lambda k: abs(a[k][c]))
        if abs(a[p][c]) < 1e-12:
            return None
        a[c], a[p] = a[p], a[c]
        for k in range(c + 1, n):
            f = a[k][c] / a[c][c]
            a[k] = [x - f * y for x, y in zip(a[k], a[c], strict=True)]
    x = [0.0] * n
    for c in reversed(range(n)):
        x[c] = (a[c][n] - sum(a[c][k] * x[k] for k in range(c + 1, n))) / a[c][c]
    return x


def _min_change(coef: list[dict[int, float]], targets: list[float], b: dict[int, float],
                lo: dict[int, float], hi: dict[int, float]) -> dict[int, float]:
    """Portions closest to b (relative) with sum_i coef[c][i] * g_i = targets[c], within [lo, hi].

    Lagrange: g_i = b_i + b_i^2 * sum_c lam_c coef[c][i]. Out-of-bound portions are fixed at the bound
    and the rest re-solved (active set)."""
    g: dict[int, float] = {}
    free = list(b)
    while free:
        rest = [t - sum(c[i] * g[i] for i in g) - sum(c[i] * b[i] for i in free) for c, t in zip(coef, targets, strict=True)]
        m = [[sum(b[i] ** 2 * ci[i] * cj[i] for i in free) for cj in coef] for ci in coef]
        lam = _solve(m, rest)
        if lam is None:  # degenerate mix: drop the last target and retry
            if len(coef) == 1:
                break
            return _min_change(coef[:-1], targets[:-1], b, lo, hi)
        trial = {i: b[i] + b[i] ** 2 * sum(mu * c[i] for mu, c in zip(lam, coef, strict=True)) for i in free}
        out = [i for i in free if not lo[i] <= trial[i] <= hi[i]]
        if not out:
            g.update(trial)
            break
        worst = max(out, key=lambda i: max(lo[i] - trial[i], trial[i] - hi[i]) / b[i])
        g[worst] = min(max(trial[worst], lo[worst]), hi[worst])
        free.remove(worst)
    for i in free:
        g.setdefault(i, b[i])
    return g


def scale_to_targets(db: FoodDB, items: list[FoodPortion], kcal: float, protein_g: float,
                     fat_g: float | None = None, min_g: float = 5, max_g: float = 400,
                     step_g: float = 5) -> list[FoodPortion]:
    """Rescale portions so a day's items hit kcal and protein (and fat, if the mix allows) exactly.

    Minimum-change solve: start from the planner's portions scaled uniformly to the energy target (b),
    then find the portions closest to b, in relative terms, that meet the targets:
        minimise sum(((g_i - b_i) / b_i)^2)  subject to  kcal(g) = K, protein(g) = P [, fat(g) = F]
    Each portion stays within 0.3-3x of b and within [min_g, max_g], so a dish keeps its character
    (no "chicken curry" with 5 g of chicken). If fat cannot also be met, only kcal and protein are
    pinned. If the mix cannot reach the targets within the bounds, the result is as close as the bounds
    allow and the caller reports the gap. Unknown codes are left unchanged.
    """
    foods = [db.get(it.food_code) for it in items]
    idx = [i for i, f in enumerate(foods) if f is not None]
    if not idx or kcal <= 0 or protein_g <= 0:
        return list(items)
    per_g = {k: {i: foods[i].per100g[k] / 100 for i in idx} for k in ("kcal", "protein_g", "fat_g")}
    start = sum(per_g["kcal"][i] * items[i].grams for i in idx)
    t = kcal / start if start > 0 else 1.0
    b = {i: min(max(items[i].grams * t, min_g), max_g) for i in idx}
    lo = {i: max(min_g, 0.3 * b[i]) for i in idx}
    hi = {i: min(max_g, 3 * b[i]) for i in idx}

    def hits(g: dict[int, float]) -> bool:
        return all(abs(sum(per_g[k][i] * g[i] for i in idx) - tgt) <= 0.02 * tgt
                   for k, tgt in (("kcal", kcal), ("protein_g", protein_g)))

    g = None
    if fat_g:
        g = _min_change([per_g["kcal"], per_g["protein_g"], per_g["fat_g"]], [kcal, protein_g, fat_g], b, lo, hi)
        g = g if hits(g) else None
    if g is None:
        g = _min_change([per_g["kcal"], per_g["protein_g"]], [kcal, protein_g], b, lo, hi)
    if not hits(g):  # infeasible within bounds: keep whichever of (kcal+protein, kcal only) misses least
        alt = _min_change([per_g["kcal"]], [kcal], b, lo, hi)

        def err(x: dict[int, float]) -> float:
            return sum(abs(sum(per_g[k][i] * x[i] for i in idx) - tgt) / tgt
                       for k, tgt in (("kcal", kcal), ("protein_g", protein_g)))
        g = min((g, alt), key=err)
    out = []
    for i, it in enumerate(items):
        grams = round(max(step_g, round(g[i] / step_g) * step_g), 1) if i in g else it.grams
        out.append(it.model_copy(update={"grams": grams}))
    return out
