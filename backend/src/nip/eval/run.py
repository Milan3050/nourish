"""Evaluation runner.

    uv run python -m nip.eval.run --suite retrieval            # offline, no LLM
    uv run python -m nip.eval.run --suite agents --judge       # scenarios + generation metrics
    uv run python -m nip.eval.run --suite redteam
    uv run python -m nip.eval.run --suite all --judge

Writes eval/results/<suite>-<timestamp>.json and a markdown summary next to it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from nip.config import get_settings
from nip.domain.models import UserProfile
from nip.eval import judge
from nip.eval.metrics import mean, ndcg_at_k, percentile, precision_at_k, recall_at_k, reciprocal_rank
from nip.security.pii import find_pii
from nip.tools.foods import get_food_db, is_allowed

ROOT = Path(__file__).resolve().parents[4]
DATASETS = ROOT / "eval" / "datasets"
RESULTS = ROOT / "eval" / "results"
KS = (1, 3, 5, 10)


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (DATASETS / name).read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------- retrieval ---------------------------------

def run_retrieval(modes=("dense", "sparse", "hybrid")) -> dict:
    from nip.rag.store import Retriever, connect, get_reranker

    data = load("retrieval.jsonl")
    retriever = Retriever(connect())
    out: dict = {"n": len(data), "embeddings": get_settings().embeddings_provider, "modes": {}}
    configs = [(m, None) for m in modes]
    reranker = get_reranker()
    if reranker:
        configs.append(("hybrid+rerank", reranker))
    for mode, rr in configs:
        retriever.reranker = rr
        per_q, lat = [], []
        for q in data:
            t0 = time.perf_counter()
            hits = retriever.search(q["query"], q["region"], k=max(KS), mode=mode.split("+")[0])
            lat.append((time.perf_counter() - t0) * 1000)
            ids = [c.id for c, _ in hits]
            rel = set(q["relevant"])
            per_q.append({"id": q["id"], "retrieved": ids[:5], "rr": reciprocal_rank(ids, rel),
                          **{f"recall@{k}": recall_at_k(ids, rel, k) for k in KS},
                          **{f"precision@{k}": precision_at_k(ids, rel, k) for k in KS},
                          "ndcg@5": ndcg_at_k(ids, rel, 5)})
        agg = {key: round(mean([p[key] for p in per_q]), 3)
               for key in [*(f"recall@{k}" for k in KS), *(f"precision@{k}" for k in KS), "ndcg@5"]}
        agg["mrr"] = round(mean([p["rr"] for p in per_q]), 3)
        agg["latency_ms_p50"] = round(percentile(lat, 50), 1)
        agg["latency_ms_p95"] = round(percentile(lat, 95), 1)
        out["modes"][mode] = {"metrics": agg, "misses": [p for p in per_q if p["rr"] == 0]}
    return out


# ---------------------------------------------------------------- agents ------------------------------------

async def run_turn(graph, profile: dict, message: str) -> tuple[dict, list[dict], float]:
    cfg = {"configurable": {"thread_id": f"eval-{uuid.uuid4().hex[:8]}"}, "recursion_limit": 80}
    events, final = [], {}
    t0 = time.perf_counter()
    async for mode, chunk in graph.astream({"request": message, "profile": profile}, cfg,
                                           stream_mode=["custom", "values"]):
        if mode == "custom":
            events.append(chunk)
        else:
            final = chunk
    return final, events, time.perf_counter() - t0


def plan_items(state: dict) -> list[dict]:
    plan = (state.get("recommendation") or {}).get("plan") or {}
    return [it for d in plan.get("days", []) for m in d["meals"] for it in m["items"]]


def check_expectations(case: dict, state: dict) -> list[str]:
    exp, fails = case.get("expect", {}), []
    ran = [r["agent"] for r in state.get("agent_runs") or []]
    if "status_in" in exp and state.get("status") not in exp["status_in"]:
        fails.append(f"status {state.get('status')} not in {exp['status_in']}")
    for a in exp.get("must_run", []):
        if a not in ran:
            fails.append(f"{a} did not run")
    for a in exp.get("must_not_run", []):
        if a in ran:
            fails.append(f"{a} ran but should not")
    rec = state.get("recommendation") or {}
    if exp.get("plan_required") and not rec.get("plan"):
        fails.append("no validated plan returned")
    vals = state.get("validations") or []
    if "verdict_in" in exp and rec.get("plan") and vals and vals[-1]["verdict"] not in exp["verdict_in"]:
        fails.append(f"verdict {vals[-1]['verdict']}")
    health = state.get("health") or {}
    if "escalation" in exp and bool(health.get("escalation_required")) != exp["escalation"]:
        fails.append(f"escalation_required={health.get('escalation_required')}")
    if exp.get("risk_flag") and exp["risk_flag"] not in {f["code"] for f in health.get("risk_flags", [])}:
        fails.append(f"missing risk flag {exp['risk_flag']}")
    if exp.get("min_citations", 0) > len(rec.get("citations") or []) + sum(len(e["citations"]) for e in state.get("evidence") or []):
        fails.append("not enough citations")
    if exp.get("flag") and exp["flag"] not in (state.get("input_flags") or []):
        fails.append(f"input flag {exp['flag']} not raised")
    if exp.get("blocked") and state.get("status") != "blocked":
        fails.append("not blocked")
    if exp.get("no_banned_output"):
        from nip.security.guard import check_output
        bad = check_output(judge.answer_text(rec))
        if bad:
            fails.append(f"banned output: {bad}")
    if exp.get("no_pii_in_state") and find_pii(json.dumps(state.get("history") or [])):
        fails.append("PII found in stored history")
    # Hard safety invariant for every case: no shown food violates the profile.
    if state.get("profile") and plan_items(state):
        p = UserProfile.model_validate(state["profile"])
        db = get_food_db()
        for it in plan_items(state):
            f = db.get(it["food_code"])
            if f is None or not is_allowed(f, p)[0]:
                fails.append(f"unsafe or unknown food shown: {it['food_name']}")
    return fails


async def run_agents(suite_file: str, use_judge: bool) -> dict:
    from langgraph.checkpoint.memory import InMemorySaver

    from nip.graph.build import build_graph

    graph = build_graph(InMemorySaver())
    cases = load(suite_file)
    results = []
    for case in cases:
        profile = case.get("profile") or {"region": "IN"}
        try:
            state, _events, secs = await run_turn(graph, profile, case["message"])
            fails = check_expectations(case, state)
            err = None
        except Exception as e:  # a crash is a failed case, not a crashed eval
            state, _events, secs, fails, err = {}, [], 0.0, [f"crash: {e}"], str(e)
        runs = state.get("agent_runs") or []
        row = {
            "id": case["id"], "name": case.get("name", case["message"][:50]), "passed": not fails, "failures": fails,
            "status": state.get("status"), "latency_s": round(secs, 2),
            "completed": state.get("status") in ("answered", "clarifying", "blocked") and not err,
            "agents": [r["agent"] for r in runs], "agent_failures": [r["agent"] for r in runs if r["status"] != "done"],
            "agent_ms": {r["agent"]: r["ms"] for r in runs}, "tokens": sum(r.get("tokens", 0) for r in runs),
            "overrides": [d["override"] for d in state.get("decisions") or [] if d.get("override")],
            "revisions": state.get("turn_revisions", 0),
        }
        rec = state.get("recommendation") or {}
        if use_judge and rec.get("summary") and state.get("status") == "answered":
            ans, ctx = judge.answer_text(rec), judge.context_text(state)
            try:
                f, unsupported = await judge.faithfulness(ans, ctx)
                cs = await judge.citation_support(ans, rec.get("citations") or [])
                row["generation"] = {
                    "faithfulness": round(f, 3), "unsupported_claims": unsupported[:5],
                    "groundedness_numeric": round(judge.numeric_grounding(ans, ctx), 3),
                    "groundedness_citations": None if cs is None else round(cs, 3),
                    "answer_relevance": round(await judge.answer_relevance(case["message"], ans), 3),
                }
            except Exception as e:
                row["generation"] = {"error": str(e)[:200]}
        results.append(row)
        print(f"  {'PASS' if row['passed'] else 'FAIL'} {case['id']} {row['name']} ({row['latency_s']}s) {fails or ''}")

    lat = [r["latency_s"] for r in results if r["completed"]]
    per_agent: dict[str, list[float]] = {}
    for r in results:
        for a, ms in r["agent_ms"].items():
            per_agent.setdefault(a, []).append(ms / 1000)
    gen = [r["generation"] for r in results if "generation" in r and "error" not in r["generation"]]
    summary = {
        "n": len(results),
        "success_rate": round(mean([r["passed"] for r in results]), 3),
        "workflow_completion_rate": round(mean([r["completed"] for r in results]), 3),
        "agent_failure_rate": round(mean([bool(r["agent_failures"]) for r in results]), 3),
        "latency_s": {"p50": round(percentile(lat, 50), 2), "p95": round(percentile(lat, 95), 2)},
        "agent_latency_s_p50": {a: round(percentile(v, 50), 2) for a, v in per_agent.items()},
        "avg_tokens": round(mean([r["tokens"] for r in results])),
        "avg_revisions": round(mean([r["revisions"] for r in results]), 2),
        "override_rate": round(mean([bool(r["overrides"]) for r in results]), 3),
    }
    if gen:
        summary["generation"] = {
            k: round(mean([g[k] for g in gen if g.get(k) is not None]), 3)
            for k in ("faithfulness", "groundedness_numeric", "groundedness_citations", "answer_relevance")
            if any(g.get(k) is not None for g in gen)
        }
    return {"summary": summary, "cases": results, "model": get_settings().llm_model,
            "judge_model": get_settings().judge_model if use_judge else None}


# ---------------------------------------------------------------- report ------------------------------------

def markdown(name: str, res: dict) -> str:
    lines = [f"# Evaluation: {name}", f"Run at {datetime.now(UTC).isoformat(timespec='seconds')}", ""]
    if "modes" in res:
        lines += [f"{res['n']} labelled queries, embeddings: {res['embeddings']}", "",
                  "| Mode | Recall@5 | Precision@5 | MRR | nDCG@5 | Recall@10 | p95 ms |", "|---|---|---|---|---|---|---|"]
        for m, v in res["modes"].items():
            x = v["metrics"]
            lines.append(f"| {m} | {x['recall@5']} | {x['precision@5']} | {x['mrr']} | {x['ndcg@5']} | {x['recall@10']} | {x['latency_ms_p95']} |")
    else:
        s = res["summary"]
        lines += [f"Model: {res['model']}" + (f", judge: {res['judge_model']}" if res.get("judge_model") else ""), "",
                  "| Metric | Value |", "|---|---|"]
        for k, v in s.items():
            lines.append(f"| {k} | {v} |")
        lines += ["", "| Case | Result | Latency (s) | Agents | Notes |", "|---|---|---|---|---|"]
        for c in res["cases"]:
            lines.append(f"| {c['id']} {c['name']} | {'pass' if c['passed'] else 'FAIL'} | {c['latency_s']} | "
                         f"{' > '.join(c['agents'])} | {'; '.join(c['failures'])[:120]} |")
    return "\n".join(lines) + "\n"


def save(name: str, res: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    path = RESULTS / f"{name}-{stamp}.json"
    path.write_text(json.dumps(res, indent=2, default=str))
    (RESULTS / f"{name}-latest.md").write_text(markdown(name, res))
    return path


def main() -> None:
    from nip.observability.logging import configure_logging

    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["retrieval", "agents", "redteam", "all"], default="all")
    ap.add_argument("--judge", action="store_true", help="compute LLM-judged generation metrics")
    a = ap.parse_args()
    get_settings().log_level = "WARNING"
    configure_logging()
    if a.suite in ("retrieval", "all"):
        res = run_retrieval()
        print(markdown("retrieval", res))
        print("saved", save("retrieval", res))
    if a.suite in ("agents", "all"):
        res = asyncio.run(run_agents("scenarios.jsonl", a.judge))
        print(markdown("agents", res))
        print("saved", save("agents", res))
    if a.suite in ("redteam", "all"):
        res = asyncio.run(run_agents("redteam.jsonl", False))
        print(markdown("redteam", res))
        print("saved", save("redteam", res))


if __name__ == "__main__":
    main()
