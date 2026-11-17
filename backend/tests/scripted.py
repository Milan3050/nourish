"""A scripted chat model for deterministic agent tests (no LLM server needed).

A policy function sees the messages and returns the next AIMessage (usually a tool call). This lets us
test orchestration, guardrails, acceptance checks and loops exactly, and record/replay real runs later.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

Policy = Callable[[str, list[BaseMessage]], AIMessage]


def call(name: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{uuid.uuid4().hex[:8]}"}])


def role_of(messages: list[BaseMessage]) -> str:
    sys = next((m.content for m in messages if isinstance(m, SystemMessage)), "")
    if "Supervisor of a nutrition" in sys:
        return "supervisor"
    if sys.startswith("You are Nourish"):
        return "reply"
    for r in ("Health Assessment", "Nutrition Knowledge", "Meal Planning", "Workout Planning", "Validation",
              "Recommendation"):
        if f"You are the {r} Agent" in sys:
            return r.split()[0].lower()
    return "unknown"


def tool_results(messages: list[BaseMessage], name: str | None = None) -> list[dict]:
    out = []
    for m in messages:
        if isinstance(m, ToolMessage) and (name is None or m.name == name):
            try:
                out.append(json.loads(m.content))
            except json.JSONDecodeError:
                out.append({"raw": m.content})
    return out


class ScriptedChatModel(BaseChatModel):
    policy: Any
    calls: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # tools are irrelevant: the policy decides
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        role = role_of(messages)
        msg = self.policy(role, messages)
        self.calls.append(role)
        return ChatResult(generations=[ChatGeneration(message=msg)])
