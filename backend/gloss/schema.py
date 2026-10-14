"""`.gloss` schema and migrations (DESIGN.md §4).

A file's version lives in `meta.schema_version`. `MIGRATIONS[i]` upgrades a file
from version i to i + 1, so version 0 is an empty database and
`SCHEMA_VERSION == len(MIGRATIONS)`. To change the schema, append a migration;
never edit one that has shipped.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

_V1 = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE assets (name TEXT PRIMARY KEY, mime TEXT, data BLOB);
CREATE TABLE slides (
  page INTEGER PRIMARY KEY, text TEXT, image BLOB,
  text_blocks TEXT,
  transcript_span TEXT
);
CREATE TABLE tree (id INTEGER PRIMARY KEY, json TEXT);
CREATE TABLE explanations (
  id INTEGER PRIMARY KEY, page INTEGER, content TEXT, model TEXT, created_at TEXT
);
CREATE INDEX explanations_page ON explanations (page);
CREATE TABLE slide_sessions (page INTEGER PRIMARY KEY, claude_session_id TEXT, updated_at TEXT);
CREATE TABLE threads (
  id TEXT PRIMARY KEY, page INTEGER, n INTEGER,
  anchor_type TEXT,
  x REAL, y REAL, w REAL, h REAL,
  crop BLOB, minimized INTEGER, created_at TEXT,
  UNIQUE (page, n)
);
CREATE TABLE messages (
  id INTEGER PRIMARY KEY,
  thread_id TEXT, role TEXT, content TEXT, created_at TEXT
);
CREATE INDEX messages_thread ON messages (thread_id);
CREATE TABLE notes (page INTEGER PRIMARY KEY, content TEXT, updated_at TEXT);
CREATE TABLE ui_state (key TEXT PRIMARY KEY, value TEXT);
"""


def _v0_to_v1(conn: sqlite3.Connection) -> None:
    for stmt in _V1.split(";"):
        if stmt.strip():
            conn.execute(stmt)


MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [_v0_to_v1]
SCHEMA_VERSION = len(MIGRATIONS)


class NotAGlossFile(Exception):
    pass


class FileTooNew(Exception):
    """The file was written by a newer Gloss than this one."""


def get_version(conn: sqlite3.Connection) -> int:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not tables:
        return 0
    if "meta" not in tables:
        raise NotAGlossFile("database has no meta table")
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        raise NotAGlossFile("meta.schema_version missing")
    return int(row[0])


def migrate(conn: sqlite3.Connection, migrations: list | None = None) -> tuple[int, int]:
    """Bring the database up to the latest version. Returns (old, new) versions.

    Each step runs in its own transaction together with the version bump, so a
    failure leaves the file at the last good version.
    """
    migrations = MIGRATIONS if migrations is None else migrations
    target = len(migrations)
    old = get_version(conn)
    if old > target:
        raise FileTooNew(f"file schema v{old}, this Gloss supports up to v{target}")
    for v in range(old, target):
        try:
            conn.execute("BEGIN")
            migrations[v](conn)
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (str(v + 1),),
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return old, target
