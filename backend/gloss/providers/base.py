"""LLM provider interface (DESIGN.md §6). Upper layers only see these types."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class SessionRef:
    id: str
    new: bool  # True: create the session with this id; False: resume it


@dataclass
class TextDelta:
    text: str


@dataclass
class Reset:
    """Discard streamed text so far: a new assistant message started (e.g. after a tool call)."""


@dataclass
class ToolUse:
    """Claude called a tool (e.g. Read a page image, Grep the course). For progress display only."""
    name: str
    input: dict


@dataclass
class Done:
    text: str  # final answer
    session_id: str | None
    model: str | None = None
    cost_usd: float | None = None
    data: Any = None  # parsed structured output when a json_schema was given


Event = TextDelta | Reset | ToolUse | Done


@dataclass(frozen=True)
class CourseToolSpec:
    """Read-only course tools over one library folder (DESIGN.md §13.4)."""
    library: Path
    folder: str = ""
    only: tuple[str, ...] = ()  # lecture labels or course ids; empty = all


class ProviderError(Exception):
    pass


class SessionNotFound(ProviderError):
    """Resume failed because the provider no longer has the session. Caller rebuilds it (§7.4)."""


class LLMProvider(Protocol):
    def run(
        self,
        prompt: str,
        *,
        images: list[Path] = (),
        session: SessionRef | None = None,
        system: str | None = None,
        json_schema: dict | None = None,
        cwd: Path | None = None,
        tools: tuple[str, ...] = ("Read",),
        course_tools: CourseToolSpec | None = None,
        model: str | None = None,
    ) -> AsyncIterator[Event]:
        """Stream one turn. `system` only takes effect when the session is new (or session is None).
        With `json_schema`, the reply is validated against it and returned parsed in `Done.data`.
        `cwd` is the folder the model may read (and search, with Grep/Glob in `tools`); it must be
        inside the provider's workspace. Images must be inside it. Providers without tools ignore both.
        `course_tools` adds the read-only course tools (§13.4); providers without tools ignore it.
        `model` overrides the provider's model for this call (e.g. config `quiz_model`).

        The last event is always `Done`; failures raise `ProviderError`.
        """
        ...
