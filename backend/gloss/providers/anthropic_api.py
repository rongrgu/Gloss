"""Anthropic API provider (DESIGN.md §6): bills an API key instead of the Claude subscription.

Install with `pip install 'glossr[api]'` and set `provider = "anthropic_api"` in config.toml.
The key is read from ANTHROPIC_API_KEY.

Differences from the CLI provider:
- No server-side sessions. A resume always raises SessionNotFound, so the tutor rebuilds the
  page context from the .gloss file on every follow-up (the stored history *is* the session).
  The system prompt and the page image carry cache breakpoints, so the repeated prefix is cheap.
- No tools: Claude can't search the rest of the course (`supports_tools = False`).
- Structured output uses `output_config.format` (json_schema). Forced tool use is rejected by
  the current Opus / Sonnet models.
- Refusal fallbacks are on for the models that support them (server-side, `fallbacks: "default"`).
"""

from __future__ import annotations

import base64
import copy
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from . import context
from .base import Capabilities, ErrorKind, Event, ProviderError, RunRequest
from .context import Part

# Config aliases -> current model IDs (claude-api reference, 2026-10).
MODEL_ALIASES = {
    "haiku": "claude-haiku-5-5",
    "sonnet": "claude-sonnet-5-5",
    "opus": "claude-opus-5-5",
    "fable": "claude-fable-5-1",
}
# Models that accept the server-side `fallbacks: "default"` form on the Claude API.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-opus-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_BREAKPOINTS = 4  # cache_control markers per request, the system prompt's included
MAX_TOKENS = 64000  # streaming, so a large cap doesn't risk HTTP timeouts
MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def resolve_model(name: str) -> str:
    return MODEL_ALIASES.get(name.strip().lower(), name.strip())


def strict_schema(schema: dict) -> dict:
    """Structured outputs need `additionalProperties: false` on every object."""
    s = copy.deepcopy(schema)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                node.setdefault("additionalProperties", False)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(s)
    return s


class AnthropicAPIProvider:
    name = "anthropic_api"
    caps = Capabilities(supports_output_schema=True, supports_image_flag=True, supports_append_system_prompt=True,
                        streams_text_deltas=True, supports_tools=False, supports_sessions=False)
    supports_tools = False  # no Read/Grep: whole-course retrieval is unavailable

    def __init__(self, model: str, api_key: str | None = None, timeout: float = 600, client: Any = None):
        self.model = resolve_model(model)
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise ProviderError("the API provider needs the anthropic SDK: pip install 'glossr[api]'") from e
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise ProviderError("provider = \"anthropic_api\" needs ANTHROPIC_API_KEY to be set")
            client = anthropic.AsyncAnthropic(api_key=key, timeout=timeout)
        self.client = client

    def build_request(self, parts: list[Part], system: str | None, json_schema: dict | None) -> dict[str, Any]:
        """One user message from the context parts (context.py), in order. Cache breakpoints: the system
        prompt, then the parts marked `cache` (end of the opening, end of the earlier Q&A) - at most
        MAX_BREAKPOINTS in all. Each earlier message is its own block, so the next question's request
        starts with the same blocks and reads them from the cache."""
        content: list[dict[str, Any]] = []
        for part in parts:
            if part.image is not None:
                p = Path(part.image)
                media = MEDIA_TYPES.get(p.suffix.lower())
                if media is None:
                    raise ProviderError(f"unsupported image type: {p.name}")
                block: dict[str, Any] = {"type": "image", "source": {
                    "type": "base64", "media_type": media, "data": base64.standard_b64encode(p.read_bytes()).decode()}}
            else:
                block = {"type": "text", "text": part.text or ""}
            if part.cache:
                block["cache_control"] = {"type": "ephemeral"}
            content.append(block)
        marked = [b for b in content if "cache_control" in b]
        for b in marked[:max(0, len(marked) - (MAX_BREAKPOINTS - (1 if system else 0)))]:
            del b["cache_control"]  # keep the last ones: they cover everything before them
        req: dict[str, Any] = {"model": self.model, "max_tokens": MAX_TOKENS,
                               "messages": [{"role": "user", "content": content}]}
        if system:
            req["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if json_schema is not None:
            req["output_config"] = {"format": {"type": "json_schema", "schema": strict_schema(json_schema)}}
        if self.model in FALLBACK_MODELS:
            req["betas"] = [FALLBACK_BETA]
            req["fallbacks"] = "default"
        return req

    async def run(self, req: RunRequest) -> AsyncIterator[Event]:
        if req.session_id is not None:
            # No server-side sessions: the caller rebuilds the context from the stored history (§7.4).
            yield Event("error", text="the API has no sessions", error_kind=ErrorKind.SESSION_INVALID)
            return
        json_schema = req.output_schema
        parts = req.parts if req.parts is not None else context.text_only(req.prompt, req.images)
        try:
            body = self.build_request(parts, req.system, json_schema)
        except ProviderError as e:
            yield Event("error", text=str(e), error_kind=e.kind)
            return
        if req.model:
            body["model"] = resolve_model(req.model)
        messages = self.client.beta.messages if "betas" in body else self.client.messages
        import anthropic

        def fail(text: str, kind: ErrorKind = ErrorKind.UNKNOWN) -> Event:
            return Event("error", text=text, error_kind=kind)

        try:
            async with messages.stream(**body) as stream:
                streamed = ""
                async for event in stream:
                    if event.type == "content_block_start" and getattr(event.content_block, "type", "") == "fallback":
                        if streamed:  # a declined model's partial text; the fallback model starts over
                            yield Event("text_final", text=streamed)
                            streamed = ""
                    elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                        streamed += event.delta.text
                        yield Event("text_delta", text=event.delta.text)
                final = await stream.get_final_message()
        except anthropic.AuthenticationError:
            yield fail("invalid API key (401): check ANTHROPIC_API_KEY", ErrorKind.NOT_LOGGED_IN)
            return
        except anthropic.PermissionDeniedError as e:
            yield fail(f"API key not permitted (403): {e.message}", ErrorKind.NOT_LOGGED_IN)
            return
        except anthropic.RateLimitError:
            yield fail("rate limited by the API (429); try again shortly", ErrorKind.QUOTA_EXCEEDED)
            return
        except anthropic.APIStatusError as e:
            if e.status_code == 529 or e.status_code >= 500:
                yield fail(f"Anthropic API temporarily unavailable ({e.status_code}); try again shortly")
            else:
                yield fail(f"API error ({e.status_code}): {e.message}")
            return
        except anthropic.APIConnectionError:
            yield fail("cannot reach the Anthropic API; check your network")
            return

        if final.stop_reason == "refusal":
            details = getattr(final, "stop_details", None)
            yield fail(f"Claude declined this request ({getattr(details, 'category', None) or 'refusal'})")
            return
        u = getattr(final, "usage", None)  # the cache fields show whether the stable prefix is being reused (DESIGN.md §6)
        usage = {"input_tokens": getattr(u, "input_tokens", None), "output_tokens": getattr(u, "output_tokens", None),
                 "cache_read_tokens": getattr(u, "cache_read_input_tokens", None),
                 "cache_write_tokens": getattr(u, "cache_creation_input_tokens", None)} if u is not None else {}
        if usage:
            yield Event("usage", data=usage)
        blocks = list(final.content)
        last_fallback = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=-1)
        text = "".join(b.text for b in blocks[last_fallback + 1:] if b.type == "text")
        data = None
        if json_schema is not None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                yield fail(f"structured output was not valid JSON: {text[:200]}")
                return
        yield Event("done", text=text, model=final.model, data=data)
