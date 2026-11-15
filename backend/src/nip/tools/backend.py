"""How agents reach tools: in-process (tests) or over MCP (default in docker-compose / prod)."""

from __future__ import annotations

import asyncio
import json
import time
from functools import lru_cache
from typing import Any, Protocol

import structlog

from nip.config import get_settings

log = structlog.get_logger("nip.tools")


class ToolBackend(Protocol):
    async def call(self, name: str, args: dict[str, Any]) -> dict: ...


class ToolError(RuntimeError):
    pass


class InProcessBackend:
    async def call(self, name: str, args: dict[str, Any]) -> dict:
        from nip.tools.registry import call_tool

        return await asyncio.to_thread(call_tool, name, args)


class MCPBackend:
    """Calls tools on the MCP server over streamable HTTP."""

    def __init__(self, url: str, timeout_s: float = 30.0):
        self.url = url
        self.timeout_s = timeout_s

    async def call(self, name: str, args: dict[str, Any]) -> dict:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(self.url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await asyncio.wait_for(session.call_tool(name, args), timeout=self.timeout_s)
        if res.isError:
            raise ToolError(" ".join(getattr(c, "text", "") for c in res.content))
        if res.structuredContent is not None:
            sc = res.structuredContent
            return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
        text = "".join(getattr(c, "text", "") for c in res.content)
        return json.loads(text)


class TimedBackend:
    """Adds timeouts, structured logs and latency to any backend."""

    def __init__(self, inner: ToolBackend, timeout_s: float = 30.0):
        self.inner = inner
        self.timeout_s = timeout_s

    async def call(self, name: str, args: dict[str, Any]) -> dict:
        t0 = time.perf_counter()
        try:
            out = await asyncio.wait_for(self.inner.call(name, args), timeout=self.timeout_s)
            log.info("tool.ok", tool=name, ms=round((time.perf_counter() - t0) * 1000, 1))
            return out
        except Exception as e:  # surfaced to the agent as an error message, never raised to the user
            log.warning("tool.error", tool=name, error=str(e)[:300])
            raise ToolError(f"{name} failed: {e}") from e


_backend_override: ToolBackend | None = None


def set_backend_override(b: ToolBackend | None) -> None:
    global _backend_override
    _backend_override = b


@lru_cache
def _default_backend() -> ToolBackend:
    s = get_settings()
    inner: ToolBackend = MCPBackend(s.mcp_server_url) if s.tool_transport == "mcp" else InProcessBackend()
    return TimedBackend(inner)


def get_backend() -> ToolBackend:
    return _backend_override or _default_backend()
