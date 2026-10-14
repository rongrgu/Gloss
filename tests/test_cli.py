import socket

import pytest

from gloss import cli
from gloss.config import seed_workspace
from gloss.ingest import create_course
from make_pdfs import make_deck


def test_free_port_skips_busy():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        busy = s.getsockname()[1]
        s.listen()
        assert cli.free_port(busy) != busy


def test_export_command(tmp_path, capsys):
    pdf = tmp_path / "lec.pdf"
    make_deck(pdf)
    create_course(pdf, tmp_path / "lec.gloss").close()
    with pytest.raises(SystemExit) as e:
        cli.main(["export", str(tmp_path / "lec.gloss")])
    assert e.value.code == 0
    assert (tmp_path / "lec.md").read_text().startswith("# lec")


def test_seed_workspace_keeps_user_edits(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "prompts").mkdir(parents=True)
    (src / "CLAUDE.md").write_text("bundled role")
    (src / "prompts" / "a.md").write_text("bundled a")
    (dst / "prompts").mkdir(parents=True)
    (dst / "prompts" / "a.md").write_text("my edit")
    seed_workspace(src, dst)
    assert (dst / "CLAUDE.md").read_text() == "bundled role"
    assert (dst / "prompts" / "a.md").read_text() == "my edit"


def test_doctor_api_provider(monkeypatch, tmp_path):
    from gloss.config import Config
    from gloss.doctor import run_checks
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    checks = {c.name: c for c in run_checks(Config(provider="anthropic_api", workspace=tmp_path))}
    assert checks["anthropic SDK"].status == "ok"
    assert checks["API key"].status == "fail" and "claude" not in checks and "login" not in checks
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    checks = {c.name: c for c in run_checks(Config(provider="anthropic_api", workspace=tmp_path))}
    assert checks["API key"].status == "ok" and "ANTHROPIC_API_KEY" not in checks
