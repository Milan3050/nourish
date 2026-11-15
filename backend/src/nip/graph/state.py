"""Shared graph state. Agents publish typed outputs here; private reasoning stays in their subgraphs."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge_evidence(old: list[dict], new: list[dict]) -> list[dict]:
    """Append evidence, de-duplicating by question (latest answer wins)."""
    by_q = {e["question"].strip().lower(): e for e in old or []}
    for e in new or []:
        by_q[e["question"].strip().lower()] = e
    return list(by_q.values())


def replace_or_reset(old: Any, new: Any) -> Any:
    return new


class NIPState(TypedDict, total=False):
    # conversation
    request: str
    history: Annotated[list[dict], operator.add]
    input_flags: list[str]
    blocked_reason: str | None
    # durable user context (survives across turns in a thread)
    profile: dict
    health: dict | None
    evidence: Annotated[list[dict], merge_evidence]
    plans: Annotated[list[dict], operator.add]
    validations: Annotated[list[dict], operator.add]
    # per-turn orchestration
    objectives: list[str]
    next: str | None
    instruction: str
    pending_questions: list[str]
    recommendation: dict | None
    turn: int
    turn_steps: int
    turn_revisions: int
    turn_tokens: int
    agent_runs: Annotated[list[dict], operator.add]
    decisions: Annotated[list[dict], operator.add]
    status: str  # running | answered | clarifying | blocked | failed
