import threading

import pytest

from gloss.store import Course, Slide


def test_slides_roundtrip(course):
    assert course.slide_count() == 3
    s = course.get_slide(2)
    assert (s.page, s.text, s.image, s.text_blocks) == (2, "slide 2", b"img", [])
    assert course.get_slide(9) is None
    assert course.slide_texts() == [(1, "slide 1"), (2, "slide 2"), (3, "slide 3")]


def test_replace_slides_requires_contiguous_pages(course):
    with pytest.raises(ValueError):
        course.replace_slides([Slide(1, "", b"", []), Slide(3, "", b"", [])])
    assert course.slide_count() == 3  # unchanged


def test_text_blocks_roundtrip(course):
    blocks = [{"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.05, "text": "Q·Kᵀ / √d"}]
    course.replace_slides([Slide(1, "t", b"i", blocks)])
    assert course.get_slide(1).text_blocks == blocks


def test_transcript_span(course):
    course.set_transcript_span(2, "so the denominator…")
    assert course.get_slide(2).transcript_span == "so the denominator…"
    with pytest.raises(KeyError):
        course.set_transcript_span(9, "x")


def test_assets(course):
    course.put_asset("source.pdf", "application/pdf", b"%PDF")
    course.put_asset("source.pdf", "application/pdf", b"%PDF-2")
    assert course.get_asset("source.pdf") == ("application/pdf", b"%PDF-2")
    assert course.list_assets() == [("source.pdf", "application/pdf")]
    assert course.get_asset("nope") is None


def test_tree(course):
    assert course.get_tree() is None
    tree = {"title": "注意力", "children": [{"title": "A", "summary": "", "slides": [1, 2, 3]}]}
    course.set_tree(tree)
    course.set_tree(tree)
    assert course.get_tree() == tree


def test_explanations_latest_wins(course):
    assert course.latest_explanation(1) is None
    course.add_explanation(1, "v1", "haiku")
    course.add_explanation(1, "v2", "opus")
    course.add_explanation(2, "other page")
    e = course.latest_explanation(1)
    assert (e.content, e.model) == ("v2", "opus")
    with pytest.raises(KeyError):
        course.add_explanation(9, "x")


def test_sessions(course):
    assert course.get_session(1) is None
    course.set_session(1, "s-a")
    course.set_session(1, "s-b")
    assert course.get_session(1) == "s-b"
    course.set_session(1, None)
    assert course.get_session(1) is None


def test_threads_numbered_per_page(course):
    a = course.create_thread(1, (0.1, 0.1, 0.2, 0.2), crop=b"png")
    b = course.create_thread(1, (0.5, 0.5, 0.1, 0.1))
    c = course.create_thread(2, (0, 0, 1, 1))
    assert (a.n, b.n, c.n) == (1, 2, 1)
    assert [t.id for t in course.list_threads(1)] == [a.id, b.id]
    got = course.get_thread(a.id)
    assert got == a and got.crop == b"png" and got.minimized is False
    assert course.thread_counts() == {1: 2, 2: 1}


def test_thread_number_not_reused_after_delete(course):
    a = course.create_thread(1, (0.1, 0.1, 0.2, 0.2))
    b = course.create_thread(1, (0.1, 0.1, 0.2, 0.2))
    course.delete_thread(a.id)
    assert course.create_thread(1, (0.1, 0.1, 0.2, 0.2)).n == 3
    course.delete_thread(b.id)


@pytest.mark.parametrize("rect", [(-0.1, 0, 0.5, 0.5), (0.6, 0, 0.5, 0.5), (0, 0, 0, 0.5), (0, 0.9, 0.5, 0.2)])
def test_thread_rect_validated(course, rect):
    with pytest.raises(ValueError):
        course.create_thread(1, rect)


def test_thread_on_missing_page(course):
    with pytest.raises(KeyError):
        course.create_thread(9, (0, 0, 0.5, 0.5))


def test_minimize(course):
    t = course.create_thread(1, (0, 0, 0.5, 0.5))
    course.set_thread_minimized(t.id, True)
    assert course.get_thread(t.id).minimized is True
    with pytest.raises(KeyError):
        course.set_thread_minimized("nope", True)


def test_messages_and_page_history(course):
    t1 = course.create_thread(1, (0, 0, 0.5, 0.5))
    t2 = course.create_thread(1, (0.5, 0.5, 0.5, 0.5))
    other = course.create_thread(2, (0, 0, 0.5, 0.5))
    course.add_message(t1.id, "user", "q1")
    course.add_message(t2.id, "user", "q2")
    course.add_message(t1.id, "assistant", "a1")
    course.add_message(other.id, "user", "elsewhere")
    course.add_message(t2.id, "assistant", "a2")
    assert [m.content for m in course.list_messages(t1.id)] == ["q1", "a1"]
    hist = course.page_history(1)
    assert [(t.n, m.content) for t, m in hist] == [(1, "q1"), (2, "q2"), (1, "a1"), (2, "a2")]
    with pytest.raises(ValueError):
        course.add_message(t1.id, "system", "x")
    with pytest.raises(KeyError):
        course.add_message("nope", "user", "x")


def test_delete_thread_removes_messages(course):
    t = course.create_thread(1, (0, 0, 0.5, 0.5))
    course.add_message(t.id, "user", "q")
    course.delete_thread(t.id)
    assert course.get_thread(t.id) is None
    assert course.list_messages(t.id) == []


def test_notes(course):
    assert course.get_note(1) == ""
    course.set_note(1, "first")
    course.set_note(1, "second")
    course.set_note(2, "")
    assert course.get_note(1) == "second"
    assert course.pages_with_notes() == {1}
    with pytest.raises(KeyError):
        course.set_note(9, "x")


def test_ui_state(course):
    assert course.get_ui("layout", "stack") == "stack"
    course.set_ui("layout", {"mode": "side", "split": [0.6, 0.58]})
    assert course.get_ui("layout") == {"mode": "side", "split": [0.6, 0.58]}


def test_set_meta_cannot_touch_schema_version(course):
    with pytest.raises(ValueError):
        course.set_meta("schema_version", "0")


def test_failed_write_rolls_back(course):
    with pytest.raises(RuntimeError):
        with course._tx() as c:
            c.execute("DELETE FROM slides")
            raise RuntimeError
    assert course.slide_count() == 3


def test_data_persists_without_explicit_save(tmp_path):
    p = tmp_path / "a.gloss"
    c = Course.create(p, "A")
    c.replace_slides([Slide(1, "t", b"i", [])])
    t = c.create_thread(1, (0, 0, 0.5, 0.5))
    c.add_message(t.id, "user", "q")
    c.set_note(1, "n")
    # A second connection sees committed data while the first is still open.
    with Course.open(p) as c2:
        assert c2.list_messages(t.id)[0].content == "q"
        assert c2.get_note(1) == "n"
    c.close()


def test_concurrent_thread_creation_gets_unique_numbers(course):
    errors = []

    def worker():
        try:
            for _ in range(10):
                course.create_thread(1, (0, 0, 0.5, 0.5))
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    assert sorted(t.n for t in course.list_threads(1)) == list(range(1, 41))
