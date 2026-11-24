import pytest
from fakes import FakeProvider
from fastapi.testclient import TestClient
from test_course_tools import library  # noqa: F401  (fixture)
from test_server import sse

from gloss.config import REPO_ROOT, Config
from gloss.course_tools import Scope
from gloss.profile import MAX_EVIDENCE, ProfileError, ProfileStore, apply_ops, findings, on_mark, on_quiz_answer
from gloss.server import create_app


def ev(page=2, pol=1, kind="thread", ref="t1", lecture="c1"):
    return {"kind": kind, "lecture": lecture, "page": page, "ref": ref, "polarity": pol, "note": "n"}


def seed(store, concept="head dim", page=2, ref="t1"):
    with store.change("digest") as ed:
        wid = ed.add(concept, "s")
        ed.attach(wid, ev(page, ref=ref))
    return wid


def test_ids_never_reused_and_evidence_capped(tmp_path):
    st = ProfileStore(tmp_path)
    assert st.weak_points() == [] and not st.path.exists()
    a, b = seed(st), seed(st, "masking")
    assert (a, b) == ("W1", "W2")
    with st.change("manual") as ed:
        ed.delete("W2")
    assert seed(st, "again") == "W3"
    with st.change("quiz") as ed:
        for i in range(MAX_EVIDENCE + 3):
            ed.attach("W1", ev(ref=f"r{i}"))
    w1 = next(w for w in st.weak_points() if w["id"] == "W1")
    assert len(w1["evidence"]) == MAX_EVIDENCE and w1["extra_evidence"] == 4  # 1 + 11 - 8


def test_apply_ops_validates_everything(tmp_path):
    st = ProfileStore(tmp_path)
    seed(st)
    seed(st, "masking", page=4, ref="t9")
    bundles = {"F1": [ev(3, ref="t3")], "F2": [ev(2, pol=-1, ref="t1")]}
    pending = {"P1": {**ev(5, kind="quiz", ref="q1"), "id": 1}}
    ops = [
        {"op": "add", "concept": "softmax saturation", "summary": "s", "evidence": ["f1"]},  # handles: any case
        {"op": "add", "concept": "no evidence", "evidence": ["F9"]},
        {"op": "add", "concept": "only understanding", "evidence": ["F2"]},  # an add needs a difficulty
        {"op": "attach", "id": "W1", "evidence": ["P1"]},
        {"op": "attach", "id": "W77", "evidence": ["F1"]},
        {"op": "status", "id": "W2", "to": "resolved", "reason": "?"},  # no evidence of understanding
        {"op": "status", "id": "W1", "to": "resolved", "reason": "got it", "evidence": ["F2"]},
        {"op": "merge", "from": "W2", "into": "W2"},
        {"op": "edit", "id": "W1", "concept": "per-head dimension"},
        {"op": "teleport"},
    ]
    with st.change("digest") as ed:
        skipped = apply_ops(ed, ops, bundles, pending)
    assert len(skipped) == 6 and any("needs evidence of understanding" in x for x in skipped)
    wps = {w["id"]: w for w in st.weak_points()}
    assert wps["W3"]["concept"] == "softmax saturation" and wps["W3"]["status"] == "open"
    assert wps["W1"]["status"] == "resolved" and wps["W1"]["concept"] == "per-head dimension"
    assert wps["W2"]["status"] == "open"
    assert st.pending() == []  # P1 was used, so it left pending
    assert st.revisions()[0]["trigger"] == "digest"


def test_pinned_status_is_not_changed_automatically(tmp_path):
    st = ProfileStore(tmp_path)
    seed(st)
    with st.change("manual") as ed:
        ed.set_status("W1", "resolved", "me", pin=True)
    with st.change("organize") as ed:
        skipped = apply_ops(ed, [{"op": "status", "id": "W1", "to": "open", "reason": "x"}], {}, {})
    assert skipped and st.weak_points()[0]["status"] == "resolved"
    on_quiz_answer(tmp_path, {"correct": False, "question_id": "q", "sources": [{"lecture": "c1", "page": 2}]})
    w = st.weak_points()[0]
    assert w["status"] == "resolved" and w["evidence"][-1]["kind"] == "quiz"  # evidence still attaches


def test_quiz_rules(tmp_path):
    st = ProfileStore(tmp_path)
    seed(st)
    ans = lambda ok, page=2: on_quiz_answer(tmp_path, {"correct": ok, "question_id": "q", "topic": "heads",  # noqa: E731
                                                       "stem": "dim?", "sources": [{"lecture": "c1", "page": page}]})
    status = lambda: st.weak_points()[0]["status"]  # noqa: E731
    ans(True)
    assert status() == "open"
    ans(True)
    assert status() == "improving"  # two right in a row
    ans(True)
    assert status() == "resolved"
    ans(False)
    assert status() == "open"  # a wrong answer reopens it
    ans(False, page=7)  # nothing on that page yet: waits for Claude to sort it
    assert [p["page"] for p in st.pending()] == [7] and "heads" in st.pending()[0]["note"]
    ans(True, page=8)  # a right answer with nothing to attach to is simply ignored
    assert len(st.pending()) == 1
    ans(None)


def test_mark_rules_and_undo(tmp_path):
    st = ProfileStore(tmp_path)
    seed(st, ref="t1")
    with st.change("digest") as ed:
        ed.attach("W1", ev(3, ref="t2"))
    on_mark(tmp_path, "c1", 2, "t1", True)
    assert st.weak_points()[0]["status"] == "improving"
    on_mark(tmp_path, "c1", 2, "other-thread", True)  # not evidence for anything: no change
    on_mark(tmp_path, "c1", 2, "t1", True)  # the same mark again is not new evidence
    assert st.weak_points()[0]["status"] == "improving"
    on_mark(tmp_path, "c1", 3, "t2", True)  # a second question understood
    assert st.weak_points()[0]["status"] == "resolved"
    on_mark(tmp_path, "c1", 2, "t1", None)  # clearing a mark changes nothing
    on_mark(tmp_path, "c1", 2, "t1", False)
    assert st.weak_points()[0]["status"] == "open"
    assert st.undo()["trigger"] == "mark"
    assert st.weak_points()[0]["status"] == "resolved"  # back to before the last automatic change
    with st.change("manual") as ed:
        ed.edit("W1", "renamed", None)
    with pytest.raises(ProfileError):
        st.undo()  # the latest change was by hand: undoing the automatic one would clobber it


def test_findings_bundle_threads():
    digest = {"weak": [{"concept": "w", "why": "y", "threads": ["a", "b", "zz"], "known": "W1"}],
              "open": [{"question": "o", "threads": ["a"]}], "understood": [{"concept": "u", "threads": ["b"]}]}
    f = findings(digest, "c1", {"a": 2, "b": 3})
    assert [(x["handle"], x["kind"], len(x["evidence"])) for x in f] == [("F1", "weak", 2), ("F2", "open", 1),
                                                                         ("F3", "understood", 1)]
    assert f[2]["evidence"][0]["polarity"] == -1 and f[0]["known"] == "W1"


def test_scope_profile_text_and_weights(library):  # noqa: F811
    root, cid = library
    st = ProfileStore(root / "CMU" / "ML")
    with st.change("digest") as ed:
        w = ed.add("多头的维度", "每个头是 d/h 还是 d")
        ed.attach(w, ev(2, lecture=cid, ref="x"))
        r = ed.add("旧问题", "已经懂了")
        ed.attach(r, ev(3, pol=-1, lecture=cid, ref="y"))
        ed.set_status(r, "resolved")
    with Scope(root, "CMU") as s:
        pages = {(x.lecture, x.page): x for x in s.study_profile()}
        assert pages[("L1", 2)].weak_points == 1 and pages[("L1", 2)].score == 3
        assert ("L1", 3) not in pages  # a note (+1) and resolved-only evidence (−2)
        text = s.study_profile_text()
        assert text.startswith("### 概念级薄弱点（学习档案）\n- W1（未解决）多头的维度：每个头是 d/h 还是 d（L1 第 2 页）")
        assert "旧问题" not in text and "### 按页" in text


# --- API ---

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
    with TestClient(create_app(Config(workspace=ws, library=root, model="fake"), provider)) as client:
        yield client, provider, root, cid


def test_api_summary_updates_profile_then_student_edits(app):
    client, provider, root, cid = app
    c = client.post("/api/courses/open", json={"path": str(root / "CMU" / "ML" / "b.gloss")}).json()
    digest = {"covered": [], "questions": [], "understood": [], "open": [], "review": [],
              "weak": [{"concept": "scaling", "why": "follow-ups", "threads": ["T1"], "known": None}]}
    provider.structured = [digest, {"ops": [{"op": "add", "concept": "Why √d", "summary": "unit variance", "evidence": ["F1"]}]}]
    ev_ = sse(client, client.post(f"/api/courses/{c['id']}/digest").json()["job"]["id"])
    assert ev_[-1]["type"] == "done" and ev_[-1]["profile"]["changes"][0]["op"] == "add", ev_
    upd = provider.calls[-1].prompt
    assert "- F1 [weak] scaling：follow-ups（第 1 页）" in upd and "（还没有未解决的薄弱点）" in upd
    prof = client.get("/api/library/profile", params={"folder": "CMU/ML"}).json()
    (w,) = prof["weak_points"]
    assert w["id"] == "W1" and w["evidence"][0]["lecture"] == cid and prof["lectures"][cid]["number"] == 1
    assert client.get("/api/library", params={"path": "CMU/ML"}).json()["has_study"]

    # the next summary names it, so the same concept keeps the same id
    provider.structured = [digest, {"ops": []}]
    sse(client, client.post(f"/api/courses/{c['id']}/digest").json()["job"]["id"])
    assert "- W1（未解决）Why √d：unit variance（证据 1 条" in [x for x in provider.calls if x.prompt.startswith("学生学完了")][-1].prompt

    # a wrong quiz answer on the same page attaches; on another page it waits in pending
    from gloss.quiz import QuizStore
    qs = QuizStore(root / "CMU" / "ML")
    qid = qs.create("q", {}, None, [{"stem": "s", "topic": "scaling", "sources": [{"lecture": cid, "page": 1}]},
                                    {"stem": "s2", "topic": "heads", "sources": [{"lecture": cid, "page": 2}]}])
    q1, q2 = (x["id"] for x in qs.get(qid)["questions"])
    for q in (q1, q2):
        client.post(f"/api/library/quizzes/{qid}/attempts", params={"folder": "CMU/ML"},
                    json={"question_id": q, "answer": "A", "correct": False})
    prof = client.get("/api/library/profile", params={"folder": "CMU/ML"}).json()
    assert prof["weak_points"][0]["evidence"][-1]["kind"] == "quiz" and [p["page"] for p in prof["pending"]] == [2]

    # organize sorts pending with one call
    provider.structured = [{"ops": [{"op": "add", "concept": "Head size", "summary": "d/h", "evidence": ["P1"]}]}]
    ev_ = sse(client, client.post("/api/library/profile/organize", params={"folder": "CMU/ML"}).json()["job"]["id"])
    assert ev_[-1]["type"] == "done"
    prof = client.get("/api/library/profile", params={"folder": "CMU/ML"}).json()
    assert [w["id"] for w in prof["weak_points"]] == ["W1", "W2"] and prof["pending"] == []

    # marking the question "got it" moves W1 along; the student can override, merge, delete, undo
    tid = next(t["id"] for t in client.get(f"/api/courses/{c['id']}/slides/1").json()["threads"] if t["n"] == 1)
    client.patch(f"/api/courses/{c['id']}/threads/{tid}", json={"understood": True})
    assert client.get("/api/library/profile", params={"folder": "CMU/ML"}).json()["weak_points"][0]["status"] == "improving"
    prof = client.post("/api/library/profile/undo", params={"folder": "CMU/ML"}).json()
    assert prof["weak_points"][0]["status"] == "open"
    prof = client.patch("/api/library/profile/W1", params={"folder": "CMU/ML"}, json={"status": "resolved"}).json()
    assert prof["weak_points"][0]["status"] == "resolved" and prof["weak_points"][0]["pinned"]
    assert client.post("/api/library/profile/undo", params={"folder": "CMU/ML"}).status_code == 409
    prof = client.post("/api/library/profile/W2/merge", params={"folder": "CMU/ML"}, json={"into": "W1"}).json()
    assert [w["id"] for w in prof["weak_points"]] == ["W1"] and len(prof["weak_points"][0]["evidence"]) == 3
    prof = client.delete("/api/library/profile/W1", params={"folder": "CMU/ML"}).json()
    assert prof["weak_points"] == []
    assert client.patch("/api/library/profile/W1", params={"folder": "CMU/ML"}, json={"status": "open"}).status_code == 404


def test_api_update_asks_to_merge_when_too_many(app):
    client, provider, root, cid = app
    st = ProfileStore(root / "CMU" / "ML")
    for i in range(16):
        seed(st, f"concept {i}", page=1, ref=f"t{i}")
    on_quiz_answer(root / "CMU" / "ML", {"correct": False, "question_id": "q", "sources": [{"lecture": cid, "page": 9}]})
    provider.structured = [{"ops": []}]
    sse(client, client.post("/api/library/profile/organize", params={"folder": "CMU/ML"}).json()["job"]["id"])
    assert "超过了 15 条。**先用 merge 合并相近的概念**" in provider.calls[-1].prompt


def test_moved_lecture_evidence_still_resolves(app):
    client, provider, root, cid = app
    st = ProfileStore(root / "CMU" / "ML")
    with st.change("digest") as ed:
        ed.attach(ed.add("x", "y"), ev(1, lecture=cid))
    client.post("/api/library/move", json={"path": "CMU/ML/b.gloss", "to": "CMU/Stats"})
    # the evidence names the lecture by id, which still finds the file in its new course
    files = {i["course_id"]: i for i in client.get("/api/library", params={"path": "CMU/Stats"}).json()["lectures"]}
    assert cid in files and st.weak_points()[0]["evidence"][0]["lecture"] == cid


def test_resummarizing_does_not_duplicate_evidence(tmp_path):
    st = ProfileStore(tmp_path)
    seed(st, ref="t1")
    bundles = {"F1": [ev(2, ref="t1"), ev(3, ref="t2")]}
    for _ in range(3):
        with st.change("digest") as ed:
            apply_ops(ed, [{"op": "attach", "id": "W1", "evidence": ["F1"]}], bundles, {})
    assert sorted(e["ref"] for e in st.weak_points()[0]["evidence"]) == ["t1", "t2"]
    assert len([r for r in st.revisions() if r["changes"]]) == 2  # the no-op repeats logged nothing


def test_resolved_names_reach_both_prompts(app):
    client, provider, root, cid = app
    st = ProfileStore(root / "CMU" / "ML")
    with st.change("manual") as ed:
        w = ed.add("Per-head dimension", "d/h")
        ed.attach(w, ev(1, lecture=cid, ref="x"))
        ed.set_status(w, "resolved", "me", pin=True)
    c = client.post("/api/courses/open", json={"path": str(root / "CMU" / "ML" / "b.gloss")}).json()
    digest = {"covered": [], "questions": [], "understood": [], "open": [], "review": [],
              "weak": [{"concept": "head size", "why": "asked again", "threads": ["T1"], "known": "W1"}]}
    provider.structured = [digest, {"ops": []}]
    sse(client, client.post(f"/api/courses/{c['id']}/digest").json()["job"]["id"])
    summary_call, update_call = provider.calls[-2:]
    assert "- W1（已解决，学生确认）Per-head dimension" in summary_call.prompt
    assert "- W1（已解决，学生确认）Per-head dimension" in update_call.prompt
    assert "（总结认为它就是 W1）" in update_call.prompt  # a resolved id is a valid `known`


# --- M11: weak points in explanations and Q&A (§15.5) -------------------------------------------------

def test_relevant_order_and_limits(app):
    client, provider, root, cid = app
    from gloss.course_tools import LectureReader
    other = LectureReader(root / "CMU" / "ML" / "a.gloss").course_id  # lecture 2 in this course
    st = ProfileStore(root / "CMU" / "ML")
    with st.change("digest") as ed:
        for concept, lec, page, n in [("far away", "elsewhere", 1, 5), ("later lecture", other, 1, 1),
                                      ("this lecture, other page", cid, 3, 1), ("this page", cid, 1, 1)]:
            w = ed.add(concept, "s" * 10)
            for i in range(n):
                ed.attach(w, ev(page, lecture=lec, ref=f"{concept}{i}"))
        ed.set_status(ed.add("resolved one", "s"), "resolved")
    from gloss.store import Course
    with Course.open(root / "CMU" / "ML" / "b.gloss") as c:  # lecture 1
        text = client.app.state.tutor.weak_points(c, 1)
    lines = [x for x in text.splitlines() if x.startswith("- ")]
    assert text.startswith("## 学生之前卡住过的地方（学习档案）") and "确实相关" in text
    assert [x.split("：")[0] for x in lines] == ["- this page", "- this lecture, other page", "- far away"]
    with Course.open(root / "CMU" / "ML" / "a.gloss") as c:  # lecture 2: lecture 1 counts as earlier
        lines = [x for x in client.app.state.tutor.weak_points(c, 1).splitlines() if x.startswith("- ")]
    assert lines[0].startswith("- later lecture") and lines[1].startswith("- this")


def test_relevant_text_is_capped(app):
    client, provider, root, cid = app
    st = ProfileStore(root / "CMU" / "ML")
    with st.change("digest") as ed:
        for i in range(3):
            ed.attach(ed.add(f"c{i}", "x" * 200), ev(1, lecture=cid, ref=f"r{i}"))
    from gloss.store import Course
    with Course.open(root / "CMU" / "ML" / "b.gloss") as c:
        lines = [x for x in client.app.state.tutor.weak_points(c, 1).splitlines() if x.startswith("- ")]
    assert len(lines) == 1  # a second 200-char line would pass ~300 characters


def test_weak_points_reach_explanation_and_qa_opening(app):
    client, provider, root, cid = app
    st = ProfileStore(root / "CMU" / "ML")
    with st.change("digest") as ed:
        ed.attach(ed.add("Why √d", "unit variance"), ev(2, lecture=cid, ref="t"))
    c = client.post("/api/courses/open", json={"path": str(root / "CMU" / "ML" / "b.gloss")}).json()
    sse(client, client.post(f"/api/courses/{c['id']}/slides/2/explanation?regenerate=true").json()["job"]["id"])
    assert "## 学生之前卡住过的地方（学习档案）" in provider.calls[-1].prompt and "- Why √d：unit variance" in provider.calls[-1].prompt
    course = client.app.state.library.get(c["id"])
    assert "- Why √d：unit variance" in client.app.state.tutor._opening(course, 2)  # a new page session's opening


def test_no_profile_or_switched_off_leaves_prompts_unchanged(tmp_path, monkeypatch, library):  # noqa: F811
    root, cid = library
    monkeypatch.setenv("GLOSS_HOME", str(tmp_path / "home"))
    ws = tmp_path / "ws"
    (ws / "prompts").mkdir(parents=True)
    src = REPO_ROOT / "claude_workspace"
    for p in [src / "CLAUDE.md", *(src / "prompts").glob("*.md")]:
        (ws / p.relative_to(src)).write_text(p.read_text())
    for use_profile in (True, False):
        provider = FakeProvider()
        with TestClient(create_app(Config(workspace=ws, library=root, model="fake", use_profile=use_profile), provider)) as cl:
            c = cl.post("/api/courses/open", json={"path": str(root / "CMU" / "ML" / "b.gloss")}).json()
            sse(cl, cl.post(f"/api/courses/{c['id']}/slides/3/explanation?regenerate=true").json()["job"]["id"])
            assert "学习档案" not in provider.calls[-1].prompt, use_profile
        if use_profile:  # now give it a profile, then switch the feature off
            with ProfileStore(root / "CMU" / "ML").change("digest") as ed:
                ed.attach(ed.add("x", "y"), ev(3, lecture=cid, ref="t"))
