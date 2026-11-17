"""The orchestration graph: intake guard -> supervisor <-> agents -> answer.

    intake ──► supervisor ──► health_assessment ─┐
                  ▲  │──► nutrition_knowledge ───┤
                  │  │──► meal_planning ─────────┤ (back to supervisor)
                  │  │──► validation ────────────┘
                  │  ├──► recommendation ──► END
                  │  └──► clarify ──► END
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from typing import Any

import structlog
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from nip.agents import specs
from nip.agents.base import AgentResult
from nip.domain.models import Citation, Recommendation, RiskFlag, UserProfile
from nip.graph.state import NIPState
from nip.graph.supervisor import enforce, fallback_decision, llm_decide
from nip.security.guard import BLOCKED_RESPONSES, check_input, check_output
from nip.tools.energy import required_missing
from nip.tools.foods import get_food_db, request_exclusions

# The message asks for a meal plan or a change to one. An objective, not a route: enforce() uses it so a
# turn cannot end without a planning attempt when the user asked for a plan.
PLAN_REQUEST = re.compile(
    r"\b(?:meal[- ]?plan|diet plan|menu|plan\b(?:\s+\S+){0,5}?\s+(?:meals?|days?|diet|menu|week|breakfast|lunch|dinner|eating)"
    r"|(?:make|give|create|build|design|prepare|suggest)\s+(?:me\s+)?(?:an?\s+)?(?:\S+\s+){0,3}?(?:plan|diet)\b|\bplan please"
    r"|(?:swap|replace|change)\s+(?:the\s+|my\s+)?(?:breakfast|lunch|dinner|snack|meal)"
    r"|make (?:tomorrow|today|day \d) (?:a|an)\b)", re.I)

# Offered when the assistant has nothing more specific to suggest.
SMALL_TALK_SUGGESTIONS = ["Plan a day of meals for my goal", "Build me a 3-day workout plan",
                          "How much protein do I need?"]
WORKOUT_REQUEST = re.compile(r"\b(?:work ?outs?|exercise (?:plan|routine|program)|training (?:plan|program|split)|"
                             r"gym (?:plan|routine)|fitness plan|strength training|exercise routine)\b", re.I)
WEIGHT_QUESTION = re.compile(r"how much should i weigh|(?:ideal|healthy|right|normal|target) (?:body )?weight|"
                             r"weight for my height|am i (?:under|over)weight", re.I)
MEAL_WORDS = re.compile(r"\b(?:meals?|diet|food|eat|menu|nutrition|breakfast|lunch|dinner)\b", re.I)

NUM_WORDS = {"one": 1, "a": 1, "single": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
DAYS = re.compile(r"\b(\d|one|a|single|two|three|four|five|six|seven)[- ](?:days?|day's)\b|\b(week|weekly)\b", re.I)


def requested_days(text: str) -> int | None:
    """Number of plan days the message asks for ('1-day plan', 'three days', 'a week'), or None."""
    m = DAYS.search(text)
    if not m:
        return None
    if m.group(2):
        return 7
    v = m.group(1).lower()
    return min(int(v), 7) if v.isdigit() else NUM_WORDS[v]


# Example ingredient names that exist in each region's table (search vocabulary for the planner).
STAPLES = {
    "IN": "green gram dal, red gram dal, bengal gram whole, rajmah, soya bean, paneer, milk cow, egg poultry, "
          "chicken, rice raw milled, wheat flour atta, rice flakes, ragi, semolina, spinach, cauliflower, "
          "bottle gourd, ladies finger, tomato, onion, banana, apple, ground nut, almond",
    "US": "chicken breast raw, egg whole raw, yogurt greek plain, milk reduced fat, tofu raw firm, salmon raw, "
          "beans black, chickpeas, lentils raw, oats, rice brown cooked, bread whole-wheat, pasta whole-wheat, "
          "potato raw, broccoli raw, spinach raw, banana raw, apple raw, peanut butter, almonds",
}

ESSENTIAL_KEYWORDS = {"age": r"\bage\b|\bold\b", "sex": r"\bsex\b|gender|male|female",
                      "height_cm": r"height|tall", "weight_kg": r"weigh"}
ESSENTIAL_QUESTIONS = {
    "age": "How old are you?",
    "sex": "What is your sex (male or female)? It changes the energy equation.",
    "height_cm": "How tall are you (cm, or feet and inches)?",
    "weight_kg": "What is your current weight (kg or lb)?",
}

log = structlog.get_logger("nip.graph")

AGENT_NODES = ["health_assessment", "nutrition_knowledge", "meal_planning", "workout_planning", "validation",
               "recommendation"]


def _writer():
    try:
        return get_stream_writer()
    except Exception:
        return lambda _e: None


def _j(x: Any) -> str:
    return json.dumps(x, default=str, indent=1)


def _ctx(state: NIPState) -> dict:
    """Per-run working context handed to agent tools (a copy; merged back explicitly)."""
    plans = state.get("plans") or []
    vals = state.get("validations") or []
    return {
        "profile": dict(state.get("profile") or {}),
        "health": state.get("health"),
        "evidence": state.get("evidence") or [],
        "plan": plans[-1] if plans else None,
        "plan_with_totals": vals[-1].get("plan_with_totals") if vals else None,
        "retrieved": {},
        "user_id": state.get("user_id"),  # scopes the my_* data tools to this user
    }


def _with_exclusions(ctx: dict, state: NIPState) -> dict:
    """Foods excluded in this message become dislikes for planning and validation only (not saved)."""
    ex = state.get("turn_exclusions") or []
    if ex:
        ctx["profile"]["dislikes"] = sorted({*ctx["profile"].get("dislikes", []), *ex})
    return ctx


def _run_record(state: NIPState, agent: str, res: AgentResult) -> dict:
    return {"turn": state.get("turn"), "agent": agent, "status": res.status, "steps": res.steps,
            "tokens": res.tokens, "ms": res.ms, "error": res.error}


# ---------------------------------------------------------------- intake ------------------------------

async def intake(state: NIPState) -> dict:
    emit = _writer()
    check = check_input(state.get("request", ""))
    turn = (state.get("turn") or 0) + 1
    region = (state.get("profile") or {}).get("region", "IN")
    upd: dict[str, Any] = {
        "request": check.text, "input_flags": check.flags, "turn": turn, "turn_steps": 0,
        "turn_exclusions": request_exclusions(get_food_db(), check.text, region),
        "workout_requested": bool(WORKOUT_REQUEST.search(check.text)),
        "weight_question": bool(WEIGHT_QUESTION.search(check.text)),
        "plan_requested": bool(PLAN_REQUEST.search(check.text)) and (
            not WORKOUT_REQUEST.search(check.text) or bool(MEAL_WORDS.search(check.text))),
        "requested_days": requested_days(check.text),
        "turn_revisions": 0, "turn_tokens": 0, "recommendation": None, "next": None, "objectives": [],
        "pending_questions": [], "status": "running", "blocked_reason": None,
        "history": [{"role": "user", "content": check.text, "turn": turn}],
    }
    emit({"type": "intake", "flags": check.flags, "blocked": check.blocked, "ts": time.time()})
    if check.blocked:
        text = BLOCKED_RESPONSES[check.reason or "empty"]
        rec = Recommendation(headline="", summary=text, disclaimer="")
        upd |= {"status": "blocked", "blocked_reason": check.reason, "recommendation": rec.model_dump(),
                "history": upd["history"] + [{"role": "assistant", "content": text, "turn": turn}]}
    elif "eating_disorder_risk" in check.flags and state.get("health"):
        h = dict(state["health"])
        h["escalation_required"] = True
        h["risk_flags"] = [*h.get("risk_flags", []), RiskFlag(
            code="eating_disorder_risk", severity="critical",
            message="Message suggests disordered eating; no restrictive plans.").model_dump()]
        upd["health"] = h
    return upd


# ---------------------------------------------------------------- supervisor --------------------------

async def supervisor(state: NIPState) -> dict:
    emit = _writer()
    t0 = time.perf_counter()
    d = await llm_decide(state) or fallback_decision(state)
    nxt, override = enforce(state, d)
    rec = {"turn": state.get("turn"), "step": state.get("turn_steps", 0) + 1, "proposed": d.next,
           "next": nxt, "reason": d.reason, "override": override, "instruction": d.instruction,
           "ms": round((time.perf_counter() - t0) * 1000, 1)}
    emit({"type": "supervisor_decision", **rec, "objectives": d.objectives, "ts": time.time()})
    log.info("supervisor.decision", **{k: v for k, v in rec.items() if k != "instruction"})
    return {"next": nxt, "instruction": d.instruction if not override else f"{d.instruction} ({override})",
            "objectives": d.objectives or state.get("objectives") or [], "decisions": [rec],
            "turn_steps": state.get("turn_steps", 0) + 1}


def route(state: NIPState) -> str:
    return state.get("next") or "recommendation"


# ---------------------------------------------------------------- agents ------------------------------

def _common_brief(state: NIPState) -> str:
    goals = state.get("user_goals") or []
    data = state.get("user_data_summary") or {}
    return (f"Today is {date.today():%A %d %B %Y}.\n"
            f"<user_message>\n{state['request']}\n</user_message>\n\n"
            f"Supervisor instruction: {state.get('instruction', '')}\n"
            f"Objectives for this request: {state.get('objectives')}\n"
            + (f"The user's goals (set by the user; numbers are theirs): {_j(goals)}\n" if goals else "")
            + (f"The user's saved data (readable with the read_my_data tool): {_j(data)}\n" if data else ""))


async def health_assessment(state: NIPState) -> dict:
    emit, ctx = _writer(), _ctx(state)
    history = [h["content"] for h in (state.get("history") or [])[-6:] if h["role"] == "user"]
    ctx["user_text"] = "\n".join([*history, state.get("request", "")])
    brief = (_common_brief(state) + f"\nEarlier user messages: {_j(history[:-1])}\n"
             f"Current profile: {_j(ctx['profile'])}\nSafety flags from the input guard: {state.get('input_flags')}")
    res = await specs.health_agent(ctx, emit).run(brief)
    upd: dict[str, Any] = {"profile": ctx["profile"], "agent_runs": [_run_record(state, "health_assessment", res)],
                           "turn_tokens": state.get("turn_tokens", 0) + res.tokens}
    sub = res.output
    if ctx.get("assessment"):
        h = dict(ctx["assessment"])
        flags = list(h.get("risk_flags", []))
        if sub:
            flags += [f.model_dump() for f in sub.extra_risk_flags]
            h["assumptions"] = [*h.get("assumptions", []), *sub.assumptions]
            h["rationale"] = f"{h.get('rationale', '')} {sub.rationale}".strip()
        if "eating_disorder_risk" in (state.get("input_flags") or []):
            flags.append(RiskFlag(code="eating_disorder_risk", severity="critical",
                                  message="Message suggests disordered eating; no restrictive plans.").model_dump())
        if any(f["code"] == "eating_disorder_risk" for f in flags):
            h["escalation_required"] = True
        h["risk_flags"] = list({f["code"]: f for f in flags}.values())
        # Energy targets exist, so nothing essential is missing: the agent's questions become optional
        # follow-ups for the answer instead of blocking it with a clarify turn.
        h["follow_up_questions"] = sub.questions_for_user[:3] if sub else []
        upd["health"] = h
        upd["pending_questions"] = []
    elif sub and not sub.profile_complete:
        upd["pending_questions"] = sub.questions_for_user
    if not ctx.get("assessment"):  # ask for exactly what the calculator needs, whatever the agent wrote
        missing = required_missing(UserProfile.model_validate(ctx["profile"]))
        asked = " ".join(upd.get("pending_questions") or []).lower()
        upd["pending_questions"] = [*(upd.get("pending_questions") or []),
                                    *(ESSENTIAL_QUESTIONS[f] for f in missing
                                      if not re.search(ESSENTIAL_KEYWORDS[f], asked))]
    return upd


async def nutrition_knowledge(state: NIPState) -> dict:
    emit, ctx = _writer(), _ctx(state)
    p = ctx["profile"]
    brief = (_common_brief(state) + "\nAnswer the evidence questions implied by the instruction and request.\n"
             f"User context: region={p.get('region')}, diet={p.get('diet_pattern')}, conditions={p.get('conditions')}, "
             f"medications={p.get('medications')}, pregnant={p.get('pregnant')}")
    res = await specs.knowledge_agent(ctx, emit).run(brief)
    upd: dict[str, Any] = {"agent_runs": [_run_record(state, "nutrition_knowledge", res)],
                           "turn_tokens": state.get("turn_tokens", 0) + res.tokens}
    if res.output:
        items = []
        for it in res.output.items:
            cits = [Citation(**ctx["retrieved"][c]).model_dump() for c in it.cited_chunk_ids if c in ctx["retrieved"]]
            answer = it.answer if not check_output(it.answer) else \
                "This touches on medicines, diagnosis or treatment; see the cited source and ask your doctor."
            items.append({"question": it.question, "answer": answer, "citations": cits,
                          "sufficient": it.sufficient and bool(cits), "turn": state.get("turn")})
        upd["evidence"] = items
    return upd


async def meal_planning(state: NIPState) -> dict:
    emit, ctx = _writer(), _with_exclusions(_ctx(state), state)
    plans, vals = state.get("plans") or [], state.get("validations") or []
    revising = ""
    turn_plan = plans and plans[-1].get("turn") == state.get("turn")
    if turn_plan and vals and vals[-1]["plan_version"] == plans[-1]["version"] and vals[-1]["verdict"] == "REJECT":
        viol = [v for v in vals[-1]["violations"] if v["severity"] in ("critical", "warning")]
        revising = f"\nREVISION: your previous plan was rejected. Fix these:\n{_j(viol[:15])}\nPrevious plan:\n{_j(plans[-1]['days'])}"
    elif plans:
        revising = f"\nThe user's current plan (modify only what the request asks):\n{_j(plans[-1]['days'])}"
    failed = [r for r in state.get("agent_runs") or []
              if r["agent"] == "meal_planning" and r.get("turn") == state.get("turn") and r["status"] == "failed"]
    if failed and failed[-1].get("error"):
        revising += (f"\nYour previous attempt this turn did not finish: {failed[-1]['error'][:900]}\n"
                     "Choose a different mix of foods that fixes this.")
    h = ctx["health"]
    evidence = [{"q": e["question"], "a": e["answer"]} for e in ctx["evidence"]][-6:]
    brief = (_common_brief(state) +
             f"\nProfile: {_j(ctx['profile'])}\nDaily targets: {_j(h['targets'])}\nLimits: {_j(h['limits'])}\n"
             f"Risk flags: {[f['code'] for f in h['risk_flags']]}\nMeals per day: {ctx['profile'].get('meals_per_day', 3)}\n"
             f"Relevant evidence: {_j(evidence)}\nNumber of days: {state.get('requested_days') or 'not stated, use 3'}.\n"
             f"Staples in this food table: {STAPLES.get(ctx['profile'].get('region', 'IN'), '')}\n"
             f"Foods the user excluded in this message (never use them): {state.get('turn_exclusions') or 'none'}"
             + revising)
    ctx["requested_days"] = state.get("requested_days")
    agent = specs.planner_agent(ctx, emit)
    res = await agent.run(brief)
    cand = ctx.get("rescaled_candidate")
    if res.output is None and cand:
        # The agent ran out of steps after its own check handed back a rescaled version of its last plan
        # (its foods, portions computed by scale_portions). Accept it only if it passes every check.
        try:
            sub = specs.PlanSubmission.model_validate(agent.prepare(cand))
            if not agent.check(sub):
                res.output = sub
                emit({"type": "agent_note", "agent": "Meal Planning Agent", "ts": time.time(),
                      "text": "accepted the tool-rescaled version of the agent's last plan (it passes every check)"})
        except Exception as e:  # a candidate that does not validate is simply not used
            log.warning("planner.candidate_rejected", error=str(e)[:200])
    upd: dict[str, Any] = {"agent_runs": [_run_record(state, "meal_planning", res)],
                           "turn_tokens": state.get("turn_tokens", 0) + res.tokens}
    if res.output:
        version = (plans[-1]["version"] + 1) if plans else 1
        db = get_food_db()
        days = [d.model_dump() for d in res.output.days]
        for d in days:  # names come from the food table, not the model
            for m in d["meals"]:
                for it in m["items"]:
                    it["food_name"] = db.get(it["food_code"]).name
        upd["plans"] = [{"version": version, "turn": state.get("turn"), "region": ctx["profile"].get("region", "IN"),
                         "notes": res.output.notes, "days": days}]
        if revising.startswith("\nREVISION"):
            upd["turn_revisions"] = state.get("turn_revisions", 0) + 1
    else:
        upd["turn_revisions"] = state.get("turn_revisions", 0) + 1
    return upd


async def workout_planning(state: NIPState) -> dict:
    from nip.tools.exercise import WEEKDAYS, library

    emit, ctx = _writer(), _ctx(state)
    p, h = ctx["profile"], ctx["health"]
    current = (state.get("workouts") or [])[-1:] or None
    brief = (_common_brief(state) +
             f"\nTraining profile: {_j({k: p.get(k) for k in ('training_days_per_week', 'session_minutes', 'experience', 'equipment', 'injuries', 'goal', 'age', 'weight_kg')})}"
             f"\nRisk flags: {[f['code'] for f in h['risk_flags']]}"
             + (f"\nThe user's current workout (change only what the request asks):\n{_j(current[0]['sessions'])}"
                if current else ""))
    agent = specs.workout_agent(ctx, emit)
    res = await agent.run(brief)
    cand = ctx.get("repaired_candidate")
    if res.output is None and cand:
        # Out of steps after the rules handed back a repaired version of the agent's plan (its exercises,
        # numbers rebalanced). Accept it only if it passes the acceptance check.
        try:
            from nip.tools.exercise import check_workout

            sub = specs.WorkoutSubmission.model_validate(cand)
            report = check_workout(ctx["profile"], sub.model_dump())
            if report["verdict"] == "PASS":
                ctx["workout_report"] = report
                res.output = sub
                emit({"type": "agent_note", "agent": "Workout Planning Agent", "ts": time.time(),
                      "text": "accepted the rule-repaired version of the agent's last plan (it passes every check)"})
        except Exception as e:
            log.warning("workout.candidate_rejected", error=str(e)[:200])
    upd: dict[str, Any] = {"agent_runs": [_run_record(state, "workout_planning", res)],
                           "turn_tokens": state.get("turn_tokens", 0) + res.tokens}
    if res.output:
        lib, report = library(), ctx.get("workout_report") or {}
        sessions = []
        for sess in res.output.model_dump()["sessions"]:
            stats = next((x for x in report.get("sessions", []) if x["day"] == sess["day"]), {})
            sessions.append({**sess, "weekday": WEEKDAYS[sess["day"] - 1], **stats,
                             "exercises": [{**x, "name": lib[x["exercise_id"]]["name"],
                                            "type": lib[x["exercise_id"]]["type"]} for x in sess["exercises"]]})
        version = (current[0]["version"] + 1) if current else 1
        upd["workouts"] = [{"version": version, "turn": state.get("turn"), "sessions": sorted(sessions, key=lambda x: x["day"]),
                            "progression": res.output.progression, "totals": report.get("totals"),
                            "violations": report.get("violations", []), "verdict": report.get("verdict")}]
        emit({"type": "workout_verdict", "verdict": report.get("verdict"), "version": version, "ts": time.time()})
    return upd


async def validation(state: NIPState) -> dict:
    emit, ctx = _writer(), _with_exclusions(_ctx(state), state)
    plan = ctx["plan"]
    ctx["plan"] = {"days": plan["days"], "region": plan["region"], "notes": plan.get("notes", ""),
                   "version": plan["version"]}
    brief = (_common_brief(state) + f"\nProfile: {_j(ctx['profile'])}\nTargets: {_j(ctx['health']['targets'])}\n"
             f"Plan version {plan['version']}:\n{_j(plan['days'])}")
    res = await specs.validator_agent(ctx, emit).run(brief)
    upd: dict[str, Any] = {"agent_runs": [_run_record(state, "validation", res)],
                           "turn_tokens": state.get("turn_tokens", 0) + res.tokens}
    rules = ctx.get("rules_report")
    if rules is None:  # agent failed before running rules: run them deterministically, never skip the gate
        from nip.tools.registry import check_plan
        out = check_plan(ctx["profile"], ctx["health"], ctx["plan"])
        rules, ctx["plan_with_totals"] = out["report"], out["plan"]
    verdict = res.output.verdict if res.output else rules["verdict"]
    extra = [v.model_dump() for v in res.output.extra_violations] if res.output else []
    upd["validations"] = [{
        "plan_version": plan["version"], "verdict": verdict, "violations": rules["violations"] + extra,
        "summary": res.output.summary if res.output else rules["summary"], "rules_summary": rules["summary"],
        "checked": rules["checked"], "plan_with_totals": ctx.get("plan_with_totals"),
    }]
    emit({"type": "validation_verdict", "verdict": verdict, "plan_version": plan["version"],
          "critical": sum(v["severity"] == "critical" for v in upd["validations"][0]["violations"]),
          "ts": time.time()})
    return upd


async def recommendation(state: NIPState) -> dict:
    emit, ctx = _writer(), _ctx(state)
    plans, vals = state.get("plans") or [], state.get("validations") or []
    plan_block = "No meal plan for this request."
    showable = None
    if plans and vals and vals[-1]["plan_version"] == plans[-1]["version"]:
        v = vals[-1]
        if v["verdict"] == "PASS":
            showable = v["plan_with_totals"]
            plan_block = f"Validated plan (PASS) with computed totals:\n{_j(showable)}"
        else:
            plan_block = (f"The latest plan was NOT approved (verdict {v['verdict']}): {v['summary']}. "
                          "Do not present it.")
    planner_ran = any(r["agent"] == "meal_planning" and r.get("turn") == state.get("turn")
                      for r in state.get("agent_runs") or [])
    new_plan_passed = showable is not None and plans[-1].get("turn") == state.get("turn")
    why = planning_failure_reason(state)
    if showable is None and planner_ran:
        plan_block += ("\nMeal planning was attempted this turn but no plan passed validation. Say plainly that "
                       f"you could not produce a safe plan this time. The reason, from the checks: {why}. Use only "
                       "this reason (the user's profile is complete); do not describe a plan.")
    elif planner_ran and not new_plan_passed:
        plan_block = ("The user asked for a change, but no revised plan passed validation this turn. The plan "
                      "below is their EXISTING plan, unchanged. Say plainly that the change could not be made "
                      "this time; do not describe any change.\n" + plan_block)
    workouts = state.get("workouts") or []
    workout = workouts[-1] if workouts and workouts[-1].get("verdict") == "PASS" else None
    if workout:
        plan_block += ("\nWorkout plan (checked by the workout rules; numbers are computed):\n"
                       + _j({"sessions": [{"weekday": x["weekday"], "focus": x["focus"], "minutes": x.get("minutes"),
                                           "kcal": x.get("kcal"), "exercises": [e["name"] for e in x["exercises"]]}
                                          for x in workout["sessions"]], "totals": workout["totals"],
                             "progression": workout["progression"]}))
    elif state.get("workout_requested"):
        if (ctx["health"] or {}).get("escalation_required"):
            plan_block += "\nNo workout plan: exercise needs clinician clearance for this person. Say so."
        else:
            plan_block += "\nA workout plan was requested but none passed the workout checks this turn. Say so plainly."
    ctx["plan_shown"] = showable is not None or workout is not None
    ctx["workout"], ctx["user_goals"] = workout, state.get("user_goals") or []  # numbers the answer may quote
    ctx["change_failed"] = planner_ran and showable is not None and not new_plan_passed
    evidence = [{"question": e["question"], "answer": e["answer"], "sufficient": e["sufficient"],
                 "citations": [{"chunk_id": c["chunk_id"], "title": c["title"]} for c in e["citations"]]}
                for e in ctx["evidence"]]
    h = ctx["health"]
    brief = (_common_brief(state) +
             f"\nProfile: {_j(ctx['profile'])}\nHealth assessment: {_j(h) if h else 'not available'}\n"
             f"{plan_block}\nEvidence: {_j(evidence)}\nInput safety flags: {state.get('input_flags')}\n"
             f"Questions still open: {state.get('pending_questions')}")
    res = await specs.recommendation_agent(ctx, emit).run(brief)
    upd: dict[str, Any] = {"agent_runs": [_run_record(state, "recommendation", res)],
                           "turn_tokens": state.get("turn_tokens", 0) + res.tokens}
    if res.output:
        o = res.output
        pool = {**{c["chunk_id"]: c for e in ctx["evidence"] for c in e["citations"]}, **ctx["retrieved"]}
        cits = [Citation(**pool[c]) for c in dict.fromkeys(o.cited_chunk_ids) if c in pool]
        rec = Recommendation(headline=o.headline, summary=o.summary, key_points=distinct_points(o.summary, o.key_points),
                             tips=distinct_points(o.summary, o.tips), citations=cits,
                             follow_up_questions=o.follow_up_questions)
        status = "answered"
    else:  # the agent could not produce an accepted answer: compose one from validated context only
        rec = fallback_answer(h, showable, ctx["evidence"], planner_ran, bool(state.get("plan_requested")), workout)
        status = "answered"
    # Final output gate, whatever wrote the answer: banned content (medication changes, cures, diagnoses,
    # guarantees) never reaches the user.
    banned = check_output(" ".join([rec.headline, rec.summary, *rec.key_points, *rec.tips]))
    if banned:
        log.warning("output.blocked", reasons=banned)
        rec = Recommendation(headline="Please check this with your doctor",
                             summary="I can't advise on this safely. Questions about medicines, diagnoses or "
                                     "treatment belong with your doctor or pharmacist. I can help with food choices "
                                     "that fit the plan they give you.", citations=rec.citations)
    if state.get("weight_question") and h and len(h.get("healthy_weight_kg") or []) == 2:
        lo, hi = h["healthy_weight_kg"]
        cut = "18.5-22.9, the Indian cut-offs" if ctx["profile"].get("region") == "IN" else "18.5-24.9"
        rec.key_points = [f"For your height, a healthy weight is {lo}-{hi} kg (BMI {cut}); your BMI is {h.get('bmi')}.",
                          *rec.key_points]
    if h and h.get("escalation_required"):
        rec.key_points = ["Because of your health situation, please review any diet change with a doctor "
                          "or registered dietitian.", *rec.key_points]
    out = rec.model_dump()
    out["plan"] = showable
    out["workout"] = workout
    upd |= {"recommendation": out, "status": status,
            "history": [{"role": "assistant", "content": rec.summary, "turn": state.get("turn")}]}
    emit({"type": "answer", "status": status, "ts": time.time()})
    return upd


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def distinct_points(summary: str, points: list[str]) -> list[str]:
    """Drop bullet points that repeat the summary (or an earlier bullet): 7B models often restate the
    summary sentence by sentence as 'key points'."""
    seen = [_words(x) for x in re.split(r"(?<=[.!?])\s+", summary) if x.strip()]
    out = []
    for p in points:
        w = _words(p)
        if w and not any(len(w & s) / len(w) >= 0.7 for s in seen):
            out.append(p)
            seen.append(w)
    return out


def planning_failure_reason(state: NIPState) -> str:
    """Plain-language reason the last planning attempt this turn failed, from its recorded check errors."""
    runs = [r for r in state.get("agent_runs") or [] if r["agent"] == "meal_planning" and r.get("turn") == state.get("turn")]
    err = (runs[-1].get("error") or "") if runs else ""
    vals = state.get("validations") or []
    if vals and vals[-1].get("verdict") != "PASS" and runs and runs[-1]["status"] == "done":
        crit = [v["message"] for v in vals[-1]["violations"] if v["severity"] == "critical"][:2]
        return "the safety check rejected the plan: " + "; ".join(crit)
    if "protein" in err and ("above" in err or "too high" in err):
        return "the foods chosen kept protein above the safe upper limit for the energy target"
    if "protein" in err:
        return "the foods chosen could not reach the protein target"
    if "kcal" in err or "Energy" in err:
        return "the foods chosen could not reach the energy target within realistic portions"
    if "unknown food_code" in err:
        return "the planner kept using foods that are not in the food table"
    if "slots" in err or "repeat" in err:
        return "the planner did not produce complete days of meals"
    return "the planner ran out of attempts before a plan passed the checks"


def fallback_answer(health: dict | None, plan: dict | None, evidence: list[dict], planner_ran: bool,
                    plan_requested: bool, workout: dict | None = None) -> Recommendation:
    """Deterministic answer used when the Recommendation agent fails its checks within its budget.

    Built only from validated state (risk flags, the validated plan's computed totals, cited evidence),
    so it carries no model-written claims."""
    usable = [e for e in evidence if e.get("sufficient") and not check_output(e["answer"])]
    cites = list({c["chunk_id"]: Citation(**c) for e in usable for c in e["citations"]}.values())
    answers = [e["answer"] for e in usable]
    if health and health.get("escalation_required"):
        answers = [a for a in answers if not specs.INTAKE_TARGET.search(a)]  # safe mode: no numeric targets
        why = "; ".join(f["message"] for f in health.get("risk_flags", []) if f["severity"] == "critical")
        return Recommendation(
            headline="Please plan this with your doctor or a registered dietitian",
            summary=f"I can't give you a personalised plan safely: {why or 'your health situation needs clinical input'}. "
                    + " ".join(answers[:2]),
            citations=cites[:4])
    if workout and not plan:
        t = workout.get("totals") or {}
        week = "; ".join(f"{x['weekday']}: {x['focus']} ({x.get('minutes', 0)} min)" for x in workout["sessions"])
        return Recommendation(headline="Your checked workout plan",
                              summary=f"{week}. About {t.get('minutes_week', 0)} minutes and {t.get('kcal_week', 0)} kcal "
                                      "of activity a week.", citations=cites[:4])
    if plan:
        days = []
        for d in plan["days"]:
            t = d.get("totals") or {}
            dishes = ", ".join(f"{m['slot'].replace('_', ' ')}: {m['name']}" for m in d["meals"])
            days.append(f"Day {d['day']} ({t.get('kcal', 0):.0f} kcal, {t.get('protein_g', 0):.0f} g protein): {dishes}.")
        return Recommendation(headline="Your validated meal plan", summary=" ".join(days), citations=cites[:4])
    if answers:
        return Recommendation(headline="What the guidelines say", summary=" ".join(answers[:3]), citations=cites[:4])
    if planner_ran or plan_requested:
        return Recommendation(headline="I couldn't build a safe plan this time",
                              summary="None of the plans I built passed the safety and target checks. Please try "
                                      "again, or ask for a simpler plan (for example one day, three meals).")
    return Recommendation(headline="How can I help?",
                          summary=CAPABILITIES,
                          follow_up_questions=SMALL_TALK_SUGGESTIONS)


CAPABILITIES = ("I can plan meals from Indian or US food tables, build a workout plan, or check whether a food, "
                "supplement or diet fits your health and medicines. What would you like to work on?")


async def respond(state: NIPState) -> dict:
    """Conversational turn chosen by the supervisor: one model call, then the deterministic reply gate."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from nip.config import get_settings
    from nip.llm import get_chat_model

    emit = _writer()
    name = (state.get("profile") or {}).get("name")
    history = [f"{h['role']}: {h['content'][:200]}" for h in (state.get("history") or [])[-5:-1]]
    text, suggestions = "", SMALL_TALK_SUGGESTIONS
    try:
        model = get_chat_model("router").with_structured_output(specs.Reply, method=get_settings().structured_method)
        out = await model.ainvoke([SystemMessage(specs.REPLY_PROMPT), HumanMessage(
            f"User's name: {name or 'unknown'}\nRecent conversation: {history}\n"
            f"<user_message>\n{state['request']}\n</user_message>")])
        problems = specs.reply_problems(out.reply)
        if problems:
            log.info("respond.rejected", problems=problems)
        else:
            text, suggestions = out.reply.strip(), (out.suggestions or SMALL_TALK_SUGGESTIONS)[:3]
    except Exception as e:  # the fixed reply below is always safe
        log.warning("respond.failed", error=str(e)[:200])
    if not text:
        text = f"Hi{' ' + name if name else ''}! {CAPABILITIES}"
    rec = Recommendation(headline="", summary=text, disclaimer="", follow_up_questions=suggestions)
    emit({"type": "answer", "status": "answered", "ts": time.time()})
    return {"recommendation": rec.model_dump() | {"plan": None, "workout": None}, "status": "answered",
            "history": [{"role": "assistant", "content": text, "turn": state.get("turn")}]}


async def clarify(state: NIPState) -> dict:
    qs = state.get("pending_questions") or ["Could you tell me your age, sex, height and weight?"]
    text = "To personalise this, I need a bit more information:\n" + "\n".join(f"- {q}" for q in qs)
    rec = Recommendation(headline="A few quick questions", summary=text, follow_up_questions=qs, disclaimer="")
    _writer()({"type": "answer", "status": "clarifying", "ts": time.time()})
    return {"recommendation": rec.model_dump() | {"plan": None}, "status": "clarifying",
            "history": [{"role": "assistant", "content": text, "turn": state.get("turn")}]}


def build_graph(checkpointer=None):
    g = StateGraph(NIPState)
    g.add_node("intake", intake)
    g.add_node("supervisor", supervisor)
    for name, fn in [("health_assessment", health_assessment), ("nutrition_knowledge", nutrition_knowledge),
                     ("meal_planning", meal_planning), ("workout_planning", workout_planning), ("validation", validation),
                     ("recommendation", recommendation), ("clarify", clarify), ("respond", respond)]:
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", lambda s: END if s.get("status") == "blocked" else "supervisor",
                            ["supervisor", END])
    g.add_conditional_edges("supervisor", route, [*AGENT_NODES, "clarify", "respond"])
    for n in ["health_assessment", "nutrition_knowledge", "meal_planning", "workout_planning", "validation"]:
        g.add_edge(n, "supervisor")
    g.add_edge("recommendation", END)
    g.add_edge("clarify", END)
    g.add_edge("respond", END)
    return g.compile(checkpointer=checkpointer)
