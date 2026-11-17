"""Runtime configuration. Every knob is an environment variable (see .env.example)."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # An empty variable (NIP_LLM_TEMPERATURE=) means "not set".
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), env_prefix="NIP_", extra="ignore",
                                      env_parse_none_str="")

    env: Literal["dev", "test", "prod"] = "dev"

    # --- LLM -----------------------------------------------------------------------------------
    # Every provider is reached through the OpenAI-compatible chat API: Ollama (/v1), OpenAI,
    # Groq, Gemini's OpenAI endpoint, vLLM... so one client covers local and hosted modes.
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen2.5:7b-instruct"
    # Cheaper/faster model for routing decisions; falls back to llm_model.
    llm_router_model: str | None = None
    # Independent model for LLM-as-judge in evals (should differ from the generator).
    llm_judge_model: str | None = None
    # Set NIP_LLM_TEMPERATURE= (empty) for models that only accept their default temperature
    # (OpenAI o-series / GPT-5 reasoning models); temperature is then not sent at all.
    llm_temperature: float | None = 0.2
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 2
    # How the router and judge get typed output. Ollama's /v1 ignores a forced tool_choice, so
    # "function_calling" returns empty messages there; "json_schema" uses constrained decoding.
    # "auto": json_schema for a local Ollama endpoint, function_calling for hosted providers (works on
    # OpenAI, Anthropic's OpenAI-compatible API, Groq, Gemini, vLLM).
    llm_structured_method: Literal["auto", "json_schema", "function_calling"] = "auto"

    # --- Embeddings / retrieval ----------------------------------------------------------------
    embeddings_provider: Literal["ollama", "fastembed", "hash"] = "ollama"
    embeddings_model: str = "nomic-embed-text"
    embeddings_base_url: str = "http://localhost:11434"  # Ollama, even when the chat LLM is hosted
    embeddings_dim: int = 768
    reranker: Literal["none", "fastembed", "llm"] = "none"
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    retrieval_top_k: int = 8
    retrieval_min_score: float = 0.0

    # --- Storage -------------------------------------------------------------------------------
    database_url: str = "postgresql://nip:nip@localhost:5432/nip"

    # --- Tools ---------------------------------------------------------------------------------
    # "mcp": agents reach tools through the MCP server (production shape).
    # "inprocess": same tool functions called directly (tests, quick dev).
    tool_transport: Literal["mcp", "inprocess"] = "inprocess"
    mcp_server_url: str = "http://localhost:8765/mcp"

    # --- Budgets & safety ----------------------------------------------------------------------
    max_supervisor_steps: int = 12
    max_agent_steps: int = 10
    max_planner_steps: int = 16  # the planner needs more tool rounds than the other agents
    max_plan_revisions: int = 3
    max_request_tokens: int = 120_000
    macro_tolerance_pct: float = 10.0

    # --- Cost accounting ------------------------------------------------------------------------
    # USD per million tokens [input, output] by model name. Unpriced models (local) cost 0.
    pricing: dict[str, list[float]] = {}
    # Optional: also price each run's tokens at this model's rates, shown as an estimate.
    pricing_reference_model: str | None = None

    # --- API / security ------------------------------------------------------------------------
    jwt_secret: str = Field(default="dev-only-change-me", repr=False)
    cors_origins: list[str] = ["http://localhost:5173"]
    rate_limit_per_minute: int = 30

    # --- Observability -------------------------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = False
    otel_endpoint: str | None = None  # e.g. http://localhost:6006/v1/traces (Arize Phoenix)
    service_name: str = "nip-api"

    @property
    def structured_method(self) -> Literal["json_schema", "function_calling"]:
        if self.llm_structured_method != "auto":
            return self.llm_structured_method
        local = any(h in self.llm_base_url for h in (":11434", "ollama"))
        return "json_schema" if local else "function_calling"

    @model_validator(mode="after")
    def _docker_hosts(self) -> Settings:
        """In a container, 'localhost' is the container itself: point local endpoints at the host instead.
        Hosted URLs are left alone, so switching to a hosted model is still a .env change only."""
        if os.environ.get("NIP_IN_DOCKER") == "1":
            for f in ("llm_base_url", "embeddings_base_url"):
                v = getattr(self, f)
                for local in ("//localhost", "//127.0.0.1"):
                    v = v.replace(local, "//host.docker.internal")
                object.__setattr__(self, f, v)
        return self

    @property
    def router_model(self) -> str:
        return self.llm_router_model or self.llm_model

    @property
    def judge_model(self) -> str:
        return self.llm_judge_model or self.llm_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
