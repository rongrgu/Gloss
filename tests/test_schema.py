import sqlite3

import pytest

from gloss import schema
from gloss.store import Course


def test_new_file_is_latest_version(tmp_path):
    with Course.create(tmp_path / "a.gloss", "A") as c:
        assert c.get_meta("schema_version") == str(schema.SCHEMA_VERSION)
        assert c.title == "A"
        assert c.get_meta("created_at")


def test_wal_mode(tmp_path):
    with Course.create(tmp_path / "a.gloss", "A") as c:
        assert c._one("PRAGMA journal_mode")[0] == "wal"


def test_reopen_keeps_data(tmp_path):
    p = tmp_path / "a.gloss"
    with Course.create(p, "A") as c:
        c.set_ui("page", 3)
    with Course.open(p) as c:
        assert c.get_ui("page") == 3


def test_create_refuses_existing(tmp_path):
    p = tmp_path / "a.gloss"
    Course.create(p, "A").close()
    with pytest.raises(FileExistsError):
        Course.create(p, "B")


def test_open_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        Course.open(tmp_path / "nope.gloss")


def test_open_non_sqlite(tmp_path):
    p = tmp_path / "x.gloss"
    p.write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(schema.NotAGlossFile):
        Course.open(p)


def test_open_other_sqlite_db(tmp_path):
    p = tmp_path / "x.gloss"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE foo (a)")
    conn.commit()
    conn.close()
    with pytest.raises(schema.NotAGlossFile):
        Course.open(p)


def test_open_empty_db_is_not_initialized(tmp_path):
    p = tmp_path / "x.gloss"
    sqlite3.connect(p).close()
    with pytest.raises(schema.NotAGlossFile):
        Course.open(p)


def test_file_too_new(tmp_path):
    p = tmp_path / "a.gloss"
    Course.create(p, "A").close()
    conn = sqlite3.connect(p)
    conn.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(schema.FileTooNew):
        Course.open(p)


def _conn(p):
    return sqlite3.connect(p, isolation_level=None)


def test_migrate_runs_pending_steps_in_order(tmp_path):
    p = tmp_path / "a.gloss"
    Course.create(p, "A").close()  # v1
    ran = []

    def v1_to_v2(conn):
        ran.append(2)
        conn.execute("ALTER TABLE notes ADD COLUMN format TEXT DEFAULT 'plain'")

    def v2_to_v3(conn):
        ran.append(3)
        conn.execute("UPDATE notes SET format = 'markdown'")

    conn = _conn(p)
    assert schema.migrate(conn, [*schema.MIGRATIONS, v1_to_v2, v2_to_v3]) == (1, 3)
    assert ran == [2, 3]
    assert schema.get_version(conn) == 3
    assert "format" in [r[1] for r in conn.execute("PRAGMA table_info(notes)")]
    # Already current: nothing runs.
    assert schema.migrate(conn, [*schema.MIGRATIONS, v1_to_v2, v2_to_v3]) == (3, 3)
    assert ran == [2, 3]


def test_failed_migration_rolls_back_to_last_good_version(tmp_path):
    p = tmp_path / "a.gloss"
    Course.create(p, "A").close()

    def good(conn):
        conn.execute("CREATE TABLE extra (a)")

    def bad(conn):
        conn.execute("CREATE TABLE half_done (a)")
        raise RuntimeError("boom")

    conn = _conn(p)
    with pytest.raises(RuntimeError):
        schema.migrate(conn, [*schema.MIGRATIONS, good, bad])
    assert schema.get_version(conn) == 2
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "extra" in tables and "half_done" not in tables


def test_closed_file_is_self_contained(tmp_path):
    """After close, the WAL is checkpointed: the .gloss alone can be copied to someone else."""
    p = tmp_path / "a.gloss"
    with Course.create(p, "A") as c:
        c.set_ui("x", 1)
        assert p.with_name("a.gloss-wal").exists()
    assert not p.with_name("a.gloss-wal").exists()
    assert not p.with_name("a.gloss-shm").exists()
