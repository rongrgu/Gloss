"""In-process fake provider for tutor/server tests."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from gloss.providers import Done, ProviderError, SessionNotFound, SessionRef, TextDelta


@dataclass
class Call:
    prompt: str
    images: list[Path]
    session: SessionRef | None
    system: str | None
    json_schema: dict | None = None


@dataclass
class FakeProvider:
    reply: str = "答案"
    forget: set[str] = field(default_factory=set)  # session ids that raise SessionNotFound on resume
    fail: bool = False
    gate: asyncio.Event | None = None  # if set, each call waits on it
    calls: list[Call] = field(default_factory=list)
    model: str = "fake-model"
    structured: list = field(default_factory=list)  # successive Done.data values for json_schema calls

    async def run(self, prompt, *, images=(), session=None, system=None, json_schema=None):
        self.calls.append(Call(prompt, list(images), session, system, json_schema))
        for p in images:
            assert Path(p).exists(), f"image not written: {p}"
        if session and not session.new and session.id in self.forget:
            raise SessionNotFound(session.id)
        if self.gate:
            await self.gate.wait()
        if self.fail:
            raise ProviderError("boom")
        if json_schema is not None:
            data = self.structured.pop(0)
            yield Done(json.dumps(data, ensure_ascii=False), None, self.model, 0.0, data)
            return
        half = len(self.reply) // 2
        yield TextDelta(self.reply[:half])
        yield TextDelta(self.reply[half:])
        yield Done(self.reply, session.id if session else None, self.model, 0.0)
