"""Generate synthetic lecture PDFs for tests (never commit real course material).

Run directly to write a sample deck:  uv run python tests/make_pdfs.py out.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

W, H = 960, 540  # 16:9 slide, points


def make_deck(
    path: str | Path | None = None,
    slides: list[list[str]] | None = None,
    *,
    title: str = "",
    rotate: dict[int, int] | None = None,
    password: str | None = None,
) -> bytes:
    """Each slide is a list of lines; line 0 is the heading. `rotate` maps 1-based page -> degrees."""
    slides = slides if slides is not None else [
        ["Attention", "Lecture 7"],
        ["Scaled dot-product", "softmax(QK^T / sqrt(d)) V"],
        ["Multi-head attention", "h heads, each d/h wide", "Concatenate and project"],
    ]
    doc = pymupdf.open()
    for i, lines in enumerate(slides, start=1):
        page = doc.new_page(width=W, height=H)
        y = 80
        for j, line in enumerate(lines):
            size = 36 if j == 0 else 22
            page.insert_text((60, y), line, fontsize=size)
            y += size * 2.2
        if rotate and i in rotate:
            page.set_rotation(rotate[i])
    if title:
        doc.set_metadata({"title": title})
    kw = {}
    if password:
        kw = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": password, "owner_pw": password}
    data = doc.tobytes(**kw)
    if path:
        Path(path).write_bytes(data)
    return data


if __name__ == "__main__":
    make_deck(sys.argv[1] if len(sys.argv) > 1 else "sample.pdf", title="Sample lecture")
