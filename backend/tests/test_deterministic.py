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


# ---- profile facts (health agent's update_profile) -------------------------------------------------

def test_profile_facts_convert_units_and_reject_unstated_numbers():
    from nip.agents.specs import record_profile_facts

    text = "I'm a 34 year old woman, 5ft 4in, 150 lb"
    res = record_profile_facts({}, {"age": 34, "sex": "woman", "height_ft": 5, "height_in": 4,
                                    "weight_lb": 150, "goal": "lose_weight"}, text)
    p = res["profile"]
    assert (p["age"], p["sex"], p["height_cm"], p["weight_kg"]) == (34, "female", 162.6, 68.0)
    assert not res["rejected"]
    guessed = record_profile_facts({}, {"age": 30, "sex": "female", "weight_kg": 70, "goal": "make a plan"},
                                   "make me a plan")
    assert guessed["profile"]["age"] is None and guessed["profile"]["weight_kg"] is None
    assert guessed["profile"]["sex"] is None
    assert set(guessed["rejected"]) == {"age", "sex", "weight_kg", "goal"}


# ---- planner tools ---------------------------------------------------------------------------------

def test_scale_portions_hits_targets_with_chosen_foods():
    from nip.tools.registry import scale_portions

    meals = [{"slot": "breakfast", "name": "Poha with milk", "items": [{"food_code": "IN-A011", "grams": 60},
                                                                     {"food_code": "IN-L002", "grams": 200}]},
             {"slot": "lunch", "name": "Moong dal, rice, palak", "items": [
                 {"food_code": "IN-B010", "grams": 60}, {"food_code": "IN-A015", "grams": 80},
                 {"food_code": "IN-C033", "grams": 100}]},
             {"slot": "dinner", "name": "Paneer with roti", "items": [{"food_code": "IN-L003", "grams": 100},
                                                                    {"food_code": "IN-A019", "grams": 80}]}]
    res = scale_portions(meals, kcal=2056, protein_g=115)
    assert abs(res["pct_of_target"]["kcal"] - 100) <= 3 and abs(res["pct_of_target"]["protein_g"] - 100) <= 3
    codes = [[i["food_code"] for i in m["items"]] for m in res["meals"]]
    assert codes == [[i["food_code"] for i in m["items"]] for m in meals]
    assert all(5 <= i["grams"] <= 500 for m in res["meals"] for i in m["items"])
    low = scale_portions(meals[:1], kcal=2056, protein_g=115)  # poha + milk cannot reach 115 g protein
    assert "advice" in low


def test_food_search_understands_everyday_names():
    db = get_food_db()
    assert db.search("moong dal", Region.IN, 1)[0].name == "Green gram, dal"
    assert db.search("palak", Region.IN, 1)[0].name == "Spinach"
    assert db.search("chickpeas", Region.US, 1)[0].name.startswith("Chickpeas")


def test_plan_slot_names_are_normalised():
    from nip.agents.specs import PlanMeal

    m = PlanMeal(slot="Evening Snack", name="x", items=[{"food_code": "IN-L003", "grams": 50}])
    assert m.slot == "snack" and m.items[0].food_name == ""


def test_ifct_two_word_allergen_names_are_tagged(db):
    """Regression: IFCT spells peanut 'Ground nut'; it was untagged and reached a peanut-allergic planner."""
    peanut = UserProfile(region=Region.IN, allergies=["peanut"])
    assert "peanut" in db.get("IN-H012").allergens
    assert all(f.code != "IN-H012" for f in db.search("groundnut", Region.IN, 20, peanut))
    assert not is_allowed(db.get("IN-H017"), UserProfile(allergies=["tree nuts"]))[0]  # pine seeds


def test_planner_check_rejects_meat_dish_names_and_returns_rescaled_days():
    import json as _json

    from nip.agents.specs import PlanSubmission, planner_agent

    profile = base_profile(diet_pattern=DietPattern.vegetarian, goal=Goal.lose_weight)
    ctx = {"profile": profile.model_dump(mode="json"), "health": assess(profile).model_dump()}
    agent = planner_agent(ctx)
    day = {"day": 1, "meals": [
        {"slot": "breakfast", "name": "Poha with milk", "items": [{"food_code": "IN-A011", "grams": 40},
                                                                 {"food_code": "IN-L002", "grams": 150}]},
        {"slot": "lunch", "name": "Dal rice", "items": [{"food_code": "IN-B010", "grams": 50},
                                                        {"food_code": "IN-A015", "grams": 60}]},
        {"slot": "dinner", "name": "Paneer roti", "items": [{"food_code": "IN-L003", "grams": 60},
                                                          {"food_code": "IN-A019", "grams": 60}]}]}
    errs = agent.check(PlanSubmission(days=[day]))
    assert any("vs target" in e for e in errs) and errs[0].startswith("Rescaled")
    fixed = _json.loads(errs[0].split("unchanged: ", 1)[1])
    assert agent.check(PlanSubmission(days=fixed)) == []  # the handed-back days pass the unchanged check
    named = {**day, "meals": [{**day["meals"][0], "name": "Chicken curry with rice"}, *day["meals"][1:]]}
    assert any("dish name" in e for e in agent.check(PlanSubmission(days=[named])))


def test_planner_check_rejects_one_meal_days():
    from nip.agents.specs import PlanSubmission, planner_agent

    profile = base_profile(diet_pattern=DietPattern.vegetarian)
    agent = planner_agent({"profile": profile.model_dump(mode="json"), "health": assess(profile).model_dump()})
    meal = {"slot": "lunch", "name": "Dal rice", "items": [{"food_code": "IN-B010", "grams": 300}]}
    errs = agent.check(PlanSubmission(days=[{"day": 1, "meals": [meal]}, {"day": 1, "meals": [meal]}]))
    assert any("repeat" in e for e in errs) and any("at least 3 meals" in e for e in errs)


def test_request_exclusions_and_token_dislikes(db):
    from nip.tools.foods import request_exclusions

    assert request_exclusions(db, "Swap the dinner for something without paneer", "IN") == ["paneer"]
    assert request_exclusions(db, "avoid brown rice and spinach", "IN") == ["brown rice", "spinach"]
    assert request_exclusions(db, "I have no time to cook, swap dinner", "US") == []
    p = UserProfile(dislikes=["brown rice", "moong dal"])
    assert not is_allowed(db.get("IN-A013"), p)[0] and is_allowed(db.get("IN-A015"), p)[0]  # brown vs milled
    assert not is_allowed(db.get("IN-B010"), p)[0] and is_allowed(db.get("IN-B001"), p)[0]  # green vs bengal gram


def test_partial_edit_keeps_untouched_meals():
    from nip.agents.specs import planner_agent

    profile = base_profile(diet_pattern=DietPattern.vegetarian)
    current = {"version": 1, "days": [{"day": 1, "meals": [
        {"slot": s, "name": s, "items": [{"food_code": "IN-B010", "food_name": "x", "grams": 100}]}
        for s in ("breakfast", "lunch", "dinner")]}]}
    agent = planner_agent({"profile": profile.model_dump(mode="json"), "health": assess(profile).model_dump(),
                           "plan": current})
    new_dinner = {"slot": "dinner", "name": "Rajma curry", "items": [{"food_code": "IN-B020", "grams": 80}]}
    out = agent.prepare({"days": [{"day": 1, "meals": [new_dinner]}]})
    meals = out["days"][0]["meals"]
    assert [m["slot"] for m in meals] == ["breakfast", "lunch", "dinner"] and meals[2]["name"] == "Rajma curry"


def test_scale_portions_balances_energy_across_meals():
    from nip.tools.registry import scale_portions

    skewed = [{"slot": "breakfast", "name": "Big breakfast", "items": [
                  {"food_code": "IN-B010", "grams": 275}, {"food_code": "IN-L003", "grams": 100},
                  {"food_code": "IN-A019", "grams": 220}]},
              {"slot": "lunch", "name": "Tiny lunch", "items": [{"food_code": "IN-A015", "grams": 20},
                                                              {"food_code": "IN-B021", "grams": 20}]},
              {"slot": "dinner", "name": "Tiny dinner", "items": [{"food_code": "IN-A013", "grams": 20},
                                                                {"food_code": "IN-B010", "grams": 20}]}]
    res = scale_portions(skewed, kcal=2056, protein_g=115, fat_g=64)
    shares = [k / sum(res["meal_kcal"]) for k in res["meal_kcal"]]
    assert all(0.2 <= x <= 0.45 for x in shares), shares


def test_dish_names_must_match_ingredients():
    from nip.tools.foods import names_missing_foods

    assert names_missing_foods("Stir-fry with paneer and brown rice", ["Ground nut", "Ladies finger"], "IN") == \
        ["paneer", "rice"]
    assert names_missing_foods("Moong dal with roti", ["Green gram, dal", "Wheat flour, atta"], "IN") == []


def test_misleading_dish_names_are_renamed_from_ingredients():
    from nip.agents.specs import _honest_dish_names

    args = {"days": [{"day": 1, "meals": [
        {"slot": "dinner", "name": "Paneer curry with spinach",
         "items": [{"food_code": "IN-H012", "grams": 60}, {"food_code": "IN-A019", "grams": 120}]},
        {"slot": "lunch", "name": "Moong dal with roti",
         "items": [{"food_code": "IN-B010", "grams": 80}, {"food_code": "IN-A019", "grams": 100}]}]}]}
    meals = _honest_dish_names(args, {"region": "IN"})["days"][0]["meals"]
    assert meals[0]["name"] == "Wheat flour with ground nut" and meals[1]["name"] == "Moong dal with roti"


def test_planner_folds_one_meal_days_into_one_day():
    from nip.agents.specs import planner_agent

    profile = base_profile(diet_pattern=DietPattern.vegetarian)
    agent = planner_agent({"profile": profile.model_dump(mode="json"), "health": assess(profile).model_dump()})
    meal = lambda slot: {"slot": slot, "name": "Dal", "items": [{"food_code": "IN-B010", "grams": 100}]}  # noqa: E731
    out = agent.prepare({"days": [{"day": 1, "meals": [meal("dinner")]}, {"day": 1, "meals": [meal("breakfast")]},
                                  {"day": 1, "meals": [meal("lunch")]}]})
    assert len(out["days"]) == 1 and [m["slot"] for m in out["days"][0]["meals"]] == ["breakfast", "lunch", "dinner"]


def test_safe_mode_answers_carry_no_intake_targets():
    from nip.agents.specs import RecommendationSubmission, recommendation_agent

    profile = base_profile(conditions=["ckd stage 3"])
    health = assess(profile).model_dump()
    assert health["escalation_required"]
    agent = recommendation_agent({"profile": profile.model_dump(mode="json"), "health": health, "retrieved": {},
                                  "evidence": [], "plan_shown": False})
    bad = RecommendationSubmission(headline="Protein and CKD", summary="Aim for 1.6 g/kg of protein per day.")
    ok = RecommendationSubmission(headline="Protein and CKD", summary="Ask a renal dietitian to set your protein.")
    assert any("Safe mode" in e for e in agent.check(bad)) and agent.check(ok) == []


def test_requested_day_count_is_enforced():
    from nip.agents.specs import planner_agent
    from nip.graph.build import requested_days

    assert requested_days("Make me a 1-day plan") == 1 and requested_days("three days please") == 3
    assert requested_days("lose 5 kg in 4 weeks, 1-day plan") == 1 and requested_days("Make me a meal plan") is None
    profile = base_profile(diet_pattern=DietPattern.vegetarian)
    agent = planner_agent({"profile": profile.model_dump(mode="json"), "health": assess(profile).model_dump(),
                           "requested_days": 1})
    meals = [{"slot": s, "name": "Dal", "items": [{"food_code": "IN-B010", "grams": 100}]}
             for s in ("breakfast", "lunch", "dinner")]
    out = agent.prepare({"days": [{"day": n, "meals": meals} for n in (1, 2, 3)]})
    assert [d["day"] for d in out["days"]] == [1]


def test_protein_is_a_range_with_a_deterministic_cap(db):
    from nip.tools.registry import scale_portions

    gain = assess(base_profile(age=26, height_cm=180, weight_kg=60, goal=Goal.gain_muscle))
    assert gain.limits.protein_g_max == min(2.2 * 60, 0.35 * gain.targets.kcal / 4)
    ckd = assess(base_profile(conditions=["ckd"]))
    assert ckd.limits.protein_g_max == round(ckd.targets.protein_g * 1.1)
    # The live failure: an egg/chicken/soya day for a 60 kg muscle-gain user was rejected 4x as 'protein too high'.
    meals = [{"slot": "breakfast", "name": "Egg omelette with roti", "items": [{"food_code": "IN-M001", "grams": 120},
                                                                          {"food_code": "IN-A019", "grams": 100}]},
             {"slot": "lunch", "name": "Chicken curry with rice", "items": [{"food_code": "IN-N002", "grams": 150},
                                                                          {"food_code": "IN-A015", "grams": 150}]},
             {"slot": "dinner", "name": "Soya curry with roti", "items": [{"food_code": "IN-B024", "grams": 80},
                                                                        {"food_code": "IN-A019", "grams": 120}]}]
    t = gain.targets
    res = scale_portions(meals, t.kcal, t.protein_g, t.fat_g, gain.limits.protein_g_max)
    assert "advice" not in res and abs(res["pct_of_target"]["kcal"] - 100) <= 3
    assert t.protein_g * 0.9 <= res["totals"]["protein_g"] <= gain.limits.protein_g_max
    shares = [k / sum(res["meal_kcal"]) for k in res["meal_kcal"]]
    assert all(0.2 <= x <= 0.45 for x in shares)


def test_bullets_that_repeat_the_summary_are_dropped():
    from nip.graph.build import distinct_points

    summary = "Your goal is to gain muscle and build weight. Aim for protein of 1.4 to 2.0 g per kg."
    pts = ["Your goal is to gain muscle and build weight.", "Eat a protein source at every meal.",
           "Aim for protein intakes of 1.4 to 2.0 g per kg daily."]
    assert distinct_points(summary, pts) == ["Eat a protein source at every meal."]
