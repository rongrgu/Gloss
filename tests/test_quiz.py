import copy

import pytest
from fakes import FakeProvider
from fastapi.testclient import TestClient
from test_course_tools import library  # noqa: F401  (fixture)
from test_server import sse

from gloss.config import REPO_ROOT, Config
from gloss.course_tools import Scope
from gloss.quiz import QuizStore, finish_question, quiz_problems
from gloss.server import create_app


def q(type_="mcq", stem="softmax 的作用？", lecture="L1", page=1, **kw):
    base = {"type": type_, "stem": stem, "options": ["归一化", "求导", "排序"] if type_ == "mcq" else [],
            "answer": "A" if type_ == "mcq" else "要点", "explanation": "因为…", "why": "你问过两次",
            "sources": [{"lecture": lecture, "page": page}], "topic": "缩放点积", "difficulty": 2}
    return {**base, **kw}


def test_quiz_problems(library):  # noqa: F811
    root, _ = library
    with Scope(root, "CMU/ML") as s:
        good = {"title": "t", "questions": [q(), q("short", "为什么除以 sqrt(d)？")]}
        assert quiz_problems(good, s, 2, ("mcq", "short")) == ([], [])
        bad = {"questions": [
            q(),
            q(stem="SOFTMAX 的作用?"),  # duplicate after normalization
            q(options=["a", "a", "b"]),
            q(answer="D", stem="x"),
            q("derivation", "推导", page=9),
            q(stem="y", lecture="L7"),
            q("short", "z", answer=" "),
        ]}
        problems, idx = quiz_problems(bad, s, 5, ("mcq", "short"))
        text = "\n".join(problems)
        assert "要求 5 道题，实际给了 7 道" in text
        assert "第 2 题：和第 1 题重复" in text and "第 3 题：选项有重复" in text
        assert "第 4 题：答案应是一个选项字母（A–C）" in text
        assert "第 5 题：题型 'derivation' 不在允许的题型里" in text and "L1 没有第 9 页（共 3 页）" in text
        assert "第 6 题：出处讲次 'L7' 不存在（可用：L1, L2）" in text and "第 7 题：答案为空" in text
        assert idx == [1, 2, 3, 4, 5, 6]


def test_finish_question_resolves_sources(library):  # noqa: F811
    root, cid = library
    with Scope(root, "CMU") as s:
        out = finish_question(q(answer="b.", difficulty=9, sources=[{"lecture": "l1", "page": 3}, {"lecture": "L1", "page": 3}]), s)
        assert out["answer"] == "B" and out["difficulty"] == 3 and out["review"] is None
        opts = finish_question(q(options=["A. 归一化", "(b) 求导", "C、排序", "A matrix", "A.I. systems"]), s)["options"]
        assert opts == ["归一化", "求导", "排序", "A matrix", "A.I. systems"]  # prefixes go; a word starting with A stays
        assert out["sources"] == [{"lecture": cid, "label": "L1", "title": "Attention", "course": "CMU/ML",
                                   "number": 1, "page": 3}]


def test_store_roundtrip(tmp_path):
    st = QuizStore(tmp_path)
    assert st.list() == [] and not st.path.exists()  # reading never creates the file
    qid = st.create("Quiz", {"count": 2}, "haiku", [{"stem": "a"}, {"stem": "b"}])
    quiz = st.get(qid)
    assert [x["n"] for x in quiz["questions"]] == [1, 2] and quiz["questions"][0]["attempt"] is None
    first = quiz["questions"][0]["id"]
    st.add_attempt(first, "B", False)
    st.add_attempt(first, "A", True)  # retry: the latest attempt counts
    assert st.get(qid)["questions"][0]["attempt"]["answer"] == "A"
    assert st.list()[0] | {"created_at": None} == {"id": qid, "title": "Quiz", "options": {"count": 2}, "model": "haiku",
                                                   "created_at": None, "count": 2, "answered": 1, "correct": 1}
    with pytest.raises(KeyError):
        st.add_attempt("nope", "", None)
    st.delete(qid)
    assert st.list() == []


@pytest.fixture
def app(tmp_path, monkeypatch, library):  # noqa: F811
    root, cid = library
    monkeypatch.setenv("GLOSS_HOME", str(tmp_path / "home"))
    ws = tmp_path / "ws"
    (ws / "prompts").mkdir(parents=True)
    src = REPO_ROOT / "claude_workspace"
    for p in [src / "CLAUDE.md", *(src / "prompts").glob("*.md")]:
        (ws / p.relative_to(src)).write_text(p.read_text())
    provider = FakeProvider()
    cfg = Config(workspace=ws, library=root, model="fake", quiz_model="opus")
    with TestClient(create_app(cfg, provider)) as client:
        yield client, provider, root, cid


def test_api_generate_retry_verify_and_answer(app):
    client, provider, root, cid = app
    first = {"title": "Attention 自测", "questions": [q(), q(stem="重复"), q(stem="重复")]}  # 3 for 2, dup
    second = {"title": "Attention 自测", "questions": [q(), q("short", "为什么除以 sqrt(d)？", page=1)]}
    verify = {"results": [{"n": 1, "ok": True, "reason": ""}, {"n": 2, "ok": False, "reason": "课件没说"}]}
    provider.structured = [first, copy.deepcopy(second), verify]
    r = client.post("/api/library/quizzes", json={"folder": "CMU/ML", "count": 2, "types": ["mcq", "short"]})
    assert r.status_code == 200, r.text
    events = sse(client, r.json()["job"]["id"])
    done = events[-1]
    assert done["type"] == "done" and done["count"] == 2, events
    assert [e["phase"] for e in events if e["type"] == "phase"] == ["generate", "generate", "verify"]

    gen, retry, check = provider.calls
    assert gen.course_tools.folder == "CMU/ML" and gen.model == "opus" and gen.json_schema["required"] == ["title", "questions"]
    assert "共 **2 道题**" in gen.prompt and "L1 第 1 页（缩放点积）：2 个问题" in gen.prompt
    assert "L1（CMU/ML 第 1 讲：Attention）" in gen.prompt and "（无）" in gen.prompt  # no feedback yet
    assert "You followed up twice" in gen.prompt  # the `why` example is in the output language
    assert "要求 2 道题，实际给了 3 道" in retry.prompt and "第 3 题：和第 2 题重复" in retry.prompt
    assert "标准答案：A" in check.prompt and "### 第 2 题（short，出处：L1 第 1 页）" in check.prompt
    assert str(gen.cwd).endswith(f"scopes/{gen.cwd.name}") and gen.cwd.is_dir()

    listing = client.get("/api/library/quizzes", params={"folder": "CMU/ML"}).json()
    assert listing["job"] is None and listing["quizzes"][0]["count"] == 2
    assert client.get("/api/library", params={"path": "CMU/ML"}).json()["has_quizzes"]
    quiz = client.get(f"/api/library/quizzes/{done['quiz_id']}", params={"folder": "CMU/ML"}).json()
    q1, q2 = quiz["questions"]
    assert q1["review"] is None and q2["review"] == "课件没说"
    assert q1["sources"][0]["lecture"] == cid and q1["sources"][0]["file"] == str(root / "CMU" / "ML" / "b.gloss")

    a = client.post(f"/api/library/quizzes/{quiz['id']}/attempts", params={"folder": "CMU/ML"},
                    json={"question_id": q1["id"], "answer": "A", "correct": True})
    assert a.json()["correct"] == 1
    assert client.get("/api/library/quizzes", params={"folder": "CMU/ML"}).json()["quizzes"][0]["correct"] == 1
    assert client.post(f"/api/library/quizzes/{quiz['id']}/attempts", params={"folder": "CMU/ML"},
                       json={"question_id": "zz"}).status_code == 404
    client.delete(f"/api/library/quizzes/{quiz['id']}", params={"folder": "CMU/ML"})
    assert client.get("/api/library/quizzes", params={"folder": "CMU/ML"}).json()["quizzes"] == []


def test_api_group_scope_and_validation(app):
    client, provider, root, cid = app
    provider.structured = [{"title": "", "questions": [q(lecture="L3", page=1)]}]
    r = client.post("/api/library/quizzes", json={"folder": "CMU", "count": 1, "types": ["mcq"], "focus": "uncovered"})
    events = sse(client, r.json()["job"]["id"])
    assert events[-1]["type"] == "done", events  # quiz_verify on: the verify call has no structured data left…
    quiz = client.get(f"/api/library/quizzes/{events[-1]['quiz_id']}", params={"folder": "CMU"}).json()
    assert quiz["title"] == "Quiz" and quiz["questions"][0]["sources"][0]["course"] == "CMU/Stats"
    assert any(e.get("phase") == "verify_failed" for e in events)  # …so the check fails and the quiz is kept
    assert "主要考学生还没问过" in provider.calls[0].prompt

    for body, code in [({"folder": "CMU", "count": 0}, 422), ({"folder": "CMU", "lectures": ["CMU/x.gloss"]}, 422), ({"folder": "CMU", "types": ["essay"]}, 422),
                       ({"folder": "CMU", "focus": "x"}, 422), ({"folder": "nope"}, 404),
                       ({"folder": "../x"}, 422)]:
        assert client.post("/api/library/quizzes", json=body).status_code == code, body


def test_api_unusable_output_is_an_error(app):
    client, provider, *_ = app
    provider.structured = [{"questions": []}, {"questions": []}]
    r = client.post("/api/library/quizzes", json={"folder": "CMU/ML", "count": 3})
    events = sse(client, r.json()["job"]["id"])
    assert events[-1]["type"] == "error" and "usable" in events[-1]["message"]


def test_api_lectures_by_path_for_never_opened_files(app):
    client, provider, root, cid = app
    with __import__("gloss.store", fromlist=["Course"]).Course.create(root / "CMU" / "ML" / "c.gloss", "New") as c:
        c.replace_slides([__import__("gloss.store", fromlist=["Slide"]).Slide(1, "fresh", b"x", [])])
        c._conn.execute("DELETE FROM meta WHERE key = 'course_id'")  # never opened by the app
    provider.structured = [{"title": "t", "questions": [q(lecture="L3", page=1)]}]
    r = client.post("/api/library/quizzes", json={"folder": "CMU/ML", "count": 1, "types": ["mcq"],
                                                  "lectures": ["CMU/ML/c.gloss"]})
    assert r.status_code == 200, r.text
    events = sse(client, r.json()["job"]["id"])
    assert events[-1]["type"] == "done", events
    assert provider.calls[0].course_tools.only and "L3（CMU/ML 第 3 讲：New）" in provider.calls[0].prompt
    assert "Attention" not in provider.calls[0].prompt.split("## 学生卡住过")[0]


def test_latest_attempts_read_only(tmp_path):
    st = QuizStore(tmp_path)
    assert st.latest_attempts() == []
    qid = st.create("Q", {}, None, [{"stem": "a", "topic": "t", "sources": [{"lecture": "c1", "page": 2}]},
                                    {"stem": "b", "topic": "u", "sources": []}])
    q1, q2 = (x["id"] for x in st.get(qid)["questions"])
    st.add_attempt(q1, "A", False)
    st.add_attempt(q1, "B", True)
    st.add_attempt(q2, "", None)
    got = {a["question_id"]: a for a in st.latest_attempts()}
    assert got[q1]["correct"] == 1 and got[q1]["topic"] == "t" and got[q1]["sources"] == [{"lecture": "c1", "page": 2}]
    assert got[q2]["correct"] is None
