"""PDF → slides table (DESIGN.md §5.1). No LLM calls."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pymupdf

from .store import Course, Slide

SOURCE_ASSET = "source.pdf"
DEFAULT_LONG_SIDE = 1600  # px; rendered slide images


class IngestError(Exception):
    pass


def ingest_pdf(
    course: Course,
    pdf: str | Path | bytes,
    *,
    long_side: int = DEFAULT_LONG_SIDE,
    progress: Callable[[int, int], None] | None = None,
) -> int:
    """Parse a PDF into `course`: store it as an asset, replace all slides. Returns page count."""
    data = pdf if isinstance(pdf, bytes) else Path(pdf).read_bytes()
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        raise IngestError(f"not a readable PDF: {e}") from e
    with doc:
        if doc.needs_pass:
            raise IngestError("PDF is password protected")
        if doc.page_count == 0:
            raise IngestError("PDF has no pages")
        slides = []
        for i, page in enumerate(doc):
            slides.append(_slide(page, i + 1, long_side))
            if progress:
                progress(i + 1, doc.page_count)
        pdf_title = (doc.metadata or {}).get("title", "").strip()

    course.put_asset(SOURCE_ASSET, "application/pdf", data)
    course.replace_slides(slides)
    if not course.title and pdf_title:
        course.set_meta("title", pdf_title)
    return len(slides)


def create_course(pdf_path: str | Path, out_path: str | Path, title: str | None = None, **kw) -> Course:
    """New `.gloss` from a PDF. Title: given > PDF metadata > file name. Removes the file on failure."""
    pdf_path, out_path = Path(pdf_path), Path(out_path)
    course = Course.create(out_path, title or "")
    try:
        ingest_pdf(course, pdf_path, **kw)
        if not course.title:
            course.set_meta("title", pdf_path.stem)
    except BaseException:
        course.close()
        for suffix in ("", "-wal", "-shm"):
            Path(str(out_path) + suffix).unlink(missing_ok=True)
        raise
    return course


def _slide(page: pymupdf.Page, number: int, long_side: int) -> Slide:
    rect = page.rect  # already accounts for /Rotate
    zoom = long_side / max(rect.width, rect.height)
    image = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")

    # Text coordinates come back in the unrotated page space; map them into the
    # displayed (rotated) space so they line up with the rendered image.
    rot = page.rotation_matrix
    blocks = []
    for x0, y0, x1, y1, text, _no, kind in page.get_text("blocks", sort=True):
        text = text.strip()
        if kind != 0 or not text:
            continue
        r = pymupdf.Rect(x0, y0, x1, y1) * rot
        r &= rect  # clip to the page
        if r.is_empty:
            continue
        blocks.append({
            "x": round(r.x0 / rect.width, 4),
            "y": round(r.y0 / rect.height, 4),
            "w": round(r.width / rect.width, 4),
            "h": round(r.height / rect.height, 4),
            "text": text,
        })
    return Slide(page=number, text=page.get_text(sort=True).strip(), image=image, text_blocks=blocks)
