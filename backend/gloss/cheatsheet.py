"""Cheatsheets: a printable summary built from the student's own notes and questions (DESIGN.md §16).

- `SheetStore`: `cheatsheets.glossc` in a course folder. Every change appends a version; the latest one is
  the sheet. The editor's chat is stored with links to the versions it produced.
- `SheetMaker`: background jobs that write a sheet (from selected materials) or revise one (from a chat
  request, optionally limited to a selected passage). Output is plain Markdown.

The input is assembled here from read-only lecture data (course_tools.Scope), so providers without tools
work too; providers with tools also get the course tools for the chosen lectures (e.g. to check a formula).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import schema
from .course_tools import Scope
from .folders import Folders
from .jobs import Job
from .providers import CourseToolSpec
from .store import Course, now

if TYPE_CHECKING:
    from .tutor import Tutor

SHEET_FILE = "cheatsheets.glossc"
PAGES = (0, 1, 2)  # 0 = no limit
# Words that fit on one A4 page in the print layout (two columns, 9pt). CJK text counts characters.
BUDGET = {"English": 900, "中文": 1500}
MATERIALS_BUDGET = 80_000  # characters of notes / questions / exams in one prompt
MAX_INSTRUCTIONS = 2000
MAX_REQUEST = 2000
HISTORY_TURNS = 6  # earlier chat messages sent with a revision request

_V1 = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE sheets (id TEXT PRIMARY KEY, title TEXT, options TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE versions (id INTEGER PRIMARY KEY, sheet_id TEXT, content TEXT, source TEXT, note TEXT, created_at TEXT);
CREATE INDEX versions_sheet ON versions (sheet_id);
CREATE TABLE messages (id INTEGER PRIMARY KEY, sheet_id TEXT, role TEXT, content TEXT, version_id INTEGER, created_at TEXT);
CREATE INDEX messages_sheet ON messages (sheet_id);
"""


def _v0_to_v1(conn: sqlite3.Connection) -> None:
    for stmt in _V1.split(";"):
        if stmt.strip():
            conn.execute(stmt)


MIGRATIONS = [_v0_to_v1]


class SheetStore:
    """`cheatsheets.glossc` in one folder. Created on first write; reading a missing file is empty."""

    _locks: dict[Path, threading.Lock] = {}

    def __init__(self, folder: Path):
        self.path = Path(folder) / SHEET_FILE
        self._lock = self._locks.setdefault(self.path, threading.Lock())

    @contextmanager
    def _conn(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        if not write and not self.path.exists():
            conn = sqlite3.connect(":memory:")
            _v0_to_v1(conn)
        else:
            conn = sqlite3.connect(self.path, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            schema.migrate(conn, MIGRATIONS)
        try:
            yield conn
        finally:
            conn.close()

    def create(self, title: str, options: dict, content: str) -> str:
        sid = uuid.uuid4().hex
        at = now()
        with self._lock, self._conn(write=True) as c:
            c.execute("BEGIN")
            c.execute("INSERT INTO sheets VALUES (?, ?, ?, ?, ?)", (sid, title, json.dumps(options, ensure_ascii=False), at, at))
            c.execute("INSERT INTO versions (sheet_id, content, source, note, created_at) VALUES (?, ?, 'generate', '', ?)",
                      (sid, content, at))
            c.execute("COMMIT")
        return sid

    def list(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT s.id, s.title, s.options, s.created_at, s.updated_at, COUNT(v.id) FROM sheets s "
                             "LEFT JOIN versions v ON v.sheet_id = s.id GROUP BY s.id ORDER BY s.updated_at DESC").fetchall()
        return [{"id": r[0], "title": r[1], "options": json.loads(r[2]), "created_at": r[3], "updated_at": r[4],
                 "versions": r[5]} for r in rows]

    def get(self, sid: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT id, title, options, created_at, updated_at FROM sheets WHERE id = ?", (sid,)).fetchone()
            if row is None:
                return None
            versions = c.execute("SELECT id, source, note, created_at, content FROM versions WHERE sheet_id = ? ORDER BY id",
                                 (sid,)).fetchall()
            msgs = c.execute("SELECT id, role, content, version_id, created_at FROM messages WHERE sheet_id = ? ORDER BY id",
                             (sid,)).fetchall()
        return {"id": row[0], "title": row[1], "options": json.loads(row[2]), "created_at": row[3], "updated_at": row[4],
                "content": versions[-1][4] if versions else "",
                "version": versions[-1][0] if versions else None,
                "versions": [{"id": v[0], "source": v[1], "note": v[2], "created_at": v[3], "chars": len(v[4])} for v in versions],
                "messages": [{"id": m[0], "role": m[1], "content": m[2], "version_id": m[3], "created_at": m[4]} for m in msgs]}

    def content(self, sid: str) -> str:
        s = self.get(sid)
        if s is None:
            raise KeyError(sid)
        return s["content"]

    def version(self, sid: str, vid: int) -> str:
        with self._conn() as c:
            row = c.execute("SELECT content FROM versions WHERE id = ? AND sheet_id = ?", (vid, sid)).fetchone()
        if row is None:
            raise KeyError(vid)
        return row[0]

    def save(self, sid: str, content: str, source: str, note: str = "", base: int | None = None) -> int:
        """Append a version (or, for consecutive hand edits, update the latest one). Returns its id.

        `base`: the version the editor started from. A hand edit only merges into the latest version when
        that is itself a hand edit and still the one the editor saw.
        """
        at = now()
        with self._lock, self._conn(write=True) as c:
            c.execute("BEGIN")
            last = c.execute("SELECT id, source FROM versions WHERE sheet_id = ? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
            if last is None:
                c.execute("ROLLBACK")
                raise KeyError(sid)
            if source == "edit" and last[1] == "edit" and (base is None or base == last[0]):
                c.execute("UPDATE versions SET content = ?, created_at = ? WHERE id = ?", (content, at, last[0]))
                vid = last[0]
            else:
                vid = c.execute("INSERT INTO versions (sheet_id, content, source, note, created_at) VALUES (?, ?, ?, ?, ?)",
                                (sid, content, source, note, at)).lastrowid
            title = title_of(content)
            c.execute("UPDATE sheets SET updated_at = ?" + (", title = ?" if title else "") + " WHERE id = ?",
                      (at, title, sid) if title else (at, sid))
            c.execute("COMMIT")
        return vid

    def rename(self, sid: str, title: str) -> int:
        """The title is the sheet's first `# ` heading (title_of): renaming rewrites it, as a new version."""
        content = self.content(sid)
        if re.search(r"^#\s+.*$", content, re.M):
            new = re.sub(r"^#\s+.*$", lambda _: f"# {title}", content, count=1, flags=re.M)
        else:
            new = f"# {title}\n\n{content}"
        return self.save(sid, new, "rename", note=title)

    def restore(self, sid: str, vid: int) -> int:
        return self.save(sid, self.version(sid, vid), "restore", note=str(vid))

    def add_message(self, sid: str, role: str, content: str, version_id: int | None = None) -> int:
        with self._lock, self._conn(write=True) as c:
            return c.execute("INSERT INTO messages (sheet_id, role, content, version_id, created_at) VALUES (?, ?, ?, ?, ?)",
                             (sid, role, content, version_id, now())).lastrowid

    def delete(self, sid: str) -> None:
        with self._lock, self._conn(write=True) as c:
            c.execute("BEGIN")
            for table, col in (("messages", "sheet_id"), ("versions", "sheet_id"), ("sheets", "id")):
                c.execute(f"DELETE FROM {table} WHERE {col} = ?", (sid,))
            c.execute("COMMIT")


# --- text helpers -------------------------------------------------------------------------------

def all_sheets(base: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Cheatsheets in `base` and every folder under it (hidden folders skipped): (its folder, summary)."""
    out = []
    for d in [base, *sorted(p for p in base.rglob("*") if p.is_dir() and not any(x.startswith(".") for x in p.relative_to(base).parts))]:
        if (d / SHEET_FILE).exists():
            out += [(d, s) for s in SheetStore(d).list()]
    return out


def title_of(md: str) -> str:
    m = re.search(r"^#\s+(.+?)\s*#*\s*$", md, re.M)
    return m.group(1).strip() if m else ""


def clean_markdown(text: str) -> str:
    """The model's answer as a sheet: drop a surrounding ``` fence and any preamble before the title."""
    t = text.strip()
    fence = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*?)\n```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    first = re.search(r"^#\s", t, re.M)
    if first and first.start() > 0 and first.start() < 400:  # a short "Here is your cheatsheet:" line
        t = t[first.start():]
    return t.strip()


def looks_like_sheet(md: str) -> bool:
    """A title line and something under it; a refusal or a one-line answer is not a sheet."""
    return bool(title_of(md)) and len(md.strip()) >= 20


def budget_text(pages: int, language: str) -> str:
    if not pages:
        return "篇幅不限，但要紧凑：这是考前快速翻看的总结，不是讲义。"
    n = BUDGET.get(language, BUDGET["English"]) * pages
    unit = "个汉字" if language == "中文" else "个英文词（words）"
    return (f"**要能打印在 {pages} 页 A4 上**（双栏、9pt 小字）：全文不超过约 **{n} {unit}**（公式按显示长度估）。"
            "放不下时优先删掉学生已经掌握、只是常识的内容，不要删学生问过和卡住过的。")


def materials_text(scope: Scope, exams: list[str]) -> str:
    """Notes, questions, outlines, weak points, wrong quiz answers and chosen past exams, within a budget.
    Questions are cut first when the total is too long: they are the longest and the answers repeat."""
    blocks: list[tuple[int, str]] = []  # (priority: lower is kept first, text)
    wps = [w for w in scope.weak_points() if w.get("status") != "resolved"]
    if wps:
        lines = []
        for w in wps:
            where = "、".join(f"{lec} 第 {p} 页" for lec, p in w.get("pages", [])[:4])
            lines.append(f"- **{w.get('concept', '')}**：{w.get('summary', '')}" + (f"（{where}）" if where else ""))
        blocks.append((0, "## 学习档案里还没解决的薄弱点\n\n" + "\n".join(lines)))
    wrong = [a for a in scope.quiz_attempts() if a["correct"] == 0]
    if wrong:
        label = {lec.reader.course_id: lec.label for lec in scope.lectures}
        lines = [f"- {a.get('topic') or ''}：{a.get('stem', '')[:200]}（"
                 + "、".join(f"{label.get(s.get('lecture'), '?')} 第 {s.get('page')} 页" for s in a["sources"]) + "）"
                 for a in wrong[:30]]
        blocks.append((0, "## 测验里答错的题\n\n" + "\n".join(lines)))
    for lec in scope.lectures:
        blocks.append((1, f"# {lec.name}\n\n" + scope.get_outline(lec.label)))
        notes = lec.reader.notes()
        if notes:
            blocks.append((1, scope.get_notes(lec.label)))
        if lec.reader.threads():
            blocks.append((2, scope.get_questions(lec.label)))
    for key in exams:
        blocks.append((3, "## 往年试卷（看常考什么、怎么考）\n\n" + scope.exam_text(key, limit=15_000)))
    total = sum(len(b) for _, b in blocks)
    for prio in (3, 2):  # over budget: shorten exams, then questions
        if total <= MATERIALS_BUDGET:
            break
        over = total - MATERIALS_BUDGET
        n = sum(1 for p, _ in blocks if p == prio)
        for i, (p, b) in enumerate(blocks):
            if p == prio and n:
                cut = min(len(b) - 500, over // n + 1)
                if cut > 0:
                    blocks[i] = (p, b[:len(b) - cut] + "\n\n（太长，后面省略）")
                    total -= cut
    return "\n\n".join(b for _, b in blocks)


def scope_key(folder: str) -> str:
    return hashlib.sha1(folder.encode()).hexdigest()[:12]


# --- jobs ---------------------------------------------------------------------------------------

class SheetMaker:
    def __init__(self, tutor: Tutor, library: Path, model: str | None = None):
        self.tutor = tutor
        self.folders = Folders(library)
        self.model = model

    def job_prefix(self, folder: str) -> str:
        return f"sheet-{scope_key(folder)}:"

    def active(self, folder: str) -> Job | None:
        jobs = self.tutor.jobs.active(self.job_prefix(folder))
        return jobs[0] if jobs else None

    def _lecture_ids(self, d: Path) -> dict[str, str]:
        """library-relative path -> course id, giving never-opened files their id (like quizzes)."""
        ids = {}
        for p, info in self.folders.all_lectures(d):
            cid = info["course_id"]
            if cid is None and not info["error"]:
                with Course.open(p) as c:
                    cid = c.id
            if cid:
                ids[self.folders.rel(p)] = cid
        return ids

    def _tools(self, folder: str, only: list[str]) -> tuple[CourseToolSpec | None, Path]:
        cwd = self.tutor.workspace / "scopes" / scope_key(folder)
        cwd.mkdir(parents=True, exist_ok=True)
        with_tools = getattr(getattr(self.tutor.provider, "caps", None), "supports_tools", True)
        return (CourseToolSpec(self.folders.root, folder, tuple(only)) if with_tools else None), cwd

    def start(self, folder: str, lectures: list[str] | None = None, exams: list[str] | None = None, pages: int = 1,
              instructions: str = "") -> Job:
        if pages not in PAGES:
            raise ValueError(f"pages must be one of {PAGES}")
        instructions = (instructions or "").strip()
        if len(instructions) > MAX_INSTRUCTIONS:
            raise ValueError(f"instructions are too long (max {MAX_INSTRUCTIONS} characters)")
        d = self.folders.folder(folder)
        ids = self._lecture_ids(d)
        if not ids:
            raise ValueError("no lectures in this folder")
        if lectures:
            unknown = [x for x in lectures if x not in ids and x not in ids.values()]
            if unknown:
                raise ValueError(f"not lectures in this folder: {unknown}")
            only = [ids.get(x, x) for x in lectures]
        else:
            only = list(ids.values())
        exams = list(exams or [])
        if self.active(folder):
            raise RuntimeError("a cheatsheet is already being written for this folder")
        options = {"lectures": only, "exams": exams, "pages": pages, "instructions": instructions}

        async def run(job: Job) -> None:
            spec, cwd = self._tools(folder, only)
            with Scope(self.folders.root, folder, only) as scope:
                if not scope.lectures:
                    raise ValueError("none of the chosen lectures could be read")
                for e in exams:
                    scope.exam(e)  # unknown ids fail here, before the call
                labels = [scope.exam(e).label for e in exams]
                prompt = self.tutor.prompts.render(
                    "cheatsheet", scope=folder or "课件库", language=self.tutor.language,
                    lectures=scope.list_lectures(), budget=budget_text(pages, self.tutor.language),
                    materials=materials_text(scope, labels),
                    tools=("可以用 course tools（`get_slide`，公式、图表多的页加 `image: true`）核对公式和定义。"
                           if spec else "没有工具：只用下面给的材料。"),
                    instructions=("\n## 学生的额外要求（和上面冲突时以这里为准；输出格式不变）\n\n" + instructions)
                    if instructions else "")
            job.emit({"type": "phase", "phase": "generate"})
            done = await self.tutor._stream(job, prompt, [], None, self.tutor.prompts.role(), cwd=cwd,
                                            course_tools=spec, model=self.model)
            content = clean_markdown(done.text)
            if not looks_like_sheet(content):
                raise ValueError("Gloss did not write a cheatsheet; try again")
            sid = SheetStore(d).create(title_of(content) or "Cheatsheet", options, content)
            job.emit({"type": "done", "sheet_id": sid})

        return self.tutor.jobs.submit(self.job_prefix(folder) + uuid.uuid4().hex[:8], "cheatsheet",
                                      self.job_prefix(folder) + "queue", run, {"folder": folder})

    def revise(self, folder: str, sid: str, request: str, selection: str = "") -> Job:
        request, selection = (request or "").strip(), (selection or "").strip()
        if not request:
            raise ValueError("say what to change")
        if len(request) > MAX_REQUEST or len(selection) > 20_000:
            raise ValueError("the request is too long")
        d = self.folders.folder(folder)
        store = SheetStore(d)
        sheet = store.get(sid)
        if sheet is None:
            raise KeyError(sid)
        if self.active(folder):
            raise RuntimeError("Gloss is still working on a cheatsheet in this folder")
        only = sheet["options"].get("lectures") or []

        async def run(job: Job) -> None:
            current = store.content(sid)
            history = store.get(sid)["messages"][-HISTORY_TURNS:]
            store.add_message(sid, "user", request + (f"\n\n> {selection[:300]}" if selection else ""))
            spec, cwd = self._tools(folder, only)
            with Scope(self.folders.root, folder, only) as scope:
                lectures = scope.list_lectures()
            pages = sheet["options"].get("pages", 0)
            prompt = self.tutor.prompts.render(
                "cheatsheet-edit", language=self.tutor.language, lectures=lectures, current=current,
                budget=budget_text(pages, self.tutor.language), request=request,
                selection=(f"\n## 只改这一部分\n\n学生在预览里选中了下面这段（是渲染后的文字，和 Markdown 源码可能略有不同："
                           f"公式、加粗等符号被去掉了）。**只改它对应的那部分，其余内容逐字保持不变。**\n\n> "
                           + selection.replace("\n", "\n> ")) if selection else "",
                history="\n".join(f"- {'学生' if m['role'] == 'user' else 'Gloss'}：{m['content'][:300]}" for m in history)
                or "（无）")
            job.emit({"type": "phase", "phase": "revise"})
            done = await self.tutor._stream(job, prompt, [], None, self.tutor.prompts.role(), cwd=cwd,
                                            course_tools=spec, model=self.model)
            reply, content = split_reply(done.text)
            if not looks_like_sheet(content):
                store.add_message(sid, "assistant", reply or "（没有改动）")
                raise ValueError("Gloss did not return the revised cheatsheet; nothing was changed")
            vid = store.save(sid, content, "ai", note=request[:200])
            store.add_message(sid, "assistant", reply or "✓", vid)
            job.emit({"type": "done", "sheet_id": sid, "version": vid})

        return self.tutor.jobs.submit(self.job_prefix(folder) + uuid.uuid4().hex[:8], "cheatsheet",
                                      self.job_prefix(folder) + "queue", run, {"folder": folder, "sheet": sid})


REPLY_MARK = "===CHEATSHEET==="


def split_reply(text: str) -> tuple[str, str]:
    """A revision answer is a one-line reply, the marker line, then the whole sheet."""
    if REPLY_MARK in text:
        reply, _, sheet = text.partition(REPLY_MARK)
        return reply.strip()[:500], clean_markdown(sheet)
    return "", clean_markdown(text)
