"""Catch-up for the library home (DESIGN.md §17): where the student left off, read from what is on disk.

No LLM call: the last lecture and page (the workspace saves `ui_state.page`), when it was last touched
(questions, notes, explanations, opening it), questions still marked "still confused", unfinished
quizzes, the latest cheatsheet and open weak points. Everything is read-only (SQLite `mode=ro`).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .cheatsheet import all_sheets
from .folders import QUIZ_FILE, Folders
from .profile import ACTIVE, ProfileStore

MAX_CONTINUE = 3
MAX_CONFUSED = 5
MAX_QUIZZES = 3
MAX_WEAK = 4
QUESTION_MAX = 160


def _lecture(path: Path) -> dict[str, Any] | None:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        one = lambda sql: (conn.execute(sql).fetchone() or [None])[0]  # noqa: E731
        times = [one("SELECT MAX(created_at) FROM messages"), one("SELECT MAX(updated_at) FROM notes WHERE content != ''"),
                 one("SELECT MAX(created_at) FROM explanations")]
        page = None
        if "ui_state" in tables:
            row = conn.execute("SELECT value FROM ui_state WHERE key = 'page'").fetchone()
            try:
                page = int(json.loads(row[0])) if row else None
            except (TypeError, ValueError):
                page = None
        confused = []
        cols = {r[1] for r in conn.execute("PRAGMA table_info(threads)")}
        if "understood" in cols:  # schema v3 (§15.2)
            for tid, pg, question, at in conn.execute(
                    "SELECT t.id, t.page, (SELECT content FROM messages m WHERE m.thread_id = t.id AND m.role = 'user' "
                    "ORDER BY m.id LIMIT 1), (SELECT MAX(created_at) FROM messages m WHERE m.thread_id = t.id) "
                    "FROM threads t WHERE t.understood = 0"):
                confused.append({"thread": tid, "page": pg, "question": (question or "")[:QUESTION_MAX], "at": at or ""})
        studied = one("SELECT COUNT(*) FROM threads") or one("SELECT COUNT(*) FROM explanations")
        return {"title": meta.get("title") or path.stem, "course_id": meta.get("course_id"),
                "slides": one("SELECT COUNT(*) FROM slides") or 0, "questions": one("SELECT COUNT(*) FROM threads") or 0,
                "status": meta.get("lecture_status") or ("studying" if studied else "new"),
                "page": page, "active_at": max((t for t in times if t), default=""), "confused": confused}
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def catch_up(root: Path, recent: list[dict[str, Any]]) -> dict[str, Any]:
    folders = Folders(root)
    opened = {it["path"]: it.get("opened_at", "") for it in recent}
    lectures, confused = [], []
    for p, _info in folders.all_lectures(folders.folder("")):
        lec = _lecture(p)
        if lec is None:
            continue
        rel = folders.rel(p.parent)
        lec.update(file=str(p), folder=rel, active_at=max(lec["active_at"], opened.get(str(p.resolve()), "")))
        confused += [{**c, "lecture": lec["title"], "file": str(p), "folder": rel} for c in lec.pop("confused")]
        lectures.append(lec)
    touched = sorted((x for x in lectures if x["active_at"]), key=lambda x: x["active_at"], reverse=True)
    last = touched[0] if touched else None
    keep_going = [x for x in touched[1:] if x["status"] != "done"][:MAX_CONTINUE]

    dirs = [root, *sorted(d for d in root.rglob("*") if d.is_dir() and not any(s.startswith(".") for s in d.relative_to(root).parts))]
    quizzes = []
    for d in dirs:
        if (d / QUIZ_FILE).exists():
            from .quiz import QuizStore  # quiz imports a lot; only when there are quizzes
            # only quizzes in progress: ones not started yet are in the quiz list next to it (§17)
            quizzes += [{**q, "folder": folders.rel(d)} for q in QuizStore(d).list() if 0 < q["answered"] < q["count"]]
    quizzes.sort(key=lambda q: q["created_at"], reverse=True)

    weak = []
    for d in sorted({Path(x["file"]).parent for x in lectures}):
        for w in ProfileStore(d).weak_points(ACTIVE):
            weak.append({"concept": w["concept"], "status": w["status"], "folder": folders.rel(d)})

    sheets = sorted(({**s, "folder": folders.rel(d)} for d, s in all_sheets(root)), key=lambda s: s["updated_at"], reverse=True)
    return {
        "last": last,
        "continue": keep_going,
        "confused": sorted(confused, key=lambda c: c["at"], reverse=True)[:MAX_CONFUSED],
        "confused_total": len(confused),
        "quizzes": [{k: q[k] for k in ("id", "title", "folder", "count", "answered", "correct", "created_at")} for q in quizzes[:MAX_QUIZZES]],
        "weak_points": weak[:MAX_WEAK],
        "weak_total": len(weak),
        "sheet": ({k: sheets[0][k] for k in ("id", "title", "folder", "updated_at")} if sheets else None),
    }
