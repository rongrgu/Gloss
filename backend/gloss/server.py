"""FastAPI app: courses, slides, explanations, Q&A, notes, UI state, SSE job streams."""

from __future__ import annotations

import json
import os
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
from .doctor import auth_status, codex_logged_in
from .export import export_markdown
from .folders import EPSILON, FolderError, Folders, key_between
from .i18n import LANGUAGE_FOR, Prefs
from .ingest import IngestError, create_course
from .jobs import JobManager
from .quiz import TYPES, QuizMaker, QuizStore
from .digest import Digester, digest_dict
from .profile import STATUSES as WP_STATUSES, ProfileError, ProfileStore, Profiler, on_mark, on_quiz_answer
from .providers import LLMProvider
from .providers.claude_cli import find_claude
from .providers.factory import Choice, choose_provider, make_provider
from .providers.adapters.codex import find_codex
from . import __version__
from .store import Course, Message, Thread
from .transcript import decode, segment
from .tutor import Tutor

TRANSCRIPT_MAX_BYTES = 5 * 1024 * 1024

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

    def new_path(self, stem: str, folder: Path | None = None) -> Path:
        stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", stem).strip(" .") or "course"
        d = folder or self.dir
        d.mkdir(parents=True, exist_ok=True)
        p, i = d / f"{stem}.gloss", 2
        while p.exists():
            p, i = d / f"{stem}-{i}.gloss", i + 1
        return p

    def open_under(self, path: Path) -> list[Course]:
        """Open courses whose file is `path` or inside it."""
        path = path.resolve()
        return [c for c in list(self._open.values()) if c.path.resolve() == path or c.path.resolve().is_relative_to(path)]

    def is_open(self, path: Path) -> bool:
        return any(c.path.resolve() == path.resolve() for c in list(self._open.values()))

    def find_open(self, path: Path) -> Course | None:
        return next((c for c in list(self._open.values()) if c.path.resolve() == path.resolve()), None)

    def moved(self, src: Path, dst: Path) -> None:
        """Keep the recent list pointing at files after a move or rename."""
        src, dst = src.resolve(), dst.resolve()
        items = self.recent()
        changed = False
        for it in items:
            p = Path(it["path"])
            if p == src or p.is_relative_to(src):
                it["path"] = str(dst / p.relative_to(src))
                changed = True
        if changed:
            self.recent_file.write_text(json.dumps([{k: it[k] for k in ("path", "title", "opened_at")}
                                                    for it in items], ensure_ascii=False, indent=1))

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
    rect: tuple[float, float, float, float] | None = None  # a region of the slide…
    quote: str | None = None  # …or a passage of the explanation
    question: str = Field(min_length=1)


class FollowUpBody(BaseModel):
    text: str = Field(min_length=1)


class ThreadPatch(BaseModel):
    """Either field may be sent alone; `understood: null` clears the mark (§15.2)."""
    minimized: bool | None = None
    understood: bool | None = None


class NoteBody(BaseModel):
    content: str


class PrefsBody(BaseModel):
    ui_language: str | None = None
    provider: str | None = None  # claude | codex: when both are installed (§6.2)


class MkdirBody(BaseModel):
    parent: str = ""
    name: str = Field(min_length=1)


class RenameBody(BaseModel):
    path: str
    name: str = Field(min_length=1)


class MoveBody(BaseModel):
    path: str
    to: str


class QuizBody(BaseModel):
    folder: str = ""
    count: int = 10
    types: list[str] = Field(default_factory=lambda: list(TYPES))
    focus: str = "weak"
    lectures: list[str] | None = None  # library-relative paths (or course ids); None = every lecture under the folder


class AttemptBody(BaseModel):
    question_id: str
    answer: str = ""
    correct: bool | None = None  # None: not marked (e.g. revealed without answering)


class WeakPointPatch(BaseModel):
    status: str | None = None  # a manual status change pins the item (§15.4.1)
    concept: str | None = None
    summary: str | None = None


class MergeBody(BaseModel):
    into: str


class ReorderBody(BaseModel):
    path: str  # the lecture being dragged
    before: str | None = None  # drop it before this lecture; None: at the end


# --- serialization ------------------------------------------------------------------

def slide_aspect(c: Course) -> float:
    """Width / height of the rendered slides (from slide 1's PNG header), so thumbnails can be laid out before loading."""
    img = c.get_slide_image(1)
    if not img or img[:8] != b"\x89PNG\r\n\x1a\n":
        return 16 / 9
    w, h = int.from_bytes(img[16:20], "big"), int.from_bytes(img[20:24], "big")
    return w / h if h else 16 / 9


def course_summary(c: Course, jobs: JobManager | None = None) -> dict[str, Any]:
    def active(kind: str) -> dict | None:
        j = jobs.get(f"{c.id}:{kind}") if jobs else None
        return j.to_dict() if j and not j.finished else None

    tree_job = jobs.get(f"{c.id}:tree") if jobs else None
    has_transcript = c.get_asset(Tutor.TRANSCRIPT_ASSET) is not None
    return {
        "transcript": {"name": c.get_meta("transcript_name") or "transcript",
                       "aligned_pages": c.transcript_pages()} if has_transcript else None,
        "align_job": active("align"),
        "id": c.id, "title": c.title, "path": str(c.path), "slide_count": c.slide_count(),
        "aspect": round(slide_aspect(c), 4),
        "tree": c.get_tree(), "tree_job": tree_job.to_dict() if tree_job and not tree_job.finished else None,
        "question_counts": c.thread_counts(), "pages_with_notes": sorted(c.pages_with_notes()),
        "confused_counts": {p: v["confused"] for p, v in c.understood_counts().items() if v["confused"]},
    }


def thread_dict(t: Thread, messages: list[Message] | None = None) -> dict[str, Any]:
    d = {"id": t.id, "page": t.page, "n": t.n, "anchor_type": t.anchor_type,
         "rect": list(t.rect) if t.rect else None, "quote": t.quote,
         "minimized": t.minimized, "created_at": t.created_at, "understood": t.understood}
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
    prefs = Prefs(app_dir() / "prefs.json")
    # Which LLM: config.toml if it says, else the installed CLI, or the user's pick when both are (§6.2).
    choice = choose_provider(config, prefs.provider)
    if provider is None:
        provider = make_provider(config, choice.name)
    else:  # tests pass their own provider
        choice = Choice(provider.name, "config", choice.available)
    jobs = JobManager(config.concurrency)

    def claude_language() -> str:
        # config.toml pins it; otherwise it follows the interface language (§9.3)
        return config.language or LANGUAGE_FOR[prefs.locale]

    tutor = Tutor(provider, jobs, config.workspace, language=claude_language(), retrieval=config.qa_retrieval,
                  models={p: config.model_for(p) for p in ("outline", "explain", "qa")},
                  locale=prefs.locale)
    library = Library(config.library, app_dir() / "recent.json")
    folders = Folders(config.library)
    quizzes = QuizMaker(tutor, config.library, model=config.model_for("quiz"), verify=config.quiz_verify)
    profiler = Profiler(tutor, config.library, model=config.model_for("digest"))
    digester = Digester(tutor, config.library, model=config.model_for("digest"), profiler=profiler)
    if config.use_profile:
        tutor.weak_points = profiler.relevant

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

    auth_cache: dict[str, Any] = {}  # provider -> (checked at, logged in?)

    def logged_in(name: str) -> bool | None:
        """Is the active CLI logged in (cached 30 s)? None when unknown; API providers: whether the key is set."""
        at, value = auth_cache.get(name, (-1e9, None))
        if time.monotonic() - at < 30:
            return value
        if name == "claude":
            exe = config.claude_bin or find_claude()
            st = auth_status(exe) if exe else None
            value = None if st is None else bool(st.get("loggedIn"))
        elif name == "codex":
            exe = config.codex_bin or find_codex()
            value = codex_logged_in(exe) if exe else False
        elif name in ("anthropic_api", "openai_api"):
            value = bool(os.environ.get("ANTHROPIC_API_KEY" if name == "anthropic_api" else "OPENAI_API_KEY"))
        else:
            value = None
        auth_cache[name] = (time.monotonic(), value)
        return value

    def provider_info() -> dict[str, Any]:
        name = tutor.provider.name
        return {"provider": None if name == "none" else name, "provider_source": choice.source,
                "available": choice.available, "needs_choice": choice.needs_choice,
                "logged_in": logged_in(name) if name != "none" else False}

    @app.get("/api/health")
    def health():
        info = provider_info()
        return {**info, "model": config.model, "language": tutor.language, "version": __version__,
                # older fields the home page used to read
                "claude_found": "claude" in choice.available,
                }

    def prefs_dict() -> dict[str, Any]:
        return {"ui_language": prefs.ui_language, "language": tutor.language, "language_pinned": bool(config.language),
                **provider_info()}

    @app.get("/api/prefs")
    def get_prefs():
        return prefs_dict()

    @app.put("/api/prefs")
    async def put_prefs(body: PrefsBody):
        nonlocal choice
        if body.ui_language is not None:
            try:
                prefs.set_ui_language(body.ui_language)
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
            tutor.language, tutor.locale = claude_language(), prefs.locale
        if body.provider is not None:
            if choice.source == "config":
                raise HTTPException(409, "the provider is set in config.toml")
            choice = choose_provider(config)  # re-detect: something may have been installed since startup
            if body.provider not in choice.available:
                raise HTTPException(422, f"{body.provider} is not installed (found: {', '.join(choice.available) or 'none'})")
            prefs.set_provider(body.provider)
            choice = choose_provider(config, body.provider)
            if tutor.provider.name != body.provider:
                tutor.set_provider(make_provider(config, body.provider))
        return prefs_dict()

    @app.get("/api/recent")
    def recent():
        return library.recent()

    # --- library folders (§13.2) ---

    @app.exception_handler(FolderError)
    async def folder_error(request: Request, e: FolderError):
        return JSONResponse({"detail": str(e)}, status_code=e.status)

    def summary(c: Course) -> dict[str, Any]:
        d = course_summary(c, jobs)
        parent = c.path.resolve().parent
        d["folder"] = folders.rel(parent) if parent.is_relative_to(folders.root) else None
        latest = c.latest_digest()
        job = digester.active(c)
        d["lecture"] = {"status": c.lecture_status(),
                        "digest_at": latest.created_at if latest else None,
                        "new_since_digest": c.messages_after(latest.up_to_message_id) if latest else None,
                        "digest_job": job.to_dict() if job else None}
        return d

    def opened(c: Course) -> Course:
        if c.lecture_status() == "new":
            c.set_lecture_status("studying")
        return c

    def close_under(p: Path) -> None:
        """Close open lectures at or under `p` before moving them; refuse while Claude is working on one."""
        open_now = library.open_under(p)
        if any(jobs.active(f"{c.id}:") for c in open_now):
            raise FolderError("Claude is still working on this lecture; try again when it finishes", 409)
        for c in open_now:
            library.close(c.id)

    def write_sort_key(path: Path, key: float) -> None:
        c = library.find_open(path)
        if c is not None:
            c.set_meta("sort_key", repr(key))
            return
        with Course.open(path) as c:
            c.set_meta("sort_key", repr(key))

    @app.get("/api/library")
    def library_listing(path: str = ""):
        return folders.listing(path, library.is_open)

    @app.get("/api/library/tree")
    def library_tree():
        return folders.tree()

    @app.post("/api/library/folders")
    def make_folder(body: MkdirBody):
        return {"path": folders.mkdir(body.parent, body.name)}

    @app.delete("/api/library/folders")
    def remove_folder(path: str):
        folders.rmdir(path)
        return {"ok": True}

    @app.post("/api/library/rename")
    def rename_entry(body: RenameBody):
        close_under(folders.resolve(body.path))
        src, dst = folders.rename(body.path, body.name)
        library.moved(src, dst)
        return {"path": folders.rel(dst)}

    @app.post("/api/library/move")
    def move_entry(body: MoveBody):
        close_under(folders.resolve(body.path))
        dst_dir = folders.folder(body.to)
        last = [info["sort_key"] for _, info in folders.lectures(dst_dir)]
        src, dst = folders.move(body.path, body.to)
        library.moved(src, dst)
        if dst.is_file() and src != dst:  # moved in the app: goes to the end of the target course
            write_sort_key(dst, (max(last) if last else 0) + 1)
        return {"path": folders.rel(dst)}

    # --- pre-quiz (§13.5) ---

    @app.post("/api/library/quizzes")
    async def start_quiz(body: QuizBody):
        try:
            job = quizzes.start(body.folder, body.count, tuple(body.types), body.focus, body.lectures)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        return {"job": job.to_dict()}

    @app.get("/api/library/quizzes")
    async def list_quizzes(folder: str = ""):
        job = quizzes.active(folder)
        return {"quizzes": QuizStore(folders.folder(folder)).list(), "job": job.to_dict() if job else None}

    @app.get("/api/library/quizzes/{qid}")
    def get_quiz(qid: str, folder: str = ""):
        d = folders.folder(folder)
        q = QuizStore(d).get(qid)
        if q is None:
            raise HTTPException(404, "no such quiz")
        files = {info["course_id"]: str(p) for p, info in folders.all_lectures(d) if info["course_id"]}
        for question in q["questions"]:
            for src in question["sources"]:
                src["file"] = files.get(src["lecture"])  # None: the lecture was moved out or deleted
        return q

    @app.post("/api/library/quizzes/{qid}/attempts")
    def add_attempt(qid: str, body: AttemptBody, folder: str = ""):
        store = QuizStore(folders.folder(folder))
        q = store.get(qid)
        if q is None or body.question_id not in {x["id"] for x in q["questions"]}:
            raise HTTPException(404, "no such question")
        attempt = store.add_attempt(body.question_id, body.answer, body.correct)
        # update B (§15.4.2): each source lecture's own course folder holds its profile
        question = next(x for x in q["questions"] if x["id"] == body.question_id)
        where = {info["course_id"]: p.parent for p, info in folders.all_lectures(folders.folder(folder)) if info["course_id"]}
        by_folder: dict[Path, list] = {}
        for s in question.get("sources", []):
            if s.get("lecture") in where:
                by_folder.setdefault(where[s["lecture"]], []).append(s)
        for d, srcs in by_folder.items():
            on_quiz_answer(d, {"correct": body.correct, "question_id": body.question_id, "topic": question.get("topic", ""),
                               "stem": question.get("stem", ""), "sources": srcs})
        return attempt

    @app.delete("/api/library/quizzes/{qid}")
    def delete_quiz(qid: str, folder: str = ""):
        QuizStore(folders.folder(folder)).delete(qid)
        return {"ok": True}

    # --- study profile (§15.4) ---

    def profile_dict(folder: str) -> dict[str, Any]:
        d = folders.folder(folder)
        store = ProfileStore(d)
        lectures = {}
        for i, (p, info) in enumerate(folders.lectures(d), 1):
            if info["course_id"]:
                lectures[info["course_id"]] = {"file": str(p), "title": info["title"], "number": i}
        job = profiler.active(folder)
        return {"weak_points": store.weak_points(), "pending": store.pending(), "revisions": store.revisions(),
                "lectures": lectures, "job": job.to_dict() if job else None}

    @app.get("/api/library/profile")
    async def get_profile(folder: str = ""):
        return profile_dict(folder)

    @app.post("/api/library/profile/organize")
    async def organize_profile(folder: str = ""):
        return {"job": profiler.organize(folder).to_dict()}

    @app.patch("/api/library/profile/{wid}")
    def patch_weak_point(wid: str, body: WeakPointPatch, folder: str = ""):
        store = ProfileStore(folders.folder(folder))
        with store.change("manual") as ed:
            if ed.get(wid) is None:
                raise HTTPException(404, "no such weak point")
            if body.status is not None:
                if body.status not in WP_STATUSES:
                    raise HTTPException(422, f"status must be one of {WP_STATUSES}")
                ed.set_status(wid, body.status, "set by the student", pin=True)
            if body.concept is not None or body.summary is not None:
                ed.edit(wid, (body.concept or "").strip() or None, body.summary)
        return profile_dict(folder)

    @app.post("/api/library/profile/{wid}/merge")
    def merge_weak_point(wid: str, body: MergeBody, folder: str = ""):
        store = ProfileStore(folders.folder(folder))
        with store.change("manual") as ed:
            if ed.get(wid) is None or ed.get(body.into) is None or wid == body.into:
                raise HTTPException(422, "bad ids")
            ed.merge(wid, body.into, "merged by the student")
        return profile_dict(folder)

    @app.delete("/api/library/profile/{wid}")
    def delete_weak_point(wid: str, folder: str = ""):
        store = ProfileStore(folders.folder(folder))
        with store.change("manual") as ed:
            if ed.get(wid) is None:
                raise HTTPException(404, "no such weak point")
            ed.delete(wid)
        return profile_dict(folder)

    @app.post("/api/library/profile/undo")
    def undo_profile(folder: str = ""):
        try:
            ProfileStore(folders.folder(folder)).undo()
        except ProfileError as e:
            raise HTTPException(409, str(e)) from e
        return profile_dict(folder)

    @app.post("/api/library/reorder")
    def reorder(body: ReorderBody):
        p = folders.resolve(body.path)
        if not p.is_file():
            raise FolderError("not a lecture", 404)
        order = [f for f, _ in folders.lectures(p.parent) if f.resolve() != p.resolve()]
        keys = {f: info["sort_key"] for f, info in folders.lectures(p.parent)}
        if body.before is None:
            i = len(order)
        else:
            b = folders.resolve(body.before)
            i = next((k for k, f in enumerate(order) if f.resolve() == b), None)
            if i is None:
                raise FolderError("`before` is not a lecture in the same course")
        prev = keys[order[i - 1]] if i > 0 else None
        nxt = keys[order[i]] if i < len(order) else None
        if prev is not None and nxt is not None and nxt - prev < EPSILON:
            # precision used up: renumber the whole course 1, 2, 3… in the new order
            for k, f in enumerate([*order[:i], p, *order[i:]], 1):
                write_sort_key(f, float(k))
        else:
            write_sort_key(p, key_between(prev, nxt))
        return folders.listing(folders.rel(p.parent), library.is_open)

    # --- courses ---

    @app.post("/api/courses/import")
    async def import_pdf(file: UploadFile = File(...), title: str | None = Form(None), folder: str = Form("")):
        stem = Path(file.filename or "course.pdf").stem
        out = library.new_path(stem, folders.folder(folder))
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / f"{stem}.pdf"  # create_course falls back to the file name for the title
            pdf.write_bytes(await file.read())
            try:
                course = create_course(pdf, out, title=title or None)
            except IngestError as e:
                raise HTTPException(422, str(e)) from e
        return summary(opened(library.add(course)))

    @app.post("/api/courses/open")
    def open_course(body: OpenBody):
        try:
            return summary(opened(library.open(Path(body.path))))
        except FileNotFoundError as e:
            raise HTTPException(404, "file not found") from e
        except (schema.NotAGlossFile, schema.FileTooNew) as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/api/courses/{cid}")
    async def get_course(cid: str):
        return summary(library.get(cid))

    @app.get("/api/courses/{cid}/export.md")
    def export(cid: str):
        c = library.get(cid)
        name = f"{c.path.stem}.md"
        return Response(export_markdown(c, prefs.locale), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    @app.post("/api/courses/{cid}/transcript")
    async def upload_transcript(cid: str, file: UploadFile = File(...)):
        c = library.get(cid)
        data = await file.read()
        if len(data) > TRANSCRIPT_MAX_BYTES:
            raise HTTPException(413, "transcript too large (max 5 MB)")
        if not segment(decode(data)):
            raise HTTPException(422, "transcript is empty")
        c.put_asset(Tutor.TRANSCRIPT_ASSET, "text/plain", data)
        c.set_meta("transcript_name", file.filename or "transcript")
        c.set_transcript_spans({})
        return {"job": tutor.align_transcript(c).to_dict()}

    @app.post("/api/courses/{cid}/transcript/align")
    async def realign_transcript(cid: str):
        c = library.get(cid)
        try:
            return {"job": tutor.align_transcript(c).to_dict()}
        except KeyError as e:
            raise HTTPException(404, "no transcript uploaded") from e

    @app.delete("/api/courses/{cid}/transcript")
    async def delete_transcript(cid: str):
        c = library.get(cid)
        if jobs.get(f"{cid}:align") and not jobs.get(f"{cid}:align").finished:
            raise HTTPException(409, "alignment in progress")
        c.delete_asset(Tutor.TRANSCRIPT_ASSET)
        c.set_transcript_spans({})
        return {"ok": True}

    @app.post("/api/courses/{cid}/tree")
    async def build_tree(cid: str, regenerate: bool = False):
        c = library.get(cid)
        job = tutor.build_tree(c, regenerate=regenerate)
        return {"tree": c.get_tree()} if job is None else {"job": job.to_dict()}

    # --- lecture summary (§15.3) ---

    @app.post("/api/courses/{cid}/digest")
    async def make_digest(cid: str):
        return {"job": digester.start(library.get(cid)).to_dict()}

    @app.get("/api/courses/{cid}/digest")
    def get_digest(cid: str):
        c = library.get(cid)
        d = c.latest_digest()
        if d is None:
            raise HTTPException(404, "no summary yet")
        return digest_dict(c, d)

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
            "page": page, "text": s.text, "text_blocks": s.text_blocks, "transcript_span": s.transcript_span,
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
            t, m, job = tutor.ask(c, page, body.rect, body.question, quote=body.quote)
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
        sent = body.model_fields_set
        if "minimized" in sent and body.minimized is not None:
            c.set_thread_minimized(tid, body.minimized)
        if "understood" in sent:
            c.set_thread_understood(tid, body.understood)
            folder = profiler.folder_of(c)
            if folder is not None and (folder / "study.glossp").exists():  # update B (§15.4.2)
                on_mark(folder, c.id, c.get_thread(tid).page, tid, body.understood)
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
