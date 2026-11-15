"""`gloss mcp-serve`: the course tools (course_tools.py) as a stdio MCP server for the claude CLI.

Minimal MCP: newline-delimited JSON-RPC 2.0 with initialize / tools/list / tools/call;
notifications are ignored. Verified against claude 2.1.314 (DESIGN.md §13.4).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import IO, Any

from .course_tools import TOOLS, Scope, ToolError, call

SERVER_NAME = "gloss"


def handle(scope: Scope, req: dict[str, Any]) -> dict[str, Any] | None:
    method, rid = req.get("method"), req.get("id")
    if rid is None:  # notification
        return None
    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": (req.get("params") or {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}}, "serverInfo": {"name": SERVER_NAME, "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = req.get("params") or {}
        try:
            result = {"content": call(scope, params.get("name", ""), params.get("arguments") or {})}
        except (ToolError, KeyError, ValueError, TypeError) as e:
            msg = f"缺少参数 {e}" if isinstance(e, KeyError) else str(e)
            result = {"content": [{"type": "text", "text": msg}], "isError": True}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def serve(root: Path, folder: str, only: list[str] | None, stdin: IO[str] = sys.stdin, stdout: IO[str] = sys.stdout) -> None:
    with Scope(root, folder, only) as scope:
        for line in stdin:
            try:
                req = json.loads(line)
            except ValueError:
                continue
            resp = handle(scope, req)
            if resp is not None:
                stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                stdout.flush()


def config(root: Path, folder: str, only: list[str] | None = None) -> dict[str, Any]:
    """The --mcp-config JSON that starts this server for one scope."""
    args = ["-m", "gloss", "mcp-serve", "--library", str(root), "--folder", folder]
    if only:
        args += ["--only", ",".join(only)]
    return {"mcpServers": {SERVER_NAME: {"command": sys.executable, "args": args}}}


def allowed_tools() -> list[str]:
    return [f"mcp__{SERVER_NAME}__{t['name']}" for t in TOOLS]
