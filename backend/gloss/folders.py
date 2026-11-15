"""The library as a folder tree (DESIGN.md §13.2).

A lecture is one `.gloss`; a course is a folder that directly holds lectures; a group is
a folder that only holds folders. Any depth, any names: the file system is the data model.
Paths in the API are relative to the library root with `/` separators ('' is the root).
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from . import schema

LECTURE_SUFFIX = ".gloss"
QUIZ_FILE = "quizzes.glossq"
BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
IGNORED = {".DS_Store", "Thumbs.db", "desktop.ini"}


class FolderError(Exception):
    """Bad path or operation; `status` is the HTTP status to report."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def natural_key(name: str) -> tuple:
    """`Lecture 2` before `Lecture 10`."""
    return tuple((0, int(t), "") if t.isdigit() else (1, 0, t.casefold()) for t in re.split(r"(\d+)", name) if t)


def clean_name(name: str) -> str:
    name = BAD_NAME.sub("_", name).strip(" .")
    if not name:
        raise FolderError("empty name")
    return name


def peek(path: Path) -> dict[str, Any]:
    """Lecture info for listings, read-only (no migrate, no writes; safe while the app has it open)."""
    info: dict[str, Any] = {"title": path.stem, "slide_count": 0, "questions": 0, "course_id": None,
                            "sort_key": None, "error": None}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        return {**info, "error": str(e)}
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        if int(meta.get("schema_version") or 0) > schema.SCHEMA_VERSION:
            info["error"] = "made by a newer Gloss"
        info["title"] = meta.get("title") or path.stem
        info["course_id"] = meta.get("course_id")
        info["slide_count"] = conn.execute("SELECT COUNT(*) FROM slides").fetchone()[0]
        info["questions"] = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        info["sort_key"] = sort_key(meta, path)
    except (sqlite3.Error, ValueError) as e:
        info["error"] = "not a Gloss file" if isinstance(e, sqlite3.DatabaseError) else str(e)
        info["sort_key"] = path.stat().st_mtime
    finally:
        conn.close()
    return info


def sort_key(meta: dict[str, str], path: Path) -> float:
    """Lecture order inside a course: `meta.sort_key` if set (manual order), else import time."""
    if meta.get("sort_key"):
        return float(meta["sort_key"])
    try:
        return datetime.fromisoformat(meta["created_at"]).timestamp()
    except (KeyError, ValueError):
        return path.stat().st_mtime


class Folders:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()

    # --- paths ---

    def resolve(self, rel: str) -> Path:
        parts = [p for p in (rel or "").replace("\\", "/").split("/") if p]
        if any(p in (".", "..") or p.startswith(".") for p in parts):
            raise FolderError("bad path")
        p = (self.root.joinpath(*parts)).resolve()
        if not p.is_relative_to(self.root):
            raise FolderError("path outside the library")
        return p

    def rel(self, p: Path) -> str:
        return p.resolve().relative_to(self.root).as_posix() if p.resolve() != self.root else ""

    def folder(self, rel: str) -> Path:
        if not rel:
            self.root.mkdir(parents=True, exist_ok=True)
        p = self.resolve(rel)
        if not p.is_dir():
            raise FolderError("no such folder", 404)
        return p

    # --- reading ---

    def _entries(self, d: Path) -> tuple[list[Path], list[Path]]:
        folders, lectures = [], []
        for e in d.iterdir():
            if e.name.startswith(".") or e.name in IGNORED:
                continue
            if e.is_dir():
                folders.append(e)
            elif e.suffix == LECTURE_SUFFIX:
                lectures.append(e)
        return sorted(folders, key=lambda p: natural_key(p.name)), lectures

    def kind(self, d: Path) -> str:
        folders, lectures = self._entries(d)
        return "course" if lectures else "group" if folders else "empty"

    def lectures(self, d: Path) -> list[tuple[Path, dict[str, Any]]]:
        """Lectures directly in `d`, in course order."""
        _, files = self._entries(d)
        items = [(f, peek(f)) for f in files]
        return sorted(items, key=lambda it: (it[1]["sort_key"], natural_key(it[0].name)))

    def all_lectures(self, d: Path) -> list[tuple[Path, dict[str, Any]]]:
        """Every lecture under `d`, depth first: this folder's lectures in order, then each subfolder's."""
        out = self.lectures(d)
        for sub in self._entries(d)[0]:
            out += self.all_lectures(sub)
        return out

    def listing(self, rel: str, is_open: Callable[[Path], bool] = lambda p: False) -> dict[str, Any]:
        d = self.folder(rel)
        folders, _ = self._entries(d)
        lectures = self.lectures(d)
        crumbs = [{"name": "", "path": ""}]
        for i, part in enumerate(Path(self.rel(d)).parts):
            crumbs.append({"name": part, "path": "/".join(Path(self.rel(d)).parts[: i + 1])})
        sub = []
        for f in folders:
            f_folders, f_lectures = self._entries(f)
            sub.append({"path": self.rel(f), "name": f.name, "kind": self.kind(f),
                        "lectures": len(f_lectures), "folders": len(f_folders)})
        return {
            "path": self.rel(d), "name": d.name if d != self.root else "", "root": str(self.root),
            "crumbs": crumbs, "kind": "course" if lectures else "group" if folders else "empty",
            "mixed": bool(lectures and folders),
            "folders": sub,
            "lectures": [{"path": self.rel(f), "file": str(f), "open": is_open(f), **info} for f, info in lectures],
            "has_quizzes": (d / QUIZ_FILE).exists(),
        }

    def tree(self) -> list[dict[str, Any]]:
        """Every folder (for "move to…" pickers)."""
        self.root.mkdir(parents=True, exist_ok=True)
        out = [{"path": "", "depth": 0, "kind": self.kind(self.root)}]

        def walk(d: Path, depth: int) -> None:
            for f in self._entries(d)[0]:
                out.append({"path": self.rel(f), "depth": depth, "kind": self.kind(f)})
                walk(f, depth + 1)
        walk(self.root, 1)
        return out

    # --- writing ---

    def mkdir(self, parent: str, name: str) -> str:
        p = self.folder(parent) / clean_name(name)
        if p.exists():
            raise FolderError("a file or folder with that name already exists", 409)
        p.mkdir()
        return self.rel(p)

    def rename(self, rel: str, name: str) -> tuple[Path, Path]:
        src = self._existing(rel)
        name = clean_name(name)
        if src.is_file() and not name.endswith(LECTURE_SUFFIX):
            name += LECTURE_SUFFIX
        dst = src.with_name(name)
        if dst == src:
            return src, dst
        if dst.exists() and dst.resolve() != src.resolve():  # allow case-only renames on case-insensitive disks
            raise FolderError("a file or folder with that name already exists", 409)
        src.rename(dst)
        return src, dst

    def move(self, rel: str, to: str) -> tuple[Path, Path]:
        src = self._existing(rel)
        dst_dir = self.folder(to)
        if src.is_dir() and (dst_dir == src or dst_dir.is_relative_to(src)):
            raise FolderError("cannot move a folder into itself")
        dst = dst_dir / src.name
        if dst == src:
            return src, dst
        if dst.exists():
            raise FolderError(f"{to or 'the library'} already has a {src.name}", 409)
        shutil.move(str(src), str(dst))
        return src, dst

    def rmdir(self, rel: str) -> None:
        d = self._existing(rel)
        if not d.is_dir() or d == self.root:
            raise FolderError("not a folder")
        if any(e.name not in IGNORED for e in d.iterdir()):
            raise FolderError("folder is not empty", 409)
        for e in d.iterdir():
            e.unlink()
        d.rmdir()

    def _existing(self, rel: str) -> Path:
        p = self.resolve(rel)
        if p == self.root:
            raise FolderError("cannot change the library folder itself")
        if not p.exists():
            raise FolderError("not found", 404)
        return p


def key_between(before: float | None, after: float | None) -> float:
    """A sort key for a lecture dropped between two neighbours (None: list edge)."""
    if before is None and after is None:
        return 0.0
    if before is None:
        return after - 1
    if after is None:
        return before + 1
    return (before + after) / 2


EPSILON = 1e-6
