"""Minimal MCP server over stdio, exposing the capability registry 1:1.

Deliberately dependency-free: newline-delimited JSON-RPC on stdin/stdout, logs on stderr only.
Every tool call delegates to backend.capabilities.REGISTRY, so HTTP, MCP and the web agent share one
contract. Supports initialize / tools/list / tools/call / ping; other methods return JSON-RPC errors.

Run:  .venv/bin/python -m backend.mcp_server [--database data/database/catalog.sqlite]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.capabilities import REGISTRY
from backend.catalog.database import DEFAULT_DATABASE, connect

SERVER_NAME = "aipaperbase"
SERVER_VERSION = "0.3.0"
DEFAULT_PROTOCOL = "2024-11-05"
SUPPORTED_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}


def log(message: str) -> None:
    print(f"[mcp] {message}", file=sys.stderr, flush=True)


class McpServer:
    def __init__(self, database: Path) -> None:
        self.database = database

    def _call_tool(self, name: str, arguments: dict) -> dict:
        if not self.database.exists():
            return {"content": [{"type": "text", "text": "catalog database not found; run backend.catalog.import_csv"}],
                    "isError": True}
        with connect(self.database, read_only=True) as connection:
            result = REGISTRY.call(connection, name, arguments)
        payload = result.as_dict()
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                "isError": not result.ok, "structuredContent": payload}

    def handle(self, message: dict) -> dict | None:
        method = message.get("method")
        msg_id = message.get("id")
        if msg_id is None and method and method.startswith("notifications/"):
            return None
        if method == "initialize":
            requested = (message.get("params") or {}).get("protocolVersion")
            protocol = requested if requested in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
            return {"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": REGISTRY.tools()}}
        if method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not name or not isinstance(arguments, dict):
                return {"jsonrpc": "2.0", "id": msg_id,
                        "error": {"code": -32602, "message": "tools/call requires name and object arguments"}}
            return {"jsonrpc": "2.0", "id": msg_id, "result": self._call_tool(name, arguments)}
        if method in ("resources/list", "prompts/list"):
            key = method.split("/")[0]
            return {"jsonrpc": "2.0", "id": msg_id, "result": {key: []}}
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601, "message": f"method not found: {method}"}}

    def serve(self) -> None:
        log(f"ready; database={self.database}; tools={len(REGISTRY.names())}")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                print(json.dumps({"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32700, "message": "parse error"}}), flush=True)
                continue
            if not isinstance(message, dict):
                continue
            response = self.handle(message)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="AIPaperbase Agent MCP server (stdio)")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()
    McpServer(args.database).serve()


if __name__ == "__main__":
    main()
