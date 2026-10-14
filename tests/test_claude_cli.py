import asyncio
import json
import stat
import sys
from pathlib import Path

import pytest

from gloss.providers import Done, ProviderError, Reset, SessionNotFound, SessionRef, TextDelta, ToolUse
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


def run(provider, mode, monkeypatch, **kw):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)

    async def go():
        return [ev async for ev in provider.run("question?", **kw)]

    return asyncio.run(go())


def test_stream_and_done(cli, monkeypatch):
    evs = run(cli, "ok", monkeypatch, session=SessionRef("s-1", new=True), system="be nice")
    assert evs == [TextDelta("Hel"), TextDelta("lo"), Done("Hello", "s-1", "claude-fake-1", 0.01)]
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
    assert a[a.index("--session-id") + 1] == "s-1"
    assert a[a.index("--append-system-prompt") + 1] == "be nice"
    assert "--resume" not in a


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
    assert isinstance(evs[-1], Done)


def test_tool_use_emits_reset_and_final_text(cli, monkeypatch):
    evs = run(cli, "tooluse", monkeypatch)
    assert evs == [TextDelta("Let me look."), ToolUse("Read", {"file_path": "/x/page-7.png"}), Reset(),
                   TextDelta("Final "), TextDelta("answer"),
                   Done("Final answer", "ephemeral-session", "claude-fake-1", 0.01)]


def test_missing_session(cli, monkeypatch):
    with pytest.raises(SessionNotFound):
        run(cli, "missing", monkeypatch, session=SessionRef("gone", new=False))


def test_json_schema(cli, monkeypatch):
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    evs = run(cli, "structured", monkeypatch, json_schema=schema)
    assert evs[-1].data == {"echo_schema": schema}


def test_api_error(cli, monkeypatch):
    with pytest.raises(ProviderError, match="401.*Invalid API key"):
        run(cli, "error", monkeypatch)


def test_crash(cli, monkeypatch):
    with pytest.raises(ProviderError, match="exited 2.*something exploded"):
        run(cli, "crash", monkeypatch)


def test_timeout_kills_process(cli, monkeypatch):
    cli.timeout = 1
    with pytest.raises(ProviderError, match="timed out"):
        run(cli, "hang", monkeypatch)


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
    with pytest.raises(ProviderError, match="outside the working folder"):
        run(cli, "ok", monkeypatch, images=[outside])


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
    with pytest.raises(ProviderError, match="outside the working folder"):
        run(cli, "ok", monkeypatch, images=[other], cwd=cli.workspace / "courses" / "a")


@pytest.mark.parametrize("kw, msg", [
    (dict(cwd=Path("/tmp")), "cwd outside workspace"),
    (dict(tools=("Read", "Bash")), "read-only"),
    (dict(tools=()), "read-only"),
])
def test_unsafe_options_refused(cli, monkeypatch, kw, msg):
    with pytest.raises(ProviderError, match=msg):
        run(cli, "ok", monkeypatch, **kw)


def test_claude_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    p = ClaudeCLIProvider(tmp_path)
    with pytest.raises(ProviderError, match="not found"):
        p.build_args(session=None, system=None)
