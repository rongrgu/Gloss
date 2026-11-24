import pytest
from fakes import FakeProvider
from fastapi.testclient import TestClient
from test_server import import_deck, sse

from gloss.config import REPO_ROOT, Config
from gloss.digest import clean, digest_problems, handles, render, threads_text
from gloss.export import export_markdown
from gloss.quiz import QuizStore
from gloss.server import create_app
from gloss.store import Course, Slide


@pytest.fixture
def course(tmp_path):
    (tmp_path / "lib" / "ML").mkdir(parents=True)
    c = Course.create(tmp_path / "lib" / "ML" / "l.gloss", "Attention")
    c.replace_slides([Slide(p, f"slide {p} text", b"img", [{"x": 0, "y": 0, "w": 1, "h": 0.5, "text": f"formula {p}"}])
                      for p in (1, 2, 3)])
    yield c
    c.close()


def ask(c, page, q, a=None, follow=None, understood=None):
    t = c.create_thread(page, (0, 0, 1, 0.5))
    c.add_message(t.id, "user", q)
    if a:
        c.add_message(t.id, "assistant", a)
    if follow:
        c.add_message(t.id, "user", follow)
    if understood is not None:
        c.set_thread_understood(t.id, understood)
    return c.get_thread(t.id)


def test_handles_and_threads_text(course):
    t2 = ask(course, 2, "why sqrt d?", "variance", follow="and if d small?", understood=False)
    t1 = ask(course, 1, "what is Q?", "query" * 300)
    hs = handles([t2, t1])
    assert list(hs) == ["T1", "T2"] and hs["T1"].id == t1.id  # page order, not creation order
    text = threads_text(course, hs, watermark=0)
    assert "### [T2] 第 2 页 · 问题 1（学生标了：还没懂）" in text
    assert "(student marked: still confused)" in threads_text(course, hs, 0, "English") and "学生追问（新）：and if d small?" in text
    assert "> formula 2" in text  # what the box covered
    assert "…" in text and "query" * 161 not in text  # answers are cut
    wm = course.max_message_id()
    ask(course, 3, "new one")
    assert "（新）" not in threads_text(course, handles([t1, t2]), wm)


def test_budget_keeps_confused_first(course, monkeypatch):
    import gloss.digest as d
    monkeypatch.setattr(d, "THREADS_BUDGET", 120)
    ask(course, 1, "plain " * 30)
    c2 = ask(course, 3, "confused?", understood=False)
    text = threads_text(course, handles(course.list_threads(1) + course.list_threads(3)), 0)
    assert c2 and "confused?" in text and "plain" not in text and "还有 1 个问题" in text


def test_clean_keeps_only_supported_claims(course):
    t1 = ask(course, 1, "q1")
    t2 = ask(course, 2, "q2")
    hs = handles([t1, t2])
    raw = {
        "covered": [{"topic": "A", "summary": "s", "pages": [1, 9]}, {"topic": "B", "summary": "s", "pages": [42]}],
        "questions": [{"concept": "c", "summary": "s", "threads": ["T1", "[t2]", "T9"]}],
        "weak": [{"concept": "w", "why": "y", "threads": ["T9"], "known": None},  # no real evidence: dropped
                 {"concept": "w2", "why": "y", "threads": ["T2"], "known": "W7"}],
        "understood": [{"concept": "u", "threads": []}],
        "open": [{"question": "o", "threads": ["T1"]}],
        "review": [{"page": p, "why": "r"} for p in (1, 1, 2, 3, 9, 0)],
    }
    assert digest_problems(raw) == []
    out = clean(raw, hs, 3, known_ids={"W3"})
    assert out["covered"] == [{"topic": "A", "summary": "s", "pages": [1]}]
    assert out["questions"][0]["threads"] == [t1.id, t2.id]
    assert [w["concept"] for w in out["weak"]] == ["w2"] and out["weak"][0]["known"] is None  # W7 isn't real
    assert out["understood"] == [] and out["open"][0]["threads"] == [t1.id]
    assert [r["page"] for r in out["review"]] == [1, 2, 3]
    assert digest_problems({"covered": []})[0].startswith("缺少")
    assert "threads" in digest_problems({**raw, "weak": [{"concept": "x"}]})[0]


def test_render_links(course):
    t = ask(course, 2, "q")
    data = {"covered": [{"topic": "A", "summary": "s", "pages": [1, 2]}], "questions": [],
            "weak": [{"concept": "w", "why": "y", "threads": [t.id], "known": None}],
            "understood": [], "open": [], "review": [{"page": 2, "why": "r"}]}
    en = render(data, course, "en")
    assert f"[slide 2 #1](gloss:{course.id}/2/{t.id})" in en and f"[Slide 1](gloss:{course.id}/1)" in en
    assert "## Now understood\n\n(none)" in en
    assert "## 还没弄懂的" in render(data, course, "zh")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("GLOSS_HOME", str(tmp_path / "home"))
    ws = tmp_path / "ws"
    (ws / "prompts").mkdir(parents=True)
    src = REPO_ROOT / "claude_workspace"
    for p in [src / "CLAUDE.md", *(src / "prompts").glob("*.md")]:
        (ws / p.relative_to(src)).write_text(p.read_text())
    provider = FakeProvider()
    provider.supports_tools = False  # like anthropic_api: summaries must not need tools
    cfg = Config(workspace=ws, library=tmp_path / "lib", model="fake", digest_model="opus")
    with TestClient(create_app(cfg, provider)) as client:
        yield client, provider, tmp_path


def out_for(tid_handle="T1"):
    return {"covered": [{"topic": "Attention", "summary": "s", "pages": [1]}],
            "questions": [{"concept": "scaling", "summary": "why sqrt", "threads": [tid_handle]}],
            "weak": [{"concept": "scaling", "why": "follow-ups", "threads": [tid_handle], "known": None}],
            "understood": [], "open": [], "review": [{"page": 2, "why": "formula"}]}


def test_api_digest_lifecycle(app):
    client, provider, tmp = app
    c = import_deck(client)
    cid = c["id"]
    assert c["lecture"]["status"] == "studying" and c["lecture"]["digest_at"] is None  # opened = studying
    assert client.get(f"/api/courses/{cid}/digest").status_code == 404
    th = client.post(f"/api/courses/{cid}/slides/2/threads", json={"rect": [0.1, 0.1, 0.5, 0.3], "question": "why?"}).json()
    sse(client, th["job"]["id"])

    provider.structured = [{"covered": []}, out_for(), {"ops": []}]  # malformed first: one retry with feedback
    ev = sse(client, client.post(f"/api/courses/{cid}/digest").json()["job"]["id"])
    assert ev[-1]["type"] == "done", ev
    first, retry = [x for x in provider.calls if x.prompt.startswith("学生学完了")][-2:]
    assert first.tools == ("Read",) and first.course_tools is None and first.model == "opus"
    assert "[T1] 第 2 页 · 问题 1" in first.prompt and "学生（新）：why?" in first.prompt
    assert "缺少 questions" in retry.prompt
    d = client.get(f"/api/courses/{cid}/digest").json()
    assert d["data"]["weak"][0]["threads"] == [th["thread"]["id"]] and d["new_messages"] == 0
    assert f"gloss:{cid}/2/{th['thread']['id']}" in d["content"]
    lec = client.get(f"/api/courses/{cid}").json()["lecture"]
    assert lec["status"] == "done" and lec["new_since_digest"] == 0

    # a new follow-up after the summary: "N new" shows up; the next summary is incremental
    sse(client, client.post(f"/api/courses/{cid}/threads/{th['thread']['id']}/messages", json={"text": "more?"}).json()["job"]["id"])
    assert client.get(f"/api/courses/{cid}").json()["lecture"]["new_since_digest"] == 2
    listing = client.get("/api/library", params={"path": ""}).json()
    assert listing["lectures"][0]["status"] == "done" and listing["lectures"][0]["new_since_digest"] == 2
    provider.structured = [out_for(), {"ops": []}]
    sse(client, client.post(f"/api/courses/{cid}/digest").json()["job"]["id"])
    again = [x for x in provider.calls if x.prompt.startswith("学生学完了")][-1].prompt
    prev = again.split("## 上一次的总结")[1].split("## 已知的薄弱点")[0]
    assert '"T1"' in prev and th["thread"]["id"] not in prev  # the previous summary, with ids turned back into handles
    assert "学生（新）" not in again and "学生追问（新）：more?" in again

    # export carries the summary, with app-only links made plain
    md = client.get(f"/api/courses/{cid}/export.md").text
    assert "## Study summary" in md and "gloss:" not in md


def test_api_digest_malformed_twice_is_an_error(app):
    client, provider, _ = app
    cid = import_deck(client)["id"]
    provider.structured = [{}, {}]
    ev = sse(client, client.post(f"/api/courses/{cid}/digest").json()["job"]["id"])
    assert ev[-1]["type"] == "error"
    assert client.get(f"/api/courses/{cid}").json()["lecture"]["status"] == "studying"


def test_quiz_answers_reach_the_prompt(app):
    client, provider, tmp = app
    c = import_deck(client)
    st = QuizStore(tmp / "lib")  # a quiz in the folder above (here: the library itself)
    qid = st.create("q", {}, None, [{"stem": "What does sqrt(d) fix?", "topic": "scaling",
                                     "sources": [{"lecture": c["id"], "page": 2}]}])
    st.add_attempt(st.get(qid)["questions"][0]["id"], "B", False)
    provider.structured = [out_for(), {"ops": []}]
    sse(client, client.post(f"/api/courses/{c['id']}/digest").json()["job"]["id"])
    digest_call = next(x for x in provider.calls if x.prompt.startswith("学生学完了"))
    assert "- 答错：scaling（第 2 页）— What does sqrt(d) fix?" in digest_call.prompt


def test_export_without_digest_unchanged(course):
    assert "Study summary" not in export_markdown(course, "en")
