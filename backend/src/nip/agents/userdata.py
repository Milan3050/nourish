"""Read-only tools over the signed-in user's own data: goals, saved plans, calendar and past conversations.

Agents decide when to look; these tools fetch, scoped to the user id the API put in the run's context, so an
agent can never read another user's data. The API registers its store at startup; without one (tests,
scripts) the tools say no data is available. Everything returned is also kept in ctx["user_data"] so the
Recommendation agent's number check accepts figures quoted from it.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from langchain_core.tools import StructuredTool

_store: Any = None


def set_store(store: Any) -> None:
    global _store
    _store = store


def _plan_summary(p: dict) -> dict:
    d = p.get("data") or {}
    if p.get("kind") == "meal":
        days = d.get("days") or []
        return {"id": p["id"], "kind": "meal", "title": p["title"], "saved": p["created_at"][:10], "days": len(days),
                "kcal_per_day": [round((x.get("totals") or {}).get("kcal", 0)) for x in days]}
    t = d.get("totals") or {}
    return {"id": p["id"], "kind": "workout", "title": p["title"], "saved": p["created_at"][:10],
            "sessions_per_week": t.get("sessions"), "minutes_per_week": t.get("minutes_week"),
            "kcal_per_week": t.get("kcal_week")}


def _plan_detail(p: dict) -> dict:
    d = p.get("data") or {}
    if p.get("kind") == "meal":
        return {**_plan_summary(p), "days": [{
            "day": x["day"], "totals": {k: round(v) for k, v in (x.get("totals") or {}).items()
                                        if k in ("kcal", "protein_g", "carbs_g", "fat_g")},
            "meals": [{"slot": m["slot"], "name": m["name"],
                       "items": [f"{i['food_name']} {round(i['grams'])} g" for i in m["items"]]} for m in x["meals"]]}
            for x in d.get("days") or []]}
    return {**_plan_summary(p), "sessions": [{
        "weekday": s.get("weekday"), "focus": s.get("focus"), "minutes": s.get("minutes"), "kcal": s.get("kcal"),
        "exercises": [f"{e['name']} " + (f"{e.get('sets')} x {e.get('reps')}" if e.get("type") == "strength"
                                         else f"{e.get('minutes')} min") for e in s.get("exercises") or []]}
        for s in d.get("sessions") or []], "progression": d.get("progression", "")}


async def inventory(user: str | None) -> dict:
    """Counts the supervisor sees, so it knows questions about the user's data can be answered."""
    if not (_store and user):
        return {}
    today = date.today().isoformat()
    week = (date.today() + timedelta(days=7)).isoformat()
    cal = [e for e in await _store.list(user, "calendar", 2000) if today <= e["date"] <= week]
    return {"goals": len(await _store.list(user, "goal")), "saved_plans": len(await _store.list(user, "plan")),
            "calendar_next_7_days": len(cal), "past_conversations": len({r["thread_id"] for r in await _store.list(user, "run")})}


def user_data_tools(ctx: dict) -> list[StructuredTool]:
    user = ctx.get("user_id")

    def keep(name: str, value: Any) -> Any:
        ctx.setdefault("user_data", {})[name] = value
        return value

    async def unavailable() -> dict | None:
        return None if (_store and user) else {"error": "the user's saved data is not available in this session"}

    async def my_goals() -> dict:
        """The user's goals (weight, workout, nutrition, habit) with targets, current values and status."""
        if err := await unavailable():
            return err
        goals = [{k: g.get(k) for k in ("title", "kind", "target", "unit", "current", "start", "deadline", "status")}
                 for g in await _store.list(user, "goal")]
        return keep("goals", {"goals": goals})

    async def my_saved_plans() -> dict:
        """The user's saved meal plans and workouts (id, title, when saved, headline numbers)."""
        if err := await unavailable():
            return err
        return keep("saved_plans", {"plans": [_plan_summary(p) for p in await _store.list(user, "plan")]})

    async def my_saved_plan(plan_id: str) -> dict:
        """Full contents of one saved plan by id (meals and portions, or workout sessions and exercises)."""
        if err := await unavailable():
            return err
        p = await _store.get(user, "plan", plan_id)
        return keep(f"plan_{plan_id}", _plan_detail(p)) if p else {"error": f"no saved plan with id {plan_id}"}

    async def my_calendar(start_date: str | None = None, end_date: str | None = None) -> dict:
        """The user's scheduled meal plans and workouts between two dates (YYYY-MM-DD; default: the next 7 days),
        with whether each was marked done."""
        if err := await unavailable():
            return err
        start = start_date or date.today().isoformat()
        end = end_date or (date.fromisoformat(start) + timedelta(days=7)).isoformat()
        items = [{k: e.get(k) for k in ("date", "kind", "title", "plan_title", "kcal", "minutes", "done")}
                 for e in await _store.list(user, "calendar", 2000) if start <= e["date"] <= end]
        return keep(f"calendar_{start}_{end}", {"from": start, "to": end, "entries": sorted(items, key=lambda e: e["date"])})

    async def my_past_conversations(limit: int = 8) -> dict:
        """The user's recent requests and what came of them (date, request, answer headline, plan or workout)."""
        if err := await unavailable():
            return err
        runs = await _store.list(user, "run", max(1, min(limit, 20)))
        return keep("past_conversations", {"recent": [{
            "when": r["created_at"][:16], "request": r["request"][:160], "answer": r.get("headline", ""),
            "meal_plan": r.get("has_plan", False), "workout": r.get("has_workout", False)} for r in runs]})

    return [StructuredTool.from_function(coroutine=f, name=f.__name__, description=f.__doc__)
            for f in (my_goals, my_saved_plans, my_saved_plan, my_calendar, my_past_conversations)]


def read_my_data_tool(ctx: dict) -> StructuredTool:
    """All of the user-data lookups behind one tool, so a small model has one choice to make, not five."""
    tools = {t.name: t for t in user_data_tools(ctx)}

    async def read_my_data(what: Literal["goals", "saved_plans", "saved_plan", "calendar", "past_conversations"],
                           plan_id: str | None = None, start_date: str | None = None,
                           end_date: str | None = None) -> dict:
        """Read the user's own saved data: what='goals' | 'saved_plans' | 'saved_plan' (needs plan_id) |
        'calendar' (optional start_date/end_date, YYYY-MM-DD; default next 7 days) | 'past_conversations'."""
        if what == "saved_plan":
            if not plan_id:
                return {"error": "saved_plan needs plan_id (get ids with what='saved_plans')"}
            return await tools["my_saved_plan"].ainvoke({"plan_id": plan_id})
        if what == "calendar":
            return await tools["my_calendar"].ainvoke({"start_date": start_date, "end_date": end_date})
        return await tools[f"my_{what}"].ainvoke({})

    return StructuredTool.from_function(coroutine=read_my_data, name="read_my_data", description=read_my_data.__doc__)
