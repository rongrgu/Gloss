"""Environment check (DESIGN.md §10). Run with `gloss doctor` (or scripts/doctor.py)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Config, config_path
from .providers.adapters.codex import find_codex
from .providers.claude_cli import clean_env, find_claude
from .providers.factory import provider_name

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


def codex_logged_in(codex: str) -> bool | None:
    """`codex login status` exits 0 when logged in (docs; not checked against a real codex). None if it can't run."""
    try:
        return subprocess.run([codex, "login", "status"], capture_output=True, text=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
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


def provider_live_test(cfg: Config) -> Check:
    """One tiny request through Gloss's own provider code (any provider). Uses a little quota or money."""
    import asyncio

    from .providers import ErrorKind, ProviderError, RunRequest
    from .providers.factory import make_provider

    hints = {ErrorKind.NOT_INSTALLED: "not installed", ErrorKind.NOT_LOGGED_IN: "not logged in / key rejected",
             ErrorKind.QUOTA_EXCEEDED: "usage limit or credit exhausted"}
    try:
        p = make_provider(cfg)

        async def go():
            return [e async for e in p.run(RunRequest("Reply with exactly: ok", cwd=cfg.workspace))]
        evs = asyncio.run(go())
    except ProviderError as e:
        return Check("fail", "test prompt", str(e))
    last = evs[-1] if evs else None
    if last is None or last.kind != "done":
        kind = last.error_kind if last is not None else None
        return Check("fail", "test prompt", f"{hints.get(kind, 'failed')}: {(last.text if last else 'no output')[:200]}")
    return Check("ok", "test prompt", f"{p.name} answered {last.text.strip()[:40]!r}")


def sdk_check(module: str, extra: str) -> Check:
    try:
        mod = __import__(module)
        return Check("ok", f"{module} SDK", f"installed ({getattr(mod, '__version__', '?')})")
    except ImportError:
        return Check("fail", f"{module} SDK", f"missing: pip install 'glossr[{extra}]' (or uv sync --extra {extra})")


def key_check(var: str, provider: str) -> Check:
    # only whether it is set: the key itself is never printed
    return (Check("ok", "API key", f"{var} is set; calls are billed to it") if os.environ.get(var)
            else Check("fail", "API key", f'provider = "{provider}" needs {var}'))


def api_checks() -> list[Check]:
    return [sdk_check("anthropic", "anthropic"), key_check("ANTHROPIC_API_KEY", "anthropic_api")]


def codex_checks(cfg: Config, live: bool) -> list[Check]:
    """Codex CLI (DESIGN.md §6). Not verified against a real codex: `codex login status` output may differ."""
    codex = cfg.codex_bin or find_codex()
    if not codex:
        return [Check("fail", "codex", "not found on PATH. Install the Codex CLI and run `codex login`.")]
    try:
        ver = subprocess.run([codex, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        ver = f"(could not run: {e})"
    checks = [Check("ok", "codex", f"{codex}  {ver}")]
    try:
        st = subprocess.run([codex, "login", "status"], capture_output=True, text=True, timeout=20)
        first = (st.stdout or st.stderr).strip().splitlines()[:1]
        checks.append(Check("ok", "login", first[0] if first else "logged in") if st.returncode == 0
                      else Check("fail", "login", "not logged in. Run `codex login`."))
    except (OSError, subprocess.SubprocessError):
        checks.append(Check("warn", "login", "could not run `codex login status`; try `gloss doctor --live`"))
    if cfg.codex_auth != "api_key":
        for var in ("OPENAI_API_KEY", "CODEX_API_KEY"):
            if os.environ.get(var):
                checks.append(Check("warn", var, "is set in your shell. Gloss removes it when calling codex so your "
                                                 "Codex login is used (set [llm] codex_auth = \"api_key\" to bill the key)."))
    if live:
        checks.append(provider_live_test(cfg))
    return checks


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

    name = provider_name(cfg)
    if name is None:  # not set in config.toml: detect the installed CLI (§6.2)
        from .config import app_dir
        from .i18n import Prefs
        from .providers.factory import LABELS, choose_provider
        ch = choose_provider(cfg, Prefs(app_dir() / "prefs.json").provider)
        name = ch.name
        if ch.source == "none":
            checks.append(Check("fail", "AI", "neither Claude Code (`claude`) nor Codex (`codex`) is installed. "
                                              "Install one (https://claude.com/claude-code or the OpenAI Codex CLI) "
                                              "and log in, or set a provider in config.toml."))
        elif ch.source == "pending":
            checks.append(Check("ok", "AI", "both Claude Code and Codex are installed: choose one on Gloss's home page "
                                            "(Claude Code until you do)"))
        else:
            why = {"choice": "your choice in the app", "auto": "the only one installed"}[ch.source]
            checks.append(Check("ok", "AI", f"{LABELS[name]} ({why})"))
        if name is None:
            name = "none"
    if name != "claude":
        checks = [c for c in checks if c.name != "ANTHROPIC_API_KEY"]  # only matters for the claude CLI
    if name == "anthropic_api":
        checks += api_checks() + ([provider_live_test(cfg)] if live else [])
    elif name == "openai_api":
        checks += [sdk_check("openai", "openai"), key_check("OPENAI_API_KEY", "openai_api")]
        checks += [provider_live_test(cfg)] if live else []
    elif name == "codex":
        checks += codex_checks(cfg, live)
    elif name == "claude":
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
