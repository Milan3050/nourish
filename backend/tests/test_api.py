"""API: auth, profile, SSE stream end to end (in-memory checkpointer, scripted LLM)."""

import json

import httpx
import pytest

from nip.llm import set_model_override
from tests.scripted import ScriptedChatModel
from tests.test_graph import VEG_PROFILE, veg_world


@pytest.fixture
async def client(retriever, monkeypatch):
    monkeypatch.setenv("NIP_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    from nip.config import get_settings
    get_settings.cache_clear()
    from nip.tools import registry
    monkeypatch.setattr(registry, "_retriever", lambda: retriever)
    from nip.api.main import app, lifespan
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            yield c
    get_settings.cache_clear()


async def test_requires_auth(client):
    assert (await client.get("/api/profile")).status_code == 401
    r = await client.get("/api/profile", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


async def test_profile_and_stream(client):
    tok = (await client.post("/api/auth/dev-token", json={"name": "Lezm"})).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    bad = await client.put("/api/profile", json={"age": 500}, headers=h)
    assert bad.status_code == 422
    ok = await client.put("/api/profile", json={**VEG_PROFILE, "allergies": []}, headers=h)
    assert ok.status_code == 200
    set_model_override(ScriptedChatModel(policy=veg_world()))
    events = []
    async with client.stream("POST", "/api/chat/abc/stream", json={"message": "1-day plan"}, headers=h) as r:
        assert r.status_code == 200
        ev = None
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                ev = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                events.append((ev, json.loads(line.split(":", 1)[1])))
    set_model_override(None)
    kinds = [e for e, _ in events]
    assert kinds[-1] == "final"
    final = events[-1][1]
    assert final["status"] == "answered" and final["recommendation"]["plan"]
    thread = await client.get("/api/threads/abc", headers=h)
    assert thread.status_code == 200 and thread.json()["history"]
    other = (await client.post("/api/auth/dev-token", json={"name": "x"})).json()["token"]
    assert (await client.get("/api/threads/abc", headers={"Authorization": f"Bearer {other}"})).status_code == 404
