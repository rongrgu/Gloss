"""OpenAIAPIProvider against a fake SDK client (no network, no key). Shapes per openai 3.59.0."""

import asyncio
import json
import types

import httpx2
import openai
import pytest

from gloss.providers import ErrorKind, RunRequest
from gloss.providers.context import qa_session
from gloss.providers.openai_api import DEFAULT_MODEL, OpenAIAPIProvider, resolve_model


def ev(type_, **kw):
    return types.SimpleNamespace(type=type_, **kw)


def response(text, status="completed", cached=0, written=0, **kw):
    usage = types.SimpleNamespace(input_tokens=100, output_tokens=5,
                                  input_tokens_details=types.SimpleNamespace(cached_tokens=cached, cache_write_tokens=written))
    return types.SimpleNamespace(output_text=text, status=status, usage=usage, model="gpt-5.5-2026-04-23",
                                 error=None, incomplete_details=None, **kw)


class FakeStream:
    def __init__(self, events, final, error=None):
        self.events, self.final, self.error = events, final, error
        self.exited = False

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, *exc):
        self.exited = True
        return False

    def __aiter__(self):
        async def gen():
            for e in self.events:
                yield e
        return gen()

    async def get_final_response(self):
        return self.final


class FakeClient:
    def __init__(self):
        self.requests, self.next = [], None
        self.responses = types.SimpleNamespace(stream=self._stream)

    def _stream(self, **req):
        self.requests.append(req)
        return self.next


@pytest.fixture
def api():
    c = FakeClient()
    return OpenAIAPIProvider(None, client=c), c


def collect(p, req):
    async def go():
        return [e async for e in p.run(req)]
    return asyncio.run(go())


def test_model_defaults():
    assert resolve_model(None) == resolve_model("") == resolve_model("sonnet") == DEFAULT_MODEL
    assert resolve_model("gpt-6-sol") == "gpt-6-sol"


def test_stream_parts_cache_breakpoints_and_usage(api, tmp_path):
    p, c = api
    img = tmp_path / "page-2.png"
    img.write_bytes(b"\x89PNG fake")
    c.next = FakeStream([ev("response.created"), ev("response.output_text.delta", delta="你"),
                         ev("response.output_text.delta", delta="好"), ev("response.completed")],
                        response("你好", cached=1800, written=64))
    parts = qa_session(img, "OPENING", ("HEAD", ["学生：q1", "助教（Thread #1）：a1"], "## 现在的消息"), "q2", [])
    evs = collect(p, RunRequest("q2", system="助教", parts=parts))
    assert [(e.kind, e.text) for e in evs] == [("text_delta", "你"), ("text_delta", "好"), ("usage", ""), ("done", "你好")]
    assert evs[2].data == {"input_tokens": 100, "output_tokens": 5, "cache_read_tokens": 1800, "cache_write_tokens": 64}
    (req,) = c.requests
    assert req["model"] == DEFAULT_MODEL and req["instructions"] == "助教" and req["store"] is False
    content = req["input"][0]["content"]
    assert [b["type"] for b in content] == ["input_image", "input_text", "input_text", "input_text", "input_text", "input_text"]
    assert content[0]["image_url"].startswith("data:image/png;base64,")
    assert [i for i, b in enumerate(content) if "prompt_cache_breakpoint" in b] == [1, 4]  # opening, end of history


def test_prefix_is_byte_identical(api, tmp_path):
    p, c = api
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG")
    for _ in range(2):
        c.next = FakeStream([], response("ok"))
        collect(p, RunRequest("q", system="s", parts=qa_session(img, "O", ("H", ["e"], "C"), "q", [])))
    a, b = (json.dumps(r, ensure_ascii=False, sort_keys=True) for r in c.requests)
    assert a == b


def test_structured_output_strict_and_cleaned(api):
    p, c = api
    schema = {"type": "object", "properties": {"title": {"type": "string"}, "subtitle": {"type": "string"}},
              "required": ["title"]}
    c.next = FakeStream([], response('{"title": "T", "subtitle": null}'))
    done = collect(p, RunRequest("q", output_schema=schema))[-1]
    assert done.data == {"title": "T"}
    fmt = c.requests[0]["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["name"]
    assert fmt["schema"]["required"] == ["title", "subtitle"] and fmt["schema"]["additionalProperties"] is False


def test_failed_and_incomplete(api):
    p, c = api
    c.next = FakeStream([], response("", status="incomplete"))
    c.next.final.incomplete_details = types.SimpleNamespace(reason="max_output_tokens")
    e = collect(p, RunRequest("q"))[-1]
    assert e.kind == "error" and "max_output_tokens" in e.text


def test_no_sessions(api):
    p, _ = api
    assert collect(p, RunRequest("q", session_id="x"))[-1].error_kind == ErrorKind.SESSION_INVALID


def _status_error(cls, status, body=None):
    req = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    return cls("boom", response=httpx2.Response(status, request=req), body=body)


@pytest.mark.parametrize("err, msg, kind", [
    (lambda: _status_error(openai.AuthenticationError, 401), "invalid API key", ErrorKind.NOT_LOGGED_IN),
    (lambda: _status_error(openai.RateLimitError, 429, {"code": "insufficient_quota"}), "out of credit", ErrorKind.QUOTA_EXCEEDED),
    (lambda: _status_error(openai.RateLimitError, 429), "rate limited", ErrorKind.QUOTA_EXCEEDED),
    (lambda: _status_error(openai.InternalServerError, 503), "temporarily unavailable", ErrorKind.UNKNOWN),
    (lambda: _status_error(openai.BadRequestError, 400), "API error (400)", ErrorKind.UNKNOWN),
    (lambda: openai.APIConnectionError(request=httpx2.Request("POST", "https://x")), "cannot reach", ErrorKind.UNKNOWN),
])
def test_errors(api, err, msg, kind):
    p, c = api
    c.next = FakeStream([], None, error=err())
    e = collect(p, RunRequest("q"))[-1]
    assert e.kind == "error" and msg in e.text and e.error_kind == kind


def test_closing_early_closes_the_stream(api):
    p, c = api
    c.next = FakeStream([ev("response.output_text.delta", delta="a"), ev("response.output_text.delta", delta="b")],
                        response("ab"))

    async def go():
        gen = p.run(RunRequest("q"))
        await gen.__anext__()
        await gen.aclose()
    asyncio.run(go())
    assert c.next.exited


def test_missing_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(Exception, match="OPENAI_API_KEY"):
        OpenAIAPIProvider(None)
