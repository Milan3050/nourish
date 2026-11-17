"""Check that the configured model provider works for everything the agents need.

    make check-llm          # or: cd backend && uv run python -m nip.llm_check

Runs a plain call, a structured routing decision, a tool call and an embedding against whatever .env
points at, and prints a hint for each failure. Use it after switching NIP_LLM_* to a hosted provider.
"""

from __future__ import annotations

import asyncio
import sys
import time

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from nip.config import get_settings
from nip.llm import get_chat_model

HINTS = {
    "auth": "check NIP_LLM_API_KEY (and that the key can use this model)",
    "model": "check NIP_LLM_MODEL is a model name this provider serves",
    "temperature": "this model rejects a custom temperature: set NIP_LLM_TEMPERATURE= (empty)",
    "connect": "check NIP_LLM_BASE_URL (OpenAI-compatible endpoint, usually ending in /v1)",
}


def hint(err: Exception) -> str:
    e = str(err).lower()
    if "401" in e or "auth" in e or "api key" in e:
        return HINTS["auth"]
    if "temperature" in e:
        return HINTS["temperature"]
    if "404" in e or "model" in e and ("not found" in e or "does not exist" in e):
        return HINTS["model"]
    if "connect" in e or "resolve" in e or "timed out" in e:
        return HINTS["connect"]
    return "see the error above"


@tool
def lookup_food(name: str) -> str:
    """Look up a food by name in the food table."""
    return f"{name}: 100 kcal per 100 g"


async def main() -> int:
    s = get_settings()
    print(f"provider  {s.llm_base_url}\nmodel     {s.llm_model} (router {s.router_model}, judge {s.judge_model})")
    print(f"typed output via {s.structured_method}; temperature {s.llm_temperature if s.llm_temperature is not None else 'not sent'}")
    print(f"embeddings {s.embeddings_provider}:{s.embeddings_model} at {s.embeddings_base_url}\n")
    failed = 0

    async def step(name: str, fn):
        nonlocal failed
        t0 = time.perf_counter()
        try:
            detail = await fn()
            print(f"  ok    {name:<22} {time.perf_counter() - t0:5.1f}s  {detail}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {name:<22} {type(e).__name__}: {str(e)[:200]}\n        hint: {hint(e)}")

    async def plain():
        msg = await get_chat_model("agent").ainvoke([HumanMessage("Reply with the single word OK.")])
        usage = getattr(msg, "usage_metadata", None) or {}
        return f"{str(msg.content).strip()[:30]!r} ({usage.get('total_tokens', '?')} tokens)"

    async def routing():
        from nip.graph.supervisor import SUPERVISOR_PROMPT, Decision

        model = get_chat_model("router").with_structured_output(Decision, method=s.structured_method)
        d = await model.ainvoke([SystemMessage(SUPERVISOR_PROMPT), HumanMessage(
            "<user_message>I take warfarin, can I eat spinach?</user_message>\nCurrent state of the work: {}")])
        if not isinstance(d, Decision):
            raise ValueError(f"no structured decision returned (got {d!r}); try NIP_LLM_STRUCTURED_METHOD=json_schema "
                             "or function_calling")
        return f"next={d.next}"

    async def tools():
        msg = await get_chat_model("agent").bind_tools([lookup_food]).ainvoke(
            [HumanMessage("Use the lookup_food tool to look up paneer.")])
        if not msg.tool_calls:
            raise ValueError("the model answered without calling the tool; agents need tool calling")
        return f"called {msg.tool_calls[0]['name']}({msg.tool_calls[0]['args']})"

    async def embeddings():
        from nip.rag.embeddings import get_embedder

        vec = await asyncio.to_thread(get_embedder().embed_query, "protein for muscle gain")
        if len(vec) != s.embeddings_dim:
            raise ValueError(f"got {len(vec)} dimensions, index expects {s.embeddings_dim}")
        return f"{len(vec)} dimensions"

    for name, fn in (("plain call", plain), ("structured routing", routing), ("tool calling", tools),
                     ("embeddings", embeddings)):
        await step(name, fn)
    print("\nall checks passed" if not failed else f"\n{failed} check(s) failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
