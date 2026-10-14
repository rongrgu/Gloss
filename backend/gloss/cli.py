"""`gloss` command.

    gloss                 start the app and open it in the browser
    gloss doctor [--live] check that Gloss can run here
    gloss export FILE     write a course as Markdown
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

DEFAULT_PORT = 8765


def free_port(start: int, tries: int = 20) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit(f"no free port in {start}–{start + tries - 1}; pass --port")


def open_when_ready(url: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url + "api/health", timeout=2).close()
            webbrowser.open(url)
            return
        except OSError:
            time.sleep(0.3)


def serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .config import Config
    from .doctor import run_checks
    from .server import create_app

    cfg = Config.load()
    problems = [c for c in run_checks(cfg) if c.status == "fail"]
    for c in problems:
        print(f"  ✗ {c.name}: {c.detail}", file=sys.stderr)
    if problems:
        print("  (run `gloss doctor` for details; starting anyway)\n", file=sys.stderr)

    port = args.port or free_port(DEFAULT_PORT)
    url = f"http://127.0.0.1:{port}/"
    print(f"Gloss is running at {url}  (Ctrl+C to stop)", flush=True)
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
    uvicorn.run(create_app(cfg), host="127.0.0.1", port=port, log_level="warning")
    return 0


def export(args: argparse.Namespace) -> int:
    from .config import app_dir
    from .export import export_markdown
    from .i18n import Prefs
    from .store import Course

    src = Path(args.file)
    out = Path(args.output) if args.output else src.with_suffix(".md")
    with Course.open(src) as c:
        out.write_text(export_markdown(c, Prefs(app_dir() / "prefs.json").locale), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="gloss", description="Ask questions right on your lecture slides.")
    ap.add_argument("--port", type=int, help=f"port to listen on (default: first free from {DEFAULT_PORT})")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    sub = ap.add_subparsers(dest="cmd")
    d = sub.add_parser("doctor", help="check that Gloss can run here")
    d.add_argument("--live", action="store_true", help="also send a tiny test prompt (uses a little quota)")
    e = sub.add_parser("export", help="export a .gloss course as Markdown")
    e.add_argument("file")
    e.add_argument("-o", "--output", help="output path (default: next to the file, .md)")
    args = ap.parse_args(argv)

    if args.cmd == "doctor":
        from .doctor import main as doctor_main
        sys.exit(doctor_main(["--live"] if args.live else []))
    if args.cmd == "export":
        sys.exit(export(args))
    sys.exit(serve(args))


if __name__ == "__main__":
    main()
