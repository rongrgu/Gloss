"""Environment check (DESIGN.md §10). Run with `gloss doctor` (or scripts/doctor.py)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Config, config_path
from .providers.claude_cli import clean_env, find_claude

STATIC_DIR = Path(__file__).parent / "static"
PROMPTS = ["build-tree", "explain-slide", "qa-system", "qa-opening", "qa-rebuild", "align-transcript", "qa-retrieval"]


@dataclass
class Check:
    status: str  # ok | warn | fail
    name: str
    detail: str


def auth_status(claude: str) -> dict | None:
    """`claude auth status --json` with ANTHROPIC_API_KEY removed, as Gloss will call it. None if it can't be read."""
    try:
        p = subprocess.run([claude, "auth", "status", "--json"], capture_output=True, text=True,
                           env=clean_env(), timeout=20)
        return json.loads(p.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def live_test(claude: str, cfg: Config) -> Check:
    """Send a tiny prompt the way Gloss does (uses a little quota)."""
    args = [claude, "-p", "--restricted", "--tools", "Read", "--allowedTools", "Read", "--permission-prompts", "none",
            "--no-session-persistence", "--output-format", "json", "--model", cfg.model]
    try:
        p = subprocess.run(args, input="Reply with exactly: ok", capture_output=True, text=True, env=clean_env(),
                           cwd=cfg.workspace if cfg.workspace.exists() else None, timeout=120)
        out = json.loads(p.stdout)
    except subprocess.TimeoutExpired:
        return Check("fail", "test prompt", "claude did not answer within 120s")
    except (OSError, json.JSONDecodeError):
        return Check("fail", "test prompt", f"unexpected output: {(p.stderr or p.stdout).strip()[:200]}")
    if out.get("is_error"):
        return Check("fail", "test prompt", str(out.get("result"))[:200])
    return Check("ok", "test prompt", f"model {cfg.model} answered {str(out.get('result')).strip()[:40]!r}")


def api_checks() -> list[Check]:
    try:
        import anthropic
        sdk = Check("ok", "anthropic SDK", f"installed ({anthropic.__version__})")
    except ImportError:
        sdk = Check("fail", "anthropic SDK", "missing: pip install 'gloss[api]' (or uv sync --extra api)")
    key = (Check("ok", "API key", "ANTHROPIC_API_KEY is set; calls are billed to it")
           if os.environ.get("ANTHROPIC_API_KEY")
           else Check("fail", "API key", "provider = \"anthropic_api\" needs ANTHROPIC_API_KEY"))
    return [sdk, key]


def cli_checks(cfg: Config, live: bool) -> list[Check]:
    claude = cfg.claude_bin or find_claude()
    if not claude:
        return [Check("fail", "claude", "not found on PATH. Install Claude Code (https://claude.com/claude-code) "
                                        "and run `claude` once to log in.")]
    try:
        ver = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        ver = f"(could not run: {e})"
    checks = [Check("ok", "claude", f"{claude}  {ver}")]
    st = auth_status(claude)
    if st is None:
        checks.append(Check("warn", "login", "could not read `claude auth status`; try `gloss doctor --live`"))
    elif not st.get("loggedIn"):
        checks.append(Check("fail", "login", "not logged in. Run `claude` and use /login (or `claude auth login`)."))
    elif st.get("authMethod") != "claude.ai":
        checks.append(Check("warn", "login", f"logged in via {st.get('authMethod')}, not a Claude subscription; "
                                             "calls will be billed to that account"))
    else:
        sub = st.get("subscriptionType") or "subscription"
        hint = f" Pro has a smaller quota; model \"{cfg.model}\" may use it up fast — consider \"sonnet\" or \"haiku\"." \
            if sub == "pro" and "opus" in cfg.model.lower() else ""
        checks.append(Check("ok", "login", f"claude.ai ({sub}).{hint}"))
    if live:
        checks.append(live_test(claude, cfg))
    return checks


def run_checks(cfg: Config | None = None, live: bool = False) -> list[Check]:
    checks: list[Check] = []
    v = sys.version_info
    checks.append(Check("ok" if v >= (3, 11) else "fail", "python", f"{v.major}.{v.minor}.{v.micro}"
                        + ("" if v >= (3, 11) else " — Gloss needs 3.11+")))

    if cfg is None:
        try:
            cfg = Config.load()
            p = config_path()
            checks.append(Check("ok", "config", f"{p} (model {cfg.model}, language {cfg.language or 'follows the interface'})" if p.exists()
                                else f"defaults (model {cfg.model}); create {p} to change them"))
        except Exception as e:  # noqa: BLE001
            checks.append(Check("fail", "config", str(e)))
            cfg = Config()

    if os.environ.get("ANTHROPIC_API_KEY"):
        checks.append(Check("warn", "ANTHROPIC_API_KEY",
                            "is set in your shell. Gloss removes it when calling claude so your subscription is used, "
                            "but other tools you run will bill that API key."))

    if cfg.provider == "anthropic_api":
        checks = [c for c in checks if c.name != "ANTHROPIC_API_KEY"]  # expected here, not a warning
        checks += api_checks()
    else:
        checks += cli_checks(cfg, live)

    missing = [p for p in PROMPTS if not (cfg.workspace / "prompts" / f"{p}.md").exists()]
    if not (cfg.workspace / "CLAUDE.md").exists() or missing:
        checks.append(Check("fail", "workspace", f"{cfg.workspace} is incomplete (missing: "
                                                 f"{', '.join(['CLAUDE.md'] * (not (cfg.workspace / 'CLAUDE.md').exists()) + missing)})"))
    else:
        checks.append(Check("ok", "workspace", str(cfg.workspace)))

    checks.append(Check("ok", "frontend", "built") if (STATIC_DIR / "index.html").exists()
                  else Check("fail", "frontend", "not built: cd frontend && npm install && npm run build"))
    checks.append(Check("ok", "library", f"new courses are saved in {cfg.library}"))
    return checks


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="gloss doctor", description="Check that Gloss can run here.")
    ap.add_argument("--live", action="store_true", help="also send a tiny test prompt (uses a little quota)")
    args = ap.parse_args(argv)
    marks = {"ok": "✓", "warn": "!", "fail": "✗"}
    checks = run_checks(live=args.live)
    for c in checks:
        print(f" {marks[c.status]} {c.name:<18} {c.detail}")
    fails = sum(c.status == "fail" for c in checks)
    print("\nAll good." if not fails else f"\n{fails} problem(s) to fix.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
