"""`codex exec` adapter (OpenAI Codex CLI). DESIGN.md §6.

NOT YET RUN AGAINST A REAL CODEX: Codex was not installed on the development machine. Flags and event
shapes follow codex 0.172.0's source (codex-rs/exec/src/cli.rs, exec_events.rs) and official docs; the
open questions are listed in DESIGN.md §6 and checked by tests/test_contract_cli.py when it is enabled.

- Read-only always: `--sandbox read-only`. Gloss never lets a model write files (§2); this is never widened.
- Prompt on stdin (`-`); JSONL on stdout (`--json`); whole messages only (no token deltas).
- Sessions are Codex threads: `thread.started.thread_id`, resumed with `codex exec … resume <id> -`.
- Images with `-i`; structured output with `--output-schema <file>` (strict form, see providers/schemas.py).
- Extra instructions with `-c developer_instructions=<TOML string>`, so concurrent calls in one course
  folder don't race on a shared AGENTS.md.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path

from ..base import Capabilities, ErrorKind, Event, RunRequest
from ..cli_runner import Prepared
from ..schemas import from_strict, parse_json, to_strict


def find_codex() -> str | None:
    return shutil.which("codex") or shutil.which("codex.cmd")


def toml_string(s: str) -> str:
    """A TOML basic string. JSON string escapes are a subset TOML accepts."""
    return json.dumps(s, ensure_ascii=False)


class CodexAdapter:
    name = "codex"
    caps = Capabilities(supports_output_schema=True, supports_image_flag=True, supports_append_system_prompt=True,
                        streams_text_deltas=False)

    def __init__(self, codex_bin: str | None = None, use_api_key: bool = False):
        self.codex_bin = codex_bin
        self.use_api_key = use_api_key  # config chose API-key auth: keep CODEX_API_KEY in the environment

    def find(self) -> str | None:
        return self.codex_bin or find_codex()

    def env(self) -> dict[str, str]:
        """Default: the CLI's own saved login. An API key in the environment would switch billing silently."""
        env = dict(os.environ)
        if not self.use_api_key:
            env.pop("OPENAI_API_KEY", None)
            env.pop("CODEX_API_KEY", None)
        return env

    def runtime_instructions_filename(self) -> str:
        return "AGENTS.md"

    def build_cmd(self, req: RunRequest, exe: str, cwd: Path, schema_file: Path | None = None) -> list[str]:
        # Options before `resume`: -C / --sandbox only take effect there, and the rest are global flags.
        args = [exe, "exec", "--json", "--color", "never", "--sandbox", "read-only", "--skip-git-repo-check",
                "-C", str(cwd)]
        if req.model:
            args += ["-m", req.model]
        for img in req.images:
            args += ["-i", str(Path(img).resolve())]
        if schema_file is not None:
            args += ["--output-schema", str(schema_file)]
        if req.system and not req.session_id:  # like Claude: instructions are fixed when a session starts
            args += ["-c", f"developer_instructions={toml_string(req.system)}"]
        if req.course_tools is not None:  # the read-only course tools (§13.4) as an MCP server
            from ...mcp_server import SERVER_NAME, config
            srv = config(req.course_tools.library, req.course_tools.folder, list(req.course_tools.only))["mcpServers"][SERVER_NAME]
            args += ["-c", f"mcp_servers.{SERVER_NAME}.command={toml_string(srv['command'])}",
                     "-c", f"mcp_servers.{SERVER_NAME}.args={json.dumps(srv['args'])}"]
        if req.session_id:
            args += ["resume", req.session_id, "-"]
        else:
            if not req.persist:
                args.append("--ephemeral")
            args.append("-")
        return args

    def prepare(self, req: RunRequest, exe: str, cwd: Path) -> Prepared:
        schema_file = None
        if req.output_schema is not None:
            schema_file = cwd / f".schema-{uuid.uuid4().hex[:8]}.json"
            schema_file.write_text(json.dumps(to_strict(req.output_schema)))
        return Prepared(self.build_cmd(req, exe, cwd, schema_file), req.prompt,
                        cleanup=[schema_file] if schema_file else [])

    def parse_line(self, obj: dict, state: dict) -> list[Event]:
        t = obj.get("type")
        req: RunRequest | None = state.get("req")
        if t == "thread.started":
            state["thread_id"] = obj.get("thread_id")
            return [Event("session", session_id=obj.get("thread_id"), raw=obj)]
        if t in ("item.started", "item.completed"):
            item = obj.get("item") or {}
            it = item.get("type")
            if t == "item.completed" and it == "agent_message":
                state["text"] = item.get("text") or ""
                return [Event("text_final", text=state["text"], raw=obj)]
            if t == "item.started" and it == "command_execution":
                return [Event("tool", name="command", data={"command": item.get("command")}, raw=obj)]
            if t == "item.started" and it == "mcp_tool_call":
                args = item.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                return [Event("tool", name=f"mcp__{item.get('server')}__{item.get('tool')}", data=args or {}, raw=obj)]
            return []
        if t == "turn.completed":
            u = obj.get("usage") or {}
            out = [Event("usage", data={"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                                        "cache_read_tokens": u.get("cached_input_tokens"),
                                        "cache_write_tokens": u.get("cache_write_input_tokens"),
                                        "reasoning_tokens": u.get("reasoning_output_tokens")}, raw=obj)]
            text = state.get("text", "")
            data = None
            if req is not None and req.output_schema is not None:
                try:
                    data = from_strict(parse_json(text), req.output_schema)
                except ValueError:
                    return [*out, Event("error", text=f"codex: structured output was not valid JSON: {text[:200]}",
                                        error_kind=ErrorKind.UNKNOWN, raw=obj)]
            out.append(Event("done", text=text, session_id=state.get("thread_id"), model=req.model if req else None,
                             data=data, raw=obj))
            return out
        if t in ("turn.failed", "error"):
            msg = (obj.get("error") or {}).get("message") if t == "turn.failed" else obj.get("message")
            msg = msg or json.dumps(obj)[:300]
            return [Event("error", text=f"codex error: {msg}", error_kind=self.classify_error(msg, None, req), raw=obj)]
        return []

    def classify_error(self, text: str, returncode: int | None, req: RunRequest | None) -> ErrorKind:
        """Keyword guesses: Codex's exact messages are not documented (DESIGN.md §6, to verify)."""
        if req is not None and req.session_id and re.search(r"(session|thread|rollout|conversation)[^\n]{0,40}"
                                                            r"(not found|no such|does not exist|missing)"
                                                            r"|no (saved )?(session|rollout|conversation)",
                                                            text, re.I):
            return ErrorKind.SESSION_INVALID
        if re.search(r"not logged in|codex login|unauthori[sz]ed|\b401\b|invalid api key|authentication", text, re.I):
            return ErrorKind.NOT_LOGGED_IN
        if re.search(r"usage limit|rate limit|\b429\b|quota|insufficient", text, re.I):
            return ErrorKind.QUOTA_EXCEEDED
        return ErrorKind.UNKNOWN

