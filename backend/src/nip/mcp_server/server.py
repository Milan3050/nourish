"""MCP server exposing the nutrition tools.

    uv run python -m nip.mcp_server.server          # streamable HTTP on :8765/mcp

Any MCP client (the agents here, Claude Desktop, an IDE) can use the same tools. It binds to
localhost by default; in production it runs on the private network only (see docs/deployment.md).
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from nip.observability.logging import configure_logging
from nip.tools import registry

mcp = FastMCP("nutrition-tools", host=os.environ.get("NIP_MCP_HOST", "127.0.0.1"),
              port=int(os.environ.get("NIP_MCP_PORT", "8765")))

for fn in registry.TOOLS.values():
    mcp.tool()(fn)


def main() -> None:
    configure_logging()
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
