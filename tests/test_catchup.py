"""Catch-up on the library home (DESIGN.md §17): read-only, no LLM."""

from test_course_tools import library  # noqa: F401  (fixture)
from test_quiz import app  # noqa: F401  (fixture)

from gloss.catchup import catch_up
from gloss.quiz import QuizStore
from gloss.store import Course


def test_last_lecture_page_confused_quizzes_weak_points(library):  # noqa: F811
    root, cid = library
    b = root / "CMU" / "ML" / "b.gloss"
    with Course.open(b) as c:
        c.set_ui("page", 3)
        t = next(t for t in c.list_threads(1) if t.n == 1)
        c.set_thread_understood(t.id, False)
    st = QuizStore(root / "CMU" / "ML")
    st.create("Not started", {}, None, [{"stem": "a"}])  # left to the quiz list
    mock = st.create("Mock", {}, None, [{"stem": "a"}, {"stem": "b"}])
    st.add_attempt(st.get(mock)["questions"][0]["id"], "A", True)
    out = catch_up(root, [{"path": str((root / "CMU" / "Stats" / "s.gloss").resolve()), "opened_at": "2000-01-01T00:00:00+00:00"}])
    last = out["last"]
    assert last["title"] == "Attention" and last["page"] == 3 and last["folder"] == "CMU/ML" and last["questions"] == 2
    assert [x["title"] for x in out["continue"]] == ["Bayes"]  # only opened, long ago: still listed after the last one
    assert out["confused_total"] == 1 and out["confused"][0]["question"] == "为什么要除以 sqrt(d)？" and out["confused"][0]["page"] == 1
    assert [(q["title"], q["answered"], q["count"]) for q in out["quizzes"]] == [("Mock", 1, 2)]  # in progress only
    assert out["sheet"] is None and out["weak_total"] == 0


def test_empty_library(tmp_path):
    (tmp_path / "lib").mkdir()
    out = catch_up(tmp_path / "lib", [])
    assert out["last"] is None and out["continue"] == [] and out["quizzes"] == [] and out["confused"] == []


def test_api(app):  # noqa: F811
    client, *_ = app
    r = client.get("/api/library/catchup").json()
    assert set(r) >= {"last", "continue", "confused", "quizzes", "weak_points", "sheet"}


def test_api_deep_quizzes_and_gloss_upload(app):  # noqa: F811
    """The library home lists every course's quizzes; a Gloss file can be uploaded into the library (§9.1, §17)."""
    from gloss.quiz import QuizStore
    client, provider, root, cid = app
    QuizStore(root / "CMU" / "ML").create("ML mock", {}, None, [{"stem": "a"}])
    assert client.get("/api/library/quizzes").json()["quizzes"] == []
    deep = client.get("/api/library/quizzes", params={"deep": "true"}).json()["quizzes"]
    assert [(q["title"], q["folder"]) for q in deep] == [("ML mock", "CMU/ML")]

    data = (root / "CMU" / "ML" / "b.gloss").read_bytes()
    r = client.post("/api/courses/upload", data={"folder": ""}, files={"file": ("shared.gls", data, "application/octet-stream")})
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["path"].endswith("lib/shared.gls") and c["title"] == "Attention" and c["id"] != cid  # its own id
    bad = client.post("/api/courses/upload", data={"folder": ""}, files={"file": ("x.gls", b"not sqlite", "application/octet-stream")})
    assert bad.status_code == 422 and not (root / "x.gls").exists()
    assert client.post("/api/courses/upload", data={"folder": ""}, files={"file": ("x.pdf", b"%PDF", "application/pdf")}).status_code == 422
