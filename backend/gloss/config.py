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


PURPOSES = ("outline", "explain", "qa", "digest", "quiz", "cheatsheet")  # outline = mind map + transcript alignment


@dataclass
class Config:
    # claude | codex (your own CLI login and plan) | anthropic_api | openai_api (billed to the API key in
    # ANTHROPIC_API_KEY / OPENAI_API_KEY). "claude_cli" is the old name for "claude". Also `[llm] provider`.
    provider: str | None = None  # None: detect the installed CLI (and ask when both are there), §6.2
    model: str = "sonnet"  # the default for every use; Claude aliases are ignored by codex / openai_api
    models: dict[str, str] = field(default_factory=dict)  # `[llm.models]`: outline / explain / qa / digest / quiz
    codex_auth: str = "login"  # codex: "login" (the CLI's saved login) | "api_key" (keep CODEX_API_KEY in the env)
    concurrency: int = 2  # global cap on simultaneous LLM calls
    language: str | None = None  # explanation / answer language; None follows the interface language (§9.3)
    workspace: Path = field(default_factory=default_workspace)
    library: Path = field(default_factory=lambda: Path.home() / "Gloss")  # where new .gloss files go
    claude_bin: str | None = None  # default: found on PATH
    codex_bin: str | None = None  # provider "codex": the codex executable; default: found on PATH
    timeout: float = 600  # seconds per call
    qa_retrieval: bool = True  # answers may search the whole course (Grep/Read in the course folder)
    quiz_model: str | None = None  # model for pre-quiz generation (§13.5); None = `model`
    quiz_verify: bool = True  # a second call checks every answer key against the slides
    digest_model: str | None = None  # model for lecture summaries (§15.3); None = `model`
    use_profile: bool = True  # mention relevant study-profile weak points in explanations and Q&A (§15.5)

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or config_path()
        if not path.exists():
            return cls()
        data = tomllib.loads(path.read_text())
        llm = data.pop("llm", None)  # [llm] provider / codex_auth, [llm.models] per use (DESIGN.md §6)
        if llm is not None:
            if not isinstance(llm, dict) or set(llm) - {"provider", "models", "codex_auth"}:
                raise ValueError(f"unknown keys in [llm] of {path}: {sorted(set(llm) - {'provider', 'models', 'codex_auth'})}")
            for k in ("provider", "codex_auth"):
                if llm.get(k):
                    data[k] = llm[k]
            models = llm.get("models") or {}
            bad = set(models) - set(PURPOSES)
            if bad:
                raise ValueError(f"unknown [llm.models] keys in {path}: {sorted(bad)} (use {', '.join(PURPOSES)})")
            data["models"] = {k: v for k, v in models.items() if v}
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown config keys in {path}: {sorted(unknown)}")
        for k in ("workspace", "library"):
            if k in data:
                data[k] = Path(data[k]).expanduser()
        return cls(**data)

    def model_for(self, purpose: str) -> str | None:
        """The model for one use (`[llm.models]`), or None for the provider's default (`model`).
        `quiz_model` / `digest_model` are the older names for the quiz and digest entries."""
        if self.models.get(purpose):
            return self.models[purpose]
        return {"quiz": self.quiz_model, "digest": self.digest_model}.get(purpose)
