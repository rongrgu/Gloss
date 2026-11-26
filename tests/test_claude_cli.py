import asyncio
import json
import stat
import sys
from pathlib import Path

import pytest

from gloss.providers import ErrorKind, RunRequest, SessionRef
from gloss.providers.adapters.claude import ClaudeAdapter
from gloss.providers.claude_cli import ClaudeCLIProvider

FAKE = Path(__file__).parent / "fake_claude.py"


@pytest.fixture
def cli(tmp_path, monkeypatch):
    exe = tmp_path / "claude"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-stripped")
    ws = tmp_path / "ws"
    ws.mkdir()
    p = ClaudeCLIProvider(ws, model="haiku", claude_bin=str(exe), timeout=5)
    p.calls = lambda: [json.loads(line) for line in log.read_text().splitlines()]
    return p


def run(provider, mode, monkeypatch, session=None, json_schema=None, **kw):
    """Run one request through the CLI runner + Claude adapter against tests/fake_claude.py.
    `session` keeps the old meaning: None one-off, SessionRef(id, new=True) start, new=False resume."""
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    req = RunRequest("question?", persist=session is not None, output_schema=json_schema,
                     session_id=session.id if session is not None and not session.new else None, **kw)
    if "images" in kw:
        req.images = list(kw["images"])

    async def go():
        return [ev async for ev in provider.run(req)]

    return asyncio.run(go())


def kinds(evs):
    return [(e.kind, e.text or e.session_id or e.name or "") for e in evs]


def test_stream_and_done(cli, monkeypatch):
    evs = run(cli, "ok", monkeypatch, session=SessionRef("ignored", new=True), system="be nice")
    assert kinds(evs) == [("session", "ephemeral-session"), ("text_delta", "Hel"), ("text_delta", "lo"),
                          ("done", "Hello")]
    done = evs[-1]
    assert (done.session_id, done.model, done.cost_usd) == ("ephemeral-session", "claude-fake-1", 0.01)
    (call,) = cli.calls()
    a = call["args"]
    assert call["stdin"] == "question?"
    for flag in ["-p", "--restricted", "--verbose", "--include-partial-messages"]:
        assert flag in a
    assert a[a.index("--tools") + 1] == "Read"
    assert a[a.index("--allowedTools") + 1] == "Read"
    assert a[a.index("--permission-prompts") + 1] == "none"
    assert a[a.index("--output-format") + 1] == "stream-json"
    assert a[a.index("--model") + 1] == "haiku"
    assert a[a.index("--append-system-prompt") + 1] == "be nice"
    # a new session to keep: persisted (no flag), named by claude (init / result), not pre-assigned
    assert "--resume" not in a and "--no-session-persistence" not in a and "--session-id" not in a


def test_api_key_is_stripped(cli, monkeypatch):
    run(cli, "ok", monkeypatch)
    assert cli.calls()[0]["has_api_key"] is False


def test_resume_omits_system_prompt(cli, monkeypatch):
    run(cli, "ok", monkeypatch, session=SessionRef("s-1", new=False), system="ignored")
    a = cli.calls()[0]["args"]
    assert a[a.index("--resume") + 1] == "s-1"
    assert "--append-system-prompt" not in a and "--session-id" not in a


def test_no_session_is_not_persisted(cli, monkeypatch):
    evs = run(cli, "ok", monkeypatch, system="sys")
    assert "--no-session-persistence" in cli.calls()[0]["args"]
    assert "--append-system-prompt" in cli.calls()[0]["args"]
    assert evs[-1].kind == "done"


def test_tool_use_closes_the_preamble(cli, monkeypatch):
    evs = run(cli, "tooluse", monkeypatch)
    assert kinds(evs) == [("session", "ephemeral-session"), ("text_delta", "Let me look."), ("tool", "Read"),
                          ("text_final", "Let me look."), ("text_delta", "Final "), ("text_delta", "answer"),
                          ("done", "Final answer")]
    assert evs[2].data == {"file_path": "/x/page-7.png"}


def test_missing_session(cli, monkeypatch):
    evs = run(cli, "missing", monkeypatch, session=SessionRef("gone", new=False))
    assert evs[-1].kind == "error" and evs[-1].error_kind == ErrorKind.SESSION_INVALID


def test_json_schema(cli, monkeypatch):
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    evs = run(cli, "structured", monkeypatch, json_schema=schema)
    assert evs[-1].data == {"echo_schema": schema}


def test_api_error(cli, monkeypatch):
    e = run(cli, "error", monkeypatch)[-1]
    assert e.kind == "error" and e.error_kind == ErrorKind.NOT_LOGGED_IN and "401" in e.text and "Invalid API key" in e.text


def test_crash(cli, monkeypatch):
    e = run(cli, "crash", monkeypatch)[-1]
    assert e.kind == "error" and "exited 2" in e.text and "something exploded" in e.text


def test_timeout_kills_process(cli, monkeypatch):
    cli.timeout = 1
    e = run(cli, "hang", monkeypatch)[-1]
    assert e.error_kind == ErrorKind.TIMEOUT and "timed out" in e.text


def test_images_listed_with_absolute_paths(cli, monkeypatch):
    img = cli.workspace / "courses" / "c" / "page-1.png"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"png")
    run(cli, "ok", monkeypatch, images=[img])
    stdin = cli.calls()[0]["stdin"]
    assert stdin.startswith("question?")
    assert str(img.resolve()) in stdin and "Read tool" in stdin


def test_image_outside_workspace_refused(cli, monkeypatch, tmp_path):
    outside = tmp_path / "x.png"
    outside.write_bytes(b"png")
    e = run(cli, "ok", monkeypatch, images=[outside])[-1]
    assert e.kind == "error" and "outside the working folder" in e.text and not (tmp_path / "calls.jsonl").exists()


def test_cwd_and_tools(cli, monkeypatch):
    course = cli.workspace / "courses" / "c1"
    course.mkdir(parents=True)
    img = course / "page-1.png"
    img.write_bytes(b"png")
    run(cli, "ok", monkeypatch, images=[img], cwd=course, tools=("Read", "Grep", "Glob"))
    call = cli.calls()[0]
    a = call["args"]
    assert Path(call["cwd"]).resolve() == course.resolve()
    assert a[a.index("--tools") + 1:a.index("--tools") + 4] == ["Read", "Grep", "Glob"]
    assert a[a.index("--allowedTools") + 1:a.index("--allowedTools") + 4] == ["Read", "Grep", "Glob"]
    # Default: workspace root, Read only.
    run(cli, "ok", monkeypatch)
    a = cli.calls()[1]["args"]
    assert Path(cli.calls()[1]["cwd"]).resolve() == cli.workspace and a[a.index("--tools") + 2] == "--allowedTools"


def test_image_must_be_inside_cwd(cli, monkeypatch):
    (cli.workspace / "courses" / "a").mkdir(parents=True)
    other = cli.workspace / "courses" / "b" / "page-1.png"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"png")
    e = run(cli, "ok", monkeypatch, images=[other], cwd=cli.workspace / "courses" / "a")[-1]
    assert e.kind == "error" and "outside the working folder" in e.text


@pytest.mark.parametrize("kw, msg", [
    (dict(cwd=Path("/tmp")), "cwd outside workspace"),
    (dict(tools=("Read", "Bash")), "read-only"),
    (dict(tools=()), "read-only"),
])
def test_unsafe_options_refused(cli, monkeypatch, kw, msg):
    e = run(cli, "ok", monkeypatch, **kw)[-1]
    assert e.kind == "error" and msg in e.text


def test_claude_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    e = asyncio.run(_collect(ClaudeCLIProvider(tmp_path), RunRequest("x")))[-1]
    assert e.error_kind == ErrorKind.NOT_INSTALLED and "not found" in e.text


async def _collect(p, req):
    return [ev async for ev in p.run(req)]


@pytest.mark.parametrize("text, status, kind", [
    ("No conversation found with session ID: x", None, ErrorKind.SESSION_INVALID),
    ("Not logged in · Please run /login", None, ErrorKind.NOT_LOGGED_IN),
    ("anything", 401, ErrorKind.NOT_LOGGED_IN),
    ("Claude usage limit reached. Your limit will reset at 5pm", None, ErrorKind.QUOTA_EXCEEDED),
    ("x", 429, ErrorKind.QUOTA_EXCEEDED),
    ("something else", 500, ErrorKind.UNKNOWN),
])
def test_classify_error(text, status, kind):
    assert ClaudeAdapter().classify_error(text, 1, None, status) == kind


def test_parse_recorded_real_stream():
    """tests/fixtures/claude_stream.jsonl: a real `claude -p` run (2.1.331, haiku) that Reads a file first."""
    import json as _json
    a, state, evs = ClaudeAdapter(), {}, []
    for line in (Path(__file__).parent / "fixtures" / "claude_stream.jsonl").read_text().splitlines():
        evs += a.parse_line(_json.loads(line), state)
    k = [e.kind for e in evs]
    assert k[0] == "session" and k[-1] == "done" and "tool" in k and k.count("usage") == 1
    tool = next(e for e in evs if e.kind == "tool")
    assert tool.name == "Read" and tool.data["file_path"].endswith("note.txt")
    done = evs[-1]
    assert "MARIGOLD" in done.text and done.model and done.model.startswith("claude-haiku")
    # the streamed text after the tool call is the answer
    after_tool = "".join(e.text for e in evs[k.index("tool"):] if e.kind == "text_delta")
    assert "MARIGOLD" in after_tool
