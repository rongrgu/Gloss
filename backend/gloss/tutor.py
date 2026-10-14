"""Explanations and per-slide Q&A on top of a provider (DESIGN.md §5.4, §7)."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import pymupdf

from .jobs import Job, JobManager
from .providers import Done, LLMProvider, ProviderError, Reset, SessionNotFound, SessionRef, TextDelta
from .store import Course, Message, Thread

NONE = "（无）"
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


class Tutor:
    def __init__(self, provider: LLMProvider, jobs: JobManager, workspace: Path, language: str = "中文"):
        self.provider = provider
        self.jobs = jobs
        self.workspace = Path(workspace)
        self.prompts = Prompts(workspace)
        self.language = language

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
            done = await self._stream(job, prompt, [Scratch(self.workspace, course).slide(page)],
                                      session=None, system=self.prompts.role())
            e = course.add_explanation(page, done.text, done.model)
            job.emit({"type": "done", "explanation": {"page": page, "content": e.content, "model": e.model,
                                                      "created_at": e.created_at}})

        return self.jobs.submit(f"{course.id}:explain:{page}", "explain", None, run, {"page": page})

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

    def ask(self, course: Course, page: int, rect: tuple[float, float, float, float],
            question: str) -> tuple[Thread, Message, Job]:
        question = question.strip()
        if not question:
            raise ValueError("empty question")
        image = course.get_slide_image(page)
        if image is None:
            raise KeyError(f"no slide {page}")
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
            first_ids = _first_user_message_ids(course, page)
            is_new = msg.id in first_ids
            current = self._tagged(course, thread, msg, is_new)
            current_images = [p for p in [scratch.crop(thread)] if is_new and p]

            done = None
            sid = course.get_session(page)
            if sid:
                try:
                    done = await self._stream(job, current, current_images, SessionRef(sid, new=False), None)
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
                done = await self._stream(job, prompt, [scratch.slide(page), *current_images], ref, system)
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
        if is_new:
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

    async def _stream(self, job: Job, prompt: str, images: list[Path], session: SessionRef | None,
                      system: str | None) -> Done:
        async for ev in self.provider.run(prompt, images=images, session=session, system=system):
            if isinstance(ev, TextDelta):
                job.emit({"type": "delta", "text": ev.text})
            elif isinstance(ev, Reset):
                job.emit({"type": "reset"})
            elif isinstance(ev, Done):
                return ev
        raise ProviderError("provider ended without a result")


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
