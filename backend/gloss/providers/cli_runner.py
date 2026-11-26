"""CLIProvider: one subprocess runner shared by every CLI adapter (claude -p, codex exec).

The adapter decides the command, the environment, how stdout JSONL lines become `Event`s, and how
a failure is classified. The runner owns the process: stdin, line reading, stderr, the timeout, and
killing the child on timeout or cancellation (no zombies).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol

from .base import Capabilities, ErrorKind, Event, ProviderError, RunRequest

log = logging.getLogger(__name__)
LINE_LIMIT = 64 * 1024 * 1024  # stream lines can carry base64 images from tool results


class Prepared:
    """What an adapter needs to run one request: the argv, stdin text, and files to delete afterwards."""

    def __init__(self, cmd: list[str], stdin: str, cleanup: list[Path] | None = None):
        self.cmd, self.stdin, self.cleanup = cmd, stdin, cleanup or []


class CLIAdapter(Protocol):
    name: str
    caps: Capabilities

    def find(self) -> str | None: ...  # the executable, or None if not installed
    def prepare(self, req: RunRequest, exe: str, cwd: Path) -> Prepared: ...  # build_cmd + stdin
    def parse_line(self, obj: dict, state: dict) -> list[Event]: ...  # one JSONL object -> 0..n events
    def env(self) -> dict[str, str]: ...
    def runtime_instructions_filename(self) -> str: ...  # "CLAUDE.md" / "AGENTS.md"
    def classify_error(self, text: str, returncode: int | None, req: RunRequest) -> ErrorKind: ...


class CLIProvider:
    """An `LLMProvider` that runs a CLI through an adapter."""

    def __init__(self, adapter: CLIAdapter, workspace: Path, model: str | None = None, timeout: float = 600):
        self.adapter = adapter
        self.name = adapter.name
        self.caps = adapter.caps
        self.workspace = Path(workspace).resolve()
        self.model = model
        self.timeout = timeout

    @property
    def supports_tools(self) -> bool:  # older call sites ask this
        return self.caps.supports_tools

    def resolve_cwd(self, cwd: Path | None) -> Path:
        d = Path(cwd).resolve() if cwd else self.workspace
        if not d.is_relative_to(self.workspace):
            raise ProviderError(f"cwd outside workspace: {d}")
        return d

    async def run(self, req: RunRequest) -> AsyncIterator[Event]:
        exe = self.adapter.find()
        if not exe:
            yield Event("error", text=f"{self.name} CLI not found on PATH; install it and log in",
                        error_kind=ErrorKind.NOT_INSTALLED)
            return
        if req.model is None and self.model:
            req.model = self.model
        try:
            workdir = self.resolve_cwd(req.cwd)
            workdir.mkdir(parents=True, exist_ok=True)
            prep = self.adapter.prepare(req, exe, workdir)
        except ProviderError as e:
            yield Event("error", text=str(e), error_kind=e.kind)
            return
        proc = await asyncio.create_subprocess_exec(
            *prep.cmd, cwd=workdir, env=self.adapter.env(), limit=LINE_LIMIT,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stderr_task = asyncio.create_task(proc.stderr.read())
        deadline = time.monotonic() + self.timeout
        state: dict = {"req": req}  # adapters may read the request while parsing (e.g. its output schema)
        finished = False  # a done or error event was produced
        try:
            proc.stdin.write(prep.stdin.encode())
            await proc.stdin.drain()
            proc.stdin.close()
            while True:
                remaining = deadline - time.monotonic()
                try:
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    line = await asyncio.wait_for(proc.stdout.readline(), remaining)
                except asyncio.TimeoutError:
                    yield Event("error", text=f"{self.name} timed out after {self.timeout:.0f}s",
                                error_kind=ErrorKind.TIMEOUT)
                    return
                if not line:
                    break
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    log.debug("%s: non-JSON line skipped: %.200s", self.name, line)
                    continue
                if not isinstance(obj, dict):
                    continue
                for ev in self.adapter.parse_line(obj, state):
                    if ev.kind in ("done", "error"):
                        finished = True
                    yield ev
            rc = await proc.wait()
            stderr = (await stderr_task).decode(errors="replace")
            if not finished:
                kind = self.adapter.classify_error(stderr, rc, req)
                yield Event("error", text=f"{self.name} exited {rc} without a result: {_last_line(stderr)}",
                            error_kind=kind)
        finally:
            if proc.returncode is None:  # timeout, cancellation, or the consumer stopped early
                proc.kill()
                await proc.wait()
            if not stderr_task.done():
                stderr_task.cancel()
            for p in prep.cleanup:
                p.unlink(missing_ok=True)


def _last_line(s: str) -> str:
    lines = [ln for ln in s.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else "(no output)"
