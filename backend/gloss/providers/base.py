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
class Done:
    text: str  # final answer
    session_id: str | None
    model: str | None = None
    cost_usd: float | None = None
    data: Any = None  # parsed structured output when a json_schema was given


Event = TextDelta | Reset | Done


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
    ) -> AsyncIterator[Event]:
        """Stream one turn. `system` only takes effect when the session is new (or session is None).
        With `json_schema`, the reply is validated against it and returned parsed in `Done.data`.

        The last event is always `Done`; failures raise `ProviderError`.
        """
        ...
