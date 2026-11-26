"""Contract tests against the real APIs (cost a little money). Off by default; need the key in the env:

    GLOSS_CONTRACT_API=anthropic ANTHROPIC_API_KEY=… uv run pytest tests/test_contract_api.py
    GLOSS_CONTRACT_API=openai    OPENAI_API_KEY=…    uv run pytest tests/test_contract_api.py

Streams a short prompt, sends a small image, and checks that a second request with the same long
prefix reads it from the cache (the prefix is well over the minimum cacheable length).
"""

import asyncio
import os

import pytest

from gloss.providers import RunRequest
from gloss.providers.context import Part

WANT = os.environ.get("GLOSS_CONTRACT_API", "")
PNG_1x1 = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                        "1f15c4890000000d49444154789c6360f8cfc0f01f0005000201e5274fa60000000049454e44ae426082")


def make(name):
    if name == "anthropic":
        from gloss.providers.anthropic_api import AnthropicAPIProvider
        return AnthropicAPIProvider("haiku")
    from gloss.providers.openai_api import OpenAIAPIProvider
    return OpenAIAPIProvider(None)


def collect(p, req):
    async def go():
        return [e async for e in p.run(req)]
    return asyncio.run(go())


@pytest.mark.skipif(not WANT, reason="set GLOSS_CONTRACT_API=anthropic|openai (and the key) to run")
def test_stream_image_and_cache(tmp_path):
    p = make(WANT)
    evs = collect(p, RunRequest("Reply with exactly: ok"))
    assert evs[-1].kind == "done", evs[-1]
    assert any(e.kind in ("text_delta", "text_final") for e in evs)
    img = tmp_path / "dot.png"
    img.write_bytes(PNG_1x1)
    assert collect(p, RunRequest("What color is this pixel? One word.", images=[img]))[-1].kind == "done"
    prefix = "Background for the questions below. " + " ".join(f"Fact {i}: the value of item {i} is {i * 7}." for i in range(400))
    usages = []
    for q in ("What is the value of item 3? Digits only.", "What is the value of item 5? Digits only."):
        evs = collect(p, RunRequest(q, system="Answer from the background.",
                                    parts=[Part(text=prefix, cache=True), Part(text=q)]))
        assert evs[-1].kind == "done", evs[-1]
        usages.append(next(e.data for e in evs if e.kind == "usage"))
    print("usage:", usages)
    assert (usages[1]["cache_read_tokens"] or 0) > 0, usages
