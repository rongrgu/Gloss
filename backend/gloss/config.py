"""App configuration (`config.toml`, see config.example.toml)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def app_dir() -> Path:
    """Per-user app data (recent files list). Not inside any .gloss."""
    if env := os.environ.get("GLOSS_HOME"):
        return Path(env)
    return Path.home() / ".gloss"


@dataclass
class Config:
    provider: str = "claude_cli"  # claude_cli | anthropic_api (M6)
    model: str = "sonnet"
    concurrency: int = 2  # global cap on simultaneous LLM calls
    language: str = "中文"  # explanation / answer language
    workspace: Path = field(default_factory=lambda: REPO_ROOT / "claude_workspace")
    library: Path = field(default_factory=lambda: Path.home() / "Gloss")  # where new .gloss files go
    claude_bin: str | None = None  # default: found on PATH
    timeout: float = 600  # seconds per call

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or Path(os.environ.get("GLOSS_CONFIG", REPO_ROOT / "config.toml"))
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
