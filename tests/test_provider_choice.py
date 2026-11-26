"""Which LLM runs when config.toml doesn't say (DESIGN.md §6.2): detect claude / codex, ask when both."""

import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from test_server import sse

from gloss.config import REPO_ROOT, Config
from gloss.doctor import run_checks
from gloss.providers.factory import choose_provider
from gloss.server import create_app


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("GLOSS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))  # nothing found unless the config names a binary
    w = tmp_path / "ws"
    (w / "prompts").mkdir(parents=True)
    src = REPO_ROOT / "claude_workspace"
    for p in [src / "CLAUDE.md", *(src / "prompts").glob("*.md")]:
        (w / p.relative_to(src)).write_text(p.read_text())
    return w


def cfg(ws, **kw):
    return Config(workspace=ws, library=ws.parent / "lib", **kw)


def test_choose_provider(ws):
    both = dict(claude_bin="/x/claude", codex_bin="/x/codex")
    assert vars(choose_provider(cfg(ws, provider="openai_api", **both))) == {
        "name": "openai_api", "source": "config", "available": ["claude", "codex"]}
    assert choose_provider(cfg(ws, provider="claude_cli")).name == "claude"  # the old name
    assert (lambda c: (c.name, c.source, c.needs_choice))(choose_provider(cfg(ws, **both))) == ("claude", "pending", True)
    assert (lambda c: (c.name, c.source))(choose_provider(cfg(ws, **both), "codex")) == ("codex", "choice")
    assert (lambda c: (c.name, c.source))(choose_provider(cfg(ws, codex_bin="/x/codex"))) == ("codex", "auto")
    assert (lambda c: (c.name, c.source))(choose_provider(cfg(ws, codex_bin="/x/codex"), "claude")) == ("codex", "auto")
    assert (lambda c: (c.name, c.source))(choose_provider(cfg(ws))) == (None, "none")


def test_both_installed_ask_then_remember(ws):
    c = cfg(ws, claude_bin="/x/claude", codex_bin="/x/codex")
    with TestClient(create_app(c)) as client:
        p = client.get("/api/prefs").json()
        assert (p["provider"], p["provider_source"], p["needs_choice"], p["available"]) == \
            ("claude", "pending", True, ["claude", "codex"])
        assert client.put("/api/prefs", json={"provider": "gemini"}).status_code == 422
        p = client.put("/api/prefs", json={"provider": "codex"}).json()
        assert (p["provider"], p["provider_source"], p["needs_choice"]) == ("codex", "choice", False)
        assert client.app.state.tutor.provider.name == "codex"
        h = client.get("/api/health").json()
        assert h["provider"] == "codex" and h["version"]
    assert json.loads((ws.parent / "home" / "prefs.json").read_text()) == {"provider": "codex"}
    with TestClient(create_app(c)) as client:  # remembered across restarts
        assert client.get("/api/prefs").json()["provider_source"] == "choice"
    checks = {x.name: x for x in run_checks(c)}
    assert "Codex (your choice in the app)" in checks["AI"].detail


def test_config_pins_the_provider(ws):
    with TestClient(create_app(cfg(ws, provider="claude", claude_bin="/x/claude", codex_bin="/x/codex"))) as client:
        p = client.get("/api/prefs").json()
        assert (p["provider"], p["provider_source"], p["needs_choice"]) == ("claude", "config", False)
        assert client.put("/api/prefs", json={"provider": "codex"}).status_code == 409


def test_none_installed(ws):
    from make_pdfs import make_deck
    c = cfg(ws)
    with TestClient(create_app(c)) as client:
        p = client.get("/api/prefs").json()
        assert (p["provider"], p["provider_source"], p["available"]) == (None, "none", [])
        course = client.post("/api/courses/import", files={"file": ("l.pdf", make_deck(), "application/pdf")}).json()
        job = client.post(f"/api/courses/{course['id']}/slides/1/explanation").json()["job"]
        err = sse(client, job["id"])[-1]
        assert err["type"] == "error" and "Neither Claude Code" in err["message"]
    checks = {x.name: x for x in run_checks(c)}
    assert checks["AI"].status == "fail" and "neither" in checks["AI"].detail


def test_only_one_installed_needs_no_choice(ws):
    with TestClient(create_app(cfg(ws, codex_bin="/x/codex"))) as client:
        p = client.get("/api/prefs").json()
        assert (p["provider"], p["provider_source"], p["needs_choice"]) == ("codex", "auto", False)


def test_version_flag():
    out = subprocess.run([sys.executable, "-m", "gloss", "--version"], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.startswith("gloss ")
