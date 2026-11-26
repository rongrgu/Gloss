"""AnthropicAPIProvider against a fake SDK client (no network, no key)."""

import asyncio
import types

import anthropic
import httpx2
import pytest

from gloss.providers import ErrorKind, ProviderError, RunRequest, SessionRef
from gloss.providers.anthropic_api import AnthropicAPIProvider, resolve_model, strict_schema
from gloss.providers.context import Part, qa_session


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


def collect(provider, prompt="hi", session=None, json_schema=None, **kw):
    req = RunRequest(prompt, persist=session is not None, output_schema=json_schema,
                     session_id=session.id if session is not None and not session.new else None, **kw)

    async def go():
        return [e async for e in provider.run(req)]
    return asyncio.run(go())


def kinds(evs):
    return [(e.kind, e.text) for e in evs]


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
    f = final(block("text", text="你好"))
    f.usage = types.SimpleNamespace(input_tokens=40, output_tokens=2, cache_read_input_tokens=1800,
                                    cache_creation_input_tokens=60)
    client.next = FakeStream([delta("你"), delta("好")], f)
    parts = qa_session(img, "OPENING", ("HEAD", ["学生：q1", "助教（Thread #1）：a1"], "## 现在的消息"), "问题", [crop])
    evs = collect(p, "问题", system="助教", parts=parts)
    assert kinds(evs) == [("text_delta", "你"), ("text_delta", "好"), ("usage", ""), ("done", "你好")]
    assert evs[2].data == {"input_tokens": 40, "output_tokens": 2, "cache_read_tokens": 1800, "cache_write_tokens": 60}
    assert evs[-1].model == "claude-sonnet-5-5" and evs[-1].session_id is None  # the API has no sessions
    used_beta, req = client.requests[0]
    assert used_beta and req["betas"] == ["server-side-fallback-2026-07-01"] and req["fallbacks"] == "default"
    assert req["model"] == "claude-sonnet-5-5" and req["max_tokens"] == 64000
    assert req["system"] == [{"type": "text", "text": "助教", "cache_control": {"type": "ephemeral"}}]
    content = req["messages"][0]["content"]
    assert [b["type"] for b in content] == ["image", "text", "text", "text", "text", "text", "image"]
    assert [b.get("text") for b in content[1:6]] == ["OPENING", "HEAD", "学生：q1", "助教（Thread #1）：a1", "## 现在的消息\n\n问题"]
    # breakpoints: system + end of the opening + end of the earlier Q&A (stable prefix); never the new question
    assert [i for i, b in enumerate(content) if "cache_control" in b] == [1, 4]
    assert content[0]["source"]["media_type"] == "image/png"


def test_prefix_is_byte_identical_and_breakpoints_capped(api, tmp_path):
    import json
    p, client = api
    img = tmp_path / "page-2.png"
    img.write_bytes(b"\x89PNG fake")
    for _ in range(2):
        client.next = FakeStream([], final(block("text", text="ok")))
        collect(p, "q", system="s", parts=qa_session(img, "O", ("H", ["e1"], "C"), "q", []))
    a, b = (json.dumps(r[1], ensure_ascii=False, sort_keys=True) for r in client.requests)
    assert a == b  # same input, same bytes: nothing time- or order-dependent
    many = [Part(text=str(i), cache=True) for i in range(6)]
    req = p.build_request(many, "sys", None)
    marked = [b["text"] for b in req["messages"][0]["content"] if "cache_control" in b]
    assert marked == ["3", "4", "5"]  # 3 + the system prompt = the API's limit of 4


def test_haiku_has_no_fallbacks():
    client = FakeClient()
    p = AnthropicAPIProvider("haiku", client=client)
    client.next = FakeStream([], final(block("text", text="ok"), model="claude-haiku-5-5"))
    collect(p)
    used_beta, req = client.requests[0]
    assert not used_beta and "fallbacks" not in req and "betas" not in req


def test_resume_always_rebuilds(api):
    p, _ = api
    assert collect(p, session=SessionRef("old", new=False))[-1].error_kind == ErrorKind.SESSION_INVALID


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
    assert kinds(evs) == [("text_delta", "部分"), ("text_final", "部分"), ("text_delta", "完整回答"), ("done", "完整回答")]


def test_refusal(api):
    p, client = api
    f = final(stop="refusal")
    f.stop_details = types.SimpleNamespace(category="cyber")
    client.next = FakeStream([], f)
    e = collect(p)[-1]
    assert e.kind == "error" and "declined" in e.text and "cyber" in e.text


def _status_error(cls, status):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(status, request=req), body=None)


@pytest.mark.parametrize("err, msg, kind", [
    (lambda: _status_error(anthropic.AuthenticationError, 401), "invalid API key", ErrorKind.NOT_LOGGED_IN),
    (lambda: _status_error(anthropic.RateLimitError, 429), "rate limited", ErrorKind.QUOTA_EXCEEDED),
    (lambda: _status_error(anthropic.InternalServerError, 529), "temporarily unavailable", ErrorKind.UNKNOWN),
    (lambda: _status_error(anthropic.BadRequestError, 400), "API error (400)", ErrorKind.UNKNOWN),
    (lambda: anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")), "cannot reach", ErrorKind.UNKNOWN),
])
def test_errors(api, err, msg, kind):
    p, client = api
    client.next = FakeStream([], None, error=err())
    e = collect(p)[-1]
    assert e.kind == "error" and msg in e.text and e.error_kind == kind


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


def test_closing_early_closes_the_sdk_stream(api):
    """Cancelling (the job goes away) must end the HTTP stream, not keep generating tokens."""
    p, client = api
    exited = []

    class Tracked(FakeStream):  # `async with` looks __aexit__ up on the class
        async def __aexit__(self, *exc):
            exited.append(True)
            return False
    client.next = Tracked([delta("a"), delta("b"), delta("c")], final(block("text", text="abc")))

    async def go():
        gen = p.run(RunRequest("q"))
        first = await gen.__anext__()
        await gen.aclose()
        return first
    assert asyncio.run(go()).text == "a" and exited == [True]
