"""The five domain agents: goals, tools, submission schemas and acceptance checks.

Each factory returns a GoalAgent bound to the current run's context. Tools close over that context
(profile, targets, region) so the LLM cannot, for example, search another region's foods or check a
plan against someone else's allergies.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, field_validator

from nip.agents.base import Emit, GoalAgent
from nip.config import get_settings
from nip.data.taxonomy import alias_terms, allergens_for, meats_for
from nip.domain.models import RiskFlag, UserProfile, Violation
from nip.security.guard import check_output, wrap_untrusted
from nip.tools.backend import get_backend
from nip.tools.foods import SLOT_ENERGY_SHARE, get_food_db, is_allowed, names_missing_foods
from nip.tools.registry import scale_portions as scale_portions_tool


def _tool(fn, name: str, description: str) -> StructuredTool:
    return StructuredTool.from_function(coroutine=fn, name=name, description=description)


# ==================================================================================================
# Health Assessment
# ==================================================================================================

class HealthSubmission(BaseModel):
    profile_complete: bool = Field(description="True if age, sex, height and weight are known.")
    questions_for_user: list[str] = Field(default=[], description="Short questions for missing essentials.")
    extra_risk_flags: list[RiskFlag] = Field(default=[], description="Risks you noticed in the user's words.")
    assumptions: list[str] = []
    rationale: str = Field(default="", description="One or two sentences on the assessment.")


HEALTH_GOAL = """Establish an accurate, safe health profile for this user so other agents can plan for them.
1. Record any facts the user stated (age, sex, height, weight, activity, goal, diet pattern, allergies,
   conditions, medications, pregnancy, region...) with `update_profile`, in the user's own units
   (weight_lb, height_ft + height_in): the tool converts them. Only record what the user actually said;
   values the user did not state are rejected.
2. Call `assess_energy` to get energy needs, targets, limits and risk flags from the deterministic calculator.
3. If essentials are missing, do not guess: submit profile_complete=false with 1-3 short questions.
4. Add risk flags the calculator cannot see from the user's words (e.g. signs of disordered eating:
   code `eating_disorder_risk`, severity critical; mentions of medications or conditions not yet recorded).
5. You may use `search_guidelines` when a condition needs context."""


class ProfileFacts(BaseModel):
    """Facts the user stated, as flat optional fields. Omit anything the user did not say.

    Enums are plain strings here so one bad value is reported per field instead of failing the call.
    """
    age: int | None = None
    sex: str | None = Field(default=None, description="male | female")
    height_cm: float | None = None
    height_ft: float | None = Field(default=None, description="Feet part when the user gave feet/inches")
    height_in: float | None = Field(default=None, description="Inches part (or total inches)")
    weight_kg: float | None = None
    weight_lb: float | None = Field(default=None, description="Use when the user gave pounds")
    target_weight_kg: float | None = None
    target_weight_lb: float | None = None
    timeline_weeks: int | None = None
    activity: str | None = Field(default=None, description="sedentary | light | moderate | active | very_active")
    goal: str | None = Field(default=None, description="lose_weight | maintain | gain_muscle | gain_weight | "
                                                       "manage_condition | general_health")
    region: str | None = Field(default=None, description="IN | US")
    diet_pattern: str | None = Field(default=None, description="omnivore | eggetarian | vegetarian | vegan | "
                                                               "pescatarian | jain")
    religious_rules: list[str] | None = Field(default=None, description="halal | kosher | no_beef | no_pork")
    allergies: list[str] | None = None
    intolerances: list[str] | None = None
    dislikes: list[str] | None = None
    conditions: list[str] | None = None
    medications: list[str] | None = None
    pregnant: bool | None = None
    breastfeeding: bool | None = None
    meals_per_day: int | None = None
    budget: str | None = Field(default=None, description="low | medium | high")
    cuisine_preferences: list[str] | None = None


# Numeric facts that must be traceable to the user's own words (or the stored profile).
GROUNDED_NUMBERS = ("age", "height_cm", "height_ft", "height_in", "weight_kg", "weight_lb",
                    "target_weight_kg", "target_weight_lb", "timeline_weeks")
LB_TO_KG = 0.45359237
SEX_WORDS = re.compile(r"\b(male|female|man|woman|boy|girl|guy|lady|m|f|he|she|him|her|husband|wife|mother|"
                       r"father|mom|dad|son|daughter|pregnant)\b", re.I)


def _stated(value: float, text_numbers: set[float]) -> bool:
    return any(abs(value - n) <= 0.51 for n in text_numbers)


def record_profile_facts(profile: dict, facts: dict, user_text: str) -> dict:
    """Deterministically merge stated facts: unit conversion, grounding and per-field validation.

    Returns {"profile", "recorded", "rejected"}. A number the user never said is rejected, so the
    model cannot fill gaps in the profile with plausible guesses.
    """
    text_nums = {float(m) for m in re.findall(r"\d+(?:\.\d+)?", user_text)}
    text_nums |= {float(v) for v in profile.values() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    facts = {k: v for k, v in facts.items() if v not in (None, "", [])}
    rejected: dict[str, str] = {}
    for k in GROUNDED_NUMBERS:
        if k in facts and not _stated(float(facts[k]), text_nums):
            rejected[k] = (f"{facts.pop(k)} is not a number the user stated. Pass values in the user's own units "
                          "(weight_lb, height_ft/height_in); if they did not say it, ask instead of guessing")
    if "sex" in facts and not profile.get("sex") and not SEX_WORDS.search(user_text):
        rejected["sex"] = f"{facts.pop('sex')!r} is not stated by the user; ask instead of guessing"
    if "height_ft" in facts or "height_in" in facts:
        facts["height_cm"] = round(facts.pop("height_ft", 0) * 30.48 + facts.pop("height_in", 0) * 2.54, 1)
    for unit in ("weight", "target_weight"):
        if f"{unit}_lb" in facts:
            facts[f"{unit}_kg"] = round(facts.pop(f"{unit}_lb") * LB_TO_KG, 1)
    merged, recorded = {**profile}, []
    for k, v in facts.items():
        if k == "sex" and isinstance(v, str):
            v = {"m": "male", "f": "female", "man": "male", "woman": "female"}.get(v.lower(), v.lower())
        if isinstance(merged.get(k), list) and isinstance(v, list):
            v = sorted({*merged[k], *v})
        trial = {**merged, k: v}
        try:
            UserProfile.model_validate(trial)
        except Exception as e:
            msg = e.errors()[0]["msg"] if hasattr(e, "errors") else str(e)
            rejected[k] = f"invalid value {v!r}: {msg}"[:200]
            continue
        merged, recorded = trial, [*recorded, k]
    return {"profile": UserProfile.model_validate(merged).model_dump(mode="json"), "recorded": recorded,
            "rejected": rejected}


def health_agent(ctx: dict, emit: Emit | None = None) -> GoalAgent:
    be = get_backend()

    async def update_profile(**facts) -> dict:
        """Record facts the user stated. Pass only fields the user actually gave; never guess values.
        Give pounds as weight_lb and feet/inches as height_ft + height_in; the tool converts units."""
        res = record_profile_facts(ctx["profile"], facts, ctx.get("user_text", ""))
        ctx["profile"] = res["profile"]
        return {"ok": not res["rejected"], **res}

    async def assess_energy() -> dict:
        """Compute BMR, TDEE, macro targets, nutrient limits and risk flags for the current profile."""
        res = await be.call("assess_energy", {"profile": ctx["profile"]})
        ctx["assessment"] = res.get("assessment")
        return res

    async def search_guidelines(query: str) -> str:
        """Search vetted nutrition guidelines for the user's region."""
        return await _guideline_search(be, ctx, query)

    def check(sub: HealthSubmission) -> list[str]:
        errs = []
        if sub.profile_complete and ctx.get("assessment") is None:
            errs.append("Call assess_energy successfully before submitting profile_complete=true.")
        if not sub.profile_complete and not sub.questions_for_user:
            errs.append("Profile is incomplete: include questions_for_user.")
        return errs

    tools = [
        StructuredTool.from_function(coroutine=update_profile, name="update_profile",
                                     description=update_profile.__doc__, args_schema=ProfileFacts),
        _tool(assess_energy, "assess_energy", assess_energy.__doc__),
        _tool(search_guidelines, "search_guidelines", search_guidelines.__doc__),
    ]
    return GoalAgent("Health Assessment Agent", HEALTH_GOAL, tools, HealthSubmission, check,
                     get_settings().max_agent_steps, emit)


# ==================================================================================================
# Nutrition Knowledge (agentic RAG)
# ==================================================================================================

class EvidenceAnswer(BaseModel):
    question: str
    answer: str = Field(description="2-4 sentences, only what the cited passages support.")
    cited_chunk_ids: list[str] = Field(default=[], description="chunk_id values from search results.")
    sufficient: bool = Field(default=True, description="False if the retrieved passages do not answer the question.")


class KnowledgeSubmission(BaseModel):
    items: list[EvidenceAnswer] = Field(min_length=1)


KNOWLEDGE_GOAL = """Answer the evidence questions you are given using ONLY the vetted guideline corpus.
- Break compound questions into separate searches. Rewrite vague or colloquial questions into precise
  search queries (e.g. "is sugar bad for my diabetes" -> "added sugar limit type 2 diabetes").
- Read the results. If they do not answer the question, search again with different terms (up to 3 tries).
- Answer each question in your own words, citing the chunk_id of every passage you used.
- If the corpus does not contain an answer, mark sufficient=false instead of using outside knowledge."""


async def _guideline_search(be, ctx: dict, query: str, k: int = 5) -> str:
    region = (ctx.get("profile") or {}).get("region")
    res = await be.call("search_guidelines", {"query": query, "region": region, "k": k})
    seen = ctx.setdefault("retrieved", {})
    blocks = []
    for r in res["results"]:
        seen[r["chunk_id"]] = r
        blocks.append({"chunk_id": r["chunk_id"], "title": r["title"], "source": r["source"],
                       "text": wrap_untrusted(r["snippet"], r["source"])})
    if not blocks:
        return json.dumps({"query": query, "results": [], "note": "no matching passages"})
    return json.dumps({"query": query, "results": blocks})


def knowledge_agent(ctx: dict, emit: Emit | None = None) -> GoalAgent:
    be = get_backend()

    async def search_guidelines(query: str, k: int = 5) -> str:
        """Hybrid (keyword + semantic) search over the guideline corpus. Returns passages with chunk_ids."""
        return await _guideline_search(be, ctx, query, k)

    def check(sub: KnowledgeSubmission) -> list[str]:
        seen = ctx.get("retrieved", {})
        errs = []
        for it in sub.items:
            bad = [c for c in it.cited_chunk_ids if c not in seen]
            if bad:
                errs.append(f"'{it.question}': cited ids not returned by search: {bad}")
            if it.sufficient and not it.cited_chunk_ids:
                errs.append(f"'{it.question}': sufficient=true needs at least one citation.")
        return errs

    return GoalAgent("Nutrition Knowledge Agent", KNOWLEDGE_GOAL,
                     [_tool(search_guidelines, "search_guidelines", search_guidelines.__doc__)],
                     KnowledgeSubmission, check, get_settings().max_agent_steps, emit)


# ==================================================================================================
# Meal Planning
# ==================================================================================================

class PlanItem(BaseModel):
    food_code: str
    food_name: str = Field(default="", description="Optional; filled from the food table.")
    grams: float = Field(gt=0, le=1000)


SLOT_ALIASES = {"morning_snack": "mid_morning", "mid_morning_snack": "mid_morning", "afternoon_snack": "snack",
                "evening_snack": "snack", "snacks": "snack", "supper": "dinner", "brunch": "breakfast"}


class PlanMeal(BaseModel):
    slot: Literal["breakfast", "mid_morning", "lunch", "snack", "dinner", "bedtime"]

    @field_validator("slot", mode="before")
    @classmethod
    def _slot(cls, v):
        if isinstance(v, str):
            v = re.sub(r"[\s-]+", "_", v.strip().lower())
            return SLOT_ALIASES.get(v, v)
        return v

    name: str = Field(description="Dish name the user will recognise, e.g. 'Moong dal khichdi with curd'.")
    items: list[PlanItem] = Field(min_length=1)
    notes: str = ""


class PlanDay(BaseModel):
    day: int = Field(ge=1, le=7)
    meals: list[PlanMeal] = Field(min_length=1)


class PlanSubmission(BaseModel):
    days: list[PlanDay] = Field(min_length=1, max_length=7)
    notes: str = Field(default="", description="Short notes: swaps made, prep tips.")


PLANNER_GOAL = """Build a meal plan that meets the user's daily targets and respects every constraint.
You choose the dishes and ingredients; tools do all the arithmetic.
1. `search_foods` with a list of plain ingredient names (one call can look up many). It only returns
   foods from the user's region that respect their allergies, diet pattern and religious rules.
   Use the exact food_code values it returns.
2. Compose realistic dishes from the user's cuisine for each meal, with rough starting grams (raw weight).
   Include a protein-dense food in most meals.
3. Call `scale_portions` ONCE with your whole draft plan (all days, all meals). It rescales the grams so
   every day hits the kcal, protein and fat targets. If a day gets advice, change its foods and call again.
4. Submit the returned days unchanged: submit_result(days=<days from scale_portions>). Vary dishes across days.
If you are revising a rejected plan, fix every listed violation first. To change part of the user's current
plan, you may submit only the changed meals: the others are kept."""


def _short(name: str) -> str:
    head = name.split(",")[0].strip()
    return head[:1].upper() + head[1:].lower()


def _honest_dish_names(args: dict, profile: dict) -> dict:
    """A dish name that mentions foods the dish does not contain ("... with spinach" and no spinach) is
    replaced by a name built from its real ingredients, so a misleading label never reaches the user."""
    db, region = get_food_db(), profile.get("region", "IN")
    days = args.get("days") if isinstance(args.get("days"), list) else []
    for d in days:
        for m in (d.get("meals") or []) if isinstance(d, dict) else []:
            if not isinstance(m, dict) or not isinstance(m.get("name"), str):
                continue
            foods = [(f, it.get("grams") or 0) for it in m.get("items") or []
                     if isinstance(it, dict) and (f := db.get(str(it.get("food_code"))))]
            if foods and names_missing_foods(m["name"], [f.name for f, _ in foods], region):
                main = [_short(f.name) for f, _ in sorted(foods, key=lambda x: -x[1])][:3]
                main = list(dict.fromkeys(main))
                m["name"] = main[0] + (" with " + " and ".join(x.lower() for x in main[1:]) if main[1:] else "")
    return args


def planner_agent(ctx: dict, emit: Emit | None = None) -> GoalAgent:
    be = get_backend()
    s = get_settings()
    targets = ctx["health"]["targets"]
    tol = s.macro_tolerance_pct

    async def search_foods(ingredients: list[str]) -> dict:
        """Look up several ingredients at once by plain name, e.g. ["green gram dal", "rice raw", "paneer"].
        Returns allowed foods from the user's food table for each name, with kcal and protein per 100 g."""
        if isinstance(ingredients, str):
            ingredients = [ingredients]
        out = {}
        for q in ingredients[:12]:
            res = await be.call("search_foods", {"query": q, "region": ctx["profile"]["region"],
                                                 "profile": ctx["profile"], "limit": 3})
            out[q] = [{"food_code": f["code"], "name": f["name"], "kcal": f["per_100g"]["kcal"],
                       "protein_g": f["per_100g"]["protein_g"]} for f in res["results"]] or res.get("hint")
        return {"results": out}

    async def scale_portions(days: list[dict]) -> dict:
        """Pass your whole draft plan: [{"day": 1, "meals": [{slot, name, items: [{food_code, grams}]}]}, ...].
        Rescales every day's grams so each day hits the daily kcal, protein and fat targets, keeping your
        foods. Returns days in the submit_result format."""
        if days and isinstance(days[0], dict) and "meals" not in days[0]:
            days = [{"day": 1, "meals": days}]  # a bare list of meals
        out, report = [], []
        for n, d in enumerate(days, 1):
            res = await be.call("scale_portions", {"meals": d.get("meals", []), "kcal": targets["kcal"],
                                                   "protein_g": targets["protein_g"], "fat_g": targets["fat_g"]})
            out.append({"day": d.get("day", n), "meals": res["meals"]})
            t = res["totals"]
            report.append({"day": d.get("day", n), "pct_of_target": res["pct_of_target"],
                           "totals": {k: round(t[k]) for k in ("kcal", "protein_g", "carbs_g", "fat_g")},
                           **({"advice": res["advice"]} if "advice" in res else {}),
                           **({"unknown_codes": res["unknown_codes"]} if res["unknown_codes"] else {})})
        ok = all("advice" not in r and "unknown_codes" not in r for r in report)
        return {"days": out, "report": report,
                "next": "Submit these days unchanged with submit_result." if ok else
                        "Fix the days with advice (change foods), then call scale_portions again."}

    def check(sub: PlanSubmission) -> list[str]:
        db = get_food_db()
        profile = UserProfile.model_validate(ctx["profile"])
        blocked = {t for a in [*profile.allergies, *profile.intolerances] for t in alias_terms(a)}
        errs: list[str] = []
        nums = [d.day for d in sub.days]
        want = ctx.get("requested_days")
        if want and len(sub.days) != want:
            errs.append(f"the user asked for {want} day(s); the plan has {len(sub.days)}")
        if len(set(nums)) != len(nums):
            errs.append(f"day numbers repeat ({nums}): put all of a day's meals in one day entry, days 1..N")
        need = min(profile.meals_per_day, 3)
        for d in sub.days:
            slots = {m.slot for m in d.meals}
            if len(slots) < need or len(slots) != len(d.meals):
                errs.append(f"day {d.day}: needs at least {need} meals with different slots "
                            f"(breakfast, lunch, dinner...); has {sorted(m.slot for m in d.meals)}")
        if errs:
            return errs
        for d in sub.days:
            kcal = protein = 0.0
            for m in d.meals:
                named = allergens_for(m.name) & blocked
                if named or (profile.diet_pattern != "omnivore" and meats_for(m.name)):
                    errs.append(f"day {d.day} {m.slot}: dish name '{m.name}' names a food this user does not eat")
                foods_in = [f.name for it in m.items if (f := db.get(it.food_code))]
                absent = names_missing_foods(m.name, foods_in, profile.region.value)
                if absent:
                    errs.append(f"day {d.day} {m.slot}: dish name '{m.name}' mentions {absent} but the ingredients "
                                "do not include it; rename the dish to match its ingredients or add the food")
                for it in m.items:
                    f = db.get(it.food_code)
                    if f is None:
                        errs.append(f"day {d.day} {m.slot}: unknown food_code {it.food_code}; use codes from search_foods")
                        continue
                    ok, why = is_allowed(f, profile)
                    if not ok:
                        errs.append(f"day {d.day} {m.slot}: {f.name} not allowed ({why})")
                    kcal += f.per100g["kcal"] * it.grams / 100
                    protein += f.per100g["protein_g"] * it.grams / 100
            missed = [f"day {d.day}: {label} {val:.0f} vs target {tgt:.0f} (tolerance {tol:.0f}%)"
                      for label, val, tgt in (("kcal", kcal, targets["kcal"]), ("protein_g", protein, targets["protein_g"]))
                      if tgt and abs(val - tgt) / tgt * 100 > tol]
            errs += missed
        only_targets = errs and all("vs target" in e for e in errs)
        if only_targets:  # the foods are fine: hand back the deterministic rescale as feedback
            fixed = []
            for d in sub.days:
                res = scale_portions_tool([m.model_dump() for m in d.meals], targets["kcal"], targets["protein_g"],
                                          targets["fat_g"])
                if "advice" in res:
                    errs.append(f"day {d.day}: {res['advice']}")
                    break
                fixed.append({"day": d.day, "meals": res["meals"]})
            else:
                errs.insert(0, "Rescaled with scale_portions; these days meet the targets. Submit them "
                               "unchanged: " + json.dumps(fixed))
        return errs[:14]

    def prepare(args: dict) -> dict:
        """Edits of an existing plan: meals the submission leaves out are kept from the current plan, so
        "swap the dinner" can be submitted as just the new dinner."""
        args = _honest_dish_names(args, ctx["profile"])
        current = (ctx.get("plan") or {}).get("days") or []
        days = args.get("days") if isinstance(args.get("days"), list) else None
        if not days:
            return args
        merged: dict[int, dict] = {}
        for d in days:  # several entries for one day (one meal each) are folded together
            if not isinstance(d, dict):
                return args
            n = d.get("day", 1)
            merged.setdefault(n, {"day": n, "meals": []})["meals"] += list(d.get("meals") or [])
        old = {d["day"]: d for d in current}
        for n, d in merged.items():
            have = {m.get("slot") for m in d["meals"] if isinstance(m, dict)}
            d["meals"] += [m for m in old.get(n, {}).get("meals", []) if m["slot"] not in have]
        order = list(SLOT_ENERGY_SHARE)
        for d in merged.values():
            d["meals"].sort(key=lambda m: order.index(m["slot"]) if m.get("slot") in order else 99)
        out = [merged[n] for n in sorted(merged)]
        want = ctx.get("requested_days")
        if want and len(out) > want:  # the user asked for fewer days than drafted: keep the first ones
            out = out[:want]
        return {**args, "days": out}

    tools = [_tool(search_foods, "search_foods", search_foods.__doc__),
             _tool(scale_portions, "scale_portions", scale_portions.__doc__)]
    return GoalAgent("Meal Planning Agent", PLANNER_GOAL, tools, PlanSubmission, check,
                     max(s.max_agent_steps, 16), emit, prepare=prepare)


# ==================================================================================================
# Validation
# ==================================================================================================

class ValidatorSubmission(BaseModel):
    verdict: Literal["PASS", "REJECT", "ESCALATE"]
    extra_violations: list[Violation] = Field(default=[], description="Issues the rules cannot see.")
    summary: str = Field(description="One sentence for the planner or the user.")


VALIDATOR_GOAL = """Decide whether the latest meal plan is safe and fit to show this user.
- Call `run_safety_rules` first. Its critical violations are binding: you cannot PASS a plan the rules
  reject, and you must ESCALATE when the rules escalate.
- Then review what rules cannot judge: is it realistic and culturally appropriate, varied, practical for the
  user's budget, consistent with the evidence? Add those as extra_violations (critical only if unsafe).
- Use `search_guidelines` to check a specific concern against the corpus if needed.
- Verdict: PASS, REJECT (with fixable violations for the planner) or ESCALATE (needs a clinician)."""


def validator_agent(ctx: dict, emit: Emit | None = None) -> GoalAgent:
    be = get_backend()

    async def run_safety_rules() -> dict:
        """Run the deterministic rule engine on the latest plan (allergens, diet, targets, limits, interactions)."""
        res = await be.call("check_plan", {"profile": ctx["profile"], "health": ctx["health"],
                                           "plan": ctx["plan"]})
        ctx["rules_report"] = res["report"]
        ctx["plan_with_totals"] = res["plan"]
        days = [{"day": d["day"], "totals": d["totals"]} for d in res["plan"]["days"]]
        return {"report": res["report"], "day_totals": days}

    async def search_guidelines(query: str) -> str:
        """Search vetted guidelines to check a specific concern."""
        return await _guideline_search(be, ctx, query, 3)

    def check(sub: ValidatorSubmission) -> list[str]:
        rules = ctx.get("rules_report")
        if rules is None:
            return ["Call run_safety_rules before submitting."]
        if rules["verdict"] == "ESCALATE" and sub.verdict != "ESCALATE":
            return ["The rule engine escalated; verdict must be ESCALATE."]
        if rules["verdict"] == "REJECT" and sub.verdict == "PASS":
            return ["The rule engine found critical violations; you cannot PASS this plan."]
        if sub.verdict in ("REJECT", "ESCALATE") and rules["verdict"] == "PASS" and \
                not any(v.severity == "critical" for v in sub.extra_violations):
            return [f"To {sub.verdict} a plan the rules passed, add at least one critical extra_violation "
                    "explaining the safety problem; otherwise PASS it."]
        return []

    tools = [_tool(run_safety_rules, "run_safety_rules", run_safety_rules.__doc__),
             _tool(search_guidelines, "search_guidelines", search_guidelines.__doc__)]
    return GoalAgent("Validation Agent", VALIDATOR_GOAL, tools, ValidatorSubmission, check,
                     get_settings().max_agent_steps, emit)


# ==================================================================================================
# Recommendation
# ==================================================================================================

class RecommendationSubmission(BaseModel):
    headline: str = Field(max_length=120)
    summary: str = Field(description="3-6 sentences, personal and direct, grounded in the context.")
    key_points: list[str] = Field(default=[], max_length=6)
    tips: list[str] = Field(default=[], max_length=5)
    cited_chunk_ids: list[str] = []
    follow_up_questions: list[str] = Field(default=[], max_length=3)


RECOMMENDATION_GOAL = """Write the user-facing answer to their request from the context you are given.
- Answer the user's actual question first, directly. If a validated plan is shown, the summary names the
  day's dishes and its kcal and protein totals from the plan, and says how they fit the user's goal.
- Personalise it to their profile and goal. Explain the why briefly, cite evidence by chunk_id
  (chunk_id values come from the evidence or search results, never food codes).
- Only say what the context supports. No generic filler (hydration, sleep, "consult regularly") unless
  the evidence says it.
- Every number you mention (kcal, grams, mg) must appear in the context; do not compute new numbers.
- If the plan was escalated or a safety flag is set, lead with that: give general guidance only and advise
  seeing a doctor or registered dietitian. Never tell the user to change medication, never diagnose.
- If a plan could not be validated, say so honestly and do not present it.
- You may call `search_guidelines` if you need a citation for a claim."""

# Presenting a plan ("here is your plan", "this 1-day meal plan is designed...") when none was validated.
CLAIMS_PLAN = re.compile(r"\b(?:here(?:'s| is)|this|your)\s+(?:[\w-]+\s+){0,3}(?:meal\s+)?plan\b"
                         r"(?!\s+(?:could|couldn't|can't|cannot|was not|wasn't|is not|isn't|failed|needs))", re.I)
# Claiming an edit was made ("your dinner is now...", "paneer has been replaced") when it was not.
CLAIMS_CHANGE = re.compile(r"\b(?:is now|are now|now (?:has|includes|features)|has been (?:replaced|removed|swapped|"
                           r"changed|updated|revised|added)|have been (?:replaced|removed|swapped|changed|updated|"
                           r"added)|(?:i|we)(?:'ve| have)? (?:replaced|swapped|removed|changed|updated|revised))\b", re.I)
FOOD_CODE = re.compile(r"^(?:IN|US)-[A-Z0-9]+$")
# Quantitative intake targets ("1.6 g/kg", "120 g of protein a day", "1800 kcal"): not allowed in safe mode,
# where the person needs clinician-set targets (kidney disease, pregnancy, minors, T1D, eating disorders).
INTAKE_TARGET = re.compile(r"\d+(?:\.\d+)?\s*(?:-|to)?\s*\d*(?:\.\d+)?\s*(?:g|grams?)\s*(?:/|per)\s*(?:kg|kilo)"
                           r"|\d[\d,]*\s*(?:kcal|calories)"
                           r"|\d+\s*(?:g|grams?)\s+(?:of\s+)?(?:protein|carbs?|carbohydrates?|fat)\b", re.I)
UNIT_NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(kcal|calories|g\b|grams|mg\b|µg|mcg)", re.I)


def known_numbers(ctx: dict) -> set[float]:
    nums: set[float] = set()

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            nums.add(float(x))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            for m in re.findall(r"\d[\d,]*(?:\.\d+)?", x):
                nums.add(float(m.replace(",", "")))

    for key in ("health", "plan_with_totals", "evidence", "retrieved", "profile"):
        walk(ctx.get(key))
    return nums


def ungrounded_numbers(text: str, nums: set[float]) -> list[str]:
    bad = []
    for m in UNIT_NUMBER.finditer(text):
        val = float(m.group(1).replace(",", ""))
        if not any(abs(val - n) <= max(1.0, 0.03 * n) for n in nums):
            bad.append(m.group(0))
    return bad


def recommendation_agent(ctx: dict, emit: Emit | None = None) -> GoalAgent:
    be = get_backend()

    async def search_guidelines(query: str) -> str:
        """Search vetted guidelines for a citation."""
        return await _guideline_search(be, ctx, query, 3)

    def prepare(args: dict) -> dict:
        """Food codes listed as citations are a format mix-up, not sources: drop them. Any other unknown
        id is still rejected by the check (citations must come from retrieved passages)."""
        ids = args.get("cited_chunk_ids")
        if isinstance(ids, list):
            args = {**args, "cited_chunk_ids": [c for c in ids if not FOOD_CODE.match(str(c))]}
        return args

    def check(sub: RecommendationSubmission) -> list[str]:
        errs = []
        allowed = set(ctx.get("retrieved", {})) | {c["chunk_id"] for e in ctx.get("evidence", [])
                                                   for c in e.get("citations", [])}
        bad = [c for c in sub.cited_chunk_ids if c not in allowed]
        if bad:
            errs.append(f"Unknown chunk ids cited: {bad}")
        text = " ".join([sub.headline, sub.summary, *sub.key_points, *sub.tips])
        for why in check_output(text):
            errs.append(f"Not allowed: the text {why}. Rephrase.")
        if (ctx.get("health") or {}).get("escalation_required") and INTAKE_TARGET.search(text):
            errs.append(f"Safe mode: this person needs clinician-set targets, so give no numeric intake targets "
                        f"('{INTAKE_TARGET.search(text).group(0)}'). Give general guidance and advise a clinician.")
        if ctx.get("change_failed") and CLAIMS_CHANGE.search(text):
            errs.append(f"The requested change was NOT made (no revised plan passed validation), but the text "
                        f"says it was ('{CLAIMS_CHANGE.search(text).group(0)}'). Say the change could not be made.")
        if not ctx.get("plan_shown", True) and CLAIMS_PLAN.search(text):
            errs.append(f"No validated plan is shown to the user, but the text presents one "
                        f"('{CLAIMS_PLAN.search(text).group(0)}'). Do not describe a plan.")
        ungrounded = ungrounded_numbers(text, known_numbers(ctx))
        if ungrounded:
            errs.append(f"These numbers are not in the context: {ungrounded}. Use context numbers or omit them.")
        return errs

    return GoalAgent("Recommendation Agent", RECOMMENDATION_GOAL,
                     [_tool(search_guidelines, "search_guidelines", search_guidelines.__doc__)],
                     RecommendationSubmission, check, get_settings().max_agent_steps, emit, prepare=prepare)
