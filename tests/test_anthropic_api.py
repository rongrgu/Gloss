"""AnthropicAPIProvider against a fake SDK client (no network, no key)."""

import asyncio
import types

import anthropic
import httpx2
import pytest

from gloss.providers import Done, ProviderError, Reset, SessionNotFound, SessionRef, TextDelta
from gloss.providers.anthropic_api import AnthropicAPIProvider, resolve_model, strict_schema


def ev(type_, **kw):
    return types.SimpleNamespace(type=type_, **kw)


def delta(text):
    return ev("content_block_delta", delta=types.SimpleNamespace(type="text_delta", text=text))


def block(type_, **kw):
    return types.SimpleNamespace(type=type_, **kw)


class FakeStream:
    def __init__(self, events, final, error=None):
        self.events, self.final, self.error = events, final, error

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for e in self.events:
                yield e
        return gen()

    async def get_final_message(self):
        return self.final


class FakeMessages:
    def __init__(self, owner):
        self.owner = owner

    def stream(self, **req):
        self.owner.requests.append((self is self.owner.beta.messages, req))
        return self.owner.next


class FakeClient:
    def __init__(self):
        self.requests = []
        self.messages = FakeMessages(self)
        self.beta = types.SimpleNamespace()
        self.beta.messages = FakeMessages(self)
        self.next = None


def final(*blocks, stop="end_turn", model="claude-sonnet-5-5"):
    return types.SimpleNamespace(content=list(blocks), stop_reason=stop, model=model, stop_details=None)


def collect(provider, prompt="hi", **kw):
    async def go():
        return [e async for e in provider.run(prompt, **kw)]
    return asyncio.run(go())


@pytest.fixture
def api():
    client = FakeClient()
    return AnthropicAPIProvider("sonnet", client=client), client


def test_aliases():
    assert resolve_model("Sonnet") == "claude-sonnet-5-5"
    assert resolve_model("haiku") == "claude-haiku-5-5"
    assert resolve_model("claude-opus-5") == "claude-opus-5"


def test_stream_images_caching_and_fallbacks(api, tmp_path):
    p, client = api
    img = tmp_path / "page-2.png"
    img.write_bytes(b"\x89PNG fake")
    crop = tmp_path / "crop-x.png"
    crop.write_bytes(b"\x89PNG crop")
    client.next = FakeStream([delta("你"), delta("好")], final(block("text", text="你好")))
    evs = collect(p, "问题", images=[img, crop], session=SessionRef("s1", new=True), system="助教")
    assert evs == [TextDelta("你"), TextDelta("好"), Done("你好", "s1", "claude-sonnet-5-5", None, None)]
    used_beta, req = client.requests[0]
    assert used_beta and req["betas"] == ["server-side-fallback-2026-07-01"] and req["fallbacks"] == "default"
    assert req["model"] == "claude-sonnet-5-5" and req["max_tokens"] == 64000
    assert req["system"] == [{"type": "text", "text": "助教", "cache_control": {"type": "ephemeral"}}]
    content = req["messages"][0]["content"]
    assert [b["type"] for b in content] == ["image", "image", "text"]
    assert content[0]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in content[1]
    assert content[0]["source"]["media_type"] == "image/png" and content[2]["text"] == "问题"


def test_haiku_has_no_fallbacks():
    client = FakeClient()
    p = AnthropicAPIProvider("haiku", client=client)
    client.next = FakeStream([], final(block("text", text="ok"), model="claude-haiku-5-5"))
    collect(p)
    used_beta, req = client.requests[0]
    assert not used_beta and "fallbacks" not in req and "betas" not in req


def test_resume_always_rebuilds(api):
    p, _ = api
    with pytest.raises(SessionNotFound):
        collect(p, session=SessionRef("old", new=False))


def test_structured_output(api):
    p, client = api
    schema = {"type": "object", "properties": {"slides": {"type": "array", "items": {
        "type": "object", "properties": {"page": {"type": "integer"}}}}}}
    client.next = FakeStream([], final(block("text", text='{"slides": [{"page": 1}]}')))
    evs = collect(p, json_schema=schema)
    assert evs[-1].data == {"slides": [{"page": 1}]}
    fmt = client.requests[0][1]["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["properties"]["slides"]["items"]["additionalProperties"] is False
    assert "additionalProperties" not in schema  # caller's schema untouched
    assert strict_schema({"type": "string"}) == {"type": "string"}


def test_fallback_mid_stream_resets_and_keeps_final_text(api):
    p, client = api
    events = [delta("部分"), ev("content_block_start", content_block=block("fallback")), delta("完整回答")]
    client.next = FakeStream(events, final(block("text", text="部分"), block("fallback"), block("text", text="完整回答")))
    evs = collect(p)
    assert evs == [TextDelta("部分"), Reset(), TextDelta("完整回答"), Done("完整回答", None, "claude-sonnet-5-5", None, None)]


def test_refusal(api):
    p, client = api
    f = final(stop="refusal")
    f.stop_details = types.SimpleNamespace(category="cyber")
    client.next = FakeStream([], f)
    with pytest.raises(ProviderError, match="拒绝.*cyber"):
        collect(p)


def _status_error(cls, status):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(status, request=req), body=None)


@pytest.mark.parametrize("err, msg", [
    (lambda: _status_error(anthropic.AuthenticationError, 401), "API key 无效"),
    (lambda: _status_error(anthropic.RateLimitError, 429), "限流"),
    (lambda: _status_error(anthropic.InternalServerError, 529), "暂时不可用"),
    (lambda: _status_error(anthropic.BadRequestError, 400), "API 错误（400）"),
    (lambda: anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")), "连不上"),
])
def test_errors(api, err, msg):
    p, client = api
    client.next = FakeStream([], None, error=err())
    with pytest.raises(ProviderError, match=msg):
        collect(p)


def test_missing_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        AnthropicAPIProvider("sonnet")


def test_tutor_disables_retrieval_without_tools(tmp_path):
    from gloss.jobs import JobManager
    from gloss.tutor import Tutor

    async def go():
        return Tutor(AnthropicAPIProvider("sonnet", client=FakeClient()), JobManager(), tmp_path).retrieval

    assert asyncio.run(go()) is False
