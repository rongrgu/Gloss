import pymupdf
import pytest
from make_pdfs import H, W, make_deck

from gloss.ingest import IngestError, create_course, ingest_pdf
from gloss.store import Course


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def test_create_course_from_pdf(tmp_path):
    pdf = tmp_path / "lec7.pdf"
    make_deck(pdf)
    with create_course(pdf, tmp_path / "lec7.gloss") as c:
        assert c.slide_count() == 3
        assert c.title == "lec7"  # no metadata title -> file name
        assert c.get_asset("source.pdf") == ("application/pdf", pdf.read_bytes())
        s = c.get_slide(2)
        assert "Scaled dot-product" in s.text and "softmax" in s.text
        assert png_size(s.image) == (1600, 900)


def test_title_precedence(tmp_path):
    pdf = tmp_path / "x.pdf"
    make_deck(pdf, title="Lecture 7: Attention")
    with create_course(pdf, tmp_path / "a.gloss") as c:
        assert c.title == "Lecture 7: Attention"
    with create_course(pdf, tmp_path / "b.gloss", title="Mine") as c:
        assert c.title == "Mine"


def test_text_blocks_are_relative_and_match_text(tmp_path):
    pdf = tmp_path / "x.pdf"
    make_deck(pdf)
    with create_course(pdf, tmp_path / "a.gloss") as c:
        blocks = c.get_slide(3).text_blocks
    texts = [b["text"] for b in blocks]
    assert texts[0] == "Multi-head attention"
    assert "Concatenate and project" in texts
    for b in blocks:
        assert 0 <= b["x"] and 0 <= b["y"] and b["x"] + b["w"] <= 1 and b["y"] + b["h"] <= 1
    head = blocks[0]
    # Heading was drawn at (60, 80) baseline on a 960x540 page.
    assert head["x"] == pytest.approx(60 / W, abs=0.01)
    assert head["y"] < 80 / H < head["y"] + head["h"]


def test_rotated_page_blocks_follow_rendered_image(tmp_path):
    pdf = tmp_path / "x.pdf"
    make_deck(pdf, slides=[["TOPLEFT"]], rotate={1: 90})
    with create_course(pdf, tmp_path / "a.gloss") as c:
        s = c.get_slide(1)
    w, h = png_size(s.image)
    assert h > w  # portrait after rotation
    (b,) = s.text_blocks
    # Rotated 90° clockwise, text that was at the top-left ends up at the top-right.
    assert b["x"] > 0.5 and b["y"] < 0.5
    # And the rendered image actually has ink inside that box.
    pix = pymupdf.Pixmap(s.image)
    cx, cy = int((b["x"] + b["w"] / 2) * pix.width), int((b["y"] + b["h"] / 2) * pix.height)
    region = [pix.pixel(x, y) for x in range(cx - 20, cx + 20) for y in range(cy - 40, cy + 40)]
    assert any(sum(p) < 300 for p in region)


def test_long_side_option(tmp_path):
    pdf = tmp_path / "x.pdf"
    make_deck(pdf)
    with create_course(pdf, tmp_path / "a.gloss", long_side=800) as c:
        assert png_size(c.get_slide(1).image) == (800, 450)


def test_progress_callback(tmp_path):
    seen = []
    pdf = tmp_path / "x.pdf"
    make_deck(pdf)
    create_course(pdf, tmp_path / "a.gloss", progress=lambda i, n: seen.append((i, n))).close()
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_reingest_replaces_slides(tmp_path):
    with Course.create(tmp_path / "a.gloss", "A") as c:
        ingest_pdf(c, make_deck())
        ingest_pdf(c, make_deck(slides=[["only one"]]))
        assert c.slide_count() == 1
        assert c.title == "A"  # existing title kept


def test_blank_page_has_no_blocks(tmp_path):
    with Course.create(tmp_path / "a.gloss", "A") as c:
        ingest_pdf(c, make_deck(slides=[[]]))
        s = c.get_slide(1)
        assert s.text == "" and s.text_blocks == []


@pytest.mark.parametrize(
    "data, msg",
    [
        (b"not a pdf at all", "readable"),
        (make_deck(password="secret"), "password"),
    ],
)
def test_bad_pdfs(tmp_path, data, msg):
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(data)
    out = tmp_path / "bad.gloss"
    with pytest.raises(IngestError, match=msg):
        create_course(pdf, out)
    assert not out.exists()  # no half-made course left behind
