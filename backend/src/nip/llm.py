"""Chat model factory. One OpenAI-compatible client covers Ollama, OpenAI, Groq, Gemini, vLLM."""

from __future__ import annotations

from typing import Literal

from langchain_core.language_models import BaseChatModel

from nip.config import get_settings

Role = Literal["agent", "router", "judge"]
_override: BaseChatModel | None = None


def set_model_override(model: BaseChatModel | None) -> None:
    """Tests and evals inject a scripted or recorded model here."""
    global _override
    _override = model


def get_chat_model(role: Role = "agent") -> BaseChatModel:
    if _override is not None:
        return _override
    from langchain_openai import ChatOpenAI

    s = get_settings()
    model = {"agent": s.llm_model, "router": s.router_model, "judge": s.judge_model}[role]
    # Temperature is omitted entirely when unset, for models that reject anything but their default.
    temp = None if s.llm_temperature is None else (0.0 if role in ("router", "judge") else s.llm_temperature)
    return ChatOpenAI(
        model=model,
        base_url=s.llm_base_url,
        api_key=s.llm_api_key,
        timeout=s.llm_timeout_s,
        max_retries=s.llm_max_retries,
        **({"temperature": temp} if temp is not None else {}),
    )
