"""HTTP API: auth, profile, streaming chat (SSE), thread state, health and metrics.

    uv run uvicorn nip.api.main:app --reload --port 8000
"""

from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any

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

from nip.config import get_settings
from nip.domain.models import UserProfile
from nip.graph.build import build_graph
from nip.observability.logging import configure_logging
from nip.observability.tracing import configure_tracing

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
        S.graph = build_graph(saver)
        log.info("api.ready", checkpointer="postgres")
    except Exception as e:
        if s.env == "prod":
            raise
        log.warning("api.db_unavailable_using_memory", error=str(e)[:200])
        S.graph = build_graph(InMemorySaver())
    yield
    if S.db:
        await S.db.close()
    if S.saver_cm:
        await S.saver_cm.__aexit__(None, None, None)


app = FastAPI(title="Nutrition Intelligence Platform", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT"], allow_headers=["Authorization", "Content-Type"])


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
    return {"profile": await load_profile(user)}


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
    inp: dict[str, Any] = {"request": body.message}
    if not (snapshot.values or {}).get("profile"):
        inp["profile"] = await load_profile(user) or {"region": "IN"}

    async def events():
        t0 = time.perf_counter()
        status = "error"
        final: dict = {}
        try:
            async for mode, chunk in S.graph.astream(inp, cfg, stream_mode=["custom", "values"]):
                if mode == "custom":
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
            yield {"event": "final", "data": json.dumps(public_state(final), default=str)}
        except Exception as e:
            log.exception("chat.error", thread=thread_id)
            yield {"event": "error", "data": json.dumps({"message": "Something went wrong. Please try again.",
                                                         "detail": str(e)[:200] if get_settings().env == "dev" else None})}
        finally:
            REQUESTS.labels(status).inc()
            LATENCY.observe(time.perf_counter() - t0)

    return EventSourceResponse(events())


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
