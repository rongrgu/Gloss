"""In-process fake provider for tutor/server tests."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import uuid

from gloss.providers import Capabilities, ErrorKind, Event, RunRequest, SessionRef


@dataclass
class Call:
    prompt: str
    images: list[Path]
    session: SessionRef | None
    system: str | None
    json_schema: dict | None = None
    cwd: Path | None = None
    tools: tuple = ("Read",)
    course_tools: object = None
    model: str | None = None


@dataclass
class FakeProvider:
    reply: str = "答案"
    forget: set[str] = field(default_factory=set)  # session ids that are SESSION_INVALID on resume
    fail: bool = False
    gate: asyncio.Event | None = None  # if set, each call waits on it
    calls: list[Call] = field(default_factory=list)
    model: str = "fake-model"
    structured: list = field(default_factory=list)  # successive Done.data values for output_schema calls
    tool_calls: list = field(default_factory=list)  # `tool` Events to emit before answering
    name: str = "fake"
    caps: Capabilities = Capabilities(True, False, True, True)

    async def run(self, req: RunRequest):
        # A view of the call in the old terms the tests use: SessionRef(id, new) or None for one-offs.
        new = req.session_id is None
        sid = req.session_id or (f"fake-{uuid.uuid4().hex[:12]}" if req.persist else None)
        session = SessionRef(sid, new) if req.persist or req.session_id else None
        self.calls.append(Call(req.prompt, list(req.images), session, req.system, req.output_schema, req.cwd,
                               tuple(req.tools), req.course_tools, req.model))
        if sid:
            yield Event("session", session_id=sid)
        for t in self.tool_calls:
            yield t
        for p in req.images:
            assert Path(p).exists(), f"image not written: {p}"
        if req.session_id and req.session_id in self.forget:
            yield Event("error", text=f"No conversation found with session ID: {req.session_id}",
                        error_kind=ErrorKind.SESSION_INVALID)
            return
        if self.gate:
            await self.gate.wait()
        if self.fail:
            yield Event("error", text="boom", error_kind=ErrorKind.UNKNOWN)
            return
        if req.output_schema is not None:
            data = self.structured.pop(0)
            yield Event("done", text=json.dumps(data, ensure_ascii=False), model=self.model, cost_usd=0.0, data=data)
            return
        half = len(self.reply) // 2
        yield Event("text_delta", text=self.reply[:half])
        yield Event("text_delta", text=self.reply[half:])
        yield Event("done", text=self.reply, session_id=sid, model=self.model, cost_usd=0.0)
