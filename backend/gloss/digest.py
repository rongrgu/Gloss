"""Per-lecture study summaries (DESIGN.md §15.3).

One call per lecture, no tools: the backend assembles everything (outline, every question with a short
handle T1…Tn, notes, quiz answers, the previous summary) and sends it on stdin, so every provider works.
Claude returns JSON; the backend keeps only claims backed by real questions, maps handles back to
thread ids, renders Markdown, and stores both. Thread links are `gloss:<course_id>/<page>/<thread id>`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import i18n
from .folders import QUIZ_FILE
from .jobs import Job
from .store import Course, Thread
from .tutor import covered_text

if TYPE_CHECKING:
    from .tutor import Tutor

ANSWER_MAX = 800  # the question says more about where the student is stuck than the answer
THREADS_BUDGET = 40_000  # characters of questions in one prompt; lower-priority threads are dropped first
MAX_REVIEW = 5

_ITEM = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
_REFS = {"type": "array", "items": {"type": "string"}}
DIGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "covered": {"type": "array", "items": _ITEM(
            {"topic": {"type": "string"}, "summary": {"type": "string"},
             "pages": {"type": "array", "items": {"type": "integer"}}}, ["topic", "summary", "pages"])},
        "questions": {"type": "array", "items": _ITEM(
            {"concept": {"type": "string"}, "summary": {"type": "string"}, "threads": _REFS},
            ["concept", "summary", "threads"])},
        "weak": {"type": "array", "items": _ITEM(
            {"concept": {"type": "string"}, "why": {"type": "string"}, "threads": _REFS,
             "known": {"type": ["string", "null"]}}, ["concept", "why", "threads", "known"])},
        "understood": {"type": "array", "items": _ITEM(
            {"concept": {"type": "string"}, "threads": _REFS}, ["concept", "threads"])},
        "open": {"type": "array", "items": _ITEM(
            {"question": {"type": "string"}, "threads": _REFS}, ["question", "threads"])},
        "review": {"type": "array", "items": _ITEM(
            {"page": {"type": "integer"}, "why": {"type": "string"}}, ["page", "why"])},
    },
    "required": ["covered", "questions", "weak", "understood", "open", "review"],
}
SECTIONS = ("covered", "questions", "weak", "understood", "open", "review")
WITH_THREADS = ("questions", "weak", "understood", "open")


# --- input ----------------------------------------------------------------------------------------

def handles(threads: list[Thread]) -> dict[str, Thread]:
    """T1, T2… in page order. Short handles are copied back far more reliably than 32-char ids."""
    ordered = sorted(threads, key=lambda t: (t.page, t.n))
    return {f"T{i}": t for i, t in enumerate(ordered, 1)}


def _priority(t: Thread, msgs: list) -> tuple:
    users = sum(1 for m in msgs if m.role == "user")
    return (t.understood is not False, -users)  # "still confused" first, then the most follow-ups


# The student's marks appear verbatim in the prompt; models copy them into the summary, so they are
# written in the output language (like the quiz `why` example, §13.5).
MARK_LABELS = {"中文": {True: "（学生标了：懂了）", False: "（学生标了：还没懂）"},
               "English": {True: " (student marked: got it)", False: " (student marked: still confused)"}}


def threads_text(course: Course, hs: dict[str, Thread], watermark: int, language: str = "中文") -> str:
    blocks: list[tuple[tuple, str, int]] = []
    for h, t in hs.items():
        msgs = course.list_messages(t.id)
        if not msgs:
            continue
        mark = MARK_LABELS.get(language, MARK_LABELS["English"]).get(t.understood, "")
        head = f"### [{h}] 第 {t.page} 页 · 问题 {t.n}{mark}"
        lines = [head]
        if t.anchor_type == "explanation_text" and t.quote:
            lines.append("针对讲解里的这段话：\n" + "\n".join(f"> {x}" for x in t.quote.splitlines() if x.strip()))
        elif t.rect:
            covered = covered_text(course.get_slide(t.page).text_blocks, t.rect)
            if covered:
                lines.append("框选的区域覆盖的文字：\n" + "\n".join(f"> {x}" for x in covered))
        first = True
        for m in msgs:
            new = "（新）" if m.id > watermark else ""
            if m.role == "user":
                lines.append(f"学生{'' if first else '追问'}{new}：{m.content.strip()}")
                first = False
            else:
                a = m.content.strip()
                lines.append(f"助教{new}：{a[:ANSWER_MAX] + ('…' if len(a) > ANSWER_MAX else '')}")
        blocks.append((_priority(t, msgs), "\n".join(lines), t.page * 1000 + t.n))
    if not blocks:
        return "（这一讲没有提问。）"
    kept, used = [], 0
    for prio, text, order in sorted(blocks, key=lambda b: b[0]):
        if used + len(text) > THREADS_BUDGET and kept:
            continue
        kept.append((order, text))
        used += len(text)
    dropped = len(blocks) - len(kept)
    out = "\n\n".join(text for _, text in sorted(kept))
    return out + (f"\n\n（还有 {dropped} 个问题因为太长没有列出。）" if dropped else "")


def notes_text(course: Course) -> str:
    notes = [(p, course.get_note(p)) for p in sorted(course.pages_with_notes())]
    return "\n\n".join(f"### 第 {p} 页\n{c.strip()}" for p, c in notes) or ""


def quiz_text(course: Course, library: Path | None) -> str:
    """Latest answers to quiz questions citing this lecture, from quizzes in its folder or any folder above
    (inside the library)."""
    from .quiz import QuizStore

    d = course.path.resolve().parent
    dirs = [d]
    if library is not None and d.is_relative_to(library.resolve()):
        while d != library.resolve():
            d = d.parent
            dirs.append(d)
    lines = []
    for folder in dirs:
        if not (folder / QUIZ_FILE).exists():
            continue
        for a in QuizStore(folder).latest_attempts():
            pages = [s["page"] for s in a["sources"] if s.get("lecture") == course.id]
            if pages and a["correct"] is not None:
                verdict = "答对" if a["correct"] else "答错"
                lines.append(f"- {verdict}：{a['topic']}（第 {'、'.join(map(str, pages))} 页）— {a['stem'][:200]}")
    return "\n".join(lines)


def previous_text(prev: dict | None, by_id: dict[str, str]) -> str:
    """The previous summary's JSON with thread ids turned back into this run's handles."""
    if not prev:
        return ""
    data = json.loads(json.dumps(prev))
    for sec in WITH_THREADS:
        for item in data.get(sec, []):
            item["threads"] = [by_id[t] for t in item.get("threads", []) if t in by_id]
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=1) + "\n```"


# --- output ---------------------------------------------------------------------------------------

def digest_problems(data: Any) -> list[str]:
    """Structural problems worth one retry. Bad references are not problems: they are dropped (clean)."""
    if not isinstance(data, dict):
        return ["输出必须是一个对象"]
    probs = [f"缺少 {k}" for k in SECTIONS if not isinstance(data.get(k), list)]
    if probs:
        return probs
    for sec in WITH_THREADS:
        for i, item in enumerate(data[sec], 1):
            if not isinstance(item, dict) or not isinstance(item.get("threads"), list):
                probs.append(f"{sec} 第 {i} 条格式不对（需要 threads 数组）")
    return probs


def clean(data: dict, hs: dict[str, Thread], n_pages: int, known_ids: set[str]) -> dict:
    """Keep only what the material supports: references must be this lecture's questions and pages;
    a claim whose references are all invalid is dropped. Handles become thread ids."""
    def refs(xs: Any) -> list[str]:
        out = []
        for x in xs if isinstance(xs, list) else []:
            t = hs.get(str(x).strip().strip("[]").upper())
            if t and t.id not in out:
                out.append(t.id)
        return out

    def pages(xs: Any) -> list[int]:
        return sorted({p for p in (xs if isinstance(xs, list) else []) if isinstance(p, int) and 1 <= p <= n_pages})

    out: dict[str, list] = {k: [] for k in SECTIONS}
    for item in data.get("covered", []):
        ps = pages(item.get("pages"))
        if isinstance(item, dict) and item.get("topic") and ps:
            out["covered"].append({"topic": str(item["topic"]), "summary": str(item.get("summary", "")), "pages": ps})
    for sec, text_key in (("questions", "concept"), ("weak", "concept"), ("understood", "concept"), ("open", "question")):
        for item in data.get(sec, []):
            if not isinstance(item, dict) or not str(item.get(text_key, "")).strip():
                continue
            ts = refs(item.get("threads"))
            if not ts:
                continue  # nothing real backs it up (§15.1: every conclusion needs evidence)
            kept = {text_key: str(item[text_key]).strip(), "threads": ts}
            if sec == "questions":
                kept["summary"] = str(item.get("summary", ""))
            if sec == "weak":
                kept["why"] = str(item.get("why", ""))
                k = item.get("known")
                kept["known"] = k if isinstance(k, str) and k in known_ids else None
            out[sec].append(kept)
    seen = set()
    for item in data.get("review", []):
        p = item.get("page") if isinstance(item, dict) else None
        if isinstance(p, int) and 1 <= p <= n_pages and p not in seen and len(out["review"]) < MAX_REVIEW:
            seen.add(p)
            out["review"].append({"page": p, "why": str(item.get("why", ""))})
    return out


def render(data: dict, course: Course, locale: str) -> str:
    def t(key: str, **kw: object) -> str:
        return i18n.tr(locale, key, **kw)

    threads = {}
    for p in range(1, course.slide_count() + 1):
        for th in course.list_threads(p):
            threads[th.id] = th

    def links(ids: list[str]) -> str:
        out = []
        for tid in ids:
            th = threads.get(tid)
            if th:
                out.append(f"[{t('question_ref', p=th.page, n=th.n)}](gloss:{course.id}/{th.page}/{tid})")
        return ("（" + "，".join(out) + "）") if locale == "zh" and out else (" (" + ", ".join(out) + ")") if out else ""

    def page_link(p: int) -> str:
        return f"[{t('page', p=p)}](gloss:{course.id}/{p})"

    colon = t("colon")
    md = [f"# {t('digest')} · {course.title}", ""]
    blocks = [
        ("digest_covered", [f"- **{c['topic']}**（{'、'.join(page_link(p) for p in c['pages'])}）{colon}{c['summary']}"
                            if locale == "zh" else
                            f"- **{c['topic']}** ({', '.join(page_link(p) for p in c['pages'])}){colon}{c['summary']}"
                            for c in data["covered"]]),
        ("digest_questions", [f"- **{q['concept']}**{colon}{q['summary']}{links(q['threads'])}" for q in data["questions"]]),
        ("digest_weak", [f"- **{w['concept']}**{colon}{w['why']}{links(w['threads'])}" for w in data["weak"]]),
        ("digest_understood", [f"- **{u['concept']}**{links(u['threads'])}" for u in data["understood"]]),
        ("digest_open", [f"- {o['question']}{links(o['threads'])}" for o in data["open"]]),
        ("digest_review", [f"- {page_link(r['page'])}{colon}{r['why']}" for r in data["review"]]),
    ]
    for key, lines in blocks:
        md += [f"## {t(key)}", ""] + (lines or [t("digest_empty")]) + [""]
    return "\n".join(md).rstrip() + "\n"


# --- the job --------------------------------------------------------------------------------------

class Digester:
    def __init__(self, tutor: Tutor, library: Path | None = None, model: str | None = None, profiler=None):
        self.tutor = tutor
        self.library = library
        self.model = model
        self.profiler = profiler  # §15.4: names known weak points in the prompt, then updates the profile

    def job_id(self, course: Course) -> str:
        return f"{course.id}:digest"

    def active(self, course: Course) -> Job | None:
        j = self.tutor.jobs.get(self.job_id(course))
        return j if j and not j.finished else None

    def start(self, course: Course) -> Job:
        async def run(job: Job) -> None:
            watermark = course.max_message_id()
            all_threads = [t for p in range(1, course.slide_count() + 1) for t in course.list_threads(p)]
            hs = handles(all_threads)
            by_id = {t.id: h for h, t in hs.items()}
            prev = course.latest_digest()
            known_text, known_ids = self.profiler.known(course) if self.profiler else ("", set())
            values = dict(
                lecture_title=course.title, language=self.tutor.language, outline=self.tutor._outline(course),
                threads=threads_text(course, hs, prev.up_to_message_id if prev else 0, self.tutor.language),
                notes=notes_text(course), quiz=quiz_text(course, self.library),
                previous=previous_text(prev.data if prev else None, by_id), known_weak_points=known_text)
            feedback = ""
            for _attempt in range(2):
                prompt = self.tutor.prompts.render("digest-lecture", **values, feedback=feedback)
                done = await self.tutor._stream(job, prompt, [], None, self.tutor.prompts.role(),
                                                json_schema=DIGEST_SCHEMA, cwd=self.tutor._dir(course), model=self.model)
                problems = digest_problems(done.data)
                if not problems:
                    break
                job.emit({"type": "reset"})
                feedback = "## 上一次的结果有问题，请修正后重新输出\n\n" + "\n".join(f"- {p}" for p in problems)
            if problems:
                raise ValueError("Claude's summary was malformed twice; try again")
            data = clean(done.data, hs, course.slide_count(), known_ids)
            d = course.add_digest(data, render(data, course, self.tutor.locale), watermark, done.model)
            course.set_lecture_status("done")
            profile = None
            folder = self.profiler.folder_of(course) if self.profiler else None
            if folder is not None:  # update A (§15.4.2): the summary's findings go into the course profile
                from .profile import findings
                job.emit({"type": "phase", "phase": "profile"})
                try:
                    profile = await self.profiler.update(
                        job, folder, "digest", findings(data, course.id, {t.id: t.page for t in all_threads}))
                except Exception as e:  # noqa: BLE001 — the summary itself is saved; the profile can be organized later
                    job.emit({"type": "phase", "phase": "profile_failed", "message": str(e)})
            job.emit({"type": "done", "digest": digest_dict(course, d), "profile": profile})

        return self.tutor.jobs.submit(self.job_id(course), "digest", f"{course.id}:digest", run)


def digest_dict(course: Course, d) -> dict[str, Any]:
    return {"content": d.content, "data": d.data, "up_to_message_id": d.up_to_message_id, "model": d.model,
            "created_at": d.created_at, "new_messages": course.messages_after(d.up_to_message_id)}

