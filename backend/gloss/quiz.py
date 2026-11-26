"""Quiz: exam practice generated from a library folder (DESIGN.md §13.5, §13.8).

- `QuizStore`: `quizzes.glossq` in the folder (SQLite, same migrate framework as .gloss).
- `QuizMaker`: one background job per quiz: study profile -> generate (course tools + JSON
  schema) -> deterministic checks, one retry -> optional verify call -> save.
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
from .exams import all_exams
from .folders import QUIZ_FILE, Folders
from .jobs import Job
from .providers import CourseToolSpec
from .store import Course, now

if TYPE_CHECKING:
    from .tutor import Tutor

TYPES = ("mcq", "short", "derivation")
FOCUS = ("weak", "balanced", "uncovered")
MODES = ("new", "replicate")  # §13.8: from the course, or modeled on a past exam
MAX_COUNT = 30
MAX_INSTRUCTIONS = 2000
EXAM_TEXT_MAX = 20000  # chars of the past exam put straight into the prompt
LETTERS = "ABCDE"
OPTION_PREFIX = re.compile(r"^\s*\(?[A-Ea-e](?:[.):]\s+|[．、：]\s*)")  # "A. ", "(b) ", "C、" — not "A.I."

# The `why` example is given in the output language: models copy the language of examples.
WHY_EXAMPLE = {
    "中文": "「你在 L2 第 12 页对这个推导追问了两次」或「这个主题你还没问过，但它是本讲的重点」",
    "English": "\"You followed up twice on this derivation on L2 slide 12\" or "
               "\"You haven't asked about this topic yet, but it is central to this lecture\"",
}

FOCUS_TEXT = {
    "weak": "大约 70% 的题考学生卡住过的地方（上面的列表），其余覆盖还没问过的重要主题。",
    "balanced": "均匀覆盖每一讲的主要主题；学生卡住过的地方可以多考一点。",
    "uncovered": "主要考学生还没问过、也没记笔记的主题（对照每讲的主题和上面的列表）；卡住过的地方最多考两道。",
}

QUIZ_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "questions": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": list(TYPES)},
                "stem": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
                "answer": {"type": "string"},
                "explanation": {"type": "string"},
                "sources": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"lecture": {"type": "string"}, "page": {"type": "integer"}},
                    "required": ["lecture", "page"]}},
                "why": {"type": "string"},
                "topic": {"type": "string"},
                "difficulty": {"type": "integer"},
            },
            "required": ["type", "stem", "options", "answer", "explanation", "sources", "why", "topic", "difficulty"],
        }},
    },
    "required": ["title", "questions"],
}

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {
        "type": "object",
        "properties": {"n": {"type": "integer"}, "ok": {"type": "boolean"}, "reason": {"type": "string"}},
        "required": ["n", "ok", "reason"]}}},
    "required": ["results"],
}


# --- storage -------------------------------------------------------------------------------------

_V1 = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE quizzes (id TEXT PRIMARY KEY, title TEXT, options TEXT, model TEXT, created_at TEXT);
CREATE TABLE quiz_questions (id TEXT PRIMARY KEY, quiz_id TEXT, n INTEGER, data TEXT);
CREATE INDEX quiz_questions_quiz ON quiz_questions (quiz_id);
CREATE TABLE attempts (id INTEGER PRIMARY KEY, question_id TEXT, answer TEXT, correct INTEGER, created_at TEXT);
CREATE INDEX attempts_question ON attempts (question_id);
"""


def _v0_to_v1(conn: sqlite3.Connection) -> None:
    for stmt in _V1.split(";"):
        if stmt.strip():
            conn.execute(stmt)


MIGRATIONS = [_v0_to_v1]


class QuizStore:
    """`quizzes.glossq` in one folder. Created on first write."""

    def __init__(self, folder: Path):
        self.path = Path(folder) / QUIZ_FILE
        self._lock = threading.Lock()

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

    def create(self, title: str, options: dict, model: str | None, questions: list[dict]) -> str:
        qid = uuid.uuid4().hex
        with self._lock, self._conn(write=True) as c:
            c.execute("BEGIN")
            c.execute("INSERT INTO quizzes VALUES (?, ?, ?, ?, ?)",
                      (qid, title, json.dumps(options, ensure_ascii=False), model, now()))
            for n, q in enumerate(questions, 1):
                c.execute("INSERT INTO quiz_questions VALUES (?, ?, ?, ?)",
                          (uuid.uuid4().hex, qid, n, json.dumps(q, ensure_ascii=False)))
            c.execute("COMMIT")
        return qid

    def list(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT id, title, options, model, created_at FROM quizzes ORDER BY created_at DESC").fetchall()
            out = []
            for qid, title, options, model, created in rows:
                n = c.execute("SELECT COUNT(*) FROM quiz_questions WHERE quiz_id = ?", (qid,)).fetchone()[0]
                latest = self._latest(c, qid)
                out.append({"id": qid, "title": title, "options": json.loads(options), "model": model,
                            "created_at": created, "count": n, "answered": len(latest),
                            "correct": sum(1 for a in latest.values() if a["correct"] == 1)})
        return out

    def get(self, qid: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT id, title, options, model, created_at FROM quizzes WHERE id = ?", (qid,)).fetchone()
            if row is None:
                return None
            latest = self._latest(c, qid)
            qs = c.execute("SELECT id, n, data FROM quiz_questions WHERE quiz_id = ? ORDER BY n", (qid,)).fetchall()
        return {"id": row[0], "title": row[1], "options": json.loads(row[2]), "model": row[3], "created_at": row[4],
                "questions": [{"id": i, "n": n, **json.loads(d), "attempt": latest.get(i)} for i, n, d in qs]}

    def add_attempt(self, question_id: str, answer: str, correct: bool | None) -> dict[str, Any]:
        with self._lock, self._conn(write=True) as c:
            if c.execute("SELECT 1 FROM quiz_questions WHERE id = ?", (question_id,)).fetchone() is None:
                raise KeyError(question_id)
            at = now()
            c.execute("INSERT INTO attempts (question_id, answer, correct, created_at) VALUES (?, ?, ?, ?)",
                      (question_id, answer, None if correct is None else int(correct), at))
        return {"answer": answer, "correct": None if correct is None else int(correct), "created_at": at}

    def latest_attempts(self) -> list[dict[str, Any]]:
        """Every question's latest attempt, read-only (no migrate; safe from the MCP server). For §15.2."""
        if not self.path.exists():
            return []
        try:
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        except sqlite3.Error:
            return []
        try:
            rows = conn.execute(
                "SELECT q.id, q.quiz_id, q.data, a.correct, a.created_at FROM attempts a "
                "JOIN quiz_questions q ON q.id = a.question_id ORDER BY a.id").fetchall()
        except sqlite3.Error:
            return []
        finally:
            conn.close()
        latest: dict[str, dict[str, Any]] = {}
        for qid, quiz_id, data, correct, at in rows:
            d = json.loads(data)
            latest[qid] = {"question_id": qid, "quiz_id": quiz_id, "correct": correct, "created_at": at,
                           "topic": d.get("topic", ""), "stem": d.get("stem", ""), "sources": d.get("sources") or []}
        return list(latest.values())

    def rename(self, qid: str, title: str) -> bool:
        with self._lock, self._conn(write=True) as c:
            return c.execute("UPDATE quizzes SET title = ? WHERE id = ?", (title, qid)).rowcount > 0

    def delete(self, qid: str) -> None:
        with self._lock, self._conn(write=True) as c:
            c.execute("BEGIN")
            c.execute("DELETE FROM attempts WHERE question_id IN (SELECT id FROM quiz_questions WHERE quiz_id = ?)", (qid,))
            c.execute("DELETE FROM quiz_questions WHERE quiz_id = ?", (qid,))
            c.execute("DELETE FROM quizzes WHERE id = ?", (qid,))
            c.execute("COMMIT")

    @staticmethod
    def _latest(c: sqlite3.Connection, qid: str) -> dict[str, dict[str, Any]]:
        rows = c.execute(
            "SELECT a.question_id, a.answer, a.correct, a.created_at FROM attempts a "
            "JOIN quiz_questions q ON q.id = a.question_id WHERE q.quiz_id = ? ORDER BY a.id", (qid,)).fetchall()
        return {r[0]: {"answer": r[1], "correct": r[2], "created_at": r[3]} for r in rows}  # last one wins


def all_quizzes(base: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Quizzes in `base` and every folder under it (hidden ones skipped), newest first: (its folder, summary)."""
    out = []
    dirs = [base, *(d for d in base.rglob("*") if d.is_dir() and not any(x.startswith(".") for x in d.relative_to(base).parts))]
    for d in dirs:
        if (d / QUIZ_FILE).exists():
            out += [(d, q) for q in QuizStore(d).list()]
    return sorted(out, key=lambda x: x[1]["created_at"], reverse=True)


# --- checks --------------------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"\W+", "", s.casefold())


def quiz_problems(data: Any, scope: Scope, count: int | None, types: tuple[str, ...]) -> tuple[list[str], list[int]]:
    """Problems the model can act on, and the 0-based indexes of questions that are unusable as they are.

    `count` None: the number is up to the model (a replicated exam, or the student's own instructions), 1–30.
    """
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        return ["输出必须是带 questions 数组的对象"], []
    problems, bad = [], []
    qs = data["questions"]
    if count is not None and len(qs) != count:
        problems.append(f"要求 {count} 道题，实际给了 {len(qs)} 道")
    elif count is None and not 1 <= len(qs) <= MAX_COUNT:
        problems.append(f"题数要在 1–{MAX_COUNT} 道之间，实际给了 {len(qs)} 道")
    pages = {lec.label: lec.reader.slide_count() for lec in scope.lectures}
    seen: dict[str, int] = {}
    for i, q in enumerate(qs, 1):
        mine = []
        if not isinstance(q, dict):
            problems.append(f"第 {i} 题不是对象")
            bad.append(i - 1)
            continue
        if q.get("type") not in types:
            mine.append(f"题型 {q.get('type')!r} 不在允许的题型里（{', '.join(types)}）")
        if not str(q.get("stem", "")).strip():
            mine.append("题干为空")
        opts = q.get("options") or []
        if q.get("type") == "mcq":
            if not 2 <= len(opts) <= 5:
                mine.append(f"选择题需要 2–5 个选项，现在是 {len(opts)} 个")
            elif len({_norm(str(o)) for o in opts}) != len(opts):
                mine.append("选项有重复")
            ans = str(q.get("answer", "")).strip().upper().rstrip(".)")
            if ans not in LETTERS[:len(opts)] or len(ans) != 1:
                mine.append(f"答案应是一个选项字母（A–{LETTERS[max(0, len(opts) - 1)]}），现在是 {q.get('answer')!r}")
        elif not str(q.get("answer", "")).strip():
            mine.append("答案为空")
        srcs = q.get("sources") or []
        if not srcs:
            mine.append("没有出处")
        for s in srcs:
            lec, page = str(s.get("lecture", "")).strip().upper(), s.get("page")
            if lec not in pages:
                mine.append(f"出处讲次 {s.get('lecture')!r} 不存在（可用：{', '.join(pages)}）")
            elif not isinstance(page, int) or not 1 <= page <= pages[lec]:
                mine.append(f"{lec} 没有第 {page} 页（共 {pages[lec]} 页）")
        key = _norm(str(q.get("stem", "")))
        if key and key in seen:
            mine.append(f"和第 {seen[key]} 题重复")
        seen.setdefault(key, i)
        if mine:
            bad.append(i - 1)
            problems += [f"第 {i} 题：{m}" for m in mine]
    return problems, bad


def finish_question(q: dict, scope: Scope) -> dict:
    """Normalize a checked question and resolve its sources to stable lecture ids (course_id)."""
    out = {k: q.get(k) for k in ("type", "stem", "options", "answer", "explanation", "why", "topic", "difficulty")}
    # models sometimes number the options themselves; the UI numbers them
    out["options"] = [OPTION_PREFIX.sub("", str(o)).strip() for o in (q.get("options") or [])] if q.get("type") == "mcq" else []
    if q.get("type") == "mcq":
        out["answer"] = str(q["answer"]).strip().upper().rstrip(".)")
    try:
        out["difficulty"] = min(3, max(1, int(q.get("difficulty") or 2)))
    except (TypeError, ValueError):
        out["difficulty"] = 2
    out["sources"] = []
    seen = set()
    for s in q.get("sources") or []:
        lec = scope.get(str(s["lecture"]))
        if (lec.label, int(s["page"])) in seen:
            continue
        seen.add((lec.label, int(s["page"])))
        out["sources"].append({"lecture": lec.reader.course_id, "label": lec.label, "title": lec.reader.title,
                               "course": lec.course, "number": lec.number, "page": int(s["page"])})
    out["review"] = None  # set by the verify pass: reason the answer key looks wrong
    return out


def scope_key(folder: str) -> str:
    """Folder paths contain '/' and spaces; job ids and scratch dirs use this instead."""
    return hashlib.sha1(folder.encode()).hexdigest()[:12]


# --- generation ---------------------------------------------------------------------------------

def prompt_parts(scope: Scope, mode: str, exam: str | None, count: int, types: tuple[str, ...], focus: str,
                 instructions: str) -> dict[str, str]:
    """The parts of prompts/quiz.md that depend on the dialog's choices (§13.8)."""
    types_text = "、".join(f"`{t}`" for t in types)
    override = "（如果下面「学生的额外要求」里写了题数或题型，以那里为准）" if instructions else ""
    out = {"past_exam": "", "exams_hint": "", "instructions": ""}
    if mode == "replicate":
        e = scope.exam(exam or "")
        out["task"] = f"仿照学生上传的往年试卷 {e.label}「{e.meta.get('title', '')}」，出一份**结构相同的新卷**"
        out["settings"] = (
            f"- **仿出**：逐题对应 {e.label}：同样的题型、考点、难度，题数和原卷一样（最多 {MAX_COUNT} 道）{override}。"
            "原卷的大题有几个小问时，可以拆成几道题。\n"
            "- **换数字、换条件、换角度，不要照抄原题**；学生做完应该能检验自己会不会原卷那类题。\n"
            "- 原卷某道题考的内容在课件里找不到出处，就跳过这道题。\n"
            f"- `why` 写这道题仿的是原卷第几题、考什么，例如「仿 {e.label} 第 3 题：换了初始条件」。")
        out["past_exam"] = (f"## 要仿的往年试卷：{e.name}\n\n下面是抽取的文字；公式、图表多的页或扫描件，用 "
                            f"`get_past_exam`（`exam: \"{e.label}\"`、`page`、`image: true`）看原图。\n\n"
                            + scope.exam_text(e.label, limit=EXAM_TEXT_MAX))
    else:
        out["task"] = f"出一份测验，共 **{count} 道题**{override}"
        out["settings"] = f"- **侧重**：{FOCUS_TEXT[focus]}\n- **题型**：只用这些：{types_text}{override}。"
        if scope.exams:
            out["exams_hint"] = ("## 往年试卷\n\n范围里有学生上传的往年测验 / 试卷，可以用 `list_past_exams`、"
                                 "`get_past_exam` 看它们的题型、难度和常考点，作为参考，**不要照抄原题**：\n\n"
                                 + scope.list_past_exams())
    if instructions:
        out["instructions"] = ("\n## 学生的额外要求（和上面的设定冲突时，以这里为准；但出处、输出格式和语言的规则不变）\n\n"
                               + instructions)
    return out


class QuizMaker:
    def __init__(self, tutor: Tutor, library: Path, model: str | None = None, verify: bool = True):
        self.tutor = tutor
        self.folders = Folders(library)
        self.model = model
        self.verify = verify

    def job_prefix(self, folder: str) -> str:
        return f"quiz-{scope_key(folder)}:"

    def active(self, folder: str) -> Job | None:
        jobs = self.tutor.jobs.active(self.job_prefix(folder))
        return jobs[0] if jobs else None

    def start(self, folder: str, count: int = 10, types: tuple[str, ...] = TYPES, focus: str = "weak",
              lectures: list[str] | None = None, mode: str = "new", exam: str | None = None,
              instructions: str = "") -> Job:
        if not getattr(self.tutor.provider, "supports_tools", True):
            raise ValueError("quizzes need a CLI provider (Claude Code or Codex): they read the course with tools")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        instructions = (instructions or "").strip()
        if len(instructions) > MAX_INSTRUCTIONS:
            raise ValueError(f"instructions are too long (max {MAX_INSTRUCTIONS} characters)")
        if not 1 <= count <= MAX_COUNT:
            raise ValueError(f"count must be 1–{MAX_COUNT}")
        types = tuple(t for t in TYPES if t in types)
        if not types:
            raise ValueError(f"types must include some of {TYPES}")
        if focus not in FOCUS:
            raise ValueError(f"focus must be one of {FOCUS}")
        d = self.folders.folder(folder)
        exam_meta = None
        if mode == "replicate":
            exam_meta = next((m for _, m in all_exams(d) if m["id"] == exam), None)
            if exam_meta is None:
                raise ValueError("choose a past exam in this folder to replicate")
        # a fixed number and fixed types only without the student's own instructions and outside replicate mode
        fixed = mode == "new" and not instructions
        check_count = count if fixed else None
        check_types = types if fixed else TYPES
        lecture_files = [p for p, _ in self.folders.all_lectures(d)]
        if not lecture_files:
            raise ValueError("no lectures in this folder")
        ids: dict[str, str] = {}  # library-relative path -> course id
        for p, info in self.folders.all_lectures(d):
            cid = info["course_id"]
            if cid is None and not info["error"]:
                with Course.open(p) as c:  # never-opened files get their stable id now, so questions can point at them
                    cid = c.id
            if cid:
                ids[self.folders.rel(p)] = cid
        if lectures:  # chosen by path in the dialog; the scope filters by course id
            unknown = [x for x in lectures if x not in ids and x not in ids.values()]
            if unknown:
                raise ValueError(f"not lectures in this folder: {unknown}")
            lectures = [ids.get(x, x) for x in lectures]
        options = {"count": count, "types": list(types), "focus": focus, "lectures": lectures or [], "mode": mode,
                   "exam": {"id": exam_meta["id"], "title": exam_meta["title"]} if exam_meta else None,
                   "instructions": instructions}
        if self.active(folder):
            raise RuntimeError("a quiz is already being generated for this folder")
        job_id = self.job_prefix(folder) + uuid.uuid4().hex[:8]

        async def run(job: Job) -> None:
            spec = CourseToolSpec(self.folders.root, folder, tuple(lectures or ()))
            cwd = self.tutor.workspace / "scopes" / scope_key(folder)
            cwd.mkdir(parents=True, exist_ok=True)
            with Scope(self.folders.root, folder, lectures) as scope:
                if not scope.lectures:
                    raise ValueError("none of the chosen lectures could be read")
                values = dict(
                    scope=folder or "课件库", language=self.tutor.language, lectures=scope.list_lectures(),
                    why_example=WHY_EXAMPLE.get(self.tutor.language, WHY_EXAMPLE["English"]),
                    profile=scope.study_profile_text(30),
                    **prompt_parts(scope, mode, exam, count, types, focus, instructions))
                feedback = ""
                for attempt in range(2):
                    prompt = self.tutor.prompts.render("quiz", **values, feedback=feedback)
                    job.emit({"type": "phase", "phase": "generate", "attempt": attempt + 1})
                    done = await self.tutor._stream(job, prompt, [], None, self.tutor.prompts.role(),
                                                    json_schema=QUIZ_SCHEMA, cwd=cwd, course_tools=spec,
                                                    model=self.model)
                    problems, bad = quiz_problems(done.data, scope, check_count, check_types)
                    if not problems:
                        break
                    job.emit({"type": "reset"})
                    feedback = ("## 上一次的结果有问题，请修正后重新输出全部题目\n\n"
                                + "\n".join(f"- {p}" for p in problems[:30]))
                data = done.data if isinstance(done.data, dict) else {"questions": []}
                qs = [q for i, q in enumerate(data.get("questions") or []) if i not in set(bad)][:check_count or MAX_COUNT]
                questions = [finish_question(q, scope) for q in qs]
                if not questions:
                    raise ValueError("Gloss did not produce any usable questions; try again")
                if self.verify:
                    job.emit({"type": "phase", "phase": "verify"})
                    await self._verify(job, questions, scope, spec, cwd)
                title = str(data.get("title") or "").strip() or "Quiz"
                qid = QuizStore(d).create(title, {**options, "requested": check_count}, done.model, questions)
            job.emit({"type": "done", "quiz_id": qid, "count": len(questions), "requested": check_count})

        return self.tutor.jobs.submit(job_id, "quiz", self.job_prefix(folder) + "queue", run, {"folder": folder})

    async def _verify(self, job: Job, questions: list[dict], scope: Scope, spec: CourseToolSpec, cwd: Path) -> None:
        lines = []
        for n, q in enumerate(questions, 1):
            src = "、".join(f"{s['label']} 第 {s['page']} 页" for s in q["sources"])
            opts = "\n".join(f"   {LETTERS[i]}. {o}" for i, o in enumerate(q["options"]))
            lines.append(f"### 第 {n} 题（{q['type']}，出处：{src}）\n{q['stem']}\n{opts}\n标准答案：{q['answer']}")
        prompt = self.tutor.prompts.render("quiz-verify", questions="\n\n".join(lines), language=self.tutor.language)
        try:
            done = await self.tutor._stream(job, prompt, [], None, self.tutor.prompts.role(),
                                            json_schema=VERIFY_SCHEMA, cwd=cwd, course_tools=spec, model=self.model)
        except Exception as e:  # noqa: BLE001 — the quiz is still usable without the check
            job.emit({"type": "phase", "phase": "verify_failed", "message": str(e)})
            return
        for r in (done.data or {}).get("results", []) if isinstance(done.data, dict) else []:
            n = r.get("n")
            if isinstance(n, int) and 1 <= n <= len(questions) and r.get("ok") is False:
                questions[n - 1]["review"] = str(r.get("reason") or "").strip() or "?"
