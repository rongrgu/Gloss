"""OpenAI API provider (DESIGN.md §6): the official `openai` SDK, Responses API, billed to OPENAI_API_KEY.

Install with `pip install 'glossr[openai]'` and set `provider = "openai_api"`. Names below were checked
against the installed SDK (openai 3.59.0): `client.responses.stream(...)`, `input_text` / `input_image`
parts, `prompt_cache_breakpoint: {"mode": "explicit"}`, `text.format` json_schema, events
`response.output_text.delta` / `response.completed` / `response.failed` / `response.incomplete` / `error`,
`usage.input_tokens_details.cached_tokens` / `cache_write_tokens`.

Like the Anthropic API provider: no sessions (every Q&A turn sends the page's stored history, built by
context.py with a stable prefix), no tools, structured output via a strict JSON schema.
"""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from . import context
from .base import Capabilities, ErrorKind, Event, ProviderError, RunRequest
from .context import Part
from .schemas import from_strict, parse_json, to_strict

DEFAULT_MODEL = "gpt-5.5"  # used when config leaves the model empty (or names a Claude alias); see README
CLAUDE_ALIASES = {"haiku", "sonnet", "opus", "fable"}
MAX_OUTPUT_TOKENS = 64000
MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def resolve_model(name: str | None) -> str:
    n = (name or "").strip()
    return DEFAULT_MODEL if not n or n.lower() in CLAUDE_ALIASES else n


class OpenAIAPIProvider:
    name = "openai_api"
    caps = Capabilities(supports_output_schema=True, supports_image_flag=True, supports_append_system_prompt=True,
                        streams_text_deltas=True, supports_tools=False, supports_sessions=False)
    supports_tools = False

    def __init__(self, model: str | None, api_key: str | None = None, timeout: float = 600, client: Any = None):
        self.model = resolve_model(model)
        if client is None:
            try:
                import openai
            except ImportError as e:
                raise ProviderError("the OpenAI API provider needs the openai SDK: pip install 'glossr[openai]'") from e
            key = api_key or os.environ.get("OPENAI_API_KEY")
            if not key:
                raise ProviderError('provider = "openai_api" needs OPENAI_API_KEY to be set', ErrorKind.NOT_LOGGED_IN)
            client = openai.AsyncOpenAI(api_key=key, timeout=timeout)
        self.client = client

    def build_request(self, parts: list[Part], system: str | None, json_schema: dict | None,
                      model: str | None = None) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        for part in parts:
            if part.image is not None:
                p = Path(part.image)
                media = MEDIA_TYPES.get(p.suffix.lower())
                if media is None:
                    raise ProviderError(f"unsupported image type: {p.name}")
                data = base64.standard_b64encode(p.read_bytes()).decode()
                block: dict[str, Any] = {"type": "input_image", "detail": "auto",
                                         "image_url": f"data:{media};base64,{data}"}
            else:
                block = {"type": "input_text", "text": part.text or ""}
            if part.cache:  # the end of a stable prefix (context.py); OpenAI also caches identical prefixes itself
                block["prompt_cache_breakpoint"] = {"mode": "explicit"}
            content.append(block)
        req: dict[str, Any] = {"model": resolve_model(model) if model else self.model,
                               "input": [{"role": "user", "content": content}],
                               "max_output_tokens": MAX_OUTPUT_TOKENS,
                               "store": False}  # nothing about the student's course is kept server-side
        if system:
            req["instructions"] = system
        if json_schema is not None:
            req["text"] = {"format": {"type": "json_schema", "name": "gloss_output", "strict": True,
                                      "schema": to_strict(json_schema)}}
        return req

    async def run(self, req: RunRequest) -> AsyncIterator[Event]:
        if req.session_id is not None:
            yield Event("error", text="the API has no sessions", error_kind=ErrorKind.SESSION_INVALID)
            return
        parts = req.parts if req.parts is not None else context.text_only(req.prompt, req.images)
        try:
            body = self.build_request(parts, req.system, req.output_schema, req.model)
        except ProviderError as e:
            yield Event("error", text=str(e), error_kind=e.kind)
            return
        import openai

        def fail(text: str, kind: ErrorKind = ErrorKind.UNKNOWN) -> Event:
            return Event("error", text=text, error_kind=kind)

        final = None
        try:
            async with self.client.responses.stream(**body) as stream:
                async for ev in stream:
                    t = getattr(ev, "type", "")
                    if t == "response.output_text.delta":
                        yield Event("text_delta", text=ev.delta)
                    elif t == "error":
                        yield fail(f"OpenAI API error: {getattr(ev, 'message', '')}")
                        return
                final = await stream.get_final_response()
        except openai.AuthenticationError:
            yield fail("invalid API key (401): check OPENAI_API_KEY", ErrorKind.NOT_LOGGED_IN)
            return
        except openai.PermissionDeniedError as e:
            yield fail(f"API key not permitted (403): {e.message}", ErrorKind.NOT_LOGGED_IN)
            return
        except openai.RateLimitError as e:
            if getattr(e, "code", None) == "insufficient_quota":
                yield fail("the OpenAI account is out of credit (insufficient_quota)", ErrorKind.QUOTA_EXCEEDED)
            else:
                yield fail("rate limited by the API (429); try again shortly", ErrorKind.QUOTA_EXCEEDED)
            return
        except openai.APIStatusError as e:
            if e.status_code >= 500:
                yield fail(f"OpenAI API temporarily unavailable ({e.status_code}); try again shortly")
            else:
                yield fail(f"API error ({e.status_code}): {e.message}")
            return
        except openai.APIConnectionError:
            yield fail("cannot reach the OpenAI API; check your network")
            return

        u = getattr(final, "usage", None)
        if u is not None:
            details = getattr(u, "input_tokens_details", None)
            yield Event("usage", data={"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                                       "cache_read_tokens": getattr(details, "cached_tokens", None),
                                       "cache_write_tokens": getattr(details, "cache_write_tokens", None)})
        status = getattr(final, "status", None)
        if status in ("failed", "incomplete"):
            why = getattr(getattr(final, "error", None), "message", None) or \
                getattr(getattr(final, "incomplete_details", None), "reason", None) or status
            yield fail(f"OpenAI response {status}: {why}")
            return
        text = final.output_text
        data = None
        if req.output_schema is not None:
            try:
                data = from_strict(parse_json(text), req.output_schema)
            except ValueError:
                yield fail(f"structured output was not valid JSON: {text[:200]}")
                return
        yield Event("done", text=text, model=getattr(final, "model", None), data=data)
