"""Read/write layer for one course file (`.gloss`, SQLite). See DESIGN.md §4.

Everything is written immediately (autosave). One `Course` may be shared across
threads; calls are serialized by a lock.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import schema


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Slide:
    page: int
    text: str
    image: bytes
    text_blocks: list[dict[str, Any]]  # [{x, y, w, h, text}], 0–1 relative
    transcript_span: str | None = None


@dataclass
class Explanation:
    id: int
    page: int
    content: str
    model: str | None
    created_at: str


ANCHORS = ("slide_region", "explanation_text")
QUOTE_MAX = 2000


@dataclass
class Thread:
    id: str
    page: int
    n: int
    anchor_type: str  # 'slide_region' (x/y/w/h + crop) | 'explanation_text' (quote)
    x: float | None
    y: float | None
    w: float | None
    h: float | None
    crop: bytes | None
    minimized: bool
    created_at: str
    quote: str | None = None

    @property
    def rect(self) -> tuple[float, float, float, float] | None:
        return None if self.x is None else (self.x, self.y, self.w, self.h)


@dataclass
class Message:
    id: int
    thread_id: str
    role: str  # 'user' | 'assistant'
    content: str
    created_at: str


class Course:
    def __init__(self, conn: sqlite3.Connection, path: Path):
        self._conn = conn
        self._lock = threading.RLock()
        self.path = path

    # --- lifecycle ---------------------------------------------------------

    @classmethod
    def create(cls, path: str | Path, title: str) -> Course:
        path = Path(path)
        if path.exists():
            raise FileExistsError(path)
        course = cls._connect(path, new=True)
        with course._tx():
            course._set_meta("title", title)
            course._set_meta("created_at", now())
        return course

    @classmethod
    def open(cls, path: str | Path) -> Course:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)
        return cls._connect(path, new=False)

    @classmethod
    def _connect(cls, path: Path, new: bool) -> Course:
        # isolation_level=None: we issue BEGIN/COMMIT ourselves.
        conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        try:
            if not new and schema.get_version(conn) == 0:
                raise schema.NotAGlossFile("empty database")
            conn.execute("PRAGMA journal_mode = WAL")
            schema.migrate(conn)
        except sqlite3.DatabaseError as e:
            conn.close()
            raise schema.NotAGlossFile(str(e)) from e
        except BaseException:
            conn.close()
            raise
        return cls(conn, path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Course:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def _one(self, sql: str, params: tuple = ()) -> tuple | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # --- meta --------------------------------------------------------------

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def get_meta(self, key: str) -> str | None:
        row = self._one("SELECT value FROM meta WHERE key = ?", (key,))
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        if key == "schema_version":
            raise ValueError("schema_version is managed by migrations")
        with self._tx():
            self._set_meta(key, value)

    @property
    def title(self) -> str:
        return self.get_meta("title") or ""

    @property
    def id(self) -> str:
        """Stable id for this course (scratch dir, job keys). Created on first use."""
        with self._lock:
            cid = self.get_meta("course_id")
            if cid is None:
                cid = uuid.uuid4().hex
                with self._tx():
                    self._set_meta("course_id", cid)
            return cid

    # --- assets ------------------------------------------------------------

    def put_asset(self, name: str, mime: str, data: bytes) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO assets (name, mime, data) VALUES (?, ?, ?) "
                "ON CONFLICT (name) DO UPDATE SET mime = excluded.mime, data = excluded.data",
                (name, mime, data),
            )

    def get_asset(self, name: str) -> tuple[str, bytes] | None:
        row = self._one("SELECT mime, data FROM assets WHERE name = ?", (name,))
        return (row[0], row[1]) if row else None

    def delete_asset(self, name: str) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM assets WHERE name = ?", (name,))

    def list_assets(self) -> list[tuple[str, str]]:
        return [(r[0], r[1]) for r in self._all("SELECT name, mime FROM assets ORDER BY name")]

    # --- slides ------------------------------------------------------------

    def replace_slides(self, slides: list[Slide]) -> None:
        """Replace all slides at once (ingest). Pages must be 1..N."""
        if [s.page for s in slides] != list(range(1, len(slides) + 1)):
            raise ValueError("slides must be numbered 1..N in order")
        with self._tx() as c:
            c.execute("DELETE FROM slides")
            c.executemany(
                "INSERT INTO slides (page, text, image, text_blocks, transcript_span) VALUES (?, ?, ?, ?, ?)",
                [(s.page, s.text, s.image, json.dumps(s.text_blocks), s.transcript_span) for s in slides],
            )

    def slide_count(self) -> int:
        return self._one("SELECT COUNT(*) FROM slides")[0]

    def get_slide(self, page: int) -> Slide | None:
        row = self._one(
            "SELECT page, text, image, text_blocks, transcript_span FROM slides WHERE page = ?", (page,)
        )
        return _slide(row) if row else None

    def slide_texts(self) -> list[tuple[int, str]]:
        """(page, text) for every slide, without images. For tree building."""
        return [(r[0], r[1]) for r in self._all("SELECT page, text FROM slides ORDER BY page")]

    def get_slide_image(self, page: int) -> bytes | None:
        row = self._one("SELECT image FROM slides WHERE page = ?", (page,))
        return row[0] if row else None

    def set_transcript_span(self, page: int, span: str | None) -> None:
        with self._tx() as c:
            self._require_page(c, page)
            c.execute("UPDATE slides SET transcript_span = ? WHERE page = ?", (span, page))

    def set_transcript_spans(self, spans: dict[int, str]) -> None:
        """Replace every slide's transcript span at once (pages not in `spans` get none)."""
        with self._tx() as c:
            c.execute("UPDATE slides SET transcript_span = NULL")
            c.executemany("UPDATE slides SET transcript_span = ? WHERE page = ?",
                          [(text, page) for page, text in spans.items()])

    def transcript_pages(self) -> list[int]:
        return [r[0] for r in self._all("SELECT page FROM slides WHERE transcript_span IS NOT NULL ORDER BY page")]

    def _require_page(self, c: sqlite3.Connection, page: int) -> None:
        if c.execute("SELECT 1 FROM slides WHERE page = ?", (page,)).fetchone() is None:
            raise KeyError(f"no slide {page}")

    # --- tree --------------------------------------------------------------

    def get_tree(self) -> dict | None:
        row = self._one("SELECT json FROM tree WHERE id = 1")
        return json.loads(row[0]) if row else None

    def set_tree(self, tree: dict) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO tree (id, json) VALUES (1, ?) ON CONFLICT (id) DO UPDATE SET json = excluded.json",
                (json.dumps(tree, ensure_ascii=False),),
            )

    # --- explanations ------------------------------------------------------

    def add_explanation(self, page: int, content: str, model: str | None = None) -> Explanation:
        with self._tx() as c:
            self._require_page(c, page)
            ts = now()
            cur = c.execute(
                "INSERT INTO explanations (page, content, model, created_at) VALUES (?, ?, ?, ?)",
                (page, content, model, ts),
            )
            return Explanation(cur.lastrowid, page, content, model, ts)

    def latest_explanation(self, page: int) -> Explanation | None:
        row = self._one(
            "SELECT id, page, content, model, created_at FROM explanations WHERE page = ? ORDER BY id DESC LIMIT 1",
            (page,),
        )
        return Explanation(*row) if row else None

    # --- slide sessions (Claude session cache, §7) -------------------------

    def get_session(self, page: int) -> str | None:
        row = self._one("SELECT claude_session_id FROM slide_sessions WHERE page = ?", (page,))
        return row[0] if row else None

    def set_session(self, page: int, session_id: str | None) -> None:
        with self._tx() as c:
            if session_id is None:
                c.execute("DELETE FROM slide_sessions WHERE page = ?", (page,))
            else:
                c.execute(
                    "INSERT INTO slide_sessions (page, claude_session_id, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT (page) DO UPDATE SET claude_session_id = excluded.claude_session_id, "
                    "updated_at = excluded.updated_at",
                    (page, session_id, now()),
                )

    # --- threads & messages ------------------------------------------------

    def create_thread(
        self,
        page: int,
        rect: tuple[float, float, float, float] | None,
        crop: bytes | None = None,
        anchor_type: str = "slide_region",
        quote: str | None = None,
    ) -> Thread:
        """A question on a slide region (`rect`, 0–1) or on a passage of the explanation (`quote`)."""
        if anchor_type not in ANCHORS:
            raise ValueError(f"unknown anchor_type {anchor_type!r}")
        if anchor_type == "slide_region":
            if rect is None or quote is not None:
                raise ValueError("slide_region threads need a rect and no quote")
            x, y, w, h = rect
            if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w and 0 < h and x + w <= 1 + 1e-9 and y + h <= 1 + 1e-9):
                raise ValueError(f"rect must be 0–1 relative and inside the slide: {rect}")
        else:
            quote = (quote or "").strip()
            if rect is not None or crop is not None or not quote:
                raise ValueError("explanation_text threads need a quote and no rect / crop")
            if len(quote) > QUOTE_MAX:
                raise ValueError(f"quote longer than {QUOTE_MAX} characters")
            x = y = w = h = None
        with self._tx() as c:
            self._require_page(c, page)
            n = c.execute("SELECT COALESCE(MAX(n), 0) + 1 FROM threads WHERE page = ?", (page,)).fetchone()[0]
            t = Thread(uuid.uuid4().hex, page, n, anchor_type, x, y, w, h, crop, False, now(), quote)
            c.execute(
                "INSERT INTO threads (id, page, n, anchor_type, x, y, w, h, crop, minimized, created_at, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (t.id, t.page, t.n, t.anchor_type, x, y, w, h, crop, 0, t.created_at, quote),
            )
            return t

    _THREAD_COLS = "id, page, n, anchor_type, x, y, w, h, crop, minimized, created_at, quote"

    def get_thread(self, thread_id: str) -> Thread | None:
        row = self._one(f"SELECT {self._THREAD_COLS} FROM threads WHERE id = ?", (thread_id,))
        return _thread(row) if row else None

    def list_threads(self, page: int) -> list[Thread]:
        return [_thread(r) for r in self._all(f"SELECT {self._THREAD_COLS} FROM threads WHERE page = ? ORDER BY n", (page,))]

    def thread_counts(self) -> dict[int, int]:
        """page -> number of questions, for the mind-map badges."""
        return dict(self._all("SELECT page, COUNT(*) FROM threads GROUP BY page"))

    def set_thread_minimized(self, thread_id: str, minimized: bool) -> None:
        with self._tx() as c:
            if c.execute("UPDATE threads SET minimized = ? WHERE id = ?", (int(minimized), thread_id)).rowcount == 0:
                raise KeyError(thread_id)

    def delete_thread(self, thread_id: str) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
            c.execute("DELETE FROM threads WHERE id = ?", (thread_id,))

    def add_message(self, thread_id: str, role: str, content: str) -> Message:
        if role not in ("user", "assistant"):
            raise ValueError(role)
        with self._tx() as c:
            if c.execute("SELECT 1 FROM threads WHERE id = ?", (thread_id,)).fetchone() is None:
                raise KeyError(thread_id)
            ts = now()
            cur = c.execute(
                "INSERT INTO messages (thread_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (thread_id, role, content, ts),
            )
            return Message(cur.lastrowid, thread_id, role, content, ts)

    def list_messages(self, thread_id: str) -> list[Message]:
        return [
            Message(*r)
            for r in self._all(
                "SELECT id, thread_id, role, content, created_at FROM messages WHERE thread_id = ? ORDER BY id",
                (thread_id,),
            )
        ]

    def page_history(self, page: int) -> list[tuple[Thread, Message]]:
        """Every message on a page in global order, with its thread. Used to rebuild a session (§7.4)."""
        threads = {t.id: t for t in self.list_threads(page)}
        rows = self._all(
            "SELECT m.id, m.thread_id, m.role, m.content, m.created_at FROM messages m "
            "JOIN threads t ON t.id = m.thread_id WHERE t.page = ? ORDER BY m.id",
            (page,),
        )
        return [(threads[r[1]], Message(*r)) for r in rows]

    # --- notes -------------------------------------------------------------

    def get_note(self, page: int) -> str:
        row = self._one("SELECT content FROM notes WHERE page = ?", (page,))
        return row[0] if row else ""

    def set_note(self, page: int, content: str) -> None:
        with self._tx() as c:
            self._require_page(c, page)
            c.execute(
                "INSERT INTO notes (page, content, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT (page) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at",
                (page, content, now()),
            )

    def pages_with_notes(self) -> set[int]:
        return {r[0] for r in self._all("SELECT page FROM notes WHERE content != ''")}

    # --- ui state ----------------------------------------------------------

    def get_ui(self, key: str, default: Any = None) -> Any:
        row = self._one("SELECT value FROM ui_state WHERE key = ?", (key,))
        return json.loads(row[0]) if row else default

    def set_ui(self, key: str, value: Any) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO ui_state (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )


def _slide(row: tuple) -> Slide:
    return Slide(row[0], row[1], row[2], json.loads(row[3]) if row[3] else [], row[4])


def _thread(row: tuple) -> Thread:
    return Thread(*row[:9], bool(row[9]), row[10], row[11])
