"""LLM-as-judge for generation metrics. Uses NIP_LLM_JUDGE_MODEL (ideally a different model from the generator).

- Faithfulness: share of the answer's factual claims supported by the context it was given.
- Groundedness: share of cited passages that actually support the claims they are attached to,
  combined with a deterministic check that every number in the answer exists in the context.
- Answer relevance: does the answer address what the user asked (1-5, normalised to 0-1)?
"""

from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from nip.config import get_settings
from nip.llm import get_chat_model


class ClaimVerdicts(BaseModel):
    claims: list[str] = Field(description="Atomic factual claims made in the answer")
    supported: list[bool] = Field(description="For each claim, is it supported by the context?")


class CitationVerdicts(BaseModel):
    supports: list[bool] = Field(description="For each cited passage, does it support the answer's claims?")


class Relevance(BaseModel):
    score: int = Field(ge=1, le=5, description="1 = ignores the question, 5 = fully and directly answers it")
    reason: str


JUDGE_SYSTEM = "You are a strict evaluator of a nutrition assistant. Judge only from the material given. Be literal."


async def _structured(schema, prompt: str):
    model = get_chat_model("judge").with_structured_output(schema, method=get_settings().structured_method)
    return await model.ainvoke([SystemMessage(JUDGE_SYSTEM), HumanMessage(prompt)])


async def faithfulness(answer: str, context: str) -> tuple[float, list[str]]:
    v = await _structured(ClaimVerdicts, f"CONTEXT:\n{context[:12000]}\n\nANSWER:\n{answer}\n\n"
                          "List the answer's atomic factual claims (skip greetings and advice to see a doctor), "
                          "and mark each as supported or not by the CONTEXT.")
    pairs = list(zip(v.claims, v.supported, strict=False))
    if not pairs:
        return 1.0, []
    return sum(s for _, s in pairs) / len(pairs), [c for c, s in pairs if not s]


async def citation_support(answer: str, citations: list[dict]) -> float | None:
    if not citations:
        return None
    passages = "\n\n".join(f"[{i}] {c['title']}: {c['snippet']}" for i, c in enumerate(citations))
    v = await _structured(CitationVerdicts, f"ANSWER:\n{answer}\n\nCITED PASSAGES:\n{passages}\n\n"
                          f"For each of the {len(citations)} passages, in order, say whether it supports a claim "
                          "in the answer.")
    s = v.supports[: len(citations)]
    return sum(s) / len(s) if s else 0.0


async def answer_relevance(question: str, answer: str) -> float:
    v = await _structured(Relevance, f"QUESTION:\n{question}\n\nANSWER:\n{answer}\n\nScore relevance 1-5.")
    return (v.score - 1) / 4


def numeric_grounding(answer: str, context: str) -> float:
    """Share of unit-bearing numbers in the answer that appear (within 3%) in the context."""
    from nip.agents.specs import UNIT_NUMBER

    nums = {float(m.replace(",", "")) for m in re.findall(r"\d[\d,]*(?:\.\d+)?", context)}
    found = [float(m.group(1).replace(",", "")) for m in UNIT_NUMBER.finditer(answer)]
    if not found:
        return 1.0
    ok = sum(1 for x in found if any(abs(x - n) <= max(1.0, 0.03 * n) for n in nums))
    return ok / len(found)


def answer_text(rec: dict) -> str:
    return " ".join([rec.get("headline", ""), rec.get("summary", ""), *rec.get("key_points", []), *rec.get("tips", [])])


def context_text(state: dict) -> str:
    parts = []
    if state.get("health"):
        parts.append("HEALTH: " + json.dumps(state["health"]))
    vals = state.get("validations") or []
    if vals and vals[-1].get("plan_with_totals"):
        parts.append("PLAN: " + json.dumps(vals[-1]["plan_with_totals"]))
    for e in state.get("evidence") or []:
        parts.append(f"EVIDENCE Q: {e['question']} A: {e['answer']}")
        parts += [f"SOURCE {c['chunk_id']}: {c['snippet']}" for c in e["citations"]]
    for c in (state.get("recommendation") or {}).get("citations", []):
        parts.append(f"SOURCE {c['chunk_id']}: {c['snippet']}")
    parts.append("PROFILE: " + json.dumps(state.get("profile") or {}))
    return "\n".join(parts)
