"""CodexAdapter (`codex exec`) with the generic CLI runner. Not run against a real Codex (see adapters/codex.py)."""

import asyncio
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from gloss.providers import CourseToolSpec, ErrorKind, Event, RunRequest
from gloss.providers.adapters.codex import CodexAdapter, toml_string
from gloss.providers.cli_runner import CLIProvider
from gloss.providers.schemas import from_strict, to_strict

FAKE = Path(__file__).parent / "fake_codex.py"
FIXTURE = Path(__file__).parent / "fixtures" / "codex_exec.jsonl"


@pytest.fixture
def codex(tmp_path, monkeypatch):
    exe = tmp_path / "codex"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-be-stripped")
    monkeypatch.setenv("CODEX_API_KEY", "should-be-stripped")
    ws = tmp_path / "ws"
    ws.mkdir()
    p = CLIProvider(CodexAdapter(str(exe)), ws, model="gpt-5-codex", timeout=5)
    p.calls = lambda: [json.loads(line) for line in log.read_text().splitlines()]
    return p


def run(p, mode, monkeypatch, req=None):
    monkeypatch.setenv("FAKE_CODEX_MODE", mode)

    async def go():
        return [e async for e in p.run(req or RunRequest("question?"))]
    return asyncio.run(go())


def test_parse_fixture():
    a, state, evs = CodexAdapter(), {"req": RunRequest("x")}, []
    for line in FIXTURE.read_text().splitlines():
        evs += a.parse_line(json.loads(line), state)
    assert [(e.kind, e.name or e.text or e.session_id or "") for e in evs] == [
        ("session", "0199a213-81c0-7800-8aa1-bbab2a035a53"),
        ("tool", "command"),
        ("text_final", "Let me check slide 3."),
        ("tool", "mcp__gloss__get_slide"),
        ("text_final", "See slide 3: each head has size d_model / h."),
        ("usage", ""),
        ("done", "See slide 3: each head has size d_model / h."),
    ]
    assert evs[3].data == {"lecture": "L1", "page": 3}
    assert evs[5].data["cache_read_tokens"] == 24448 and evs[-1].session_id.startswith("0199a213")


def test_new_one_off_command(codex, monkeypatch):
    evs = run(codex, "ok", monkeypatch, RunRequest("q?", system="be nice"))
    assert [e.kind for e in evs] == ["session", "text_final", "usage", "done"] and evs[-1].text == "Hello"
    (call,) = codex.calls()
    a = call["args"]
    assert a[:2] == ["exec", "--json"] and a[a.index("--sandbox") + 1] == "read-only"
    assert a[a.index("-C") + 1] == str(codex.workspace) and Path(call["cwd"]) == codex.workspace
    assert a[a.index("-m") + 1] == "gpt-5-codex" and "--skip-git-repo-check" in a
    assert a[a.index("-c") + 1] == 'developer_instructions="be nice"'
    assert a[-2:] == ["--ephemeral", "-"] and "resume" not in a and call["stdin"] == "q?"
    assert not call["has_openai_key"] and not call["has_codex_key"]


def test_persisted_session_then_resume(codex, monkeypatch):
    run(codex, "ok", monkeypatch, RunRequest("q", persist=True, system="sys"))
    assert "--ephemeral" not in codex.calls()[0]["args"]
    run(codex, "ok", monkeypatch, RunRequest("q2", session_id="t-1", system="ignored on resume"))
    a = codex.calls()[1]["args"]
    assert a[-3:] == ["resume", "t-1", "-"] and a.index("--sandbox") < a.index("resume")
    assert "developer_instructions" not in " ".join(a)


def test_missing_session_is_session_invalid(codex, monkeypatch):
    e = run(codex, "missing", monkeypatch, RunRequest("q", session_id="gone"))[-1]
    assert e.kind == "error" and e.error_kind == ErrorKind.SESSION_INVALID and "no rollout" in e.text


def test_turn_failed_usage_limit(codex, monkeypatch):
    e = run(codex, "failed", monkeypatch)[-1]
    assert e.kind == "error" and e.error_kind == ErrorKind.QUOTA_EXCEEDED


def test_images_and_course_tools(codex, monkeypatch, tmp_path):
    img = tmp_path / "page-1.png"
    img.write_bytes(b"png")
    run(codex, "ok", monkeypatch, RunRequest("q", images=[img, img],
                                              course_tools=CourseToolSpec(tmp_path / "lib", "ML", ("L1",))))
    a = codex.calls()[0]["args"]
    assert [a[i + 1] for i, x in enumerate(a) if x == "-i"] == [str(img.resolve())] * 2
    overrides = [a[i + 1] for i, x in enumerate(a) if x == "-c"]
    assert any(o.startswith("mcp_servers.gloss.command=") for o in overrides)
    args_override = next(o for o in overrides if o.startswith("mcp_servers.gloss.args="))
    assert json.loads(args_override.split("=", 1)[1])[-2:] == ["--only", "L1"]


def test_structured_output_strict_schema_and_cleanup(codex, monkeypatch):
    schema = {"type": "object", "properties": {"title": {"type": "string"}, "subtitle": {"type": "string"},
                                               "echo": {"type": "object"}}, "required": ["title"]}
    done = run(codex, "structured", monkeypatch, RunRequest("q", output_schema=schema))[-1]
    sent = done.data["echo"]
    assert sent["required"] == ["title", "subtitle", "echo"] and sent["additionalProperties"] is False
    assert sent["properties"]["subtitle"]["type"] == ["string", "null"]
    assert done.data["title"] == "T" and "subtitle" not in done.data  # the null optional key is gone again
    assert not list(codex.workspace.glob(".schema-*.json"))  # temp schema file removed


def test_timeout_and_cancel_kill_the_process(codex, monkeypatch, tmp_path):
    codex.timeout = 1
    e = run(codex, "hang", monkeypatch)[-1]
    assert e.error_kind == ErrorKind.TIMEOUT
    pidfile = tmp_path / "pid"
    monkeypatch.setenv("FAKE_CODEX_PID", str(pidfile))
    monkeypatch.setenv("FAKE_CODEX_MODE", "hang")
    codex.timeout = 30

    async def cancel_after_first_event():
        gen = codex.run(RunRequest("q"))
        first = await gen.__anext__()
        await gen.aclose()  # the consumer goes away (e.g. job cancelled)
        return first

    assert asyncio.run(cancel_after_first_event()).kind == "session"
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # killed and reaped: no zombie


def test_not_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    p = CLIProvider(CodexAdapter(), tmp_path)

    async def go():
        return [e async for e in p.run(RunRequest("q"))]
    assert asyncio.run(go())[-1].error_kind == ErrorKind.NOT_INSTALLED


def test_api_key_auth_keeps_the_key(monkeypatch):
    monkeypatch.setenv("CODEX_API_KEY", "k")
    assert "CODEX_API_KEY" in CodexAdapter(use_api_key=True).env()
    assert "CODEX_API_KEY" not in CodexAdapter().env()


@pytest.mark.parametrize("text, resume, kind", [
    ("Error: no rollout found for thread id x", True, ErrorKind.SESSION_INVALID),
    ("Error: no rollout found for thread id x", False, ErrorKind.UNKNOWN),
    ("Not logged in. Run `codex login`", False, ErrorKind.NOT_LOGGED_IN),
    ("401 Unauthorized", False, ErrorKind.NOT_LOGGED_IN),
    ("You've hit your usage limit", False, ErrorKind.QUOTA_EXCEEDED),
    ("weird", False, ErrorKind.UNKNOWN),
])
def test_classify(text, resume, kind):
    assert CodexAdapter().classify_error(text, 1, RunRequest("q", session_id="s" if resume else None)) == kind


def test_toml_string_and_strict_roundtrip():
    assert toml_string('a "b"\nc') == '"a \\"b\\"\\nc"'
    schema = {"type": "object", "properties": {"items": {"type": "array", "items": {
        "type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "string", "enum": ["x"]}},
        "required": ["a"]}}}, "required": ["items"]}
    strict = to_strict(schema)
    inner = strict["properties"]["items"]["items"]
    assert inner["required"] == ["a", "b"] and inner["properties"]["b"]["enum"] == ["x", None]
    assert "additionalProperties" not in schema["properties"]["items"]["items"]  # caller's schema untouched
    assert from_strict({"items": [{"a": 1, "b": None}, {"a": 2, "b": "x"}]}, schema) == {"items": [{"a": 1}, {"a": 2, "b": "x"}]}


def test_tutor_shows_whole_messages_as_deltas(tmp_path):
    """A provider with only text_final (Codex): the SSE stream gets one delta per message, resets between."""
    from gloss.jobs import Job, JobManager
    from gloss.tutor import Tutor

    class Whole:
        name = "x"

        async def run(self, req):
            yield Event("text_final", text="Let me check.")
            yield Event("tool", name="command", data={"command": "rg x"})
            yield Event("text_final", text="Answer.")
            yield Event("done", text="Answer.")

    async def go():
        t = Tutor(Whole(), JobManager(), tmp_path)
        job = Job("j", "x")
        return await t._stream(job, "q", [], None, None), job.events

    res, events = asyncio.run(go())
    assert res.text == "Answer."
    assert [(e["type"], e.get("text", e.get("target"))) for e in events] == [
        ("delta", "Let me check."), ("tool", "files"), ("reset", None), ("delta", "Answer.")]


def test_schema_in_prompt_when_the_provider_cannot_enforce_it(tmp_path):
    from gloss.jobs import Job, JobManager
    from gloss.providers import Capabilities
    from gloss.tutor import Tutor

    class NoSchema:
        name = "x"
        caps = Capabilities(supports_output_schema=False, supports_image_flag=False,
                            supports_append_system_prompt=True, streams_text_deltas=False)
        seen = []

        async def run(self, req):
            self.seen.append(req)
            yield Event("done", text='```json\n{"a": 1}\n```' if len(self.seen) == 1 else "not json")

    p = NoSchema()

    async def go():
        t = Tutor(p, JobManager(), tmp_path)
        return [await t._stream(Job(str(i), "x"), "make it", [], None, None, json_schema={"type": "object"})
                for i in range(2)]

    ok, bad = asyncio.run(go())
    assert ok.data == {"a": 1} and bad.data is None
    assert p.seen[0].output_schema is None and "JSON Schema" in p.seen[0].prompt and '{"type": "object"}' in p.seen[0].prompt


def test_doctor_codex_and_openai(tmp_path, monkeypatch, codex):  # noqa: F811
    from gloss.config import Config
    from gloss.doctor import run_checks

    monkeypatch.setenv("FAKE_CODEX_MODE", "ok")
    exe = str(codex.adapter.codex_bin)
    checks = {c.name: c for c in run_checks(Config(provider="codex", codex_bin=exe, workspace=tmp_path), live=True)}
    assert checks["codex"].status == "ok" and checks["test prompt"].status == "ok" and "Hello" in checks["test prompt"].detail
    assert checks["OPENAI_API_KEY"].status == "warn" and checks["CODEX_API_KEY"].status == "warn"  # would switch billing
    assert "ANTHROPIC_API_KEY" not in checks
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    missing = {c.name: c for c in run_checks(Config(provider="codex", workspace=tmp_path))}
    assert missing["codex"].status == "fail" and "codex login" in missing["codex"].detail

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    oa = {c.name: c for c in run_checks(Config(provider="openai_api", workspace=tmp_path))}
    assert oa["openai SDK"].status == "ok" and oa["API key"].status == "ok" and "sk-test" not in oa["API key"].detail
    monkeypatch.delenv("OPENAI_API_KEY")
    assert {c.name: c for c in run_checks(Config(provider="openai_api", workspace=tmp_path))}["API key"].status == "fail"
