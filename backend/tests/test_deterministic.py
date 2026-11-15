"""Deterministic layer: energy maths, food filtering and the rule engine. No LLM involved."""

import pytest

from nip.domain.models import (
    Activity,
    DayPlan,
    DietPattern,
    FoodPortion,
    Goal,
    Meal,
    MealPlan,
    Region,
    Sex,
    UserProfile,
)
from nip.rules.engine import compute_plan_totals, validate_plan
from nip.tools.energy import assess, bmr_mifflin_st_jeor
from nip.tools.foods import get_food_db, is_allowed


@pytest.fixture(scope="module")
def db():
    return get_food_db()


def base_profile(**kw) -> UserProfile:
    d = dict(age=30, sex=Sex.male, height_cm=175, weight_kg=75, activity=Activity.moderate,
             goal=Goal.maintain, region=Region.IN)
    d.update(kw)
    return UserProfile(**d)


# ---- energy --------------------------------------------------------------------------------------

def test_mifflin_reference_values():
    assert bmr_mifflin_st_jeor(75, 175, 30, Sex.male) == 1699
    assert bmr_mifflin_st_jeor(60, 165, 30, Sex.female) == 1320


def test_weight_loss_respects_floor_and_flags_unrealistic_goal():
    p = base_profile(sex=Sex.female, weight_kg=60, height_cm=160, activity=Activity.sedentary,
                     goal=Goal.lose_weight, target_weight_kg=45, timeline_weeks=4)
    h = assess(p)
    assert h.targets.kcal >= 1200
    assert any(f.code == "unrealistic_goal" for f in h.risk_flags)


def test_missing_fields_raise():
    with pytest.raises(ValueError, match="weight_kg"):
        assess(UserProfile(age=30, sex=Sex.male, height_cm=170))


@pytest.mark.parametrize("kw,flag", [
    (dict(age=15), "minor"),
    (dict(sex=Sex.female, pregnant=True), "pregnancy_lactation"),
    (dict(conditions=["CKD stage 3"]), "kidney_disease"),
])
def test_escalation_cases(kw, flag):
    h = assess(base_profile(**kw))
    assert h.escalation_required
    assert flag in {f.code for f in h.risk_flags}


def test_hypertension_caps_sodium():
    assert assess(base_profile(conditions=["hypertension"])).limits.sodium_mg_max == 1500


# ---- food constraints ----------------------------------------------------------------------------

def test_allergy_aliases_block_foods(db):
    p = base_profile(allergies=["nuts", "lactose"])
    assert not is_allowed(db.get("IN-H001"), p)[0]  # almond
    assert not is_allowed(db.get("IN-L003"), p)[0]  # paneer
    assert is_allowed(db.get("IN-A015"), p)[0]  # rice


@pytest.mark.parametrize("pattern,code,allowed", [
    (DietPattern.vegetarian, "IN-M001", False),  # egg
    (DietPattern.eggetarian, "IN-M001", True),
    (DietPattern.vegan, "IN-L003", False),  # paneer
    (DietPattern.pescatarian, "IN-P068", True),  # salmon
    (DietPattern.pescatarian, "IN-O001", False),  # goat
])
def test_diet_patterns(db, pattern, code, allowed):
    assert is_allowed(db.get(code), base_profile(diet_pattern=pattern))[0] is allowed


def test_jain_excludes_root_vegetables(db):
    p = base_profile(diet_pattern=DietPattern.jain)
    potatoes = [f for f in db.search("potato", Region.IN) if "potato" in f.name.lower()]
    assert potatoes and all(not is_allowed(f, p)[0] for f in potatoes)


def test_search_is_region_filtered_and_profile_aware(db):
    p = base_profile(allergies=["dairy"])
    results = db.search("milk", Region.US, profile=p, limit=20)
    assert all(f.region == "US" for f in results)
    assert all("dairy" not in f.allergens for f in results)


# ---- rule engine ---------------------------------------------------------------------------------

def _plan(items_per_meal: list[list[tuple[str, float]]], db) -> MealPlan:
    slots = ["breakfast", "lunch", "dinner"]
    meals = [Meal(slot=s, name=s, items=[FoodPortion(food_code=c, food_name=db.get(c).name, grams=g)
                                         for c, g in items])
             for s, items in zip(slots, items_per_meal, strict=False)]
    return MealPlan(days=[DayPlan(day=1, meals=meals)], region=Region.IN)


def test_totals_are_computed_from_table(db):
    plan, unknown = compute_plan_totals(db, _plan([[("IN-A015", 100)]], db))
    assert not unknown
    assert plan.days[0].totals.kcal == pytest.approx(db.get("IN-A015").per100g["kcal"], abs=0.1)


def test_allergen_in_plan_is_rejected(db):
    p = base_profile(allergies=["peanut"])
    peanut = next(f for f in db.search("groundnut", Region.IN) if "peanut" in f.allergens)
    plan = _plan([[(peanut.code, 50)], [("IN-A015", 100)], [("IN-A015", 100)]], db)
    report = validate_plan(db, p, assess(p), plan)
    assert report.verdict == "REJECT"
    assert any(v.rule_id == "constraint" for v in report.violations)


def test_unknown_food_code_is_rejected(db):
    p = base_profile()
    plan = MealPlan(days=[DayPlan(day=1, meals=[Meal(slot="lunch", name="x", items=[
        FoodPortion(food_code="IN-ZZZ", food_name="Mystery", grams=100)])])], region=Region.IN)
    report = validate_plan(db, p, assess(p), plan)
    assert report.verdict == "REJECT"
    assert any(v.rule_id == "unknown_food" for v in report.violations)


def test_grapefruit_statin_interaction(db):
    p = base_profile(region=Region.US, medications=["Atorvastatin 20mg"])
    gf = db.search("grapefruit raw", Region.US)[0]
    plan = _plan([[(gf.code, 200)]], db)
    report = validate_plan(db, p, assess(p), plan)
    assert any(v.rule_id == "grapefruit_statin" for v in report.violations)


def test_escalation_verdict_for_pregnancy(db):
    p = base_profile(sex=Sex.female, pregnant=True)
    report = validate_plan(db, p, assess(p), _plan([[("IN-A015", 100)]], db))
    assert report.verdict == "ESCALATE"
