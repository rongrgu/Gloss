"""Past exams (DESIGN.md §13.8): storage, course tools, the API, and replicate / instructions quizzes."""

import pytest
from gloss.course_tools import Scope, ToolError, call
from gloss.exams import ExamError, Exams, all_exams
from gloss.folders import Folders
from gloss.quiz import quiz_problems
from make_pdfs import make_deck
from test_course_tools import library  # noqa: F401  (fixture)
from test_quiz import app, q  # noqa: F401  (fixture)
from test_server import sse

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ' \
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'


def make_docx() -> bytes:
    """A minimal .docx: two paragraphs, an equation, a page break, a table."""
    body = (
        '<w:p><w:r><w:t>Quiz 3</w:t></w:r></w:p>'
        '<w:p><w:r><w:t xml:space="preserve">1. Compute </w:t></w:r><m:oMath><m:r><m:t>softmax(x)</m:t></m:r></m:oMath></w:p>'
        '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
        '<w:p><w:r><w:t>2. Fill in the table</w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>h</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>d/h</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    )
    buf = __import__("io").BytesIO()
    with __import__("zipfile").ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {W}><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


EXAM = [["Midterm 2025", "1. Why divide by sqrt(d)?"], ["2. Derive softmax gradient", "3. True or false: heads share weights"]]


def test_store_add_list_delete(tmp_path):
    st = Exams(tmp_path)
    assert st.list() == []
    m = st.add("midterm.pdf", make_deck(slides=EXAM))
    assert m["title"] == "midterm" and m["pages"] == 2 and m["filename"] == "midterm.pdf"
    assert "Why divide by sqrt(d)" in st.texts(m["id"])[0]
    assert st.image(m["id"], 2).startswith(b"\x89PNG") and st.source(m["id"])[0].name == "source.pdf"
    assert [x["id"] for x in st.list()] == [m["id"]]
    for bad in ("../x", "ZZ", ""):
        with pytest.raises(ExamError):
            st.get(bad)
    with pytest.raises(ExamError):
        st.add("x.pdf", b"not a pdf")
    assert len(st.list()) == 1  # the failed upload left nothing behind
    st.delete(m["id"])
    assert st.list() == [] and not (tmp_path / ".exams").exists()


def test_hidden_from_the_folder_tree_and_found_below(library):  # noqa: F811
    root, _ = library
    Exams(root / "CMU" / "ML").add("quiz1.pdf", make_deck(slides=EXAM), title="Quiz 1")
    listing = Folders(root).listing("CMU/ML")
    assert all(not f["name"].startswith(".") for f in listing["folders"])
    assert [(Folders(root).rel(d), m["title"]) for d, m in all_exams(root / "CMU")] == [("CMU/ML", "Quiz 1")]


def test_course_tools(library):  # noqa: F811
    root, _ = library
    Exams(root / "CMU" / "ML").add("quiz1.pdf", make_deck(slides=EXAM), title="Quiz 1")
    with Scope(root, "CMU") as s:
        assert "E1（CMU/ML：Quiz 1，2 页）" in s.list_past_exams()
        whole = call(s, "get_past_exam", {"exam": "E1"})[0]["text"]
        assert "## 第 1 页" in whole and "## 第 2 页" in whole
        one = call(s, "get_past_exam", {"exam": "e1", "page": 2, "image": True})
        assert "Derive softmax" in one[0]["text"] and "第 1 页" not in one[0]["text"] and one[1]["type"] == "image"
        for args in ({"exam": "E2"}, {"exam": "E1", "page": 3}, {"exam": "E1", "image": True}):
            with pytest.raises(ToolError):
                call(s, "get_past_exam", args)
    with Scope(root, "CMU/Stats") as s:  # not in this folder's scope
        assert s.exams == [] and "没有往年试卷" in s.list_past_exams()


def test_flexible_count_and_true_false(library):  # noqa: F811
    root, _ = library
    with Scope(root, "CMU/ML") as s:
        tf = q(options=["对", "错"], answer="B", stem="判断")
        assert quiz_problems({"questions": [tf, q(), q("derivation", "推导")]}, s, None, ("mcq", "short", "derivation")) == ([], [])
        problems, _ = quiz_problems({"questions": []}, s, None, ("mcq",))
        assert "题数要在 1–30 道之间" in problems[0]


def test_api_upload_replicate_and_instructions(app):  # noqa: F811
    client, provider, *_ = app
    r = client.post("/api/library/exams", data={"folder": "CMU/ML", "title": "Midterm"},
                    files={"file": ("mid.pdf", make_deck(slides=EXAM), "application/pdf")})
    assert r.status_code == 200, r.text
    exam = r.json()
    assert exam["folder"] == "CMU/ML" and exam["pages"] == 2
    assert [e["id"] for e in client.get("/api/library/exams", params={"folder": "CMU"}).json()["exams"]] == [exam["id"]]
    f = client.get(f"/api/library/exams/{exam['id']}/file", params={"folder": "CMU/ML"})
    assert f.headers["content-type"] == "application/pdf" and f.content.startswith(b"%PDF")
    bad = client.post("/api/library/exams", data={"folder": "CMU/ML"}, files={"file": ("x.pdf", b"nope", "application/pdf")})
    assert bad.status_code == 422

    # replicate: count and types are up to the exam; three questions where 2 were "requested"
    provider.structured = [{"title": "仿期中", "questions": [q(), q("derivation", "推导 softmax 梯度"),
                                                               q(options=["对", "错"], stem="判断：多头共享权重")]}, None]
    r = client.post("/api/library/quizzes", json={"folder": "CMU/ML", "count": 2, "types": ["mcq"], "mode": "replicate",
                                                  "exam": exam["id"], "instructions": "难度高一点"})
    assert r.status_code == 200, r.text
    done = sse(client, r.json()["job"]["id"])[-1]
    assert done["type"] == "done" and done["count"] == 3 and done["requested"] is None, done
    p = provider.calls[0].prompt
    assert "仿照学生上传的往年试卷 E1「Midterm」" in p and "Why divide by sqrt(d)" in p and "get_past_exam" in p
    assert "## 学生的额外要求" in p and "难度高一点" in p and "侧重" not in p.split("## 出题要求")[1].split("- **题型的写法**")[0]
    quiz = client.get("/api/library/quizzes", params={"folder": "CMU/ML"}).json()["quizzes"][0]
    assert quiz["options"]["mode"] == "replicate" and quiz["options"]["exam"]["title"] == "Midterm"

    # new + instructions: the exams are offered as a reference; no instructions section when empty
    provider.calls.clear()
    provider.structured = [{"title": "t", "questions": [q()]}, None]
    r = client.post("/api/library/quizzes", json={"folder": "CMU/ML", "count": 5, "instructions": "只出 1 道选择题"})
    done = sse(client, r.json()["job"]["id"])[-1]
    assert done["type"] == "done" and done["count"] == 1, done
    p = provider.calls[0].prompt
    assert "共 **5 道题**（如果下面「学生的额外要求」" in p and "## 往年试卷" in p and "只出 1 道选择题" in p

    provider.calls.clear()
    provider.structured = [{"title": "t", "questions": [q()]}, None]
    r = client.post("/api/library/quizzes", json={"folder": "CMU/ML", "count": 1, "types": ["mcq"]})
    sse(client, r.json()["job"]["id"])
    assert "额外要求" not in provider.calls[0].prompt and "{{" not in provider.calls[0].prompt

    for body in ({"folder": "CMU/ML", "mode": "replicate"}, {"folder": "CMU/ML", "mode": "replicate", "exam": "ab"},
                 {"folder": "CMU/ML", "mode": "x"}, {"folder": "CMU/ML", "instructions": "x" * 2001}):
        assert client.post("/api/library/quizzes", json=body).status_code == 422, body
    assert client.delete(f"/api/library/exams/{exam['id']}", params={"folder": "CMU/ML"}).json() == {"ok": True}
    assert client.get("/api/library/exams", params={"folder": "CMU"}).json()["exams"] == []
    assert client.delete(f"/api/library/exams/{exam['id']}", params={"folder": "CMU/ML"}).status_code == 404


def test_word_files(tmp_path, library):  # noqa: F811
    st = Exams(tmp_path)
    m = st.add("quiz3.docx", make_docx())
    assert m["kind"] == "docx" and m["pages"] == 2
    p1, p2 = st.texts(m["id"])
    assert "1. Compute softmax(x)" in p1 and "Quiz 3" in p1 and "h | d/h" in p2
    path, media = st.source(m["id"])
    assert path.name == "source.docx" and "wordprocessingml" in media
    with pytest.raises(ExamError):
        st.image(m["id"], 1)
    for name, data in (("old.doc", b"\xd0\xcf\x11\xe0"), ("bad.docx", b"PK\x03\x04junk")):
        with pytest.raises(ExamError):
            st.add(name, data)
    root, _ = library
    Exams(root / "CMU" / "ML").add("quiz3.docx", make_docx())
    with Scope(root, "CMU/ML") as s:
        with pytest.raises(ToolError, match="Word"):
            call(s, "get_past_exam", {"exam": "E1", "page": 1, "image": True})
        assert "Compute softmax" in call(s, "get_past_exam", {"exam": "E1"})[0]["text"]
