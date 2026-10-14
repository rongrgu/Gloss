"""Live end-to-end check against the real `claude` CLI (uses a little quota).

    uv run python scripts/smoke_live.py [--model haiku]

Generates a deck, explains a slide, asks a question on a region, follows up,
then deletes the Claude session to force a rebuild and follows up again.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from make_pdfs import make_deck  # noqa: E402

from gloss.config import Config  # noqa: E402
from gloss.ingest import ingest_pdf  # noqa: E402
from gloss.jobs import JobManager  # noqa: E402
from gloss.providers.claude_cli import ClaudeCLIProvider  # noqa: E402
from gloss.store import Course  # noqa: E402
from gloss.tutor import Tutor  # noqa: E402


async def follow(label: str, job) -> dict:
    t0, first = time.monotonic(), None
    text = ""
    async for ev in job.subscribe():
        if ev["type"] == "delta":
            first = first or time.monotonic() - t0
            text += ev["text"]
        elif ev["type"] == "reset":
            text = ""
        elif ev["type"] == "error":
            raise SystemExit(f"[{label}] ERROR: {ev['message']}")
    print(f"\n=== {label}  (first token {first or 0:.1f}s, total {time.monotonic() - t0:.1f}s)\n{text.strip()}")
    return ev


async def main(model: str) -> None:
    cfg = Config.load()
    provider = ClaudeCLIProvider(cfg.workspace, model=model, timeout=300)
    tutor = Tutor(provider, JobManager(2), cfg.workspace, language=cfg.language)
    path = Path(tempfile.mkdtemp(prefix="gloss_smoke_")) / "smoke.gloss"
    course = Course.create(path, "Transformer 注意力机制")
    ingest_pdf(course, make_deck())
    try:
        await follow("explanation p2", tutor.explain(course, 2))
        thread, _, job = tutor.ask(course, 2, (0.0, 0.25, 1.0, 0.3), "为什么要除以 sqrt(d)？一两句话。")
        await follow("ask #1", job)
        sid = course.get_session(2)
        _, job = tutor.follow_up(course, thread.id, "如果不除会怎样？一句话。")
        await follow("follow-up #1 (resume)", job)
        assert course.get_session(2) == sid, "session id changed on resume"
        t2, _, job = tutor.ask(course, 2, (0.0, 0.0, 1.0, 0.2), "这一页标题是什么意思？一句话。")
        await follow("ask #2 (same session)", job)
        course.set_session(2, str(uuid.uuid4()))  # simulate a lost session
        _, job = tutor.follow_up(course, thread.id, "总结一下我们关于 Thread #1 聊了什么，一句话。")
        await follow("follow-up after lost session (rebuild)", job)
        assert course.get_session(2) != sid
        print("\nOK. session log:")
        for m in tutor.session_log(course, 2)["messages"]:
            print(f"  {m['role']:9} #{m['thread']}: {m['content'][:70]!r}")
    finally:
        course.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="haiku")
    asyncio.run(main(ap.parse_args().model))
