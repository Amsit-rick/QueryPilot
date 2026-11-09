"""Run Query Pilot's standard MCP server over stdio or Streamable HTTP."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.mcp_server import create_mcp_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Query Pilot MCP server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--path", default="/mcp")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="allow a non-loopback HTTP bind after external TLS/auth controls are configured",
    )
    args = parser.parse_args(argv)

    loopback_hosts = {"127.0.0.1", "localhost", "::1"}
    if args.transport == "streamable-http" and args.host not in loopback_hosts and not args.allow_remote:
        parser.error("non-loopback MCP HTTP binding requires --allow-remote")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")

    server = create_mcp_server()
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path=args.path,
            stateless_http=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
