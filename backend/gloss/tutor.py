"""Explanations and per-slide Q&A on top of a provider (DESIGN.md §5.4, §7)."""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from typing import Any

import pymupdf

from .jobs import Job, JobManager
from .providers import Done, LLMProvider, ProviderError, Reset, SessionNotFound, SessionRef, TextDelta, ToolUse
from .store import Course, Message, Thread
from . import transcript as tr

NONE = "（无）"
TREE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "children": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "slides": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["title", "summary", "slides"],
            },
        },
    },
    "required": ["title", "children"],
}
SLIDE_TEXT_LIMIT = 1500  # chars per slide sent for tree building
COVER_FRACTION = 0.3  # a text block counts as selected if this much of it is inside the rect


class Prompts:
    """Markdown templates in claude_workspace/. `{{name}}` placeholders; a leading <!-- comment --> is dropped."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)

    def _read(self, rel: str) -> str:
        text = (self.workspace / rel).read_text(encoding="utf-8")
        return re.sub(r"\A\s*<!--.*?-->\s*", "", text, flags=re.S).strip()

    def role(self) -> str:
        return self._read("CLAUDE.md")

    def render(self, name: str, **values: Any) -> str:
        template = self._read(f"prompts/{name}.md")

        def sub(m: re.Match) -> str:
            key = m.group(1)
            if key not in values:
                raise KeyError(f"prompts/{name}.md uses {{{{{key}}}}} but no value was given")
            v = values[key]
            return str(v).strip() if v not in (None, "") else NONE

        return re.sub(r"\{\{\s*(\w+)\s*\}\}", sub, template)


def thread_tag(thread: Thread, new: bool) -> str:
    if new and thread.anchor_type == "explanation_text":
        return f"[Thread #{thread.n} · 讲解中选中的一段 · 新问题]"
    if new:
        r = f"x={thread.x:.2f} y={thread.y:.2f} w={thread.w:.2f} h={thread.h:.2f}"
        return f"[Thread #{thread.n} · 选区 {r} · 新问题]"
    return f"[Thread #{thread.n} · 追问]"


def covered_text(blocks: list[dict], rect: tuple[float, float, float, float]) -> list[str]:
    x, y, w, h = rect
    out = []
    for b in blocks:
        ix = max(0.0, min(x + w, b["x"] + b["w"]) - max(x, b["x"]))
        iy = max(0.0, min(y + h, b["y"] + b["h"]) - max(y, b["y"]))
        area = b["w"] * b["h"]
        if area > 0 and ix * iy / area >= COVER_FRACTION:
            out.append(b["text"])
    return out


def crop_png(image: bytes, rect: tuple[float, float, float, float]) -> bytes:
    pix = pymupdf.Pixmap(image)
    x, y, w, h = rect
    clip = pymupdf.IRect(
        round(x * pix.width), round(y * pix.height),
        round((x + w) * pix.width), round((y + h) * pix.height),
    ) & pix.irect
    if clip.is_empty:
        raise ValueError("selection is empty")
    return pymupdf.Pixmap(pix, pix.width, pix.height, clip).tobytes("png")


class Scratch:
    """claude_workspace/courses/<course id>/: images the CLI reads with Read. Not committed; recreated on demand."""

    def __init__(self, workspace: Path, course: Course):
        self.dir = Path(workspace) / "courses" / course.id
        self.course = course

    def _write(self, name: str, data: bytes) -> Path:
        p = self.dir / name
        if not p.exists():
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(p)
        return p

    def slide(self, page: int) -> Path:
        image = self.course.get_slide_image(page)
        if image is None:
            raise KeyError(f"no slide {page}")
        return self._write(f"page-{page}.png", image)

    def crop(self, thread: Thread) -> Path | None:
        return self._write(f"crop-{thread.id}.png", thread.crop) if thread.crop else None

    def materials(self) -> None:
        """Whole-course materials for retrieval: course.md (all slide text + transcript) and every page image.
        course.md is rewritten only when its content changes (e.g. after a transcript is aligned)."""
        parts = [f"# 《{self.course.title}》全部课件内容\n"]
        for p, text in self.course.slide_texts():
            parts.append(f"## 第 {p} 页\n\n{text.strip() or '（这一页没有可抽取的文字）'}\n")
            span = self.course.get_slide(p).transcript_span
            if span:
                parts.append(f"### 第 {p} 页 · 老师讲这一页时说的话\n\n{span}\n")
        content = "\n".join(parts).encode()
        md = self.dir / "course.md"
        if not md.exists() or hashlib.sha256(md.read_bytes()).digest() != hashlib.sha256(content).digest():
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = md.with_suffix(".tmp")
            tmp.write_bytes(content)
            tmp.replace(md)
        for p in range(1, self.course.slide_count() + 1):
            self.slide(p)


class Tutor:
    def __init__(self, provider: LLMProvider, jobs: JobManager, workspace: Path, language: str = "中文",
                 retrieval: bool = True):
        self.provider = provider
        self.jobs = jobs
        self.workspace = Path(workspace)
        self.prompts = Prompts(workspace)
        self.language = language
        # Q&A may Grep/Read the whole course (§8, M6) — only with a provider that has tools.
        self.retrieval = retrieval and getattr(provider, "supports_tools", True)

    # --- explanations (§5.4) ----------------------------------------------

    def explain(self, course: Course, page: int, regenerate: bool = False) -> Job | None:
        """Start (or join) generating the explanation. None if one is cached and not regenerating."""
        if course.get_slide_image(page) is None:
            raise KeyError(f"no slide {page}")
        if not regenerate and course.latest_explanation(page):
            return None

        async def run(job: Job) -> None:
            prompt = self.prompts.render(
                "explain-slide",
                course_title=course.title,
                page=page,
                page_count=course.slide_count(),
                outline=self._outline(course),
                prev_text=_text(course, page - 1),
                text=_text(course, page),
                next_text=_text(course, page + 1),
                transcript=course.get_slide(page).transcript_span,
                language=self.language,
            )
            scratch = Scratch(self.workspace, course)
            done = await self._stream(job, prompt, [scratch.slide(page)], session=None, system=self.prompts.role(),
                                      cwd=scratch.dir)
            e = course.add_explanation(page, done.text, done.model)
            job.emit({"type": "done", "explanation": {"page": page, "content": e.content, "model": e.model,
                                                      "created_at": e.created_at}})

        return self.jobs.submit(f"{course.id}:explain:{page}", "explain", None, run, {"page": page})

    # --- mind map tree (§5.2) ---------------------------------------------

    def build_tree(self, course: Course, regenerate: bool = False) -> Job | None:
        """Start (or join) building the mind map. None if one exists and not regenerating."""
        if not regenerate and course.get_tree():
            return None
        n = course.slide_count()

        async def run(job: Job) -> None:
            slides = "\n\n".join(
                f"### 第 {p} 页\n{(t.strip()[:SLIDE_TEXT_LIMIT] or '（这一页没有可抽取的文字）')}"
                for p, t in course.slide_texts()
            )
            values = dict(course_title=course.title, page_count=n, slides=slides, language=self.language)
            feedback = ""
            for attempt in range(2):
                prompt = self.prompts.render("build-tree", **values, feedback=feedback)
                done = await self._stream(job, prompt, [], session=None, system=self.prompts.role(),
                                          json_schema=TREE_SCHEMA, cwd=self._dir(course))
                problems = tree_problems(done.data, n)
                if not problems:
                    break
                job.emit({"type": "reset"})
                feedback = ("## 上一次的结果有问题，请修正\n\n" + "\n".join(f"- {p}" for p in problems))
            tree = repair_tree(done.data, n, fallback_title=course.title)
            course.set_tree(tree)
            job.emit({"type": "done", "tree": tree})

        return self.jobs.submit(f"{course.id}:tree", "tree", None, run)

    # --- transcript alignment (§5.3) ----------------------------------------

    TRANSCRIPT_ASSET = "transcript"

    def align_transcript(self, course: Course) -> Job:
        """(Re)align the stored transcript to the slides. Replaces all transcript spans."""
        asset = course.get_asset(self.TRANSCRIPT_ASSET)
        if asset is None:
            raise KeyError("no transcript uploaded")
        n = course.slide_count()
        text = tr.decode(asset[1])
        segs = tr.segment(text, tr.target_for(text, n))
        if not segs:
            raise ValueError("transcript is empty")

        async def run(job: Job) -> None:
            slides = "\n\n".join(f"### 第 {p} 页\n{(t.strip()[:SLIDE_TEXT_LIMIT] or '（这一页没有可抽取的文字）')}"
                                  for p, t in course.slide_texts())
            values = dict(course_title=course.title, page_count=n, slides=slides,
                          last_segment=len(segs) - 1, segments=tr.for_prompt(segs))
            feedback = ""
            for _ in range(2):
                prompt = self.prompts.render("align-transcript", **values, feedback=feedback)
                done = await self._stream(job, prompt, [], session=None, system=self.prompts.role(),
                                          json_schema=tr.ALIGN_SCHEMA, cwd=self._dir(course))
                problems = tr.alignment_problems(done.data, n, len(segs))
                if not problems:
                    break
                job.emit({"type": "reset"})
                feedback = "## 上一次的结果有问题，请修正\n\n" + "\n".join(f"- {p}" for p in problems)
            spans = tr.repair_alignment(done.data, n, len(segs))
            course.set_transcript_spans({p: tr.span_text(segs, a, b) for p, (a, b) in spans.items()})
            job.emit({"type": "done", "aligned_pages": sorted(spans), "segments": len(segs)})

        return self.jobs.submit(f"{course.id}:align", "align", None, run)

    def _outline(self, course: Course) -> str:
        tree = course.get_tree()
        if not tree:
            return ""
        lines = [f"# {tree.get('title', '')}"]
        for topic in tree.get("children", []):
            pages = topic.get("slides", [])
            span = f"{min(pages)}–{max(pages)}" if pages else ""
            lines.append(f"- {topic.get('title', '')}（第 {span} 页）{topic.get('summary', '')}")
        return "\n".join(lines)

    # --- Q&A (§7) -----------------------------------------------------------

    def ask(self, course: Course, page: int, rect: tuple[float, float, float, float] | None,
            question: str, quote: str | None = None) -> tuple[Thread, Message, Job]:
        """New question on a slide region (`rect`) or on a passage of the explanation (`quote`)."""
        question = question.strip()
        if not question:
            raise ValueError("empty question")
        image = course.get_slide_image(page)
        if image is None:
            raise KeyError(f"no slide {page}")
        if (rect is None) == (quote is None):
            raise ValueError("give either rect or quote")
        if quote is not None:
            thread = course.create_thread(page, None, anchor_type="explanation_text", quote=quote)
        else:
            thread = course.create_thread(page, rect, crop=crop_png(image, rect))
        msg = course.add_message(thread.id, "user", question)
        return thread, msg, self._submit_answer(course, thread, msg)

    def follow_up(self, course: Course, thread_id: str, text: str) -> tuple[Message, Job]:
        text = text.strip()
        if not text:
            raise ValueError("empty message")
        thread = course.get_thread(thread_id)
        if thread is None:
            raise KeyError(thread_id)
        msg = course.add_message(thread.id, "user", text)
        return msg, self._submit_answer(course, thread, msg)

    def retry(self, course: Course, thread_id: str) -> Job:
        """Re-run the last user message of a thread if it has no answer (e.g. after an error)."""
        thread = course.get_thread(thread_id)
        if thread is None:
            raise KeyError(thread_id)
        msgs = course.list_messages(thread_id)
        if not msgs or msgs[-1].role != "user":
            raise ValueError("nothing to retry")
        return self._submit_answer(course, thread, msgs[-1])

    def _submit_answer(self, course: Course, thread: Thread, msg: Message) -> Job:
        page = thread.page

        async def run(job: Job) -> None:
            scratch = Scratch(self.workspace, course)
            if self.retrieval:
                scratch.materials()
            qa = dict(cwd=scratch.dir, tools=("Read", "Grep", "Glob") if self.retrieval else ("Read",))
            first_ids = _first_user_message_ids(course, page)
            is_new = msg.id in first_ids
            current = self._tagged(course, thread, msg, is_new)
            current_images = [p for p in [scratch.crop(thread)] if is_new and p]

            done = None
            sid = course.get_session(page)
            if sid:
                try:
                    done = await self._stream(job, current, current_images, SessionRef(sid, new=False), None, **qa)
                except SessionNotFound:
                    job.emit({"type": "reset"})  # rebuild below
            if done is None:
                ref = SessionRef(str(uuid.uuid4()), new=True)
                prompt = self._opening(course, page) + "\n\n"
                history = [(t, m) for t, m in course.page_history(page) if m.id < msg.id]
                if history:
                    prompt += self.prompts.render("qa-rebuild", history=self._history(course, history, first_ids))
                    prompt += "\n\n"
                prompt += current
                system = self.prompts.role() + "\n\n" + self.prompts.render("qa-system", language=self.language)
                if self.retrieval:
                    system += "\n\n" + self.prompts.render("qa-retrieval", page_count=course.slide_count())
                done = await self._stream(job, prompt, [scratch.slide(page), *current_images], ref, system, **qa)
                done.session_id = done.session_id or ref.id

            course.set_session(page, done.session_id)
            answer = course.add_message(thread.id, "assistant", done.text)
            job.emit({"type": "done", "message": _message_dict(answer)})

        return self.jobs.submit(
            f"{course.id}:qa:{msg.id}", "answer", f"{course.id}:qa:{page}", run,
            {"page": page, "thread_id": thread.id, "message_id": msg.id},
        )

    def _tagged(self, course: Course, thread: Thread, msg: Message, is_new: bool) -> str:
        parts = [thread_tag(thread, is_new)]
        if is_new and thread.anchor_type == "explanation_text":
            parts.append("选中的讲解文字：\n" + "\n".join(f"> {line}" for line in thread.quote.splitlines() if line.strip()))
        elif is_new:
            covered = covered_text(course.get_slide(thread.page).text_blocks, (thread.x, thread.y, thread.w, thread.h))
            if covered:
                parts.append("选区覆盖的文字：\n" + "\n".join(f"> {t}" for t in covered))
        parts.append(msg.content)
        return "\n".join(parts)

    def _opening(self, course: Course, page: int) -> str:
        e = course.latest_explanation(page)
        return self.prompts.render(
            "qa-opening",
            course_title=course.title,
            page=page,
            page_count=course.slide_count(),
            text=_text(course, page),
            transcript=course.get_slide(page).transcript_span,
            explanation=e.content if e else "",
        )

    def _history(self, course: Course, history: list[tuple[Thread, Message]], first_ids: set[int]) -> str:
        out = []
        for t, m in history:
            if m.role == "user":
                out.append(f"学生：{self._tagged(course, t, m, m.id in first_ids)}")
            else:
                out.append(f"助教（Thread #{t.n}）：{m.content}")
        return "\n\n".join(out)

    def session_log(self, course: Course, page: int) -> dict[str, Any]:
        """What this page's session contains, as tagged messages (the debug drawer, §9)."""
        first_ids = _first_user_message_ids(course, page)
        return {
            "session_id": course.get_session(page),
            "messages": [
                {"role": m.role, "thread": t.n,
                 "content": self._tagged(course, t, m, m.id in first_ids) if m.role == "user" else m.content}
                for t, m in course.page_history(page)
            ],
        }

    # --- streaming ----------------------------------------------------------

    def _dir(self, course: Course) -> Path:
        d = Scratch(self.workspace, course).dir
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def _stream(self, job: Job, prompt: str, images: list[Path], session: SessionRef | None,
                      system: str | None, json_schema: dict | None = None, cwd: Path | None = None,
                      tools: tuple[str, ...] = ("Read",)) -> Done:
        kw: dict[str, Any] = {"cwd": cwd, "tools": tools}
        if json_schema is not None:
            kw["json_schema"] = json_schema
        async for ev in self.provider.run(prompt, images=images, session=session, system=system, **kw):
            if isinstance(ev, ToolUse):
                job.emit({"type": "tool", "name": ev.name, "target": tool_target(ev.name, ev.input)})
            elif isinstance(ev, TextDelta):
                job.emit({"type": "delta", "text": ev.text})
            elif isinstance(ev, Reset):
                job.emit({"type": "reset"})
            elif isinstance(ev, Done):
                return ev
        raise ProviderError("provider ended without a result")


def tool_target(name: str, inp: dict) -> str:
    """Short machine-readable description of a tool call for the UI: 'page:7', 'course', 'search:<pattern>'."""
    if name == "Read":
        base = Path(str(inp.get("file_path", ""))).name
        m = re.fullmatch(r"page-(\d+)\.png", base)
        return f"page:{m.group(1)}" if m else ("course" if base == "course.md" else "file")
    if name == "Grep":
        return f"search:{str(inp.get('pattern', ''))[:60]}"
    return "files"


def tree_problems(data: Any, n: int) -> list[str]:
    """What is wrong with a generated tree, in words Claude can act on. Empty if valid."""
    if not isinstance(data, dict) or not isinstance(data.get("children"), list):
        return ["结果不是 {title, children: [...]} 的结构"]
    topics = [t for t in data["children"] if isinstance(t, dict)]
    if not topics:
        return ["没有任何主题"]
    problems = []
    seen: dict[int, str] = {}
    for t in topics:
        pages = [p for p in t.get("slides") or [] if isinstance(p, int)]
        if not pages:
            problems.append(f"主题「{t.get('title', '')}」没有任何页")
        for p in pages:
            if not 1 <= p <= n:
                problems.append(f"主题「{t.get('title', '')}」里的第 {p} 页不存在（只有 1–{n} 页）")
            elif p in seen:
                problems.append(f"第 {p} 页同时出现在「{seen[p]}」和「{t.get('title', '')}」")
            else:
                seen[p] = t.get("title", "")
    missing = [p for p in range(1, n + 1) if p not in seen]
    if missing:
        problems.append("这些页没有被分到任何主题：" + "、".join(map(str, missing)))
    return problems


def repair_tree(data: Any, n: int, fallback_title: str = "") -> dict:
    """Force a tree into shape: every page 1..n in exactly one topic, topics non-empty, pages sorted."""
    data = data if isinstance(data, dict) else {}
    seen: set[int] = set()
    topics = []
    for t in data.get("children") or []:
        if not isinstance(t, dict):
            continue
        pages = []
        for p in sorted({p for p in t.get("slides") or [] if isinstance(p, int)}):
            if 1 <= p <= n and p not in seen:
                pages.append(p)
                seen.add(p)
        if pages:
            topics.append({"title": str(t.get("title") or "未命名主题").strip(),
                           "summary": str(t.get("summary") or "").strip(), "slides": pages})
    if not topics:
        topics = [{"title": "全部 slides", "summary": "", "slides": []}]
    for p in range(1, n + 1):
        if p in seen:
            continue
        # Put a missing page with the topic holding the nearest earlier page.
        before = [t for t in topics if t["slides"] and min(t["slides"]) < p]
        target = max(before, key=lambda t: max(q for q in t["slides"] if q < p)) if before else topics[0]
        target["slides"] = sorted(target["slides"] + [p])
    return {
        "title": str(data.get("title") or fallback_title).strip(),
        "subtitle": str(data.get("subtitle") or "").strip(),
        "children": topics,
    }


def _text(course: Course, page: int) -> str:
    s = course.get_slide(page) if page >= 1 else None
    return s.text if s else ""


def _first_user_message_ids(course: Course, page: int) -> set[int]:
    seen: dict[str, int] = {}
    for t, m in course.page_history(page):
        if m.role == "user" and t.id not in seen:
            seen[t.id] = m.id
    return set(seen.values())


def _message_dict(m: Message) -> dict[str, Any]:
    return {"id": m.id, "thread_id": m.thread_id, "role": m.role, "content": m.content, "created_at": m.created_at}
