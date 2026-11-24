"""Study profile: a course's weak points by concept, with evidence (DESIGN.md §15.4).

`study.glossp` lives in the course folder (the folder that directly holds the lectures). Two kinds of update:

- A (after a lecture summary, or "organize" on demand): one Claude call returns a list of operations
  (add / attach / merge / status / edit). The backend validates every id and every piece of evidence,
  assigns new ids, refuses unsupported "resolved", leaves pinned items' status alone, and logs a revision.
- B (quiz answers and understood marks): deterministic, no Claude. Evidence attaches to weak points on
  the same lecture page (or the same question); otherwise wrong answers wait in `pending` for A.

Evidence is passed to Claude through short handles (F1… for the summary's findings, P1… for pending),
the same idea as the summary's T1… question handles.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import schema
from .folders import Folders
from .jobs import Job
from .store import Course, now

if TYPE_CHECKING:
    from .tutor import Tutor

STUDY_FILE = "study.glossp"
STATUSES = ("open", "improving", "resolved")
ACTIVE = ("open", "improving")
MAX_ACTIVE = 15  # open + improving; above this the update prompt asks for merging first
MAX_EVIDENCE = 8  # kept per weak point; older ones only add to `extra_evidence`
MAX_RESOLVED_NAMES = 20  # resolved items go into prompts by name only, so a concept that comes back keeps its id
RIGHTS_TO_IMPROVE = 2  # open -> improving after this many right quiz answers in a row
AUTO_TRIGGERS = ("digest", "organize", "quiz", "mark")
RELEVANT_LIMIT, RELEVANT_CHARS = 3, 300  # §15.5.1: at most this many weak points, about this much text
RELEVANT_HEAD = ("## 学生之前卡住过的地方（学习档案）\n\n"
                 "只有这一页的内容**确实相关**时才联系它们，一页最多提一次；不相关就完全不提，"
                 "不要每页都提醒学生哪里弱。提到时像老师一样自然地说（例如\"你之前在这里卡住过\"），"
                 "不要提\"学习档案\"\"记录\"这类说法。\n\n")

_V1 = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE weak_points (
  id TEXT PRIMARY KEY, n INTEGER, concept TEXT, summary TEXT, status TEXT,
  pinned INTEGER DEFAULT 0, extra_evidence INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT
);
CREATE TABLE evidence (
  id INTEGER PRIMARY KEY, wp_id TEXT, kind TEXT, lecture TEXT, page INTEGER, ref TEXT,
  polarity INTEGER, note TEXT, created_at TEXT
);
CREATE INDEX evidence_wp ON evidence (wp_id);
CREATE TABLE pending (
  id INTEGER PRIMARY KEY, kind TEXT, lecture TEXT, page INTEGER, ref TEXT, polarity INTEGER, note TEXT, created_at TEXT
);
CREATE TABLE revisions (
  id INTEGER PRIMARY KEY, trigger TEXT, changes TEXT, before TEXT, undone INTEGER DEFAULT 0, created_at TEXT
);
"""


def _v0_to_v1(conn: sqlite3.Connection) -> None:
    for stmt in _V1.split(";"):
        if stmt.strip():
            conn.execute(stmt)


MIGRATIONS = [_v0_to_v1]
_EV_COLS = ("kind", "lecture", "page", "ref", "polarity", "note")


class ProfileError(Exception):
    pass


class ProfileStore:
    """`study.glossp` in one course folder. Created on first write; reads of a missing file are empty."""

    _locks: dict[Path, threading.Lock] = {}

    def __init__(self, folder: Path):
        self.path = Path(folder) / STUDY_FILE
        self._lock = self._locks.setdefault(self.path.resolve(), threading.Lock())

    @contextmanager
    def _conn(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        if not write and not self.path.exists():
            conn = sqlite3.connect(":memory:")
            _v0_to_v1(conn)
        elif not write:
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        else:
            conn = sqlite3.connect(self.path, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            schema.migrate(conn, MIGRATIONS)
        try:
            yield conn
        finally:
            conn.close()

    # --- reading ---

    def weak_points(self, statuses: tuple[str, ...] = STATUSES) -> list[dict[str, Any]]:
        with self._conn() as c:
            return self._read(c, statuses)

    @staticmethod
    def _read(c: sqlite3.Connection, statuses: tuple[str, ...] = STATUSES) -> list[dict[str, Any]]:
        q = ",".join("?" * len(statuses))
        rows = c.execute(f"SELECT id, n, concept, summary, status, pinned, extra_evidence, created_at, updated_at "
                         f"FROM weak_points WHERE status IN ({q}) ORDER BY n", statuses).fetchall()
        out = []
        for wid, n, concept, summary, status, pinned, extra, created, updated in rows:
            ev = c.execute("SELECT id, kind, lecture, page, ref, polarity, note, created_at FROM evidence "
                           "WHERE wp_id = ? ORDER BY id", (wid,)).fetchall()
            out.append({"id": wid, "n": n, "concept": concept, "summary": summary, "status": status,
                        "pinned": bool(pinned), "extra_evidence": extra, "created_at": created, "updated_at": updated,
                        "evidence": [dict(zip(("id", *_EV_COLS, "created_at"), e)) for e in ev]})
        return out

    def pending(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(f"SELECT id, {', '.join(_EV_COLS)}, created_at FROM pending ORDER BY id").fetchall()
        return [dict(zip(("id", *_EV_COLS, "created_at"), r)) for r in rows]

    def revisions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT id, trigger, changes, undone, created_at FROM revisions ORDER BY id DESC LIMIT ?",
                             (limit,)).fetchall()
        return [{"id": i, "trigger": t, "changes": json.loads(ch), "undone": bool(u), "created_at": at}
                for i, t, ch, u, at in rows]

    # --- writing: everything goes through `change`, which snapshots for undo and logs a revision ---

    @contextmanager
    def change(self, trigger: str) -> Iterator[Editor]:
        with self._lock, self._conn(write=True) as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                ed = Editor(c, self._snapshot(c))
                yield ed
                if ed.changes:
                    c.execute("INSERT INTO revisions (trigger, changes, before, created_at) VALUES (?, ?, ?, ?)",
                              (trigger, json.dumps(ed.changes, ensure_ascii=False), json.dumps(ed.before), now()))
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise

    @staticmethod
    def _snapshot(c: sqlite3.Connection) -> dict[str, list]:
        return {t: [list(r) for r in c.execute(f"SELECT * FROM {t}")] for t in ("weak_points", "evidence", "pending")}

    def undo(self) -> dict[str, Any]:
        """Undo the latest revision if it was automatic and nothing has been changed since (§15.4.5)."""
        with self._lock, self._conn(write=True) as c:
            row = c.execute("SELECT id, trigger, before FROM revisions WHERE undone = 0 ORDER BY id DESC LIMIT 1").fetchone()
            if row is None or row[1] not in AUTO_TRIGGERS:
                raise ProfileError("nothing automatic to undo (the latest change was made by hand)")
            before = json.loads(row[2])
            c.execute("BEGIN IMMEDIATE")
            for t, rows in before.items():
                c.execute(f"DELETE FROM {t}")
                for r in rows:
                    c.execute(f"INSERT INTO {t} VALUES ({','.join('?' * len(r))})", r)
            c.execute("UPDATE revisions SET undone = 1 WHERE id = ?", (row[0],))
            c.execute("COMMIT")
            return {"undone": row[0], "trigger": row[1]}


class Editor:
    """Mutations inside one `ProfileStore.change()`; each appends a human-readable entry to `changes`."""

    def __init__(self, c: sqlite3.Connection, before: dict):
        self.c = c
        self.before = before
        self.changes: list[dict[str, Any]] = []

    def get(self, wid: str) -> dict[str, Any] | None:
        row = self.c.execute("SELECT id, concept, summary, status, pinned FROM weak_points WHERE id = ?", (wid,)).fetchone()
        return dict(zip(("id", "concept", "summary", "status", "pinned"), row)) if row else None

    def active_count(self) -> int:
        return self.c.execute("SELECT COUNT(*) FROM weak_points WHERE status IN ('open', 'improving')").fetchone()[0]

    def add(self, concept: str, summary: str) -> str:
        nxt = int((self.c.execute("SELECT value FROM meta WHERE key = 'next_id'").fetchone() or ["1"])[0])
        wid = f"W{nxt}"  # never reused, even after deletion
        self.c.execute("INSERT INTO meta VALUES ('next_id', ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                       (str(nxt + 1),))
        at = now()
        self.c.execute("INSERT INTO weak_points (id, n, concept, summary, status, created_at, updated_at) "
                       "VALUES (?, ?, ?, ?, 'open', ?, ?)", (wid, nxt, concept, summary, at, at))
        self.changes.append({"op": "add", "id": wid, "concept": concept})
        return wid

    def attach(self, wid: str, ev: dict[str, Any]) -> bool:
        """False if this question's latest evidence here already says the same (re-summarizing a lecture sends
        the same questions again). A changed verdict is new evidence; so is every quiz attempt."""
        if ev.get("kind") == "thread":  # same question, same verdict as its latest evidence here: nothing new
            last = self.c.execute("SELECT polarity FROM evidence WHERE wp_id = ? AND kind = 'thread' AND ref = ? "
                                  "ORDER BY id DESC LIMIT 1", (wid, ev.get("ref"))).fetchone()
            if last and last[0] == ev.get("polarity"):
                return False
        self.c.execute(f"INSERT INTO evidence (wp_id, {', '.join(_EV_COLS)}, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (wid, *[ev.get(k) for k in _EV_COLS], now()))
        ids = [r[0] for r in self.c.execute("SELECT id FROM evidence WHERE wp_id = ? ORDER BY id", (wid,))]
        if len(ids) > MAX_EVIDENCE:  # keep the newest; remember how many were dropped
            drop = ids[: len(ids) - MAX_EVIDENCE]
            self.c.execute(f"DELETE FROM evidence WHERE id IN ({','.join('?' * len(drop))})", drop)
            self.c.execute("UPDATE weak_points SET extra_evidence = extra_evidence + ? WHERE id = ?", (len(drop), wid))
        self.c.execute("UPDATE weak_points SET updated_at = ? WHERE id = ?", (now(), wid))
        return True

    def set_status(self, wid: str, to: str, reason: str = "", pin: bool = False) -> None:
        cur = self.get(wid)
        if cur["status"] == to and not pin:
            return
        self.c.execute("UPDATE weak_points SET status = ?, updated_at = ?" + (", pinned = 1" if pin else "") +
                       " WHERE id = ?", (to, now(), wid))
        self.changes.append({"op": "status", "id": wid, "from": cur["status"], "to": to, "reason": reason})

    def edit(self, wid: str, concept: str | None, summary: str | None) -> None:
        cur = self.get(wid)
        concept = concept or cur["concept"]
        summary = cur["summary"] if summary is None else summary
        self.c.execute("UPDATE weak_points SET concept = ?, summary = ?, updated_at = ? WHERE id = ?",
                       (concept, summary, now(), wid))
        self.changes.append({"op": "edit", "id": wid, "concept": concept})

    def merge(self, src: str, into: str, reason: str = "") -> None:
        self.c.execute("UPDATE evidence SET wp_id = ? WHERE wp_id = ?", (into, src))
        extra = self.c.execute("SELECT extra_evidence FROM weak_points WHERE id = ?", (src,)).fetchone()[0]
        self.c.execute("UPDATE weak_points SET extra_evidence = extra_evidence + ? WHERE id = ?", (extra, into))
        self.c.execute("DELETE FROM weak_points WHERE id = ?", (src,))
        self.changes.append({"op": "merge", "from": src, "into": into, "reason": reason})

    def delete(self, wid: str) -> None:
        self.c.execute("DELETE FROM evidence WHERE wp_id = ?", (wid,))
        self.c.execute("DELETE FROM weak_points WHERE id = ?", (wid,))
        self.changes.append({"op": "delete", "id": wid})

    def add_pending(self, ev: dict[str, Any]) -> None:
        self.c.execute(f"INSERT INTO pending ({', '.join(_EV_COLS)}, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (*[ev.get(k) for k in _EV_COLS], now()))
        self.changes.append({"op": "pending", "page": ev.get("page"), "note": ev.get("note", "")})

    def drop_pending(self, ids: list[int]) -> None:
        if ids:
            self.c.execute(f"DELETE FROM pending WHERE id IN ({','.join('?' * len(ids))})", ids)

    def wps_with(self, lecture: str, page: int | None = None, ref: str | None = None) -> list[str]:
        """Weak points with evidence on this lecture page (or exactly this question)."""
        if ref is not None:
            rows = self.c.execute("SELECT DISTINCT wp_id FROM evidence WHERE ref = ?", (ref,))
        else:
            rows = self.c.execute("SELECT DISTINCT wp_id FROM evidence WHERE lecture = ? AND page = ?", (lecture, page))
        return [r[0] for r in rows]

    def recent_quiz(self, wid: str, n: int) -> list[int]:
        rows = self.c.execute("SELECT polarity FROM evidence WHERE wp_id = ? AND kind = 'quiz' ORDER BY id DESC LIMIT ?",
                              (wid, n)).fetchall()
        return [r[0] for r in rows]


# --- B: deterministic updates (§15.4.2 B) ------------------------------------------------------------

def apply_rules(ed: Editor, wid: str, ev: dict[str, Any]) -> None:
    """Status after new evidence. Pinned items (the student decided) are never changed automatically."""
    wp = ed.get(wid)
    if wp is None or wp["pinned"]:
        return
    if ev["polarity"] > 0:  # a wrong answer or "still confused": back to open
        ed.set_status(wid, "open", ev.get("note", ""))
    elif wp["status"] == "open":
        if ev["kind"] == "quiz" and ed.recent_quiz(wid, RIGHTS_TO_IMPROVE) == [-1] * RIGHTS_TO_IMPROVE:
            ed.set_status(wid, "improving", "quiz answered right twice")
        elif ev["kind"] == "thread":  # marked "got it"
            ed.set_status(wid, "improving", "marked understood")
    elif wp["status"] == "improving":
        ed.set_status(wid, "resolved", ev.get("note", ""))


def on_quiz_answer(folder: Path, attempt: dict[str, Any]) -> None:
    """A quiz answer was marked right or wrong. `attempt` has correct, topic, stem, question_id, sources."""
    if attempt.get("correct") is None:
        return
    store = ProfileStore(folder)
    pol = -1 if attempt["correct"] else 1
    with store.change("quiz") as ed:
        for s in attempt["sources"]:
            ev = {"kind": "quiz", "lecture": s["lecture"], "page": s["page"], "ref": attempt["question_id"],
                  "polarity": pol, "note": f"{'right' if pol < 0 else 'wrong'}: {attempt.get('topic', '')}"}
            wids = ed.wps_with(s["lecture"], page=s["page"])
            for wid in wids:
                if ed.attach(wid, ev):
                    ed.changes.append({"op": "attach", "id": wid, "kind": "quiz", "polarity": pol})
                    apply_rules(ed, wid, ev)
            if not wids and pol > 0:
                ed.add_pending({**ev, "note": f"wrong: {attempt.get('topic', '')} — {attempt.get('stem', '')[:160]}"})


def on_mark(folder: Path, course_id: str, page: int, thread_id: str, understood: bool | None) -> None:
    if understood is None:
        return
    store = ProfileStore(folder)
    if not store.path.exists():
        return
    pol = -1 if understood else 1
    ev = {"kind": "thread", "lecture": course_id, "page": page, "ref": thread_id, "polarity": pol,
          "note": "marked understood" if understood else "marked still confused"}
    with store.change("mark") as ed:
        for wid in ed.wps_with(course_id, ref=thread_id):
            if ed.attach(wid, ev):  # marking the same way twice is not new evidence
                ed.changes.append({"op": "attach", "id": wid, "kind": "thread", "polarity": pol})
                apply_rules(ed, wid, ev)


# --- A: Claude organizes (§15.4.2 A) -----------------------------------------------------------------

_EVS = {"type": "array", "items": {"type": "string"}}
OPS_SCHEMA = {
    "type": "object",
    "properties": {"ops": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": ["add", "attach", "merge", "status", "edit"]},
            "id": {"type": "string"}, "from": {"type": "string"}, "into": {"type": "string"},
            "to": {"type": "string", "enum": list(STATUSES)},
            "concept": {"type": "string"}, "summary": {"type": "string"}, "reason": {"type": "string"},
            "evidence": _EVS,
        },
        "required": ["op"],
    }}},
    "required": ["ops"],
}


def findings(digest: dict[str, Any], course_id: str, page_of: dict[str, int]) -> list[dict[str, Any]]:
    """The summary's weak / understood / open items as evidence bundles F1…, one evidence row per question."""
    out = []
    for kind, pol, text_key in (("weak", 1, "concept"), ("open", 1, "question"), ("understood", -1, "concept")):
        for item in digest.get(kind, []):
            evs = [{"kind": "thread", "lecture": course_id, "page": page_of.get(t), "ref": t, "polarity": pol,
                    "note": (item.get("why") or item.get(text_key) or "")[:200]} for t in item["threads"] if t in page_of]
            if evs:
                out.append({"handle": f"F{len(out) + 1}", "kind": kind, "text": item[text_key],
                            "why": item.get("why", ""), "known": item.get("known"), "evidence": evs})
    return out


def apply_ops(ed: Editor, ops: list[Any], bundles: dict[str, list[dict]], pending_by_handle: dict[str, dict]) -> list[str]:
    """Run Claude's operations in order. Returns what was skipped and why (for the log; not an error)."""
    skipped, used_pending = [], []

    def evidence(handles: Any) -> list[dict]:
        evs = []
        for h in handles if isinstance(handles, list) else []:
            h = str(h).strip().upper()
            if h in bundles:
                evs += bundles[h]
            elif h in pending_by_handle:
                evs.append(pending_by_handle[h])
                used_pending.append(pending_by_handle[h]["id"])
        return evs

    for i, op in enumerate(ops if isinstance(ops, list) else [], 1):
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        wid = str(op.get("id", "")).strip().upper()
        if kind == "add":
            evs = evidence(op.get("evidence"))
            if not str(op.get("concept", "")).strip() or not any(e["polarity"] > 0 for e in evs):
                skipped.append(f"#{i} add: no concept or no evidence of a difficulty")
                continue
            new = ed.add(str(op["concept"]).strip(), str(op.get("summary", "")).strip())
            for e in evs:
                ed.attach(new, e)
        elif kind == "attach":
            evs = evidence(op.get("evidence"))
            if not ed.get(wid) or not evs:
                skipped.append(f"#{i} attach: unknown id {wid} or no evidence")
                continue
            new = [e for e in evs if ed.attach(wid, e)]
            for e in new:
                apply_rules(ed, wid, e)
            if new:
                ed.changes.append({"op": "attach", "id": wid, "n": len(new)})
        elif kind == "merge":
            src, into = str(op.get("from", "")).strip().upper(), str(op.get("into", "")).strip().upper()
            if src == into or not ed.get(src) or not ed.get(into):
                skipped.append(f"#{i} merge: bad ids {src} -> {into}")
                continue
            ed.merge(src, into, str(op.get("reason", "")))
        elif kind == "status":
            wp, to = ed.get(wid), op.get("to")
            if not wp or to not in STATUSES:
                skipped.append(f"#{i} status: unknown id {wid} or status {to}")
                continue
            if wp["pinned"]:
                skipped.append(f"#{i} status: {wid} was set by the student")
                continue
            evs = evidence(op.get("evidence"))
            if to == "resolved":
                has_good = any(e["polarity"] < 0 for e in evs) or ed.c.execute(
                    "SELECT 1 FROM evidence WHERE wp_id = ? AND polarity < 0", (wid,)).fetchone()
                if not has_good:
                    skipped.append(f"#{i} status: resolving {wid} needs evidence of understanding")
                    continue
            for e in evs:
                ed.attach(wid, e)
            ed.set_status(wid, to, str(op.get("reason", "")))
        elif kind == "edit":
            if not ed.get(wid):
                skipped.append(f"#{i} edit: unknown id {wid}")
                continue
            ed.edit(wid, str(op.get("concept") or "").strip() or None, op.get("summary"))
        else:
            skipped.append(f"#{i}: unknown op {kind!r}")
    ed.drop_pending(sorted(set(used_pending)))
    return skipped


def resolved_names(store: ProfileStore) -> str:
    wps = sorted(store.weak_points(("resolved",)), key=lambda w: w["updated_at"], reverse=True)[:MAX_RESOLVED_NAMES]
    return "\n".join(f"- {w['id']}（已解决{'，学生确认' if w['pinned'] else ''}）{w['concept']}" for w in wps)


def weak_points_text(wps: list[dict[str, Any]], label: dict[str, str] | None = None) -> str:
    lines = []
    for w in wps:
        pages = sorted({(label.get(e["lecture"], "?") if label else "", e["page"]) for e in w["evidence"] if e["page"]})
        where = "、".join(f"{lec} 第 {p} 页".strip() for lec, p in pages[:6])
        n = len(w["evidence"]) + w["extra_evidence"]
        status = {"open": "未解决", "improving": "在好转"}.get(w["status"], w["status"])
        lines.append(f"- {w['id']}（{status}）{w['concept']}：{w['summary']}（证据 {n} 条{f'：{where}' if where else ''}）")
    return "\n".join(lines)


class Profiler:
    def __init__(self, tutor: Tutor, library: Path, model: str | None = None):
        self.tutor = tutor
        self.folders = Folders(library)
        self.model = model

    def folder_of(self, course: Course) -> Path | None:
        """The course folder holding this lecture, if it is inside the library."""
        d = course.path.resolve().parent
        return d if d.is_relative_to(self.folders.root) else None

    def known(self, course: Course) -> tuple[str, set[str]]:
        """For the summary prompt (§15.3.3): this course's unresolved weak points, so the same concept gets the same name."""
        d = self.folder_of(course)
        if d is None:
            return "", set()
        store = ProfileStore(d)
        wps = store.weak_points(ACTIVE)
        resolved = resolved_names(store)
        text = weak_points_text(wps) + (f"\n\n已解决的（同一个概念又出现时用同一个编号）：\n{resolved}" if resolved else "")
        return text.strip(), {w["id"] for w in store.weak_points()}

    def relevant(self, course: Course, page: int) -> str:
        """Up to 3 unresolved weak points for an explanation or a Q&A opening (§15.5.1): evidence in this
        lecture first (this page before others), then in earlier lectures of the course, then the rest by
        how much evidence they have. Empty when there are none, so the section is left out."""
        folder = self.folder_of(course)
        if folder is None or not (folder / STUDY_FILE).exists():
            return ""
        wps = ProfileStore(folder).weak_points(ACTIVE)
        if not wps:
            return ""
        order = [info["course_id"] for _, info in self.folders.lectures(folder)]
        here = order.index(course.id) if course.id in order else len(order)
        earlier = set(order[:here])

        def rank(w: dict[str, Any]) -> tuple:
            lectures = {e["lecture"] for e in w["evidence"]}
            if course.id in lectures:
                same_page = any(e["lecture"] == course.id and e["page"] == page for e in w["evidence"])
                return (0, 0 if same_page else 1, -len(w["evidence"]))
            if lectures & earlier:
                return (1, 0, -len(w["evidence"]))
            return (2, 0, -len(w["evidence"]) - w["extra_evidence"])

        lines, used = [], 0
        for w in sorted(wps, key=rank)[:RELEVANT_LIMIT]:
            line = f"- {w['concept']}：{w['summary']}" if w["summary"] else f"- {w['concept']}"
            if lines and used + len(line) > RELEVANT_CHARS:
                break
            lines.append(line[:RELEVANT_CHARS])
            used += len(line)
        return RELEVANT_HEAD + "\n".join(lines)

    def job_id(self, folder: str) -> str:
        from .quiz import scope_key
        return f"profile-{scope_key(folder)}"

    def active(self, folder: str) -> Job | None:
        j = self.tutor.jobs.get(self.job_id(folder))
        return j if j and not j.finished else None

    async def update(self, job: Job, folder: Path, trigger: str, found: list[dict[str, Any]]) -> dict[str, Any]:
        """Update A: one Claude call over current weak points + `found` (summary findings) + pending evidence."""
        store = ProfileStore(folder)
        current = store.weak_points(ACTIVE)
        pend = store.pending()
        if not found and not pend:
            return {"changes": [], "skipped": []}
        p_handles = {f"P{i}": {**p} for i, p in enumerate(pend, 1)}
        lines = []
        for f in found:
            known = f"（总结认为它就是 {f['known']}）" if f.get("known") else ""
            pages = "、".join(sorted({f"第 {e['page']} 页" for e in f["evidence"]}))
            lines.append(f"- {f['handle']} [{f['kind']}] {f['text']}{known}：{f['why'] or ''}（{pages}）")
        pend_lines = [f"- {h} 第 {p['page']} 页：{p['note']}" for h, p in p_handles.items()]
        over = len(current) > MAX_ACTIVE
        prompt = self.tutor.prompts.render(
            "update-profile", language=self.tutor.language,
            current=weak_points_text(current) or "（还没有未解决的薄弱点）", resolved=resolved_names(store),
            findings="\n".join(lines), pending="\n".join(pend_lines),
            limit=(f"现在有 {len(current)} 条未解决的薄弱点，超过了 {MAX_ACTIVE} 条。**先用 merge 合并相近的概念**，"
                   "再处理新的证据；新加的条目要尽量少。") if over else f"未解决的薄弱点最多 {MAX_ACTIVE} 条。")
        done = await self.tutor._stream(job, prompt, [], None, self.tutor.prompts.role(), json_schema=OPS_SCHEMA,
                                        cwd=self.tutor.workspace, model=self.model)
        ops = (done.data or {}).get("ops", []) if isinstance(done.data, dict) else []
        bundles = {f["handle"]: f["evidence"] for f in found}
        with store.change(trigger) as ed:
            skipped = apply_ops(ed, ops, bundles, p_handles)
            changes = list(ed.changes)
        return {"changes": changes, "skipped": skipped}

    def organize(self, folder: str) -> Job:
        """Update A on pending evidence only (the "organize" button, §15.4.2)."""
        d = self.folders.folder(folder)

        async def run(job: Job) -> None:
            result = await self.update(job, d, "organize", [])
            job.emit({"type": "done", "profile": result})

        return self.tutor.jobs.submit(self.job_id(folder), "profile", self.job_id(folder), run, {"folder": folder})
