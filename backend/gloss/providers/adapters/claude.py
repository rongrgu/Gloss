"""`claude -p` adapter. Flags and event shapes verified in DESIGN.md §2.1 (and §13.4 for MCP)."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from ..base import Capabilities, ErrorKind, Event, ProviderError, RunRequest
from ..cli_runner import Prepared

# Every call: only read-only file tools exist (Read, plus Grep/Glob when asked), they are
# pre-approved, they cannot leave the working folder (--restricted, verified for all three),
# and nothing can stop to ask for permission.
BASE_FLAGS = [
    "-p", "--restricted", "--permission-prompts", "none",
    "--output-format", "stream-json", "--verbose", "--include-partial-messages",
]
READ_ONLY_TOOLS = {"Read", "Grep", "Glob"}


def find_claude() -> str | None:
    return shutil.which("claude") or shutil.which("claude.cmd")


def clean_env() -> dict[str, str]:
    """A set ANTHROPIC_API_KEY silently overrides the subscription login (§2.1)."""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    return env


class ClaudeAdapter:
    name = "claude"
    caps = Capabilities(supports_output_schema=True, supports_image_flag=False, supports_append_system_prompt=True,
                        streams_text_deltas=True)

    def __init__(self, claude_bin: str | None = None):
        self.claude_bin = claude_bin

    def find(self) -> str | None:
        return self.claude_bin or find_claude()

    def env(self) -> dict[str, str]:
        return clean_env()

    def runtime_instructions_filename(self) -> str:
        return "CLAUDE.md"

    def build_cmd(self, req: RunRequest, exe: str, mcp_config: Path | None = None) -> list[str]:
        tools = tuple(req.tools)
        if not tools or set(tools) - READ_ONLY_TOOLS:
            raise ProviderError(f"only read-only tools are allowed, got {tools}")
        allowed = [*tools]
        if mcp_config is not None:  # course tools (§13.4); MCP tools not listed here are refused, not prompted
            from ...mcp_server import allowed_tools
            allowed += allowed_tools()
        args = [exe, *BASE_FLAGS, "--tools", *tools, "--allowedTools", *allowed]
        if mcp_config is not None:
            args += ["--mcp-config", str(mcp_config), "--strict-mcp-config"]
        if req.model:
            args += ["--model", req.model]
        if req.session_id:
            args += ["--resume", req.session_id]
        elif not req.persist:
            args.append("--no-session-persistence")
        # The system prompt is snapshotted when a session starts; on resume it is ignored.
        if req.system and not req.session_id:
            args += ["--append-system-prompt", req.system]
        if req.output_schema is not None:
            args += ["--json-schema", json.dumps(req.output_schema)]
        return args

    def prepare(self, req: RunRequest, exe: str, cwd: Path) -> Prepared:
        mcp_config = None
        if req.course_tools is not None:
            from ...mcp_server import config
            mcp_config = cwd / "mcp.json"
            ct = req.course_tools
            mcp_config.write_text(json.dumps(config(ct.library, ct.folder, list(ct.only))))
        return Prepared(self.build_cmd(req, exe, mcp_config), self.stdin(req, cwd))

    @staticmethod
    def stdin(req: RunRequest, cwd: Path) -> str:
        """Images are given as paths for the Read tool, which can only reach the working folder."""
        if not req.images:
            return req.prompt
        paths = []
        for img in req.images:
            p = Path(img).resolve()
            if not p.is_relative_to(cwd.resolve()):
                raise ProviderError(f"image outside the working folder (Read cannot reach it): {p}")
            paths.append(str(p))
        return (req.prompt + "\n\n<attached_images>\n"
                + "Use the Read tool to view each of these images (absolute paths) before answering:\n"
                + "\n".join(paths) + "\n</attached_images>")

    def parse_line(self, obj: dict, state: dict) -> list[Event]:
        kind = obj.get("type")
        out: list[Event] = []
        if kind == "system" and obj.get("subtype") == "init":
            state["model"] = obj.get("model")
            if obj.get("session_id"):
                out.append(Event("session", session_id=obj["session_id"], raw=obj))
        elif kind == "stream_event" and obj.get("parent_tool_use_id") is None:
            e = obj.get("event", {})
            if e.get("type") == "message_start" and state.get("text"):
                # A new assistant message after one that had text (a preamble before a tool call):
                # close the old one so the consumer starts over with the next delta.
                out.append(Event("text_final", text=state["text"]))
                state["text"] = ""
            elif e.get("type") == "content_block_delta" and e.get("delta", {}).get("type") == "text_delta":
                state["text"] = state.get("text", "") + e["delta"]["text"]
                out.append(Event("text_delta", text=e["delta"]["text"]))
        elif kind == "assistant" and obj.get("parent_tool_use_id") is None:
            for block in obj.get("message", {}).get("content", []):
                name = block.get("name", "")
                if block.get("type") == "tool_use" and (name in READ_ONLY_TOOLS or name.startswith("mcp__gloss__")):
                    out.append(Event("tool", name=name, data=block.get("input") or {}, raw=obj))
        elif kind == "result":
            u = obj.get("usage") or {}
            if u:
                out.append(Event("usage", data={
                    "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                    "cache_read_tokens": u.get("cache_read_input_tokens"),
                    "cache_write_tokens": u.get("cache_creation_input_tokens")}))
            if obj.get("is_error"):
                errors = " ".join(obj.get("errors") or [])
                text = obj.get("result") or errors
                out.append(Event("error", error_kind=self.classify_error(f"{text} {errors}", None, None,
                                                                            obj.get("api_error_status")),
                                 text=f"claude error ({obj.get('api_error_status') or obj.get('subtype')}): {text}",
                                 raw=obj))
            else:
                out.append(Event("done", text=obj.get("result") or "", session_id=obj.get("session_id"),
                                 model=state.get("model"), cost_usd=obj.get("total_cost_usd"),
                                 data=obj.get("structured_output"), raw=obj))
        return out

    def classify_error(self, text: str, returncode: int | None, req: RunRequest | None,
                       status: int | None = None) -> ErrorKind:
        # stream-json reports a missing session as a result with subtype error_during_execution and the
        # message in `errors`; it is also printed to stderr (§2.1).
        if "No conversation found" in text:
            return ErrorKind.SESSION_INVALID
        if status in (401, 403) or re.search(r"not logged in|/login|invalid api key|authentication", text, re.I):
            return ErrorKind.NOT_LOGGED_IN
        if status == 429 or re.search(r"usage limit|rate limit|limit reached|quota", text, re.I):
            return ErrorKind.QUOTA_EXCEEDED
        return ErrorKind.UNKNOWN
