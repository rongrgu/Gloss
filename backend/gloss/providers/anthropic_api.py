"""Anthropic API provider (DESIGN.md §6): bills an API key instead of the Claude subscription.

Install with `pip install 'gloss[api]'` and set `provider = "anthropic_api"` in config.toml.
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

from .base import Done, Event, ProviderError, Reset, SessionNotFound, SessionRef, TextDelta

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
    supports_tools = False  # no Read/Grep: whole-course retrieval is unavailable

    def __init__(self, model: str, api_key: str | None = None, timeout: float = 600, client: Any = None):
        self.model = resolve_model(model)
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise ProviderError("the API provider needs the anthropic SDK: pip install 'gloss[api]'") from e
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise ProviderError("provider = \"anthropic_api\" needs ANTHROPIC_API_KEY to be set")
            client = anthropic.AsyncAnthropic(api_key=key, timeout=timeout)
        self.client = client

    def build_request(self, prompt: str, images: list[Path], system: str | None,
                      json_schema: dict | None) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        for i, img in enumerate(images):
            p = Path(img)
            media = MEDIA_TYPES.get(p.suffix.lower())
            if media is None:
                raise ProviderError(f"unsupported image type: {p.name}")
            block: dict[str, Any] = {"type": "image", "source": {
                "type": "base64", "media_type": media, "data": base64.standard_b64encode(p.read_bytes()).decode()}}
            if i == 0:
                # The first image is the page itself and is identical on every turn for this page:
                # cache through it (system prompt + page image).
                block["cache_control"] = {"type": "ephemeral"}
            content.append(block)
        content.append({"type": "text", "text": prompt})
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

    async def run(
        self,
        prompt: str,
        *,
        images: list[Path] = (),
        session: SessionRef | None = None,
        system: str | None = None,
        json_schema: dict | None = None,
        cwd: Path | None = None,  # noqa: ARG002 — no tools
        tools: tuple[str, ...] = ("Read",),  # noqa: ARG002
    ) -> AsyncIterator[Event]:
        if session is not None and not session.new:
            raise SessionNotFound(session.id)  # the caller rebuilds from stored history
        req = self.build_request(prompt, list(images), system, json_schema)
        messages = self.client.beta.messages if "betas" in req else self.client.messages
        import anthropic

        try:
            async with messages.stream(**req) as stream:
                emitted = False
                async for event in stream:
                    if event.type == "content_block_start" and getattr(event.content_block, "type", "") == "fallback":
                        if emitted:  # a declined model's partial text; the fallback model starts over
                            emitted = False
                            yield Reset()
                    elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                        emitted = True
                        yield TextDelta(event.delta.text)
                final = await stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise ProviderError("API key 无效（401）。检查 ANTHROPIC_API_KEY。") from e
        except anthropic.PermissionDeniedError as e:
            raise ProviderError(f"API key 没有权限（403）：{e.message}") from e
        except anthropic.RateLimitError as e:
            raise ProviderError("API 限流（429），请稍后重试。") from e
        except anthropic.APIStatusError as e:
            if e.status_code == 529 or e.status_code >= 500:
                raise ProviderError(f"Anthropic 服务暂时不可用（{e.status_code}），请稍后重试。") from e
            raise ProviderError(f"API 错误（{e.status_code}）：{e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ProviderError("连不上 Anthropic API，请检查网络。") from e

        if final.stop_reason == "refusal":
            details = getattr(final, "stop_details", None)
            raise ProviderError(f"Claude 拒绝回答这个请求（{getattr(details, 'category', None) or 'refusal'}）。")
        blocks = list(final.content)
        last_fallback = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=-1)
        text = "".join(b.text for b in blocks[last_fallback + 1:] if b.type == "text")
        data = None
        if json_schema is not None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                raise ProviderError(f"structured output was not valid JSON: {text[:200]}") from e
        yield Done(text=text, session_id=session.id if session else None, model=final.model, data=data)
