"""Deterministic energy and macro calculations. The LLM calls these; it never does the maths.

References (also in the knowledge corpus so the agents can cite them):
- Mifflin-St Jeor equation (Mifflin et al., 1990) for resting energy expenditure.
- Activity multipliers: standard physical-activity-level factors.
- Fiber: 14 g per 1,000 kcal (Dietary Guidelines for Americans).
- Sodium: < 2,000 mg/day (WHO), < 2,300 mg/day (DGA), 1,500 mg/day for hypertension (AHA).
"""

from __future__ import annotations

from nip.domain.models import (
    Activity,
    Goal,
    HealthProfile,
    MacroTargets,
    NutrientLimits,
    Region,
    RiskFlag,
    Sex,
    UserProfile,
)

ACTIVITY_FACTORS: dict[Activity, float] = {
    Activity.sedentary: 1.2,
    Activity.light: 1.375,
    Activity.moderate: 1.55,
    Activity.active: 1.725,
    Activity.very_active: 1.9,
}

# Calorie floors without medical supervision (commonly cited clinical guidance).
CALORIE_FLOOR = {Sex.female: 1200.0, Sex.male: 1500.0}
MAX_DEFICIT_KCAL = 750.0
MAX_DEFICIT_FRACTION = 0.25
SAFE_WEEKLY_LOSS_FRACTION = 0.01  # ~1% of body weight per week
KCAL_PER_KG_FAT = 7700.0

PROTEIN_G_PER_KG: dict[Goal, float] = {
    Goal.lose_weight: 1.4,
    Goal.maintain: 1.0,
    Goal.gain_muscle: 1.6,
    Goal.gain_weight: 1.2,
    Goal.manage_condition: 0.9,
    Goal.general_health: 0.9,
}
FAT_ENERGY_FRACTION = 0.28
PROTEIN_MAX_G_PER_KG = 2.2
PROTEIN_MAX_ENERGY_FRACTION = 0.35

KIDNEY_TERMS = ("ckd", "kidney", "renal")
HYPERTENSION_TERMS = ("hypertension", "high blood pressure", "bp")
DIABETES_TERMS = ("diabetes", "prediabetes", "t2d", "t1d")


def _has(terms: tuple[str, ...], values: list[str]) -> bool:
    return any(t in v for v in values for t in terms)


def bmi(weight_kg: float, height_cm: float) -> float:
    h = height_cm / 100
    return round(weight_kg / (h * h), 1)


def bmr_mifflin_st_jeor(weight_kg: float, height_cm: float, age: int, sex: Sex) -> float:
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return round(base + (5 if sex == Sex.male else -161), 0)


def tdee(bmr: float, activity: Activity) -> float:
    return round(bmr * ACTIVITY_FACTORS[activity], 0)


def required_missing(p: UserProfile) -> list[str]:
    missing = [f for f in ("age", "sex", "height_cm", "weight_kg") if getattr(p, f) is None]
    return missing


def escalation_triggers(p: UserProfile) -> list[str]:
    """Escalation causes visible from stated facts alone, before any energy estimate is possible."""
    found = []
    if p.age is not None and p.age < 18:
        found.append("minor")
    if p.pregnant or p.breastfeeding:
        found.append("pregnancy_lactation")
    if _has(KIDNEY_TERMS, p.conditions):
        found.append("kidney_disease")
    if "t1d" in p.conditions or any("type 1" in c for c in p.conditions):
        found.append("type1_diabetes")
    return found


def assess(p: UserProfile) -> HealthProfile:
    """Compute energy needs, macro targets, nutrient limits and risk flags for a profile.

    Raises ValueError if the fields needed for an energy estimate are missing; the Health agent
    is expected to ask the user (interrupt) rather than guess them.
    """
    missing = required_missing(p)
    if missing:
        raise ValueError(f"missing required fields: {', '.join(missing)}")
    assert p.age and p.sex and p.height_cm and p.weight_kg  # for type checkers

    flags: list[RiskFlag] = []
    assumptions: list[str] = []
    escalate = False

    if p.age < 18:
        escalate = True
        flags.append(RiskFlag(code="minor", severity="critical",
                              message="Under 18: adult energy equations and weight-loss targets do not apply."))
    if p.pregnant or p.breastfeeding:
        escalate = True
        flags.append(RiskFlag(code="pregnancy_lactation", severity="critical",
                              message="Pregnancy or breastfeeding: no calorie deficit; needs clinician input."))

    body_mass_index = bmi(p.weight_kg, p.height_cm)
    if body_mass_index < 18.5:
        flags.append(RiskFlag(code="underweight", severity="warning",
                              message=f"BMI {body_mass_index} is in the underweight range."))
    elif body_mass_index >= 30:
        flags.append(RiskFlag(code="obesity", severity="info", message=f"BMI {body_mass_index} (>= 30)."))

    base = bmr_mifflin_st_jeor(p.weight_kg, p.height_cm, p.age, p.sex)
    maintenance = tdee(base, p.activity)
    floor = CALORIE_FLOOR[p.sex]

    target = maintenance
    goal = p.goal
    if goal == Goal.lose_weight and body_mass_index < 18.5:
        flags.append(RiskFlag(code="weight_loss_underweight", severity="critical",
                              message="Weight loss requested while underweight; not supported."))
        escalate = True
        goal = Goal.maintain

    if goal == Goal.lose_weight and not (p.pregnant or p.breastfeeding or p.age < 18):
        deficit = min(MAX_DEFICIT_KCAL, MAX_DEFICIT_FRACTION * maintenance)
        if p.target_weight_kg and p.timeline_weeks:
            to_lose = p.weight_kg - p.target_weight_kg
            weekly = to_lose / p.timeline_weeks
            if weekly > SAFE_WEEKLY_LOSS_FRACTION * p.weight_kg:
                safe_weeks = int(to_lose / (SAFE_WEEKLY_LOSS_FRACTION * p.weight_kg) + 0.999)
                flags.append(RiskFlag(
                    code="unrealistic_goal", severity="warning",
                    message=(f"Losing {to_lose:.1f} kg in {p.timeline_weeks} weeks is {weekly:.2f} kg/week, "
                             f"above ~1% body weight/week. A safer timeline is about {safe_weeks} weeks."),
                ))
            else:
                deficit = min(deficit, weekly * KCAL_PER_KG_FAT / 7)
        target = maintenance - deficit
        if target < floor:
            assumptions.append(f"Target raised to the {floor:.0f} kcal floor for unsupervised plans.")
            target = floor
    elif goal == Goal.gain_muscle:
        target = maintenance + 250
    elif goal == Goal.gain_weight:
        target = maintenance + 400

    protein_per_kg = PROTEIN_G_PER_KG[goal]
    limits = NutrientLimits(sodium_mg_max=2000 if p.region == Region.IN else 2300)

    if _has(KIDNEY_TERMS, p.conditions):
        escalate = True
        protein_per_kg = min(protein_per_kg, 0.8)
        limits.potassium_mg_max = 2500
        flags.append(RiskFlag(code="kidney_disease", severity="critical",
                              message="Kidney disease: protein, potassium and phosphorus need clinician targets."))
    if _has(HYPERTENSION_TERMS, p.conditions):
        limits.sodium_mg_max = 1500
        flags.append(RiskFlag(code="hypertension", severity="warning", message="Sodium capped at 1,500 mg/day."))
    if _has(DIABETES_TERMS, p.conditions):
        limits.added_sugar_g_max = 25
        flags.append(RiskFlag(code="diabetes", severity="warning",
                              message="Prefer low-glycaemic carbohydrates; limit added sugar to 25 g/day."))
        if "type1_diabetes" in escalation_triggers(p):
            escalate = True
            flags.append(RiskFlag(code="type1_diabetes", severity="critical",
                                  message="Type 1 diabetes: carbohydrate changes affect insulin dosing."))
    if any("warfarin" in m for m in p.medications):
        limits.vitamin_k_ug_consistent = True
        flags.append(RiskFlag(code="warfarin_vitamin_k", severity="warning",
                              message="Warfarin: keep vitamin K intake consistent day to day."))

    weight_for_protein = p.weight_kg
    if body_mass_index >= 30:
        # Use an adjusted weight for protein in obesity to avoid excessive targets.
        ideal = 25 * (p.height_cm / 100) ** 2
        weight_for_protein = ideal + 0.4 * (p.weight_kg - ideal)
        assumptions.append("Protein based on adjusted body weight (BMI >= 30).")

    protein_g = round(protein_per_kg * weight_for_protein, 0)
    fat_g = round(target * FAT_ENERGY_FRACTION / 9, 0)
    carbs_g = round(max(0.0, (target - protein_g * 4 - fat_g * 9) / 4), 0)
    fiber_g = round(14 * target / 1000, 0)
    limits.saturated_fat_g_max = round(target * 0.10 / 9, 0)
    # Protein is a range, not a point: the target is the minimum that serves the goal (-10% tolerance);
    # above it, intakes are acceptable up to 2.2 g/kg and 35% of energy (top of the AMDR). Kidney disease
    # keeps the target as the cap (and is escalated anyway).
    if _has(KIDNEY_TERMS, p.conditions):
        limits.protein_g_max = round(protein_g * 1.1, 0)
    else:
        cap = min(PROTEIN_MAX_G_PER_KG * p.weight_kg, PROTEIN_MAX_ENERGY_FRACTION * target / 4)
        limits.protein_g_max = round(max(protein_g * 1.1, cap), 0)

    return HealthProfile(
        bmr_kcal=base,
        tdee_kcal=maintenance,
        targets=MacroTargets(kcal=round(target, 0), protein_g=protein_g, carbs_g=carbs_g,
                             fat_g=fat_g, fiber_g=fiber_g),
        limits=limits,
        risk_flags=flags,
        escalation_required=escalate,
        assumptions=assumptions,
        rationale=(f"BMR {base:.0f} kcal (Mifflin-St Jeor) x activity {ACTIVITY_FACTORS[p.activity]} = "
                   f"TDEE {maintenance:.0f} kcal; goal '{goal.value}' gives {target:.0f} kcal."),
    )
