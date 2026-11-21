"""Start a server over stdio (development) or streamable HTTP (Docker)."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable

from fastmcp import FastMCP

from auditguard.config import Settings, load_settings


def run(name: str, build: Callable[[Settings], FastMCP], argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog=f"python -m mcp_servers.{name}")
    parser.add_argument("--transport", choices=["stdio", "http"], default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    # stdout carries the MCP protocol over stdio, so logs go to stderr only.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    settings = load_settings()
    server = build(settings)
    transport = args.transport or settings.mcp.transport
    if transport == "stdio":
        server.run(transport="stdio", show_banner=False)
    else:
        port = args.port or settings.mcp.servers[name].port
        server.run(transport="http", host=settings.mcp.http_host, port=port, show_banner=False)
