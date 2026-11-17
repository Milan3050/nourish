"""HTTP API: auth, profile, streaming chat (SSE), thread state, health and metrics.

    uv run uvicorn nip.api.main:app --reload --port 8000
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any, Literal

import jwt
import psycopg
import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from langgraph.checkpoint.memory import InMemorySaver
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from nip.agents.userdata import inventory, set_store
from nip.api.store import Store
from nip.config import get_settings
from nip.domain.models import UserProfile
from nip.graph.build import build_graph
from nip.observability.logging import configure_logging
from nip.observability.tracing import configure_tracing
from nip.observability.usage import UsageTracker

log = structlog.get_logger("nip.api")

REQUESTS = Counter("nip_chat_requests_total", "Chat turns", ["status"])
LATENCY = Histogram("nip_chat_latency_seconds", "End-to-end turn latency", buckets=(1, 2, 5, 10, 20, 40, 80, 160))
AGENT_LATENCY = Histogram("nip_agent_latency_seconds", "Agent run latency", ["agent", "status"],
                          buckets=(0.5, 1, 2, 5, 10, 20, 40, 80))
OVERRIDES = Counter("nip_supervisor_overrides_total", "Supervisor decisions overridden by invariants")
VERDICTS = Counter("nip_validation_verdicts_total", "Validation verdicts", ["verdict"])

PROFILE_SQL = """CREATE TABLE IF NOT EXISTS user_profiles (
  user_id text PRIMARY KEY, profile jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now())"""


class AppState:
    graph: Any = None
    db: psycopg.AsyncConnection | None = None
    memory_profiles: dict[str, dict] = {}
    saver_cm: Any = None
    store: Store = Store()


S = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    configure_tracing()
    s = get_settings()
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        S.saver_cm = AsyncPostgresSaver.from_conn_string(s.database_url)
        saver = await S.saver_cm.__aenter__()
        await saver.setup()
        S.db = await psycopg.AsyncConnection.connect(s.database_url, autocommit=True)
        await S.db.execute(PROFILE_SQL)
        S.store = Store(S.db)
        await S.store.setup()
        set_store(S.store)
        S.graph = build_graph(saver)
        log.info("api.ready", checkpointer="postgres")
    except Exception as e:
        if s.env == "prod":
            raise
        log.warning("api.db_unavailable_using_memory", error=str(e)[:200])
        S.store = Store()
        set_store(S.store)
        S.graph = build_graph(InMemorySaver())
    yield
    if S.db:
        await S.db.close()
    if S.saver_cm:
        await S.saver_cm.__aexit__(None, None, None)


app = FastAPI(title="Nutrition Intelligence Platform", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


# ------------------------------------------------------------------ auth -------------------------------

class TokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)


@app.post("/api/auth/dev-token")
async def dev_token(body: TokenRequest) -> dict:
    """Development login. Production replaces this with the identity provider (OIDC) and verifies its JWTs."""
    s = get_settings()
    if s.env == "prod":
        raise HTTPException(404)
    uid = f"u_{uuid.uuid4().hex[:12]}"
    token = jwt.encode({"sub": uid, "name": body.name, "iat": int(time.time()), "exp": int(time.time()) + 7 * 86400},
                       s.jwt_secret, algorithm="HS256")
    return {"token": token, "user_id": uid}


def current_user(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "missing token")
    try:
        claims = jwt.decode(auth[7:], get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise HTTPException(401, "invalid token") from e
    return claims["sub"]


_buckets: dict[str, deque] = defaultdict(deque)


def rate_limit(user: str = Depends(current_user)) -> str:
    """Sliding-window limit per user (in-memory; Redis in production)."""
    now, window = time.time(), _buckets[user]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= get_settings().rate_limit_per_minute:
        raise HTTPException(429, "too many requests")
    window.append(now)
    return user


# ------------------------------------------------------------------ profile ----------------------------

async def load_profile(user: str) -> dict | None:
    if S.db:
        row = await (await S.db.execute("SELECT profile FROM user_profiles WHERE user_id=%s", (user,))).fetchone()
        return row[0] if row else None
    return S.memory_profiles.get(user)


async def save_profile(user: str, profile: dict) -> None:
    profile = {**profile, "user_id": user}
    if S.db:
        await S.db.execute(
            "INSERT INTO user_profiles (user_id, profile) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET profile = EXCLUDED.profile, updated_at = now()",
            (user, json.dumps(profile)))
    else:
        S.memory_profiles[user] = profile


@app.get("/api/profile")
async def get_profile(user: str = Depends(current_user)) -> dict:
    p = await load_profile(user)  # validated, so profiles saved before newer fields get their defaults
    return {"profile": UserProfile.model_validate(p).model_dump(mode="json") if p else None}


@app.put("/api/profile")
async def put_profile(profile: UserProfile, user: str = Depends(current_user)) -> dict:
    data = profile.model_dump(mode="json")
    await save_profile(user, data)
    return {"profile": data}


# ------------------------------------------------------------------ chat -------------------------------

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


def _thread_cfg(user: str, thread_id: str) -> dict:
    if not thread_id.replace("-", "").replace("_", "").isalnum() or len(thread_id) > 64:
        raise HTTPException(400, "bad thread id")
    # Namespacing by user makes cross-user thread access impossible even with a guessed id.
    return {"configurable": {"thread_id": f"{user}:{thread_id}"}, "recursion_limit": 80,
            "metadata": {"user_id": user}}


def public_state(values: dict) -> dict:
    vals = values.get("validations") or []
    return {
        "status": values.get("status"),
        "turn": values.get("turn"),
        "profile": values.get("profile"),
        "health": values.get("health"),
        "recommendation": values.get("recommendation"),
        "latest_workout": (values.get("workouts") or [None])[-1],
        "latest_validation": ({k: v for k, v in vals[-1].items() if k != "plan_with_totals"} if vals else None),
        "evidence": values.get("evidence") or [],
        "decisions": [d for d in values.get("decisions") or [] if d.get("turn") == values.get("turn")],
        "agent_runs": [r for r in values.get("agent_runs") or [] if r.get("turn") == values.get("turn")],
        "pending_questions": values.get("pending_questions") or [],
    }


@app.post("/api/chat/{thread_id}/stream")
async def chat_stream(thread_id: str, body: ChatRequest, user: str = Depends(rate_limit)):
    cfg = _thread_cfg(user, thread_id)
    snapshot = await S.graph.aget_state(cfg)
    inp: dict[str, Any] = {"request": body.message, "user_id": user, "user_data_summary": await inventory(user),
                           "user_goals": [{k: g.get(k) for k in ("kind", "title", "target", "unit", "current", "deadline")}
                                          for g in await S.store.list(user, "goal") if g.get("status") != "done"]}
    stored = await load_profile(user)
    current = (snapshot.values or {}).get("profile")
    if stored or not current:  # the profile page is the source of truth; agents' updates are saved back
        inp["profile"] = stored or {"region": "IN"}
        energy_fields = ("age", "sex", "height_cm", "weight_kg", "activity", "goal", "target_weight_kg",
                         "timeline_weeks", "region", "conditions", "medications", "pregnant", "breastfeeding")
        if current and any(current.get(k) != inp["profile"].get(k) for k in energy_fields):
            inp["health"] = None  # stale targets: the health assessment runs again
    usage = UsageTracker()
    cfg = {**cfg, "callbacks": [usage]}

    async def events():
        t0 = time.perf_counter()
        started = time.time()
        status = "error"
        final: dict = {}
        log_events: list[dict] = []
        try:
            async for mode, chunk in S.graph.astream(inp, cfg, stream_mode=["custom", "values"]):
                if mode == "custom":
                    if len(log_events) < 600:
                        log_events.append(chunk)
                    if chunk.get("type") == "supervisor_decision" and chunk.get("override"):
                        OVERRIDES.inc()
                    if chunk.get("type") == "agent_finished":
                        AGENT_LATENCY.labels(chunk["agent"], chunk.get("status", "?")).observe((chunk.get("ms") or 0) / 1000)
                    if chunk.get("type") == "validation_verdict":
                        VERDICTS.labels(chunk["verdict"]).inc()
                    yield {"event": "agent", "data": json.dumps(chunk, default=str)}
                else:
                    final = chunk
            status = final.get("status", "unknown")
            if final.get("profile"):
                await save_profile(user, final["profile"])
            run = await save_run(user, thread_id, final, log_events, usage, started, time.perf_counter() - t0)
            yield {"event": "final", "data": json.dumps(public_state(final) | {"run": run}, default=str)}
        except Exception as e:
            log.exception("chat.error", thread=thread_id)
            yield {"event": "error", "data": json.dumps({"message": "Something went wrong. Please try again.",
                                                         "detail": str(e)[:200] if get_settings().env == "dev" else None})}
        finally:
            REQUESTS.labels(status).inc()
            LATENCY.observe(time.perf_counter() - t0)

    return EventSourceResponse(events())


# ------------------------------------------------------------------ runs -------------------------------

RUN_FIELDS = ("id", "created_at", "thread_id", "turn", "request", "status", "seconds", "model", "usage", "agents",
              "headline", "has_plan", "has_workout", "source_count")


def run_sources(final: dict, events: list[dict]) -> dict:
    """What the agents looked up in the guideline library this turn: passages cited in the answer, the
    Knowledge agent's evidence, and every search with the titles it returned."""
    rec = final.get("recommendation") or {}
    turn = final.get("turn")
    cited = list({c["chunk_id"]: {k: c.get(k) for k in ("chunk_id", "title", "source", "url", "snippet")}
                  for c in rec.get("citations") or []}.values())
    evidence = [{"question": e["question"], "answer": e["answer"], "sufficient": e.get("sufficient"),
                 "citations": [{k: c.get(k) for k in ("chunk_id", "title", "source", "url")} for c in e["citations"]]}
                for e in final.get("evidence") or [] if e.get("turn") == turn]
    searches: list[dict] = []
    for e in events:
        if e.get("type") == "tool_call" and e.get("tool") == "search_guidelines":
            args = e.get("args") or {}
            searches.append({"agent": e.get("agent"), "query": args.get("query") or args.get("_preview", ""), "found": []})
        elif e.get("type") == "tool_result" and e.get("tool") == "search_guidelines" and searches:
            searches[-1]["found"] = e.get("titles") or re.findall(r'"title": "([^"]+)"', e.get("preview") or "")[:5]
    ids = {c["chunk_id"] for c in cited} | {c["chunk_id"] for ev in evidence for c in ev["citations"]}
    return {"cited": cited, "evidence": evidence, "searches": searches, "count": len(ids)}


async def save_run(user: str, thread_id: str, final: dict, events: list[dict], usage: UsageTracker,
                   started: float, seconds: float) -> dict:
    turn = final.get("turn")
    rec = final.get("recommendation") or {}
    doc = {
        "thread_id": thread_id, "turn": turn, "request": final.get("request", ""), "status": final.get("status"),
        "started_at": started, "seconds": round(seconds, 2), "model": get_settings().llm_model,
        "usage": usage.summary(), "headline": rec.get("headline") or rec.get("summary", "")[:80],
        "has_plan": bool(rec.get("plan")), "has_workout": bool(rec.get("workout")),
        "agents": [r for r in final.get("agent_runs") or [] if r.get("turn") == turn],
        "decisions": [d for d in final.get("decisions") or [] if d.get("turn") == turn],
        "events": events,
    }
    doc["final_state"] = public_state(final)  # lets the chat reopen this turn exactly as it was shown
    doc["sources"] = run_sources(final, events)
    doc["source_count"] = doc["sources"]["count"]
    saved = await S.store.add(user, "run", doc)
    return {k: saved.get(k) for k in RUN_FIELDS}


@app.get("/api/runs")
async def list_runs(user: str = Depends(current_user)) -> dict:
    runs = await S.store.list(user, "run")
    total = {"runs": len(runs), "cost_usd": round(sum(r["usage"]["cost_usd"] for r in runs), 6),
             "reference_cost_usd": round(sum(r["usage"].get("reference_cost_usd") or 0 for r in runs), 6),
             "tokens": sum(r["usage"]["input_tokens"] + r["usage"]["output_tokens"] for r in runs)}
    return {"runs": [{k: r.get(k) for k in RUN_FIELDS} for r in runs], "total": total}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str, user: str = Depends(current_user)) -> dict:
    run = await S.store.get(user, "run", run_id)
    if not run:
        raise HTTPException(404)
    return run


# ------------------------------------------------------------------ saved plans ------------------------

class SavePlanRequest(BaseModel):
    thread_id: str = Field(max_length=64)
    kind: Literal["meal", "workout"]
    title: str | None = Field(default=None, max_length=80)


@app.post("/api/plans")
async def save_plan(body: SavePlanRequest, user: str = Depends(current_user)) -> dict:
    """Save the thread's latest checked plan. Taken from server state, never from the client, so only
    plans that passed validation (meal) or the workout rules can be saved."""
    values = (await S.graph.aget_state(_thread_cfg(user, body.thread_id))).values or {}
    if body.kind == "meal":
        vals = values.get("validations") or []
        plans = values.get("plans") or []
        if not (plans and vals and vals[-1]["plan_version"] == plans[-1]["version"] and vals[-1]["verdict"] == "PASS"):
            raise HTTPException(409, "no validated meal plan in this conversation")
        data = vals[-1]["plan_with_totals"]
        default = f"{len(data['days'])}-day meal plan"
    else:
        wk = (values.get("workouts") or [None])[-1]
        if not wk or wk.get("verdict") != "PASS":
            raise HTTPException(409, "no checked workout plan in this conversation")
        data = wk
        default = f"{len(wk['sessions'])}-day workout week"
    health = values.get("health") or {}
    return await S.store.add(user, "plan", {"kind": body.kind, "title": body.title or default, "data": data,
                                            "thread_id": body.thread_id, "targets": health.get("targets")})


@app.get("/api/plans")
async def list_plans(user: str = Depends(current_user)) -> dict:
    return {"plans": await S.store.list(user, "plan")}


@app.delete("/api/plans/{plan_id}")
async def delete_plan(plan_id: str, user: str = Depends(current_user)) -> dict:
    if not await S.store.delete(user, "plan", plan_id):
        raise HTTPException(404)
    for e in await S.store.list(user, "calendar", 2000):
        if e.get("plan_id") == plan_id:
            await S.store.delete(user, "calendar", e["id"])
    return {"ok": True}


# ------------------------------------------------------------------ calendar ---------------------------

class ScheduleRequest(BaseModel):
    plan_id: str
    start_date: date
    weeks: int = Field(default=1, ge=1, le=12)


@app.post("/api/calendar")
async def schedule_plan(body: ScheduleRequest, user: str = Depends(current_user)) -> dict:
    """Put a saved plan on the calendar: meal-plan days on consecutive dates, repeating to fill `weeks`,
    workout sessions on their weekdays for `weeks` weeks from the start date."""
    plan = await S.store.get(user, "plan", body.plan_id)
    if not plan:
        raise HTTPException(404)
    entries = []
    if plan["kind"] == "meal":
        days = plan["data"]["days"]
        total = max(len(days), 7 * body.weeks)  # the plan's days repeat to fill the weeks
        for i in range(total):
            d = days[i % len(days)]
            entries.append({"date": (body.start_date + timedelta(days=i)).isoformat(), "day_index": i % len(days),
                            "title": ", ".join(m["name"] for m in d["meals"])[:120],
                            "kcal": round((d.get("totals") or {}).get("kcal", 0))})
    else:
        for w in range(body.weeks):
            for sess in plan["data"]["sessions"]:
                offset = (sess["day"] - 1 - body.start_date.weekday()) % 7 + 7 * w
                entries.append({"date": (body.start_date + timedelta(days=offset)).isoformat(),
                                "day_index": sess["day"], "title": sess["focus"],
                                "minutes": sess.get("minutes"), "kcal": sess.get("kcal")})
    out = [await S.store.add(user, "calendar", {**e, "kind": plan["kind"], "plan_id": plan["id"],
                                                 "plan_title": plan["title"], "done": False}) for e in entries]
    return {"entries": sorted(out, key=lambda e: e["date"])}


@app.get("/api/calendar")
async def get_calendar(start: date, end: date, user: str = Depends(current_user)) -> dict:
    items = [e for e in await S.store.list(user, "calendar", 2000) if start.isoformat() <= e["date"] <= end.isoformat()]
    return {"entries": sorted(items, key=lambda e: (e["date"], e["kind"]))}


class EntryUpdate(BaseModel):
    done: bool


@app.patch("/api/calendar/{entry_id}")
async def update_entry(entry_id: str, body: EntryUpdate, user: str = Depends(current_user)) -> dict:
    doc = await S.store.update(user, "calendar", entry_id, {"done": body.done})
    if not doc:
        raise HTTPException(404)
    return doc


@app.delete("/api/calendar/{entry_id}")
async def delete_entry(entry_id: str, user: str = Depends(current_user)) -> dict:
    if not await S.store.delete(user, "calendar", entry_id):
        raise HTTPException(404)
    return {"ok": True}


# ------------------------------------------------------------------ goals ------------------------------

class GoalIn(BaseModel):
    kind: Literal["weight", "workout", "nutrition", "habit"]
    title: str = Field(min_length=1, max_length=80)
    target: float | None = Field(default=None, ge=0, le=100000)
    unit: str | None = Field(default=None, max_length=20)
    current: float | None = Field(default=None, ge=0, le=100000)
    start: float | None = Field(default=None, ge=0, le=100000)
    deadline: date | None = None
    status: Literal["active", "done"] = "active"


class GoalUpdate(BaseModel):
    current: float | None = Field(default=None, ge=0, le=100000)
    status: Literal["active", "done"] | None = None
    title: str | None = Field(default=None, min_length=1, max_length=80)
    target: float | None = Field(default=None, ge=0, le=100000)


@app.get("/api/goals")
async def list_goals(user: str = Depends(current_user)) -> dict:
    return {"goals": await S.store.list(user, "goal")}


@app.post("/api/goals")
async def add_goal(body: GoalIn, user: str = Depends(current_user)) -> dict:
    data = body.model_dump(mode="json")
    if data["start"] is None:
        data["start"] = data["current"]
    return await S.store.add(user, "goal", data)


@app.patch("/api/goals/{goal_id}")
async def update_goal(goal_id: str, body: GoalUpdate, user: str = Depends(current_user)) -> dict:
    doc = await S.store.update(user, "goal", goal_id, body.model_dump(mode="json", exclude_none=True))
    if not doc:
        raise HTTPException(404)
    return doc


@app.delete("/api/goals/{goal_id}")
async def delete_goal(goal_id: str, user: str = Depends(current_user)) -> dict:
    if not await S.store.delete(user, "goal", goal_id):
        raise HTTPException(404)
    return {"ok": True}


@app.get("/api/threads")
async def list_threads(user: str = Depends(current_user)) -> dict:
    """The user's conversations, newest activity first, built from their recorded runs."""
    threads: dict[str, dict] = {}
    for r in sorted(await S.store.list(user, "run", 2000), key=lambda r: r["created_at"]):
        t = threads.setdefault(r["thread_id"], {"thread_id": r["thread_id"], "title": r["request"][:80],
                                                "started_at": r["created_at"], "turns": 0, "has_plan": False,
                                                "has_workout": False})
        t.update(updated_at=r["created_at"], turns=t["turns"] + 1, last=r.get("headline") or "",
                 has_plan=t["has_plan"] or r.get("has_plan", False),
                 has_workout=t["has_workout"] or r.get("has_workout", False))
    return {"threads": sorted(threads.values(), key=lambda t: t["updated_at"], reverse=True)}


@app.get("/api/threads/{thread_id}/turns")
async def thread_turns(thread_id: str, user: str = Depends(current_user)) -> dict:
    """Every turn of a conversation (request + the state shown), to reopen it in the chat."""
    _thread_cfg(user, thread_id)  # validates the id
    runs = sorted([r for r in await S.store.list(user, "run", 2000) if r["thread_id"] == thread_id],
                  key=lambda r: r["created_at"])
    if not runs:
        raise HTTPException(404)
    turns = [{"turn": r["turn"], "request": r["request"], "created_at": r["created_at"], "run_id": r["id"],
              "state": r.get("final_state")} for r in runs]
    if any(t["state"] is None for t in turns):  # runs recorded before final_state: fall back to thread text
        snap = await S.graph.aget_state(_thread_cfg(user, thread_id))
        answers = [h["content"] for h in (snap.values or {}).get("history", []) if h["role"] == "assistant"]
        if turns[-1]["state"] is None and snap.values:
            turns[-1]["state"] = public_state(snap.values)  # the checkpoint holds the full latest turn
        for i, t in enumerate(turns):
            if t["state"] is None:
                text = answers[i] if i < len(answers) else ""
                t["state"] = {"status": "answered", "turn": t["turn"], "recommendation": {
                    "headline": "", "summary": text, "key_points": [], "tips": [], "citations": [],
                    "follow_up_questions": [], "disclaimer": ""}, "evidence": [], "health": None}
    return {"thread_id": thread_id, "turns": turns, "events": runs[-1].get("events", [])}


@app.get("/api/threads/{thread_id}")
async def get_thread(thread_id: str, user: str = Depends(current_user)) -> dict:
    snap = await S.graph.aget_state(_thread_cfg(user, thread_id))
    if not snap.values:
        raise HTTPException(404)
    values = snap.values
    return public_state(values) | {"history": values.get("history") or []}


# ------------------------------------------------------------------ ops --------------------------------

@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "db": S.db is not None, "model": get_settings().llm_model}


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)
