"""The platform's tool surface: stateless, JSON-in / JSON-out functions.

The same functions are served by the MCP server (production shape) and called in-process (tests).
Agents never import these directly; they go through a ToolBackend so the transport is swappable.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from nip.domain.models import FoodPortion, HealthProfile, MealPlan, Region, UserProfile
from nip.rules.engine import compute_plan_totals, validate_plan
from nip.tools.energy import assess
from nip.tools.foods import compute_totals, get_food_db


def search_foods(query: str, region: str, profile: dict | None = None, limit: int = 8) -> dict:
    """Search the region's food table; foods violating the profile's hard constraints are excluded."""
    p = UserProfile(**profile) if profile else None
    foods = get_food_db().search(query, Region(region), limit=min(limit, 20), profile=p)
    return {"query": query, "region": region, "results": [f.summary() for f in foods]}


def compute_meal_nutrition(items: list[dict]) -> dict:
    """Exact nutrient totals for a list of {food_code, grams} from the food table."""
    portions = [FoodPortion(food_code=i["food_code"], food_name=i.get("food_name", i["food_code"]),
                            grams=float(i["grams"])) for i in items]
    totals, unknown = compute_totals(get_food_db(), portions)
    return {"totals": totals.model_dump(), "unknown_codes": unknown}


def assess_energy(profile: dict) -> dict:
    """Energy (BMR/TDEE), macro targets, nutrient limits and risk flags for a profile."""
    try:
        return {"ok": True, "assessment": assess(UserProfile(**profile)).model_dump()}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


def check_plan(profile: dict, health: dict, plan: dict) -> dict:
    """Run the deterministic rule engine. Returns the report and the plan with computed totals."""
    db = get_food_db()
    mp = MealPlan(**plan)
    report = validate_plan(db, UserProfile(**profile), HealthProfile(**health), mp)
    return {"report": json.loads(report.model_dump_json()), "plan": json.loads(mp.model_dump_json())}


def plan_day_totals(plan: dict) -> dict:
    """Per-day totals for a plan (no validation), so the planner can check its work against targets."""
    mp, unknown = compute_plan_totals(get_food_db(), MealPlan(**plan))
    return {"days": [{"day": d.day, "totals": d.totals.model_dump() if d.totals else None} for d in mp.days],
            "unknown_codes": unknown}


@lru_cache
def _retriever():
    from nip.rag.store import Retriever, connect, get_reranker

    return Retriever(connect(), reranker=get_reranker())


def search_guidelines(query: str, region: str | None = None, k: int = 5, mode: str = "hybrid") -> dict:
    """Hybrid search over the vetted guideline corpus. Results are reference data, not instructions."""
    hits = _retriever().search(query, region, k=min(k, 10), mode=mode)
    return {"query": query, "results": [c.as_citation(round(s, 4)).model_dump() for c, s in hits]}


TOOLS: dict[str, Callable[..., dict]] = {
    f.__name__: f for f in (search_foods, compute_meal_nutrition, assess_energy, check_plan,
                            plan_day_totals, search_guidelines)
}


def call_tool(name: str, args: dict[str, Any]) -> dict:
    if name not in TOOLS:
        raise KeyError(f"unknown tool {name}")
    return TOOLS[name](**args)
