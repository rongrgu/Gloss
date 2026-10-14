"""`claude -p` subprocess provider. Flags and event shapes verified in DESIGN.md §2.1."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from collections.abc import AsyncIterator
from pathlib import Path

from .base import Done, Event, ProviderError, Reset, SessionNotFound, SessionRef, TextDelta

# Every call: only the Read tool exists, it is pre-approved, it cannot leave the
# workspace (--restricted), and nothing can stop to ask for permission.
BASE_FLAGS = [
    "-p", "--restricted",
    "--tools", "Read", "--allowedTools", "Read", "--permission-prompts", "none",
    "--output-format", "stream-json", "--verbose", "--include-partial-messages",
]
LINE_LIMIT = 64 * 1024 * 1024  # stream lines can carry base64 images from tool results


def find_claude() -> str | None:
    return shutil.which("claude") or shutil.which("claude.cmd")


def clean_env() -> dict[str, str]:
    """A set ANTHROPIC_API_KEY silently overrides the subscription login (§2.1)."""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    return env


class ClaudeCLIProvider:
    def __init__(self, workspace: Path, model: str | None = None, claude_bin: str | None = None,
                 timeout: float = 600):
        self.workspace = Path(workspace).resolve()
        self.model = model
        self.claude_bin = claude_bin
        self.timeout = timeout

    def build_args(self, *, session: SessionRef | None, system: str | None,
                   json_schema: dict | None = None) -> list[str]:
        bin_ = self.claude_bin or find_claude()
        if not bin_:
            raise ProviderError("claude CLI not found on PATH; install Claude Code and log in")
        args = [bin_, *BASE_FLAGS]
        if self.model:
            args += ["--model", self.model]
        if session is None:
            args.append("--no-session-persistence")
        elif session.new:
            args += ["--session-id", session.id]
        else:
            args += ["--resume", session.id]
        # The system prompt is snapshotted when a session starts; on resume it is ignored.
        if system and (session is None or session.new):
            args += ["--append-system-prompt", system]
        if json_schema is not None:
            args += ["--json-schema", json.dumps(json_schema)]
        return args

    def build_prompt(self, prompt: str, images: list[Path]) -> str:
        if not images:
            return prompt
        paths = []
        for img in images:
            p = Path(img).resolve()
            if not p.is_relative_to(self.workspace):
                raise ProviderError(f"image outside workspace (Read cannot reach it): {p}")
            paths.append(str(p))
        return (
            prompt
            + "\n\n<attached_images>\n"
            + "Use the Read tool to view each of these images (absolute paths) before answering:\n"
            + "\n".join(paths)
            + "\n</attached_images>"
        )

    async def run(
        self,
        prompt: str,
        *,
        images: list[Path] = (),
        session: SessionRef | None = None,
        system: str | None = None,
        json_schema: dict | None = None,
    ) -> AsyncIterator[Event]:
        args = self.build_args(session=session, system=system, json_schema=json_schema)
        stdin = self.build_prompt(prompt, list(images)).encode()
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=self.workspace, env=clean_env(), limit=LINE_LIMIT,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stderr_task = asyncio.create_task(proc.stderr.read())
        deadline = time.monotonic() + self.timeout
        try:
            proc.stdin.write(stdin)
            await proc.stdin.drain()
            proc.stdin.close()

            model = None
            emitted = False  # text streamed in the current assistant message
            result = None
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderError(f"claude timed out after {self.timeout:.0f}s")
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), remaining)
                except asyncio.TimeoutError:
                    raise ProviderError(f"claude timed out after {self.timeout:.0f}s") from None
                if not line:
                    break
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = ev.get("type")
                if kind == "system" and ev.get("subtype") == "init":
                    model = ev.get("model")
                elif kind == "stream_event" and ev.get("parent_tool_use_id") is None:
                    e = ev.get("event", {})
                    if e.get("type") == "message_start" and emitted:
                        emitted = False
                        yield Reset()
                    elif e.get("type") == "content_block_delta" and e.get("delta", {}).get("type") == "text_delta":
                        emitted = True
                        yield TextDelta(e["delta"]["text"])
                elif kind == "result":
                    result = ev

            rc = await proc.wait()
            stderr = (await stderr_task).decode(errors="replace")
            errors = " ".join((result or {}).get("errors") or [])
            # stream-json reports it as a result with subtype error_during_execution and
            # the message in `errors`; it is also printed to stderr.
            if session and not session.new and "No conversation found" in stderr + errors:
                raise SessionNotFound(session.id)
            if result is None:
                raise ProviderError(f"claude exited {rc} without a result: {_last_line(stderr)}")
            if result.get("is_error"):
                raise ProviderError(f"claude error ({result.get('api_error_status') or result.get('subtype')}): "
                                    f"{result.get('result') or errors or _last_line(stderr)}")
            yield Done(
                text=result.get("result") or "",
                session_id=result.get("session_id"),
                model=model,
                cost_usd=result.get("total_cost_usd"),
                data=result.get("structured_output"),
            )
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            if not stderr_task.done():
                stderr_task.cancel()


def _last_line(s: str) -> str:
    lines = [ln for ln in s.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else "(no output)"
