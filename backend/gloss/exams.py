"""Past quizzes / exams kept in a library folder (DESIGN.md §13.8). No LLM calls.

Each one is a hidden directory `<folder>/.exams/<id>/` with the original file (`source.pdf` or
`source.docx`), `meta.json`, `text.json` (one string per page) and, for PDFs, `page-<n>.png`.
Word files have no page images: their text is split at explicit page breaks. Hidden directories are not part of the
folder tree (folders.py skips names starting with "."), and they move with their folder.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import pymupdf

from .store import now

EXAM_DIR = ".exams"
KINDS = {"pdf": "application/pdf",
         "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
LONG_SIDE = 1600
MAX_PAGES = 60
MAX_BYTES = 50 * 1024 * 1024


class ExamError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


class Exams:
    """The past exams stored directly in one folder."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.root = self.folder / EXAM_DIR

    def _dir(self, eid: str) -> Path:
        if not eid or not all(c in "0123456789abcdef" for c in eid):
            raise ExamError("no such exam", 404)
        d = self.root / eid
        if not (d / "meta.json").exists():
            raise ExamError("no such exam", 404)
        return d

    def add(self, filename: str, data: bytes, title: str | None = None) -> dict[str, Any]:
        if len(data) > MAX_BYTES:
            raise ExamError("file is too large (max 50 MB)")
        name = Path(filename or "exam.pdf").name
        if data[:4] == b"PK\x03\x04" or name.lower().endswith(".docx"):
            return self._add_docx(name, data, title)
        if name.lower().endswith(".doc"):
            raise ExamError("old .doc files are not supported; save it as .docx or PDF")
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as e:  # pymupdf raises several types
            raise ExamError(f"not a readable PDF: {e}") from e
        eid = uuid.uuid4().hex
        d = self.root / eid
        with doc:
            if doc.needs_pass:
                raise ExamError("PDF is password protected")
            if doc.page_count == 0:
                raise ExamError("PDF has no pages")
            if doc.page_count > MAX_PAGES:
                raise ExamError(f"too many pages ({doc.page_count}; max {MAX_PAGES})")
            d.mkdir(parents=True)
            try:
                texts = []
                for i, page in enumerate(doc, 1):
                    zoom = LONG_SIDE / max(page.rect.width, page.rect.height)
                    (d / f"page-{i}.png").write_bytes(
                        page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png"))
                    texts.append(page.get_text(sort=True).strip())
                return self._save(d, "pdf", name, data, texts, title)
            except BaseException:
                shutil.rmtree(d, ignore_errors=True)
                raise

    def _add_docx(self, name: str, data: bytes, title: str | None) -> dict[str, Any]:
        texts = docx_pages(data)
        if not any(texts):
            raise ExamError("the Word file has no text")
        d = self.root / uuid.uuid4().hex
        d.mkdir(parents=True)
        try:
            return self._save(d, "docx", name, data, texts, title)
        except BaseException:
            shutil.rmtree(d, ignore_errors=True)
            raise

    @staticmethod
    def _save(d: Path, kind: str, name: str, data: bytes, texts: list[str], title: str | None) -> dict[str, Any]:
        (d / f"source.{kind}").write_bytes(data)
        (d / "text.json").write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")
        meta = {"id": d.name, "title": (title or "").strip() or Path(name).stem, "filename": name, "kind": kind,
                "pages": len(texts), "created_at": now()}
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")  # last: marks it complete
        return meta

    def list(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        out = []
        for d in self.root.iterdir():
            try:
                out.append(json.loads((d / "meta.json").read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue  # half-written or foreign
        return sorted(out, key=lambda m: m.get("created_at", ""))

    def get(self, eid: str) -> dict[str, Any]:
        return json.loads((self._dir(eid) / "meta.json").read_text(encoding="utf-8"))

    def texts(self, eid: str) -> list[str]:
        return json.loads((self._dir(eid) / "text.json").read_text(encoding="utf-8"))

    def image(self, eid: str, page: int) -> bytes:
        """A PDF page's picture. Word files have none (ExamError 404)."""
        p = self._dir(eid) / f"page-{int(page)}.png"
        if not p.exists():
            raise ExamError(f"page {page} has no image", 404)
        return p.read_bytes()

    def source(self, eid: str) -> tuple[Path, str]:
        """The uploaded file and its media type."""
        kind = self.get(eid).get("kind", "pdf")
        return self._dir(eid) / f"source.{kind}", KINDS[kind]

    def rename(self, eid: str, title: str) -> dict[str, Any]:
        title = clean_title(title)
        d = self._dir(eid)
        meta = {**self.get(eid), "title": title}
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return meta

    def delete(self, eid: str) -> None:
        shutil.rmtree(self._dir(eid))
        if self.root.is_dir() and not any(self.root.iterdir()):
            self.root.rmdir()


def clean_title(title: str) -> str:
    """A display name: one line, 1–200 characters."""
    t = " ".join(str(title or "").split())
    if not t:
        raise ExamError("the name can't be empty")
    if len(t) > 200:
        raise ExamError("the name is too long (max 200 characters)")
    return t


def all_exams(base: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Every exam in `base` and the folders under it: (its folder, meta), parents first, then by date."""
    out = []
    for dirpath, dirnames, _files in os.walk(base):
        dirnames[:] = sorted(n for n in dirnames if not n.startswith("."))
        for meta in Exams(Path(dirpath)).list():
            out.append((Path(dirpath), meta))
    return out


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"


def docx_pages(data: bytes) -> list[str]:
    """The text of a .docx, one string per page (pages end at explicit page breaks).

    Paragraphs become lines; table cells are joined with " | "; equations keep their plain text
    (`m:t`), which is rough but enough to see what a question asks.
    """
    from xml.etree import ElementTree as ET

    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            root = ET.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise ExamError(f"not a readable Word (.docx) file: {e}") from e
    pages, lines = [], []

    def text_of(el) -> tuple[str, bool]:
        out, brk = [], False
        for x in el.iter():
            if x.tag in (f"{_W}t", f"{_M}t"):
                out.append(x.text or "")
            elif x.tag == f"{_W}tab":
                out.append("\t")
            elif x.tag == f"{_W}br" and x.get(f"{_W}type") == "page":
                brk = True
        return "".join(out), brk

    body = root.find(f"{_W}body")
    for el in (body if body is not None else []):
        if el.tag == f"{_W}p":
            line, brk = text_of(el)
            lines.append(line)
            if brk:
                pages.append(lines)
                lines = []
        elif el.tag == f"{_W}tbl":
            for row in el.iter(f"{_W}tr"):
                lines.append(" | ".join(text_of(c)[0] for c in row.iter(f"{_W}tc")))
    pages.append(lines)
    out = [re.sub(r"\n{3,}", "\n\n", "\n".join(p)).strip() for p in pages]
    while len(out) > 1 and not out[-1]:
        out.pop()
    return out[:MAX_PAGES]
