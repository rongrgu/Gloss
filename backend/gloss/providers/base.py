"""LLM provider interface (DESIGN.md §6). Upper layers only see these types.

    LLMProvider (what tutor / quiz / digest call)
    ├── CLIProvider + CLIAdapter   -> claude -p / codex exec      (cli_runner.py, adapters/)
    ├── AnthropicAPIProvider       -> anthropic SDK, Messages API (anthropic_api.py)
    └── OpenAIAPIProvider          -> openai SDK, Responses API   (openai_api.py)

A provider turns one `RunRequest` into a stream of `Event`s. Failures are `error` events with an
`ErrorKind`; `Tutor` turns them into `ProviderError` / `SessionNotFound` for the job.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class CourseToolSpec:
    """Read-only course tools over one library folder (DESIGN.md §13.4)."""
    library: Path
    folder: str = ""
    only: tuple[str, ...] = ()  # lecture labels or course ids; empty = all


@dataclass
class RunRequest:
    prompt: str
    images: list[Path] = field(default_factory=list)
    session_id: str | None = None  # resume this provider session; None = a new one
    persist: bool = False  # keep the new session so later calls can resume it (Q&A); False = one-off
    system: str | None = None  # only used when a session starts (or for one-offs)
    cwd: Path | None = None  # the folder the model may read; inside the provider's workspace
    read_dirs: list[Path] = field(default_factory=list)  # extra readable folders (API providers inline instead)
    model: str | None = None  # per-call override (e.g. quiz_model); None = the provider's default
    output_schema: dict | None = None  # structured output: the final answer must match this JSON Schema
    tools: tuple[str, ...] = ("Read",)  # read-only file tools for CLI providers: Read / Grep / Glob
    course_tools: CourseToolSpec | None = None  # read-only course tools over MCP (§13.4)
    # The same context as ordered parts with cache breakpoints (context.py). API providers send these as
    # content blocks; CLI providers use `prompt` + `images`, which are these parts flattened.
    parts: list | None = None


EventKind = Literal["session", "text_delta", "text_final", "tool", "usage", "error", "done"]


class ErrorKind(Enum):
    NOT_INSTALLED = "not_installed"
    NOT_LOGGED_IN = "not_logged_in"
    QUOTA_EXCEEDED = "quota_exceeded"
    SESSION_INVALID = "session_invalid"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass
class Event:
    """One step of a provider's output.

    - session: `session_id` is the provider's id for this conversation (Claude session / Codex thread).
    - text_delta: a piece of the answer. A text_delta after a text_final starts a new message (the
      previous one was a preamble before a tool call); the consumer drops the old text.
    - text_final: a whole message. Providers without token streaming only send these.
    - tool: the model used a tool; `name`, and `data` = its input (for progress display).
    - usage: token counts in `data` (input_tokens, output_tokens, cache_read_tokens, …).
    - error: `error_kind` + `text` (a message for the user).
    - done: the final answer in `text`, structured output in `data`, `session_id`, `model`, `cost_usd`.
    """
    kind: EventKind
    text: str = ""
    session_id: str | None = None
    name: str | None = None
    data: Any = None
    model: str | None = None
    cost_usd: float | None = None
    error_kind: ErrorKind | None = None
    raw: dict | None = None  # the provider's original event, for debugging


@dataclass(frozen=True)
class Capabilities:
    supports_output_schema: bool  # the provider can enforce a JSON Schema on the answer
    supports_image_flag: bool  # images are attached directly (else: given as paths for the Read tool)
    supports_append_system_prompt: bool  # a system prompt can be added per call (else: an instructions file)
    streams_text_deltas: bool  # token-level deltas; else whole messages (text_final) only
    supports_tools: bool = True  # can read the course folder / use course tools; False for API providers
    supports_sessions: bool = True  # can resume a conversation; False: every call rebuilds the context


class ProviderError(Exception):
    def __init__(self, message: str, kind: ErrorKind = ErrorKind.UNKNOWN):
        super().__init__(message)
        self.kind = kind


class SessionNotFound(ProviderError):
    """Resume failed because the provider no longer has the session. Caller rebuilds it (§7.4)."""

    def __init__(self, message: str = "session not found"):
        super().__init__(message, ErrorKind.SESSION_INVALID)


class LLMProvider(Protocol):
    name: str  # "claude" | "codex" | "anthropic_api" | "openai_api"; stored with page sessions
    caps: Capabilities

    def run(self, req: RunRequest) -> AsyncIterator[Event]:
        """Stream one turn. The last event is `done` or `error`."""
        ...


@dataclass(frozen=True)
class SessionRef:
    """Kept for tests' fakes: how a call relates to a page session."""
    id: str
    new: bool
