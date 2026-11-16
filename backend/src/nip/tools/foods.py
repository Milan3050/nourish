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
    if p.dislikes:  # token match with synonyms and plurals: "brown rice" excludes "Rice, raw, brown"
        name_toks = set(_tokens(food.name))
        for d in p.dislikes:
            dt = set(_tokens(expand_food_query(d, food.region)))
            if dt and dt <= name_toks:
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


# Default share of the day's energy per meal slot (renormalised over the slots a day has).
SLOT_ENERGY_SHARE = {"breakfast": 0.25, "mid_morning": 0.10, "lunch": 0.35, "snack": 0.10, "dinner": 0.30,
                     "bedtime": 0.05}


def scale_to_targets(db: FoodDB, items: list[FoodPortion], kcal: float, protein_g: float,
                     fat_g: float | None = None, meal_of: list[int] | None = None,
                     meal_share: dict[int, float] | None = None, protein_max: float | None = None,
                     min_g: float = 5, max_g: float = 500, step_g: float = 5) -> list[FoodPortion]:
    """Rescale portions so a day's items hit kcal and protein exactly, and if possible fat and each
    meal's share of the day's energy (so lunch is not 100 kcal while breakfast is 1,500).

    Minimum-change solve: start from the planner's portions scaled to the energy target (b), then find
    the portions closest to b, in relative terms, that meet the targets:
        minimise sum(((g_i - b_i) / b_i)^2)  subject to  linear targets (kcal per meal or per day,
        protein, fat)
    Target sets are tried from most to least complete: meal shares + protein + fat, meal shares +
    protein, day kcal + protein + fat, day kcal + protein. Each portion stays within 0.3-3x of b and
    within [min_g, max_g], so a dish keeps its character (no "chicken curry" with 5 g of chicken). If
    the mix cannot reach the targets within the bounds, the result is as close as the bounds allow and
    the caller reports the gap. Unknown codes are left unchanged.
    """
    foods = [db.get(it.food_code) for it in items]
    idx = [i for i, f in enumerate(foods) if f is not None]
    if not idx or kcal <= 0 or protein_g <= 0:
        return list(items)
    per_g = {k: {i: foods[i].per100g[k] / 100 for i in idx} for k in ("kcal", "protein_g", "fat_g")}
    meals = sorted({meal_of[i] for i in idx}) if meal_of and meal_share else []
    meal_kcal_target = {m: meal_share[m] * kcal for m in meals} if meals else {}
    start_meal = {m: sum(per_g["kcal"][i] * items[i].grams for i in idx if meal_of[i] == m) for m in meals}
    start = sum(per_g["kcal"][i] * items[i].grams for i in idx)

    def start_scale(i: int) -> float:
        if meals and start_meal[meal_of[i]] > 0:
            return meal_kcal_target[meal_of[i]] / start_meal[meal_of[i]]
        return kcal / start if start > 0 else 1.0
    b = {i: min(max(items[i].grams * start_scale(i), min_g), max_g) for i in idx}
    lo = {i: max(min_g, 0.3 * b[i]) for i in idx}
    hi = {i: min(max_g, 3 * b[i]) for i in idx}

    def total(k: str, x: dict[int, float]) -> float:
        return sum(per_g[k][i] * x[i] for i in idx)

    p_lo, p_hi = 0.98 * protein_g, max(protein_max or 0, 1.02 * protein_g)

    def day_ok(x: dict[int, float]) -> bool:  # kcal on target, protein inside its range with room for rounding
        return abs(total("kcal", x) - kcal) <= 0.02 * kcal and 1.01 * p_lo <= total("protein_g", x) <= 0.97 * p_hi

    def meals_ok(x: dict[int, float]) -> bool:
        return all(abs(sum(per_g["kcal"][i] * x[i] for i in idx if meal_of[i] == m) - t) <= 0.15 * t
                   for m, t in meal_kcal_target.items())

    per_meal = [({i: per_g["kcal"][i] if meal_of[i] == m else 0.0 for i in idx}, t)
                for m, t in meal_kcal_target.items()]
    day = [(per_g["kcal"], kcal)]
    prot, fat = [(per_g["protein_g"], protein_g)], [(per_g["fat_g"], fat_g)] if fat_g else []
    # Most complete first. Balanced meals and fat matter more than hitting protein to the gram, so a set
    # with protein pinned is tried before the same set with protein free inside its range.
    split = len(meals) > 1
    # protein just inside a range edge; the margin absorbs rounding portions to 5 g
    edges = [(per_g["protein_g"], 0.96 * p_hi), (per_g["protein_g"], 1.03 * p_lo)]
    attempts = ([(per_meal + prot + fat, True), (per_meal + fat, True), (per_meal + prot, True), (per_meal, True),
                 (per_meal + edges[:1], True), (per_meal + edges[1:], True)] if split else []) + \
        [(day + prot + fat, False), (day + fat, False), (day + prot, False), (day, False)]
    g = None
    for cons, needs_meals in attempts:
        if not cons[-1][1]:
            continue
        cand = _min_change([c for c, _ in cons], [t for _, t in cons], b, lo, hi)
        if day_ok(cand) and (not needs_meals or meals_ok(cand)):
            g = cand
            break
    if g is None:  # infeasible within bounds: pin protein at the nearest edge of its range, else kcal only
        def miss(x: dict[int, float]) -> float:
            p = total("protein_g", x)
            return abs(total("kcal", x) - kcal) / kcal + max(p_lo - p, p - p_hi, 0) / protein_g
        cands = [_min_change([per_g["kcal"], per_g["protein_g"]], [kcal, edge], b, lo, hi) for edge in (p_lo, p_hi)]
        g = min([*cands, _min_change([per_g["kcal"]], [kcal], b, lo, hi)], key=miss)
    out = []
    for i, it in enumerate(items):
        grams = round(max(step_g, round(g[i] / step_g) * step_g), 1) if i in g else it.grams
        out.append(it.model_copy(update={"grams": grams}))
    return out


EXCLUSION_CUES = re.compile(
    r"\b(?:without|no|not|except|instead of|other than|minus|skip|avoid|remove|replace|swap out|hold the|"
    r"don't want|do not want|dont want)\s+(?:the\s+|any\s+|my\s+)?([a-z][a-z' ,-]{1,80}?)"
    r"(?=\s*(?:[.;!?]|$|\b(?:for|in|at|on|with|please|today|tomorrow|anymore|any more|this|that|but)\b))", re.I)


NOT_FOODS = frozenset({"time", "fuss", "problem", "way", "idea", "thank", "worry", "need", "more", "less", "longer",
                       "one", "day", "meal", "dinner", "lunch", "breakfast", "snack", "plan", "cooking", "cook",
                       "change", "stress", "rush", "hurry", "budget", "money", "cost", "effort", "prep"})


def request_exclusions(db: FoodDB, text: str, region: str | None) -> list[str]:
    """Foods the user rules out in this message ("without paneer", "no rice or onion please").

    Only phrases that name a food in the region's table count, so "no time" or "swap the dinner" are
    ignored. Returned terms are used as this turn's dislikes, which every food check already enforces."""
    toks = [db._tok[f.code] for f in db.foods if not region or f.region == region]
    out = []
    for m in EXCLUSION_CUES.finditer(text):
        for phrase in re.split(r"\s*(?:,|\band\b|\bor\b|\bnor\b)\s*", m.group(1).lower()):
            words = [w for w in phrase.split() if w not in ("some", "a", "an")]
            for n in range(len(words), 0, -1):  # longest leading food name ("brown rice" before "brown")
                cand = " ".join(words[:n])
                q = set(_tokens(expand_food_query(cand, region)))
                if q and not q & NOT_FOODS and any(q <= t for t in toks):
                    out.append(cand)
                    break
    return sorted(set(out))


@lru_cache
def _head_phrases(region: str) -> list[frozenset[str]]:
    """Distinct head names in a region's table ('Green gram', 'Paneer', 'Rice'), as token sets."""
    db = get_food_db()
    heads = {frozenset(_tokens(f.name.split(",")[0])) for f in db.foods if f.region == region}
    return [h for h in heads if h and len(h) <= 3]


def names_missing_foods(dish: str, item_names: list[str], region: str) -> list[str]:
    """Foods a dish name mentions that none of its ingredients are ('Paneer curry' made of groundnut)."""
    dish_toks = set(_tokens(expand_food_query(dish, region)))
    item_toks = [set(_tokens(n)) for n in item_names]
    missing = [h for h in _head_phrases(region) if h <= dish_toks and not any(h <= t for t in item_toks)]
    # report the most specific phrases only ("green gram", not also "gram")
    return sorted({" ".join(sorted(h)) for h in missing if not any(h < o for o in missing)})
