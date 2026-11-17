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


async def test_runs_plans_calendar_and_goals(client):
    tok = (await client.post("/api/auth/dev-token", json={"name": "Asha"})).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    await client.put("/api/profile", json={**VEG_PROFILE, "allergies": []}, headers=h)
    g = (await client.post("/api/goals", json={"kind": "weight", "title": "Reach 65 kg", "target": 65, "unit": "kg",
                                               "current": 60}, headers=h)).json()
    assert g["start"] == 60
    set_model_override(ScriptedChatModel(policy=veg_world()))
    async with client.stream("POST", "/api/chat/t9/stream", json={"message": "1-day plan"}, headers=h) as r:
        lines = [line async for line in r.aiter_lines()]
    set_model_override(None)
    final = json.loads([x for x in lines if x.startswith("data:")][-1].split(":", 1)[1])
    run = final["run"]
    assert run["status"] == "answered" and run["usage"]["cost_usd"] == 0 and run["has_plan"]
    runs = (await client.get("/api/runs", headers=h)).json()
    assert runs["total"]["runs"] == 1
    detail = (await client.get(f"/api/runs/{run['id']}", headers=h)).json()
    assert detail["events"] and [a["agent"] for a in detail["agents"]][-1] == "recommendation"
    assert set(detail["sources"]) == {"cited", "evidence", "searches", "count"}
    plan = (await client.post("/api/plans", json={"thread_id": "t9", "kind": "meal"}, headers=h)).json()
    assert plan["title"] == "1-day meal plan" and plan["data"]["days"][0]["totals"]["kcal"] > 0
    assert (await client.post("/api/plans", json={"thread_id": "t9", "kind": "workout"}, headers=h)).status_code == 409
    sched = (await client.post("/api/calendar", json={"plan_id": plan["id"], "start_date": "2026-11-16", "weeks": 1},
                               headers=h)).json()["entries"]
    assert [e["date"] for e in sched][:2] == ["2026-11-16", "2026-11-17"] and len(sched) == 7
    cal = (await client.get("/api/calendar?start=2026-11-01&end=2026-11-30", headers=h)).json()["entries"]
    done = (await client.patch(f"/api/calendar/{cal[0]['id']}", json={"done": True}, headers=h)).json()
    assert done["done"] is True
    other = (await client.post("/api/auth/dev-token", json={"name": "x"})).json()["token"]
    assert (await client.get(f"/api/runs/{run['id']}", headers={"Authorization": f"Bearer {other}"})).status_code == 404
