"""The MCP server serves the same tools the agents use; results match the in-process backend."""

import os
import socket
import subprocess
import sys
import time

import pytest

from nip.tools.backend import InProcessBackend, MCPBackend


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mcp_url():
    port = _free_port()
    env = {**os.environ, "NIP_MCP_PORT": str(port), "NIP_LOG_LEVEL": "WARNING"}
    proc = subprocess.Popen([sys.executable, "-m", "nip.mcp_server.server"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.2)
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()


async def test_mcp_matches_inprocess(mcp_url):
    args = {"items": [{"food_code": "IN-A015", "grams": 150}, {"food_code": "IN-L003", "grams": 50}]}
    remote = await MCPBackend(mcp_url).call("compute_meal_nutrition", args)
    local = await InProcessBackend().call("compute_meal_nutrition", args)
    assert remote == local


async def test_mcp_lists_all_tools(mcp_url):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(mcp_url) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
    assert {"search_foods", "compute_meal_nutrition", "assess_energy", "check_plan", "search_guidelines"} <= names


async def test_mcp_rejects_foreign_host_header(mcp_url):
    """DNS-rebinding guard: only listed Host headers are served (NIP_MCP_ALLOWED_HOSTS)."""
    import httpx

    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    hdrs = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as c:
        bad = await c.post(mcp_url, json=body, headers={**hdrs, "Host": "evil.example:8765"})
        good = await c.post(mcp_url, json=body, headers=hdrs)
    assert bad.status_code in (400, 421) and good.status_code == 200
