"""Read-only tools over every lecture in a folder (DESIGN.md §13.4).

One implementation, several ways in: the stdio MCP server (`gloss mcp-serve`, for the
claude CLI), the Agent SDK in-process server (§14), and plain Python (study profile for
prompts, tests). Lectures are read with SQLite `mode=ro`: no migrations, no writes, safe
while the app has the same files open.

Lectures are named L1, L2, … in scope order (each course's own order, depth first), which
is shorter and less error-prone for the model than file names or ids.
"""

from __future__ import annotations

import base64
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import schema
from .folders import Folders

MAX_CHARS = 8000  # per tool result
EXPLANATION_MAX = 2500
ANSWER_MAX = 1200


class ToolError(Exception):
    pass


class LectureReader:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        meta = dict(self.conn.execute("SELECT key, value FROM meta").fetchall())
        if int(meta.get("schema_version") or 0) > schema.SCHEMA_VERSION:
            raise ToolError(f"{self.path.name} was made by a newer Gloss")
        self.meta = meta
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(threads)")}
        self._quote = "quote" in cols  # schema v2

    def close(self) -> None:
        self.conn.close()

    @property
    def title(self) -> str:
        return self.meta.get("title") or self.path.stem

    @property
    def course_id(self) -> str | None:
        return self.meta.get("course_id")

    def slide_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM slides").fetchone()[0]

    def slide(self, page: int) -> tuple[str, str | None]:
        row = self.conn.execute("SELECT text, transcript_span FROM slides WHERE page = ?", (page,)).fetchone()
        if row is None:
            raise ToolError(f"没有第 {page} 页（共 {self.slide_count()} 页）")
        return row[0] or "", row[1]

    def texts(self) -> list[tuple[int, str, str | None]]:
        return self.conn.execute("SELECT page, text, transcript_span FROM slides ORDER BY page").fetchall()

    def image(self, page: int) -> bytes:
        row = self.conn.execute("SELECT image FROM slides WHERE page = ?", (page,)).fetchone()
        if row is None:
            raise ToolError(f"没有第 {page} 页")
        return row[0]

    def explanation(self, page: int) -> str:
        row = self.conn.execute("SELECT content FROM explanations WHERE page = ? ORDER BY id DESC LIMIT 1",
                                (page,)).fetchone()
        return row[0] if row else ""

    def explanations(self) -> dict[int, str]:
        rows = self.conn.execute("SELECT page, content FROM explanations ORDER BY id").fetchall()
        return {p: c for p, c in rows}  # latest wins

    def tree(self) -> dict | None:
        row = self.conn.execute("SELECT json FROM tree WHERE id = 1").fetchone()
        return json.loads(row[0]) if row else None

    def threads(self, page: int | None = None) -> list[dict[str, Any]]:
        q = "quote" if self._quote else "NULL"
        sql = f"SELECT id, page, n, anchor_type, x, y, w, h, {q} FROM threads"
        rows = self.conn.execute(sql + (" WHERE page = ?" if page else "") + " ORDER BY page, n",
                                 (page,) if page else ()).fetchall()
        out = []
        for tid, p, n, anchor, x, y, w, h, quote in rows:
            msgs = self.conn.execute("SELECT role, content FROM messages WHERE thread_id = ? ORDER BY id",
                                     (tid,)).fetchall()
            out.append({"page": p, "n": n, "anchor": anchor, "rect": (x, y, w, h) if x is not None else None,
                        "quote": quote, "messages": msgs})
        return out

    def notes(self, page: int | None = None) -> dict[int, str]:
        sql = "SELECT page, content FROM notes WHERE content != ''" + (" AND page = ?" if page else "") + " ORDER BY page"
        return dict(self.conn.execute(sql, (page,) if page else ()).fetchall())


@dataclass
class Lecture:
    label: str  # L1, L2, …
    course: str  # the folder it is in, relative to the library
    number: int  # its position in that course (1-based)
    reader: LectureReader

    @property
    def name(self) -> str:
        return f"{self.label}（{self.course or '课件库'} 第 {self.number} 讲：{self.reader.title}）"


@dataclass
class PageSignal:
    lecture: str
    page: int
    score: int
    questions: int
    follow_ups: int
    has_note: bool
    unanswered: int
    asked: list[str] = field(default_factory=list)
    topic: str = ""


class Scope:
    """Every lecture under one library folder, read-only (§13.2)."""

    def __init__(self, root: Path, folder: str = "", only: list[str] | None = None):
        self.folders = Folders(root)
        self.folder = folder
        base = self.folders.folder(folder)
        self.lectures: list[Lecture] = []
        counters: dict[Path, int] = {}
        for path, _info in self.folders.all_lectures(base):
            counters[path.parent] = counters.get(path.parent, 0) + 1
            try:
                reader = LectureReader(path)
            except (sqlite3.Error, ToolError):
                continue  # unreadable or too new: not part of the scope
            label = f"L{len(self.lectures) + 1}"
            self.lectures.append(Lecture(label, self.folders.rel(path.parent), counters[path.parent], reader))
        if only:
            keep = set(only)
            for lec in self.lectures:
                if not (lec.label in keep or lec.reader.course_id in keep):
                    lec.reader.close()
            self.lectures = [lec for lec in self.lectures if lec.label in keep or lec.reader.course_id in keep]

    def close(self) -> None:
        for lec in self.lectures:
            lec.reader.close()

    def __enter__(self) -> Scope:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get(self, label: str) -> Lecture:
        for lec in self.lectures:
            if lec.label == str(label).strip().upper():
                return lec
        raise ToolError(f"没有讲次 {label}。可用的有：{', '.join(lec.label for lec in self.lectures)}")

    def by_course_id(self, cid: str) -> Lecture | None:
        return next((lec for lec in self.lectures if lec.reader.course_id == cid), None)

    # --- the tools ----------------------------------------------------------------

    def list_lectures(self) -> str:
        if not self.lectures:
            return "这个范围里没有讲次。"
        lines = []
        for lec in self.lectures:
            r = lec.reader
            topics = [t.get("title", "") for t in (r.tree() or {}).get("children", [])]
            n_threads = len(r.threads())
            lines.append(f"- {lec.name}：{r.slide_count()} 页，{n_threads} 个问题"
                         + (f"；主题：{'；'.join(topics)}" if topics else ""))
        return "\n".join(lines)

    def get_outline(self, lecture: str) -> str:
        lec = self.get(lecture)
        tree = lec.reader.tree()
        if not tree:
            return f"{lec.name} 还没有生成思维导图。共 {lec.reader.slide_count()} 页，可以用 get_slide 逐页看。"
        lines = [f"# {lec.name}", tree.get("title", "")]
        for t in tree.get("children", []):
            pages = t.get("slides", [])
            span = f"第 {min(pages)}–{max(pages)} 页" if pages else ""
            lines.append(f"- {t.get('title', '')}（{span}）{t.get('summary', '')}")
        return "\n".join(lines)

    def get_slide(self, lecture: str, page: int) -> str:
        lec = self.get(lecture)
        text, span = lec.reader.slide(int(page))
        parts = [f"# {lec.name} 第 {page} 页", "## 页面文字", text.strip() or "（这一页没有可抽取的文字）"]
        if span:
            parts += ["## 老师讲这一页时说的话", span]
        e = lec.reader.explanation(int(page))
        if e:
            parts += ["## 讲解", _cut(e, EXPLANATION_MAX)]
        note = lec.reader.notes(int(page)).get(int(page))
        if note:
            parts += ["## 学生的笔记", note]
        return "\n\n".join(parts)

    def get_slide_image(self, lecture: str, page: int) -> bytes:
        return self.get(lecture).reader.image(int(page))

    def get_questions(self, lecture: str, page: int | None = None) -> str:
        lec = self.get(lecture)
        threads = lec.reader.threads(int(page) if page else None)
        if not threads:
            return f"{lec.name}{f' 第 {page} 页' if page else ''}没有问答。"
        out = [f"# {lec.name} 的问答"]
        for t in threads:
            head = f"## 第 {t['page']} 页 · 问题 {t['n']}"
            out.append(head)
            if t["quote"]:
                out.append("（针对讲解中选中的一段）\n" + "\n".join(f"> {ln}" for ln in t["quote"].splitlines()))
            elif t["rect"]:
                x, y, w, h = t["rect"]
                out.append(f"（框选的区域 x={x:.2f} y={y:.2f} w={w:.2f} h={h:.2f}）")
            for i, (role, content) in enumerate(t["messages"]):
                if role == "user":
                    out.append(("学生：" if i == 0 else "学生追问：") + content)
                else:
                    out.append("助教：" + _cut(content, ANSWER_MAX))
        return "\n\n".join(out)

    def get_notes(self, lecture: str, page: int | None = None) -> str:
        lec = self.get(lecture)
        notes = lec.reader.notes(int(page) if page else None)
        if not notes:
            return f"{lec.name}{f' 第 {page} 页' if page else ''}没有笔记。"
        return "\n\n".join([f"# {lec.name} 的笔记"] + [f"## 第 {p} 页\n{c}" for p, c in notes.items()])

    def search_course(self, query: str, lecture: str | None = None) -> str:
        terms = _terms(query)
        if not terms:
            raise ToolError("查询为空")
        lectures = [self.get(lecture)] if lecture else self.lectures
        hits = []
        for lec in lectures:
            r = lec.reader
            expl = r.explanations()
            notes = r.notes()
            qa: dict[int, str] = {}
            for t in r.threads():
                qa[t["page"]] = qa.get(t["page"], "") + "\n" + "\n".join(c for _, c in t["messages"]) + (t["quote"] or "")
            for page, text, span in r.texts():
                for where, body, weight in [("页面文字", text or "", 3), ("问答", qa.get(page, ""), 2),
                                            ("笔记", notes.get(page, ""), 2), ("transcript", span or "", 1),
                                            ("讲解", expl.get(page, ""), 1)]:
                    low = body.casefold()
                    count = sum(low.count(t) for t in terms)
                    if count:
                        matched = sum(1 for t in terms if t in low)
                        hits.append((matched * 10 + count * weight, lec, page, where, _snippet(body, terms)))
        if not hits:
            return f"没有找到「{query}」。可以换个说法，或者用 list_lectures / get_outline 看结构。"
        hits.sort(key=lambda h: -h[0])
        lines = [f"「{query}」的搜索结果（最多 15 条，按相关度排序）："]
        for _, lec, page, where, snip in hits[:15]:
            lines.append(f"- {lec.label} 第 {page} 页 · {where}：{snip}")
        return "\n".join(lines)

    def study_profile(self, limit: int = 30) -> list[PageSignal]:
        """Where the student got stuck (§13.3): questions, follow-ups, notes, unanswered retries."""
        signals = []
        for lec in self.lectures:
            r = lec.reader
            topic_of = {}
            for t in (r.tree() or {}).get("children", []):
                for p in t.get("slides", []):
                    topic_of[p] = t.get("title", "")
            by_page: dict[int, PageSignal] = {}
            for t in r.threads():
                s = by_page.setdefault(t["page"], PageSignal(lec.label, t["page"], 0, 0, 0, False, 0))
                users = [c for role, c in t["messages"] if role == "user"]
                s.questions += 1
                s.follow_ups += max(0, len(users) - 1)
                if t["messages"] and t["messages"][-1][0] == "user":
                    s.unanswered += 1
                if users and len(s.asked) < 3:
                    s.asked.append(_cut(users[0], 120))
            for p in r.notes():
                by_page.setdefault(p, PageSignal(lec.label, p, 0, 0, 0, False, 0)).has_note = True
            for s in by_page.values():
                s.score = 2 * s.questions + s.follow_ups + int(s.has_note)
                s.topic = topic_of.get(s.page, "")
                signals.append(s)
        signals.sort(key=lambda s: (-s.score, s.lecture, s.page))
        return signals[:limit]

    def study_profile_text(self, limit: int = 30) -> str:
        sig = self.study_profile(limit)
        if not sig:
            return "学生在这些讲次里还没有提问或记笔记。"
        lines = []
        for s in sig:
            bits = [f"{s.questions} 个问题"]
            if s.follow_ups:
                bits.append(f"{s.follow_ups} 次追问")
            if s.has_note:
                bits.append("有笔记")
            if s.unanswered:
                bits.append(f"{s.unanswered} 个没答完")
            asked = "；".join(f"「{a}」" for a in s.asked)
            lines.append(f"- {s.lecture} 第 {s.page} 页{f'（{s.topic}）' if s.topic else ''}：{'，'.join(bits)}"
                         + (f"。问过：{asked}" if asked else ""))
        return "\n".join(lines)


# --- tool table (shared by the MCP server and the Agent SDK wrapper) -------------------------

TOOLS: list[dict[str, Any]] = [
    {"name": "list_lectures", "description": "列出范围内的全部讲次（L1、L2…），每讲的页数、问题数和主题。先调用这个。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_outline", "description": "某一讲的思维导图：主题、页码范围、摘要。",
     "inputSchema": {"type": "object", "properties": {"lecture": {"type": "string", "description": "如 L2"}},
                     "required": ["lecture"]}},
    {"name": "get_slide", "description": "某一讲某一页的文字、老师讲这一页时说的话、讲解和学生笔记。image=true 时附上整页图。",
     "inputSchema": {"type": "object", "properties": {
         "lecture": {"type": "string"}, "page": {"type": "integer", "minimum": 1},
         "image": {"type": "boolean", "description": "需要看图（公式、图表）时才设为 true"}},
         "required": ["lecture", "page"]}},
    {"name": "get_questions", "description": "学生在某一讲（或某一页）问过的问题、追问和助教的回答。",
     "inputSchema": {"type": "object", "properties": {"lecture": {"type": "string"}, "page": {"type": "integer", "minimum": 1}},
                     "required": ["lecture"]}},
    {"name": "get_notes", "description": "学生在某一讲（或某一页）记的笔记。",
     "inputSchema": {"type": "object", "properties": {"lecture": {"type": "string"}, "page": {"type": "integer", "minimum": 1}},
                     "required": ["lecture"]}},
    {"name": "search_course", "description": "在全部讲次的页面文字、问答、笔记、transcript、讲解里搜关键词，返回出处和片段。",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"},
                                                      "lecture": {"type": "string", "description": "只搜这一讲（可选）"}},
                     "required": ["query"]}},
    {"name": "study_profile", "description": "学生卡住过的页：按提问、追问、笔记加权排序。",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}}},
]
TOOL_NAMES = [t["name"] for t in TOOLS]


def call(scope: Scope, name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
    """Run one tool; returns MCP content blocks. Raises ToolError for bad input."""
    if name == "list_lectures":
        text = scope.list_lectures()
    elif name == "get_outline":
        text = scope.get_outline(args["lecture"])
    elif name == "get_slide":
        text = scope.get_slide(args["lecture"], args["page"])
        if args.get("image"):
            img = scope.get_slide_image(args["lecture"], args["page"])
            return [{"type": "text", "text": _cut(text, MAX_CHARS)},
                    {"type": "image", "mimeType": "image/png", "data": base64.b64encode(img).decode()}]
    elif name == "get_questions":
        text = scope.get_questions(args["lecture"], args.get("page"))
    elif name == "get_notes":
        text = scope.get_notes(args["lecture"], args.get("page"))
    elif name == "search_course":
        text = scope.search_course(args["query"], args.get("lecture"))
    elif name == "study_profile":
        text = scope.study_profile_text(int(args.get("limit") or 30))
    else:
        raise ToolError(f"unknown tool {name}")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n（结果太长，已截断。请缩小范围：指定 page，或换更具体的关键词。）"
    return [{"type": "text", "text": text}]


# --- helpers ------------------------------------------------------------------------------------

def _cut(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[:n] + "…"


_CJK = re.compile(r"[぀-ヿ㐀-鿿豈-﫿]+")


def _terms(query: str) -> list[str]:
    """Words, plus character pairs inside CJK runs (Chinese has no spaces)."""
    terms = []
    for w in query.casefold().split():
        terms.append(w)
        for run in _CJK.findall(w):
            if len(run) > 2:
                terms += [run[i:i + 2] for i in range(len(run) - 1)]
    return list(dict.fromkeys(t for t in terms if t))


def _snippet(body: str, terms: list[str], width: int = 70) -> str:
    low = body.casefold()
    i = min((low.find(t) for t in terms if t in low), default=0)
    start = max(0, i - width)
    s = body[start:i + width].replace("\n", " ").strip()
    return ("…" if start else "") + s + ("…" if i + width < len(body) else "")
