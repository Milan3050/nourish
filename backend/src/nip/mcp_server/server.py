"""MCP server exposing the nutrition tools.

    uv run python -m nip.mcp_server.server          # streamable HTTP on :8765/mcp

Any MCP client (the agents here, Claude Desktop, an IDE) can use the same tools. It binds to
localhost by default; in production it runs on the private network only (see docs/deployment.md).
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from nip.observability.logging import configure_logging
from nip.tools import registry

# DNS-rebinding protection stays on everywhere. FastMCP only enables it for loopback binds, so when the
# server binds 0.0.0.0 in a container the Host header would otherwise go unchecked. List the names
# clients use to reach it, e.g. NIP_MCP_ALLOWED_HOSTS="mcp:*,localhost:*" in docker-compose.
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("NIP_MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*,[::1]:*")
                 .split(",") if h.strip()]

mcp = FastMCP("nutrition-tools", host=os.environ.get("NIP_MCP_HOST", "127.0.0.1"),
              port=int(os.environ.get("NIP_MCP_PORT", "8765")),
              transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                                           allowed_hosts=ALLOWED_HOSTS))

for fn in registry.TOOLS.values():
    mcp.tool()(fn)


def main() -> None:
    configure_logging()
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
