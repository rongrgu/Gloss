"""App configuration (`config.toml`, see config.example.toml).

Two ways to run Gloss:
- From a git clone (`uv run gloss`): config.toml and claude_workspace/ live in the repo.
- Installed (`pip install`): config.toml and the runtime workspace live in ~/.gloss/
  (override with GLOSS_HOME). The workspace is seeded from the copy bundled in the
  package; files you edit there are never overwritten.
"""

from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]
REPO_WORKSPACE = REPO_ROOT / "claude_workspace"
BUNDLED_WORKSPACE = PACKAGE_DIR / "_workspace"  # only present in a built wheel


def app_dir() -> Path:
    """Per-user app data (recent files list; config and workspace when installed). Not inside any .gloss."""
    if env := os.environ.get("GLOSS_HOME"):
        return Path(env)
    return Path.home() / ".gloss"


def in_repo() -> bool:
    return (REPO_WORKSPACE / "CLAUDE.md").exists()


def config_path() -> Path:
    if env := os.environ.get("GLOSS_CONFIG"):
        return Path(env)
    return REPO_ROOT / "config.toml" if in_repo() else app_dir() / "config.toml"


def default_workspace() -> Path:
    if in_repo():
        return REPO_WORKSPACE
    ws = app_dir() / "workspace"
    seed_workspace(BUNDLED_WORKSPACE, ws)
    return ws


def seed_workspace(src: Path, dst: Path) -> None:
    """Copy runtime prompts into `dst`, adding missing files only (user edits win)."""
    if not src.exists():
        return
    for f in src.rglob("*.md"):
        target = dst / f.relative_to(src)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, target)


@dataclass
class Config:
    provider: str = "claude_cli"  # claude_cli | anthropic_api (M6)
    model: str = "sonnet"
    concurrency: int = 2  # global cap on simultaneous LLM calls
    language: str = "中文"  # explanation / answer language
    workspace: Path = field(default_factory=default_workspace)
    library: Path = field(default_factory=lambda: Path.home() / "Gloss")  # where new .gloss files go
    claude_bin: str | None = None  # default: found on PATH
    timeout: float = 600  # seconds per call

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or config_path()
        if not path.exists():
            return cls()
        data = tomllib.loads(path.read_text())
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown config keys in {path}: {sorted(unknown)}")
        for k in ("workspace", "library"):
            if k in data:
                data[k] = Path(data[k]).expanduser()
        return cls(**data)
