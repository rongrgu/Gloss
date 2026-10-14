"""M0: probe the local `claude` CLI behaviors Gloss depends on.

Usage:  uv run --with pymupdf python scripts/probe_cli.py [--model haiku] [--only NAME ...]

Each probe prints PASS/FAIL/INFO lines; raw outputs go to the probe dir
(printed at the end) so results can be checked by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

CLAUDE = shutil.which("claude") or shutil.which("claude.cmd")
SETTINGS = {
    "permissions": {
        "allow": ["Read"],
        "deny": ["Bash", "Edit", "Write", "WebFetch", "WebSearch"],
    }
}
SECRET_WORD = "PERIWINKLE"  # only exists inside the probe image


def clean_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    return env


class Probe:
    def __init__(self, workdir: Path, model: str):
        self.dir = workdir
        self.model = model
        self.log = workdir / "_raw"
        self.log.mkdir(exist_ok=True)
        self.results: list[tuple[str, str, str]] = []

    def run(self, name: str, args: list[str], stdin: str = "", timeout: int = 180):
        cmd = [CLAUDE, "-p", "--model", self.model, *args]
        t0 = time.monotonic()
        p = subprocess.run(
            cmd, input=stdin, capture_output=True, text=True,
            cwd=self.dir, env=clean_env(), timeout=timeout,
        )
        dt = time.monotonic() - t0
        (self.log / f"{name}.cmd.txt").write_text(" ".join(cmd) + "\n\nSTDIN:\n" + stdin)
        (self.log / f"{name}.stdout.txt").write_text(p.stdout)
        (self.log / f"{name}.stderr.txt").write_text(p.stderr)
        return p, dt

    def json(self, name: str, args: list[str], stdin: str = ""):
        p, dt = self.run(name, ["--output-format", "json", *args], stdin)
        try:
            return json.loads(p.stdout), p, dt
        except json.JSONDecodeError:
            return None, p, dt

    def report(self, status: str, name: str, detail: str):
        self.results.append((status, name, detail))
        print(f"[{status}] {name}: {detail}", flush=True)


# --- probes -----------------------------------------------------------------

def p_json(pr: Probe):
    """Basic -p with prompt on stdin, json output shape."""
    out, p, dt = pr.json("json", [], stdin="Reply with exactly: pong")
    if out is None:
        return pr.report("FAIL", "json", f"rc={p.returncode} stdout not json; stderr={p.stderr[:300]}")
    pr.report("INFO", "json", f"keys={sorted(out)} ({dt:.1f}s)")
    pr.report("PASS" if "pong" in str(out.get("result", "")).lower() else "FAIL",
              "json.result", repr(out.get("result"))[:100])
    pr.report("INFO", "json.session_id", str(out.get("session_id")))
    pr.report("INFO", "json.cost", f"total_cost_usd={out.get('total_cost_usd')} usage={out.get('usage')}")


def p_resume(pr: Probe):
    """Does --resume keep context, and does session_id change?"""
    a, _, _ = pr.json("resume.1", [], stdin=f"Remember the number 7319. Reply only: ok")
    if not a:
        return pr.report("FAIL", "resume", "first call failed")
    sid1 = a["session_id"]
    b, _, _ = pr.json("resume.2", ["--resume", sid1], stdin="What number did I ask you to remember? Digits only.")
    if not b:
        return pr.report("FAIL", "resume", "resume call failed")
    sid2 = b["session_id"]
    pr.report("PASS" if "7319" in b.get("result", "") else "FAIL", "resume.context", repr(b.get("result")))
    pr.report("INFO", "resume.session_id", f"{sid1} -> {sid2} ({'SAME' if sid1 == sid2 else 'CHANGED'})")
    c, _, _ = pr.json("resume.3", ["--resume", sid2], stdin="And what number was it again? Digits only.")
    if c:
        pr.report("PASS" if "7319" in c.get("result", "") else "FAIL", "resume.chain",
                  f"{repr(c.get('result'))} sid={c['session_id']} ({'SAME' if c['session_id'] == sid2 else 'CHANGED'})")


def p_session_id(pr: Probe):
    """Can we pre-assign the session id with --session-id?"""
    sid = str(uuid.uuid4())
    out, p, _ = pr.json("session_id", ["--session-id", sid], stdin="Reply only: ok")
    if not out:
        return pr.report("FAIL", "session_id", f"rc={p.returncode} {p.stderr[:200]}")
    pr.report("PASS" if out["session_id"] == sid else "FAIL", "session_id.preassign",
              f"asked {sid}, got {out['session_id']}")


def p_resume_missing(pr: Probe):
    """What does --resume with an unknown id look like (for rebuild logic)?"""
    bogus = str(uuid.uuid4())
    out, p, _ = pr.json("resume_missing", ["--resume", bogus], stdin="hi")
    pr.report("INFO", "resume_missing",
              f"rc={p.returncode} json={json.dumps(out)[:300] if out else None} stderr={p.stderr.strip()[:300]!r}")


def p_tools(pr: Probe):
    """Only Read is usable: Bash and Write must be refused, file must not appear."""
    target = pr.dir / "pwned.txt"
    target.unlink(missing_ok=True)
    args = ["--tools", "Read", "--allowedTools", "Read", "--permission-prompts", "none"]
    out, p, _ = pr.json("tools.bash", args, stdin=(
        "Use the Bash tool to run: echo hi > pwned.txt . If you cannot, use the Write tool "
        "to create pwned.txt containing hi. Then report exactly what happened."))
    pr.report("PASS" if not target.exists() else "FAIL", "tools.no_write",
              f"pwned.txt exists={target.exists()}; result={repr(out.get('result'))[:200] if out else p.stderr[:200]}")
    if out:
        pr.report("INFO", "tools.permission_denials", json.dumps(out.get("permission_denials"))[:300])

    # Same but WITHOUT --tools: rely only on settings.json + --allowedTools.
    target.unlink(missing_ok=True)
    out2, p2, _ = pr.json("tools.settings_only", ["--allowedTools", "Read", "--permission-prompts", "none"], stdin=(
        "Use the Bash tool to run: echo hi > pwned.txt . Then report exactly what happened."))
    pr.report("PASS" if not target.exists() else "FAIL", "tools.settings_only.no_write",
              f"pwned.txt exists={target.exists()}; denials={json.dumps(out2.get('permission_denials'))[:200] if out2 else p2.stderr[:200]}")


def p_read_outside(pr: Probe):
    """Can Read reach files outside cwd? (affects privacy of the user's disk)"""
    outside = Path(tempfile.mkdtemp(prefix="gloss_outside_")) / "outside.txt"
    outside.write_text("the outside code is MARMALADE\n")
    args = ["--tools", "Read", "--allowedTools", "Read", "--permission-prompts", "none"]
    out, p, _ = pr.json("read_outside", args, stdin=f"Read the file {outside} and tell me the code in it, one word.")
    got = out and "MARMALADE" in out.get("result", "")
    pr.report("INFO", "read_outside.default", f"read outside cwd = {bool(got)}; result={repr(out.get('result'))[:150] if out else p.stderr[:150]}")
    out, p, _ = pr.json("read_outside.restricted", ["--restricted", *args],
                        stdin=f"Read the file {outside} and tell me the code in it, one word.")
    got = out and "MARMALADE" in out.get("result", "")
    pr.report("INFO", "read_outside.restricted", f"read outside cwd = {bool(got)}; result={repr(out.get('result'))[:150] if out else p.stderr[:150]}")


def p_image(pr: Probe):
    """Claude reads a PNG via the Read tool given a relative path."""
    img = pr.dir / "courses" / "probe" / "slide1.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    make_png(img)
    args = ["--tools", "Read", "--allowedTools", "Read", "--permission-prompts", "none"]
    out, p, dt = pr.json("image", args, stdin=(
        f"Use the Read tool to look at the image courses/probe/slide1.png. "
        "What single capitalised word is written in it? Reply with just the word."))
    ok = out and SECRET_WORD in out.get("result", "").upper()
    pr.report("PASS" if ok else "FAIL", "image.read",
              f"{repr(out.get('result'))[:100] if out else p.stderr[:200]} ({dt:.1f}s)")


def p_stream(pr: Probe):
    """stream-json event shapes; with and without --include-partial-messages."""
    for name, extra in [("stream", []), ("stream.partial", ["--include-partial-messages"])]:
        p, dt = pr.run(name, ["--output-format", "stream-json", "--verbose", *extra],
                       stdin="Count from 1 to 15, one number per line.")
        types: dict[str, int] = {}
        for line in p.stdout.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = ev.get("type", "?")
            if key == "stream_event":
                key += ":" + ev.get("event", {}).get("type", "?")
            elif "subtype" in ev:
                key += ":" + ev["subtype"]
            types[key] = types.get(key, 0) + 1
        pr.report("PASS" if p.returncode == 0 and types else "FAIL", name,
                  f"rc={p.returncode} events={types} ({dt:.1f}s) stderr={p.stderr.strip()[:150]!r}")
    # Does stream-json work without --verbose?
    p, _ = pr.run("stream.noverbose", ["--output-format", "stream-json"], stdin="say hi")
    pr.report("INFO", "stream.requires_verbose", f"rc={p.returncode} stderr={p.stderr.strip()[:200]!r}")


def p_system_prompt(pr: Probe):
    """--append-system-prompt applies; does it survive --resume without being re-passed?"""
    sp = "Always end every reply with the token <<GLOSS>>."
    a, _, _ = pr.json("sysprompt.1", ["--append-system-prompt", sp], stdin="Say hello.")
    if not a:
        return pr.report("FAIL", "sysprompt", "call failed")
    pr.report("PASS" if "<<GLOSS>>" in a.get("result", "") else "FAIL", "sysprompt.applies", repr(a.get("result"))[:120])
    b, _, _ = pr.json("sysprompt.2", ["--resume", a["session_id"]], stdin="Say goodbye.")
    if b:
        pr.report("INFO", "sysprompt.after_resume_without_flag", f"marker present={'<<GLOSS>>' in b.get('result', '')}: {repr(b.get('result'))[:120]}")


def p_workspace_claude_md(pr: Probe):
    """Is claude_workspace/CLAUDE.md picked up from cwd in -p mode?"""
    out, _, _ = pr.json("claude_md", [], stdin="What is the codename defined in your project instructions? One word.")
    pr.report("PASS" if out and "HEDGEHOG" in out.get("result", "").upper() else "FAIL",
              "claude_md.loaded", repr(out.get("result"))[:120] if out else "call failed")
    out, _, _ = pr.json("claude_md.project_only", ["--setting-sources", "project"],
                        stdin="What is the codename defined in your project instructions? One word.")
    pr.report("INFO", "claude_md.setting_sources_project",
              repr(out.get("result"))[:120] if out else "call failed")


def p_json_schema(pr: Probe):
    """--json-schema for the tree-generation step."""
    schema = {"type": "object", "properties": {"title": {"type": "string"},
              "slides": {"type": "array", "items": {"type": "integer"}}}, "required": ["title", "slides"]}
    out, p, _ = pr.json("json_schema", ["--json-schema", json.dumps(schema)],
                        stdin="Give a title for a lecture on attention, covering slides 1 to 4.")
    if not out:
        return pr.report("FAIL", "json_schema", p.stderr[:200])
    pr.report("INFO", "json_schema", f"keys={sorted(out)} structured_output={json.dumps(out.get('structured_output'))[:200]} result={repr(out.get('result'))[:150]}")


def p_auth_with_api_key(pr: Probe):
    """Sanity: what does a bogus ANTHROPIC_API_KEY do? (doctor should warn about this)"""
    env = clean_env()
    env["ANTHROPIC_API_KEY"] = "sk-ant-bogus"
    p = subprocess.run([CLAUDE, "-p", "--model", pr.model, "--output-format", "json"], input="hi",
                       capture_output=True, text=True, cwd=pr.dir, env=env, timeout=120)
    (pr.log / "bogus_key.stdout.txt").write_text(p.stdout)
    pr.report("INFO", "bogus_api_key", f"rc={p.returncode} out={p.stdout.strip()[:250]!r} err={p.stderr.strip()[:150]!r}")


def p_restricted(pr: Probe):
    """The candidate production flag set: --restricted + Read only. Does everything we need still work?"""
    img = pr.dir / "courses" / "probe" / "slide1.png"
    if not img.exists():
        img.parent.mkdir(parents=True, exist_ok=True)
        make_png(img)
    base = ["--restricted", "--tools", "Read", "--allowedTools", "Read", "--permission-prompts", "none"]
    a, p, _ = pr.json("restricted.1", base, stdin=(
        "1) What is the project codename from your project instructions? "
        "2) Use Read on courses/probe/slide1.png: what word is in it? Answer as: codename,word"))
    if not a:
        return pr.report("FAIL", "restricted", p.stderr[:300])
    r = a.get("result", "").upper()
    pr.report("INFO", "restricted.claude_md_loaded", str("HEDGEHOG" in r))
    pr.report("PASS" if SECRET_WORD in r else "FAIL", "restricted.image", repr(a.get("result"))[:120])
    pr.report("INFO", "restricted.stderr", repr(p.stderr.strip()[:200]))
    b, _, _ = pr.json("restricted.2", [*base, "--resume", a["session_id"]], stdin="What word was in the image? One word.")
    pr.report("PASS" if b and SECRET_WORD in b.get("result", "").upper() else "FAIL", "restricted.resume",
              repr(b.get("result"))[:80] if b else "failed")


def p_custom_system_prompt(pr: Probe):
    """--system-prompt replaces the (large) default prompt: token cost, and does Read still work?"""
    base = ["--restricted", "--tools", "Read", "--allowedTools", "Read", "--permission-prompts", "none"]
    a, _, _ = pr.json("sysfull.default", base, stdin="Reply only: ok")
    b, _, _ = pr.json("sysfull.custom", [*base, "--system-prompt", "You are a teaching assistant. Be concise."],
                      stdin="Use the Read tool on courses/probe/slide1.png and reply with the single word in it.")
    def tokens(o):
        u = o.get("usage", {}) if o else {}
        return u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
    pr.report("INFO", "sysfull.default_tokens", f"{tokens(a)} input tokens, cost={a.get('total_cost_usd') if a else None}")
    pr.report("INFO", "sysfull.custom_tokens", f"{tokens(b)} input tokens (2 turns incl. image), cost={b.get('total_cost_usd') if b else None}")
    pr.report("PASS" if b and SECRET_WORD in b.get("result", "").upper() else "FAIL", "sysfull.read_works",
              repr(b.get("result"))[:80] if b else "failed")
    c, _, _ = pr.json("sysfull.claude_md", [*base, "--system-prompt", "You are a teaching assistant."],
                      stdin="What is the project codename from your project instructions? One word, or NONE.")
    pr.report("INFO", "sysfull.claude_md_still_loaded", repr(c.get("result"))[:80] if c else "failed")


PROBES = {f.__name__[2:]: f for f in [
    p_json, p_resume, p_session_id, p_resume_missing, p_tools, p_read_outside,
    p_image, p_stream, p_system_prompt, p_workspace_claude_md, p_json_schema, p_auth_with_api_key,
    p_restricted, p_custom_system_prompt,
]}


def make_png(path: Path):
    import pymupdf  # noqa: PLC0415

    doc = pymupdf.open()
    page = doc.new_page(width=480, height=270)
    page.insert_text((60, 150), SECRET_WORD, fontsize=48)
    page.get_pixmap(dpi=96).save(path)


def setup_workspace(d: Path):
    (d / ".claude").mkdir(parents=True)
    (d / ".claude" / "settings.json").write_text(json.dumps(SETTINGS, indent=2))
    (d / "CLAUDE.md").write_text("# Probe workspace\n\nThe project codename is HEDGEHOG.\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--only", nargs="*", choices=list(PROBES))
    ap.add_argument("--dir", type=Path, help="probe workspace (default: new temp dir)")
    args = ap.parse_args()
    if not CLAUDE:
        sys.exit("claude not found on PATH")
    print(f"claude = {CLAUDE}  version = {subprocess.run([CLAUDE, '--version'], capture_output=True, text=True).stdout.strip()}")
    print(f"ANTHROPIC_API_KEY in parent env: {'ANTHROPIC_API_KEY' in os.environ} (stripped for probes)")

    d = args.dir or Path(tempfile.mkdtemp(prefix="gloss_probe_"))
    if not (d / ".claude").exists():
        setup_workspace(d)
    pr = Probe(d, args.model)
    for name in args.only or PROBES:
        try:
            PROBES[name](pr)
        except Exception as e:  # keep going; one broken probe shouldn't hide the rest
            pr.report("FAIL", name, f"exception {type(e).__name__}: {e}")
    print(f"\nraw outputs: {pr.log}")
    fails = [r for r in pr.results if r[0] == "FAIL"]
    print(f"{len(fails)} FAIL / {len(pr.results)} checks")


if __name__ == "__main__":
    main()
