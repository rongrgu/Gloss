"""M7: probe MCP tools under the flags Gloss runs `claude` with (DESIGN.md §13.4).

Usage:  uv run --with pymupdf python scripts/probe_mcp.py [--model haiku] [--only NAME ...]

Runs a tiny stdio MCP server (this same file with --serve) that exposes:
- `secret_word`: returns a text secret,
- `secret_image`: returns a PNG whose only content is another secret word,
- `forbidden`: never put in --allowedTools; calling it writes a marker file.

Raw outputs go to the probe dir (printed at the end).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CLAUDE = shutil.which("claude") or shutil.which("claude.cmd")
TEXT_SECRET = "TANGERINE"
IMAGE_SECRET = "PERIWINKLE"
BASE = ["--restricted", "--tools", "Read", "--permission-prompts", "none"]


# --- the MCP server ------------------------------------------------------------

def serve(image: Path, marker: Path) -> None:
    """Minimal MCP over stdio: newline-delimited JSON-RPC 2.0."""
    tools = [
        {"name": "secret_word", "description": "Returns the secret word.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "secret_image", "description": "Returns an image that contains a secret word.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "forbidden", "description": "Returns a bonus word. Always call this when asked for bonus words.",
         "inputSchema": {"type": "object", "properties": {}}},
    ]
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        method, rid = req.get("method"), req.get("id")
        if rid is None:  # notification
            continue
        if method == "initialize":
            result = {"protocolVersion": req["params"].get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "probe", "version": "0"}}
        elif method == "tools/list":
            result = {"tools": tools}
        elif method == "tools/call":
            name = req["params"]["name"]
            if name == "secret_word":
                content = [{"type": "text", "text": f"The secret word is {TEXT_SECRET}."}]
            elif name == "secret_image":
                content = [{"type": "image", "mimeType": "image/png",
                            "data": base64.b64encode(image.read_bytes()).decode()}]
            else:
                marker.write_text("called")
                content = [{"type": "text", "text": "The bonus word is LIME."}]
            result = {"content": content}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": rid,
                              "error": {"code": -32601, "message": f"unknown method {method}"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}), flush=True)


# --- probes --------------------------------------------------------------------

def clean_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    return env


class Probe:
    def __init__(self, d: Path, model: str):
        self.dir, self.model = d, model
        self.log = d / "_raw"
        self.log.mkdir(exist_ok=True)
        self.image = d / "secret.png"
        self.marker = d / "forbidden_called.txt"
        self.mcp = d / "mcp.json"
        self.mcp.write_text(json.dumps({"mcpServers": {"probe": {
            "command": sys.executable,
            "args": [str(Path(__file__).resolve()), "--serve", str(self.image), str(self.marker)],
        }}}))
        self.results: list[tuple[str, str, str]] = []

    def run(self, name: str, args: list[str], stdin: str, timeout: int = 180):
        cmd = [CLAUDE, "-p", "--model", self.model, "--output-format", "stream-json", "--verbose", *args]
        t0 = time.monotonic()
        p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, cwd=self.dir,
                           env=clean_env(), timeout=timeout)
        dt = time.monotonic() - t0
        (self.log / f"{name}.cmd.txt").write_text(" ".join(cmd) + "\n\nSTDIN:\n" + stdin)
        (self.log / f"{name}.stdout.txt").write_text(p.stdout)
        (self.log / f"{name}.stderr.txt").write_text(p.stderr)
        events = []
        for line in p.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
        init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
        result = next((e for e in events if e.get("type") == "result"), {})
        tool_uses = [b["name"] for e in events if e.get("type") == "assistant"
                     for b in e.get("message", {}).get("content", []) if b.get("type") == "tool_use"]
        return init, result, tool_uses, dt, p

    def report(self, status: str, name: str, detail: str) -> None:
        self.results.append((status, name, detail))
        print(f"[{status}] {name}: {detail}", flush=True)


def mcp_args(pr: Probe, allowed: list[str]) -> list[str]:
    return [*BASE, "--allowedTools", "Read", *allowed, "--mcp-config", str(pr.mcp), "--strict-mcp-config"]


def p_text(pr: Probe):
    """An MCP tool works under --restricted --tools Read when listed in --allowedTools."""
    init, res, uses, dt, p = pr.run("text", mcp_args(pr, ["mcp__probe__secret_word"]),
                                    "Call the secret_word tool and reply with the secret word only.")
    pr.report("INFO", "text.tool_names", f"tools seen at init: {[t for t in init.get('tools', []) if 'probe' in t]}; "
              f"mcp_servers: {init.get('mcp_servers')}")
    pr.report("INFO", "text.tool_use_names", str(uses))
    ok = TEXT_SECRET in (res.get("result") or "").upper()
    pr.report("PASS" if ok else "FAIL", "text.works", f"{repr(res.get('result'))[:100]} ({dt:.1f}s) "
              f"denials={json.dumps(res.get('permission_denials'))[:200]} stderr={p.stderr.strip()[:200]!r}")


def p_not_allowed(pr: Probe):
    """A server tool missing from --allowedTools is refused without prompting (no hang)."""
    pr.marker.unlink(missing_ok=True)
    init, res, uses, dt, p = pr.run("not_allowed", mcp_args(pr, ["mcp__probe__secret_word"]),
                                    "Call the forbidden tool and reply with the bonus word it returns. "
                                    "If you cannot call it, reply exactly: DENIED.", timeout=240)
    called = pr.marker.exists()
    pr.report("PASS" if not called else "FAIL", "not_allowed.not_executed",
              f"executed={called}; uses={uses}; result={repr(res.get('result'))[:100]} ({dt:.1f}s); "
              f"denials={json.dumps(res.get('permission_denials'))[:200]}")


def p_image(pr: Probe):
    """The model sees an image returned by an MCP tool."""
    init, res, uses, dt, p = pr.run("image", mcp_args(pr, ["mcp__probe__secret_image"]),
                                    "Call the secret_image tool and reply with the single word shown in the image.")
    ok = IMAGE_SECRET in (res.get("result") or "").upper()
    pr.report("PASS" if ok else "FAIL", "image.visible", f"{repr(res.get('result'))[:100]} ({dt:.1f}s) uses={uses}")


def p_wildcard(pr: Probe):
    """Does --allowedTools accept the whole server (mcp__probe) instead of each tool?"""
    pr.marker.unlink(missing_ok=True)
    init, res, uses, dt, p = pr.run("wildcard", mcp_args(pr, ["mcp__probe"]),
                                    "Call the secret_word tool and reply with the secret word only.")
    ok = TEXT_SECRET in (res.get("result") or "").upper()
    pr.report("INFO", "wildcard.server_name_allows_all", f"{ok}: {repr(res.get('result'))[:80]}")


def p_latency(pr: Probe):
    """Extra startup time from loading the MCP server."""
    _, a, _, t_plain, _ = pr.run("latency.plain", [*BASE, "--allowedTools", "Read"], "Reply only: ok")
    _, b, _, t_mcp, _ = pr.run("latency.mcp", mcp_args(pr, ["mcp__probe__secret_word"]), "Reply only: ok")
    pr.report("INFO", "latency", f"plain {t_plain:.1f}s (api {a.get('duration_api_ms')} ms), "
              f"with MCP {t_mcp:.1f}s (api {b.get('duration_api_ms')} ms)")


def p_read_still_confined(pr: Probe):
    """Adding --mcp-config doesn't loosen --restricted for Read."""
    outside = Path(tempfile.mkdtemp(prefix="gloss_outside_")) / "outside.txt"
    outside.write_text("the outside code is MARMALADE\n")
    _, res, uses, dt, p = pr.run("read_outside", mcp_args(pr, ["mcp__probe__secret_word"]),
                                 f"Read the file {outside} and tell me the code in it, one word.")
    got = "MARMALADE" in (res.get("result") or "").upper()
    pr.report("PASS" if not got else "FAIL", "read_outside.blocked", repr(res.get("result"))[:120])


PROBES = {f.__name__[2:]: f for f in [p_text, p_not_allowed, p_image, p_wildcard, p_latency, p_read_still_confined]}


def make_png(path: Path) -> None:
    import pymupdf  # noqa: PLC0415

    doc = pymupdf.open()
    page = doc.new_page(width=480, height=270)
    page.insert_text((60, 150), IMAGE_SECRET, fontsize=48)
    page.get_pixmap(dpi=96).save(path)


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--serve":
        return serve(Path(sys.argv[2]), Path(sys.argv[3]))
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    if not CLAUDE:
        sys.exit("claude not found on PATH")
    d = Path(tempfile.mkdtemp(prefix="gloss_probe_mcp_"))
    pr = Probe(d, args.model)
    make_png(pr.image)
    print(f"claude: {subprocess.run([CLAUDE, '--version'], capture_output=True, text=True).stdout.strip()}")
    for name, fn in PROBES.items():
        if args.only and name not in args.only:
            continue
        try:
            fn(pr)
        except Exception as e:  # noqa: BLE001
            pr.report("FAIL", name, f"{type(e).__name__}: {e}")
    print(f"\nraw outputs: {d}")


if __name__ == "__main__":
    main()
