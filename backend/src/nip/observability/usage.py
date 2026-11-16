"""Token usage and cost per chat turn.

A LangChain callback attached to the turn counts input and output tokens for every model call (router,
agents, judge), grouped by model. Cost uses the per-model price table in settings (USD per million
tokens); local models are free unless priced. An optional reference model prices the same tokens as if
a hosted model had been used, labelled as an estimate.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult

from nip.config import get_settings


class UsageTracker(AsyncCallbackHandler):
    def __init__(self) -> None:
        self.models: dict[UUID, str] = {}
        self.by_model: dict[str, dict[str, int]] = {}
        self.calls = 0

    async def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, *, run_id: UUID, **kw: Any) -> None:
        params = kw.get("invocation_params") or {}
        self.models[run_id] = params.get("model") or params.get("model_name") or (kw.get("metadata") or {}).get(
            "ls_model_name") or "unknown"

    async def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kw: Any) -> None:
        model = self.models.pop(run_id, "unknown")
        tin = tout = 0
        for gens in response.generations:
            for g in gens:
                um = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                tin += um.get("input_tokens", 0)
                tout += um.get("output_tokens", 0)
        if not (tin or tout):
            tu = (response.llm_output or {}).get("token_usage") or {}
            tin, tout = tu.get("prompt_tokens", 0), tu.get("completion_tokens", 0)
        m = self.by_model.setdefault(model, {"input_tokens": 0, "output_tokens": 0, "calls": 0})
        m["input_tokens"] += tin
        m["output_tokens"] += tout
        m["calls"] += 1
        self.calls += 1

    def summary(self) -> dict:
        s = get_settings()
        tin = sum(m["input_tokens"] for m in self.by_model.values())
        tout = sum(m["output_tokens"] for m in self.by_model.values())

        def price(model: str, i: int, o: int) -> float:
            p = s.pricing.get(model)
            return (i * p[0] + o * p[1]) / 1e6 if p else 0.0

        cost = sum(price(k, m["input_tokens"], m["output_tokens"]) for k, m in self.by_model.items())
        ref = s.pricing_reference_model
        return {"input_tokens": tin, "output_tokens": tout, "calls": self.calls, "by_model": self.by_model,
                "cost_usd": round(cost, 6),
                "reference_model": ref if ref in s.pricing else None,
                "reference_cost_usd": round(price(ref, tin, tout), 6) if ref in s.pricing else None}
