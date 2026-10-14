"""FastAPI app: courses, slides, explanations, Q&A, notes, UI state, SSE job streams."""

from __future__ import annotations

import json
import re
import tempfile
import threading
import time
from urllib.parse import quote
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import schema
from .config import Config, app_dir
from .doctor import auth_status
from .export import export_markdown
from .ingest import IngestError, create_course
from .jobs import JobManager
from .providers import LLMProvider
from .providers.claude_cli import ClaudeCLIProvider, find_claude
from .store import Course, Message, Thread
from .tutor import Tutor

LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}
STATIC_DIR = Path(__file__).parent / "static"  # frontend build output (npm run build in frontend/)


# --- open courses & recent list ------------------------------------------------

class Library:
    def __init__(self, library_dir: Path, recent_file: Path):
        self.dir = library_dir
        self.recent_file = recent_file
        self._open: dict[str, Course] = {}
        self._lock = threading.Lock()

    def open(self, path: Path) -> Course:
        path = path.expanduser().resolve()
        with self._lock:
            for c in self._open.values():
                if c.path.resolve() == path:
                    break
            else:
                c = Course.open(path)
                self._open[c.id] = c
        self._touch_recent(c)
        return c

    def add(self, course: Course) -> Course:
        with self._lock:
            self._open[course.id] = course
        self._touch_recent(course)
        return course

    def get(self, cid: str) -> Course:
        c = self._open.get(cid)
        if c is None:
            raise HTTPException(404, "course not open")
        return c

    def close(self, cid: str) -> None:
        with self._lock:
            c = self._open.pop(cid, None)
        if c:
            c.close()

    def close_all(self) -> None:
        for cid in list(self._open):
            self.close(cid)

    def new_path(self, stem: str) -> Path:
        stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", stem).strip(" .") or "course"
        self.dir.mkdir(parents=True, exist_ok=True)
        p, i = self.dir / f"{stem}.gloss", 2
        while p.exists():
            p, i = self.dir / f"{stem}-{i}.gloss", i + 1
        return p

    def recent(self) -> list[dict[str, Any]]:
        try:
            items = json.loads(self.recent_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return [{**it, "exists": Path(it["path"]).exists()} for it in items]

    def _touch_recent(self, c: Course) -> None:
        items = [it for it in self.recent() if it["path"] != str(c.path.resolve())]
        items.insert(0, {"path": str(c.path.resolve()), "title": c.title,
                         "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        self.recent_file.parent.mkdir(parents=True, exist_ok=True)
        self.recent_file.write_text(json.dumps([{k: it[k] for k in ("path", "title", "opened_at")}
                                                for it in items[:20]], ensure_ascii=False, indent=1))


# --- request bodies ---------------------------------------------------------------

class OpenBody(BaseModel):
    path: str


class AskBody(BaseModel):
    rect: tuple[float, float, float, float]
    question: str = Field(min_length=1)


class FollowUpBody(BaseModel):
    text: str = Field(min_length=1)


class ThreadPatch(BaseModel):
    minimized: bool


class NoteBody(BaseModel):
    content: str


# --- serialization ------------------------------------------------------------------

def slide_aspect(c: Course) -> float:
    """Width / height of the rendered slides (from slide 1's PNG header), so thumbnails can be laid out before loading."""
    img = c.get_slide_image(1)
    if not img or img[:8] != b"\x89PNG\r\n\x1a\n":
        return 16 / 9
    w, h = int.from_bytes(img[16:20], "big"), int.from_bytes(img[20:24], "big")
    return w / h if h else 16 / 9


def course_summary(c: Course, jobs: JobManager | None = None) -> dict[str, Any]:
    tree_job = jobs.get(f"{c.id}:tree") if jobs else None
    return {
        "id": c.id, "title": c.title, "path": str(c.path), "slide_count": c.slide_count(),
        "aspect": round(slide_aspect(c), 4),
        "tree": c.get_tree(), "tree_job": tree_job.to_dict() if tree_job and not tree_job.finished else None,
        "question_counts": c.thread_counts(), "pages_with_notes": sorted(c.pages_with_notes()),
    }


def thread_dict(t: Thread, messages: list[Message] | None = None) -> dict[str, Any]:
    d = {"id": t.id, "page": t.page, "n": t.n, "anchor_type": t.anchor_type,
         "rect": [t.x, t.y, t.w, t.h], "minimized": t.minimized, "created_at": t.created_at}
    if messages is not None:
        d["messages"] = [message_dict(m) for m in messages]
    return d


def message_dict(m: Message) -> dict[str, Any]:
    return {"id": m.id, "thread_id": m.thread_id, "role": m.role, "content": m.content, "created_at": m.created_at}


# --- app ---------------------------------------------------------------------------------

def create_app(config: Config | None = None, provider: LLMProvider | None = None,
               static_dir: Path = STATIC_DIR) -> FastAPI:
    # Endpoints that touch `jobs` are `async def` so they run on the event loop
    # (JobManager is not thread-safe and starts asyncio tasks).
    config = config or Config.load()
    if provider is None:
        provider = ClaudeCLIProvider(config.workspace, model=config.model, claude_bin=config.claude_bin,
                                     timeout=config.timeout)
    jobs = JobManager(config.concurrency)
    tutor = Tutor(provider, jobs, config.workspace, language=config.language)
    library = Library(config.library, app_dir() / "recent.json")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await jobs.shutdown()
        library.close_all()

    app = FastAPI(title="Gloss", lifespan=lifespan)
    app.state.library, app.state.jobs, app.state.tutor = library, jobs, tutor

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # The API can spend the user's Claude quota and read their files: refuse
        # DNS-rebinding hosts and cross-site requests from other web pages.
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in LOCAL_HOSTS:
            return JSONResponse({"detail": "bad host"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and re.sub(r"^https?://", "", origin).rsplit(":", 1)[0] not in LOCAL_HOSTS:
            return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        return await call_next(request)

    def slide_or_404(c: Course, page: int) -> None:
        if not 1 <= page <= c.slide_count():
            raise HTTPException(404, f"no slide {page}")

    def thread_or_404(c: Course, tid: str) -> Thread:
        t = c.get_thread(tid)
        if t is None:
            raise HTTPException(404, "no such thread")
        return t

    # --- app-level ---

    auth_cache: dict[str, Any] = {"at": -1e9, "value": None}

    @app.get("/api/health")
    def health():
        claude = config.claude_bin or find_claude()
        if claude and time.monotonic() - auth_cache["at"] > 30:
            st = auth_status(claude)
            auth_cache.update(at=time.monotonic(), value=None if st is None else bool(st.get("loggedIn")))
        return {"provider": config.provider, "model": config.model, "language": config.language,
                "claude_found": bool(claude), "logged_in": auth_cache["value"] if claude else False}

    @app.get("/api/recent")
    def recent():
        return library.recent()

    # --- courses ---

    @app.post("/api/courses/import")
    async def import_pdf(file: UploadFile = File(...), title: str | None = Form(None)):
        stem = Path(file.filename or "course.pdf").stem
        out = library.new_path(stem)
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / f"{stem}.pdf"  # create_course falls back to the file name for the title
            pdf.write_bytes(await file.read())
            try:
                course = create_course(pdf, out, title=title or None)
            except IngestError as e:
                raise HTTPException(422, str(e)) from e
        return course_summary(library.add(course), jobs)

    @app.post("/api/courses/open")
    def open_course(body: OpenBody):
        try:
            return course_summary(library.open(Path(body.path)), jobs)
        except FileNotFoundError as e:
            raise HTTPException(404, "file not found") from e
        except (schema.NotAGlossFile, schema.FileTooNew) as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/api/courses/{cid}")
    async def get_course(cid: str):
        return course_summary(library.get(cid), jobs)

    @app.get("/api/courses/{cid}/export.md")
    def export(cid: str):
        c = library.get(cid)
        name = f"{c.path.stem}.md"
        return Response(export_markdown(c), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    @app.post("/api/courses/{cid}/tree")
    async def build_tree(cid: str, regenerate: bool = False):
        c = library.get(cid)
        job = tutor.build_tree(c, regenerate=regenerate)
        return {"tree": c.get_tree()} if job is None else {"job": job.to_dict()}

    @app.post("/api/courses/{cid}/close")
    def close_course(cid: str):
        library.close(cid)
        return {"ok": True}

    # --- slides ---

    @app.get("/api/courses/{cid}/slides/{page}")
    async def get_slide(cid: str, page: int):
        c = library.get(cid)
        s = c.get_slide(page)
        if s is None:
            raise HTTPException(404, f"no slide {page}")
        e = c.latest_explanation(page)
        explain_job = jobs.get(f"{cid}:explain:{page}")
        return {
            "page": page, "text": s.text, "text_blocks": s.text_blocks,
            "explanation": {"content": e.content, "model": e.model, "created_at": e.created_at} if e else None,
            "explanation_job": explain_job.to_dict() if explain_job and not explain_job.finished else None,
            "threads": [thread_dict(t, c.list_messages(t.id)) for t in c.list_threads(page)],
            "answer_jobs": [j.to_dict() for j in jobs.active(f"{cid}:qa:") if j.meta.get("page") == page],
            "note": c.get_note(page),
        }

    @app.get("/api/courses/{cid}/slides/{page}/image")
    def slide_image(cid: str, page: int):
        img = library.get(cid).get_slide_image(page)
        if img is None:
            raise HTTPException(404, f"no slide {page}")
        return Response(img, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})

    @app.post("/api/courses/{cid}/slides/{page}/explanation")
    async def explain(cid: str, page: int, regenerate: bool = False):
        c = library.get(cid)
        slide_or_404(c, page)
        job = tutor.explain(c, page, regenerate=regenerate)
        if job is None:
            e = c.latest_explanation(page)
            return {"explanation": {"content": e.content, "model": e.model, "created_at": e.created_at}}
        return {"job": job.to_dict()}

    @app.get("/api/courses/{cid}/slides/{page}/session")
    def session_log(cid: str, page: int):
        c = library.get(cid)
        slide_or_404(c, page)
        return tutor.session_log(c, page)

    @app.put("/api/courses/{cid}/slides/{page}/note")
    def put_note(cid: str, page: int, body: NoteBody):
        c = library.get(cid)
        slide_or_404(c, page)
        c.set_note(page, body.content)
        return {"ok": True}

    # --- threads ---

    @app.post("/api/courses/{cid}/slides/{page}/threads")
    async def ask(cid: str, page: int, body: AskBody):
        c = library.get(cid)
        slide_or_404(c, page)
        try:
            t, m, job = tutor.ask(c, page, body.rect, body.question)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return {"thread": thread_dict(t, [m]), "job": job.to_dict()}

    @app.post("/api/courses/{cid}/threads/{tid}/messages")
    async def follow_up(cid: str, tid: str, body: FollowUpBody):
        c = library.get(cid)
        thread_or_404(c, tid)
        try:
            m, job = tutor.follow_up(c, tid, body.text)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return {"message": message_dict(m), "job": job.to_dict()}

    @app.post("/api/courses/{cid}/threads/{tid}/retry")
    async def retry(cid: str, tid: str):
        c = library.get(cid)
        thread_or_404(c, tid)
        try:
            return {"job": tutor.retry(c, tid).to_dict()}
        except ValueError as e:
            raise HTTPException(409, str(e)) from e

    @app.get("/api/courses/{cid}/threads/{tid}/crop")
    def thread_crop(cid: str, tid: str):
        t = thread_or_404(library.get(cid), tid)
        if not t.crop:
            raise HTTPException(404, "no crop")
        return Response(t.crop, media_type="image/png")

    @app.patch("/api/courses/{cid}/threads/{tid}")
    def patch_thread(cid: str, tid: str, body: ThreadPatch):
        c = library.get(cid)
        thread_or_404(c, tid)
        c.set_thread_minimized(tid, body.minimized)
        return {"ok": True}

    @app.delete("/api/courses/{cid}/threads/{tid}")
    async def delete_thread(cid: str, tid: str):
        c = library.get(cid)
        t = thread_or_404(c, tid)
        if any(j.meta.get("thread_id") == tid for j in jobs.active(f"{cid}:qa:")):
            raise HTTPException(409, "thread has an answer in progress")
        c.delete_thread(t.id)
        return {"ok": True}

    # --- ui state ---

    @app.get("/api/courses/{cid}/ui/{key}")
    def get_ui(cid: str, key: str):
        return {"value": library.get(cid).get_ui(key)}

    @app.put("/api/courses/{cid}/ui/{key}")
    async def put_ui(cid: str, key: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict) or "value" not in body:
            raise HTTPException(422, 'body must be {"value": ...}')
        library.get(cid).set_ui(key, body["value"])
        return {"ok": True}

    # --- jobs (SSE) ---

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job (finished jobs expire)")

        async def stream() -> AsyncIterator[bytes]:
            async for ev in job.subscribe():
                yield f"event: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode()

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # The frontend uses hash routes, so serving index.html at / is all it needs.
    if (static_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app
