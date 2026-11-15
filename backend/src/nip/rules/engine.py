"""Deterministic validation of a meal plan against a person's profile and targets.

The Validation agent calls this as a tool. It is the non-negotiable part of validation: an LLM
can add judgement (groundedness, tone, plausibility) but cannot override a critical violation.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from nip.data.taxonomy import alias_terms, allergens_for
from nip.domain.models import (
    HealthProfile,
    MealPlan,
    NutritionTotals,
    Sex,
    UserProfile,
    ValidationReport,
    Violation,
)
from nip.tools.foods import FoodDB, compute_totals, is_allowed

RULES_PATH = Path(__file__).with_name("rules.yaml")


@lru_cache
def load_rules(path: Path = RULES_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _pct_off(actual: float, target: float) -> float:
    return 0.0 if target <= 0 else abs(actual - target) / target * 100


def compute_plan_totals(db: FoodDB, plan: MealPlan) -> tuple[MealPlan, list[str]]:
    """Fill every meal and day total from the food table. Returns the plan and unknown food codes."""
    unknown: list[str] = []
    for day in plan.days:
        day_total = NutritionTotals()
        for meal in day.meals:
            totals, missing = compute_totals(db, meal.items)
            meal.totals = totals
            unknown += missing
            day_total = day_total + totals
        day.totals = day_total.rounded()
    return plan, unknown


def validate_plan(db: FoodDB, profile: UserProfile, health: HealthProfile, plan: MealPlan) -> ValidationReport:
    rules = load_rules()
    v: list[Violation] = []
    checked: list[str] = []
    plan, unknown = compute_plan_totals(db, plan)

    checked.append("food_identity")
    for code in sorted(set(unknown)):
        v.append(Violation(rule_id="unknown_food", severity="critical",
                           message=f"Food code {code} is not in the food table.",
                           fix_hint="Use search_foods and pick a returned code."))

    blocked: set[str] = set()
    for a in [*profile.allergies, *profile.intolerances]:
        blocked |= alias_terms(a)

    checked += ["allergens", "diet_pattern", "religious_rules", "portion_size", "region", "interactions"]
    meds = [m.lower() for m in profile.medications]
    for day in plan.days:
        for meal in day.meals:
            for it in meal.items:
                loc = f"day {day.day} / {meal.slot} / {it.food_name}"
                food = db.get(it.food_code)
                if food is None:
                    continue
                if it.food_name.strip().lower() not in food.name.lower() and \
                        food.name.lower() not in it.food_name.strip().lower():
                    # The label shown to the user must match what was actually computed.
                    it.food_name = food.name
                ok, reason = is_allowed(food, profile)
                if not ok:
                    v.append(Violation(rule_id="constraint", severity="critical", location=loc,
                                       message=f"{food.name}: {reason}.", fix_hint="Swap for an allowed food."))
                # Second, independent allergen check from the display name (defence in depth).
                second = allergens_for(it.food_name) & blocked
                if second and ok:
                    v.append(Violation(rule_id="allergen_name_check", severity="critical", location=loc,
                                       message=f"Name suggests {', '.join(sorted(second))}."))
                if it.grams > rules["max_single_portion_g"]:
                    v.append(Violation(rule_id="portion_size", severity="warning", location=loc,
                                       message=f"{it.grams:.0f} g is an unusually large portion."))
                if food.region != profile.region.value:
                    v.append(Violation(rule_id="region", severity="info", location=loc,
                                       message=f"{food.name} comes from the {food.region} food table."))
                name = food.name.lower()
                for rule in rules["interactions"]:
                    if any(m in med for med in meds for m in rule["medications"]) and \
                            any(re.search(rf"(?<![a-z]){re.escape(k)}", name) for k in rule["food_keywords"]):
                        v.append(Violation(rule_id=rule["id"], severity=rule["severity"], location=loc,
                                           message=rule["message"]))

    checked += ["calorie_floor", "macro_targets", "sodium", "saturated_fat", "potassium", "upper_limits",
                "meal_count"]
    floor = rules["calorie_floor_kcal"]["male" if profile.sex == Sex.male else "female"]
    t = health.targets
    tol, tol2 = rules["macro_tolerance_pct"], rules["secondary_tolerance_pct"]
    vit_k: list[float] = []
    for day in plan.days:
        d = day.totals or NutritionTotals()
        where = f"day {day.day}"
        vit_k.append(d.vitamin_k_ug)
        if len(day.meals) != profile.meals_per_day:
            v.append(Violation(rule_id="meal_count", severity="warning", location=where,
                               message=f"{len(day.meals)} meals; profile asks for {profile.meals_per_day}."))
        if d.kcal < floor and not health.escalation_required:
            v.append(Violation(rule_id="calorie_floor", severity="critical", location=where,
                               message=f"{d.kcal:.0f} kcal is below the {floor} kcal floor."))
        for label, actual, target, limit, sev in (
            ("kcal", d.kcal, t.kcal, tol, "critical"),
            ("protein", d.protein_g, t.protein_g, tol, "critical"),
            ("carbs", d.carbs_g, t.carbs_g, tol2, "warning"),
            ("fat", d.fat_g, t.fat_g, tol2, "warning"),
        ):
            off = _pct_off(actual, target)
            if off > limit:
                direction = "above" if actual > target else "below"
                v.append(Violation(rule_id=f"target_{label}", severity=sev, location=where,
                                   message=f"{label} {actual:.0f} is {off:.0f}% {direction} target {target:.0f}.",
                                   fix_hint="Adjust portion grams; recompute with compute_meal_nutrition."))
        lim = health.limits
        if d.sodium_mg > lim.sodium_mg_max:
            v.append(Violation(rule_id="sodium", severity="warning", location=where,
                               message=f"Sodium {d.sodium_mg:.0f} mg exceeds {lim.sodium_mg_max:.0f} mg."))
        if lim.saturated_fat_g_max and d.saturated_fat_g > lim.saturated_fat_g_max:
            v.append(Violation(rule_id="saturated_fat", severity="warning", location=where,
                               message=f"Saturated fat {d.saturated_fat_g:.0f} g exceeds "
                                       f"{lim.saturated_fat_g_max:.0f} g."))
        if lim.potassium_mg_max and d.potassium_mg > lim.potassium_mg_max:
            v.append(Violation(rule_id="potassium", severity="critical", location=where,
                               message=f"Potassium {d.potassium_mg:.0f} mg exceeds {lim.potassium_mg_max:.0f} mg."))
        for key, ul in rules["upper_limits"].items():
            val = getattr(d, key)
            if val > ul:
                v.append(Violation(rule_id=f"upper_limit_{key}", severity="critical" if val > 1.5 * ul else "warning",
                                   location=where, message=f"{key} {val:.0f} exceeds upper limit {ul}."))

    if health.limits.vitamin_k_ug_consistent and len(vit_k) > 1:
        checked.append("vitamin_k_consistency")
        mean = sum(vit_k) / len(vit_k)
        if mean > 0 and (max(vit_k) - min(vit_k)) / mean > rules["vitamin_k_max_daily_swing"]:
            v.append(Violation(rule_id="vitamin_k_consistency", severity="warning",
                               message="Vitamin K varies a lot between days; keep it steady on warfarin."))

    escalation_codes = {f.code for f in health.risk_flags} & set(rules["escalation_flags"])
    if escalation_codes:
        verdict = "ESCALATE"
    elif any(x.severity == "critical" for x in v):
        verdict = "REJECT"
    else:
        verdict = "PASS"
    crit = sum(x.severity == "critical" for x in v)
    warn = sum(x.severity == "warning" for x in v)
    return ValidationReport(
        verdict=verdict, violations=v, checked=checked,
        summary=f"rules {rules['version']}: {crit} critical, {warn} warnings"
                + (f"; escalation: {', '.join(sorted(escalation_codes))}" if escalation_codes else ""),
    )
