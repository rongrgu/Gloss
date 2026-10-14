import asyncio

import pytest
from fakes import FakeProvider
from make_pdfs import make_deck

from gloss.config import REPO_ROOT
from gloss.ingest import ingest_pdf
from gloss.jobs import JobManager
from gloss.store import Course
from gloss.tutor import Prompts, Tutor, covered_text, crop_png

WORKSPACE = REPO_ROOT / "claude_workspace"


@pytest.fixture
def setup(tmp_path):
    ws = tmp_path / "ws"
    # Real prompt templates, scratch dir under tmp.
    (ws / "prompts").mkdir(parents=True)
    for f in ["CLAUDE.md", *[f"prompts/{p.name}" for p in (WORKSPACE / "prompts").glob("*.md")]]:
        (ws / f).write_text((WORKSPACE / f).read_text())
    course = Course.create(tmp_path / "c.gloss", "Attention")
    ingest_pdf(course, make_deck(), long_side=800)
    yield course, ws
    course.close()


def run_tutor(setup, provider, steps):
    """Run `steps(tutor, course)` (a coroutine fn) inside a loop with a fresh JobManager."""
    course, ws = setup

    async def go():
        tutor = Tutor(provider, JobManager(2), ws, language="中文")
        return await steps(tutor, course)

    return asyncio.run(go())


async def finish(job):
    return [e async for e in job.subscribe()]


def test_prompts_render_and_strip_comment(setup):
    _, ws = setup
    p = Prompts(ws)
    assert not p.role().startswith("<!--")
    out = p.render("qa-opening", course_title="T", page=2, page_count=3, text="", transcript=None, explanation="E")
    assert "第 2 / 3 页" in out and "（无）" in out and "{{" not in out
    with pytest.raises(KeyError, match="explanation"):
        p.render("qa-opening", course_title="T", page=2, page_count=3, text="", transcript="")


def test_explanation_generated_once(setup):
    fp = FakeProvider(reply="这一页讲缩放点积。")

    async def steps(tutor, course):
        job = tutor.explain(course, 2)
        evs = await finish(job)
        again = tutor.explain(course, 2)
        return evs, again, course.latest_explanation(2)

    evs, again, e = run_tutor(setup, fp, steps)
    assert [ev["type"] for ev in evs] == ["queued", "start", "delta", "delta", "done"]
    assert again is None and e.content == "这一页讲缩放点积。" and e.model == "fake-model"
    (call,) = fp.calls
    assert call.session is None and "助教" in call.system
    assert "第 2 / 3 页" in call.prompt
    assert "Scaled dot-product" in call.prompt  # this page
    assert "Attention" in call.prompt and "Multi-head" in call.prompt  # neighbours
    assert call.images[0].name == "page-2.png"


def test_regenerate_adds_version(setup):
    fp = FakeProvider()

    async def steps(tutor, course):
        await finish(tutor.explain(course, 1))
        fp.reply = "v2"
        await finish(tutor.explain(course, 1, regenerate=True))
        return course.latest_explanation(1).content

    assert run_tutor(setup, fp, steps) == "v2"


def test_first_question_opens_session(setup):
    fp = FakeProvider(reply="因为方差。")
    rect = (0.0, 0.25, 1.0, 0.3)  # covers the formula line

    async def steps(tutor, course):
        course.add_explanation(2, "已有讲解")
        thread, msg, job = tutor.ask(course, 2, rect, "为什么除以根号 d？")
        evs = await finish(job)
        return thread, evs, course

    thread, evs, course = run_tutor(setup, fp, steps)
    assert thread.n == 1 and thread.crop.startswith(b"\x89PNG")
    assert evs[-1]["message"]["content"] == "因为方差。"
    assert [m.role for m in course.list_messages(thread.id)] == ["user", "assistant"]
    (call,) = fp.calls
    assert call.session.new is True
    assert course.get_session(2) == call.session.id
    assert "问答约定" in call.system and "助教" in call.system
    p = call.prompt
    assert "已有讲解" in p  # opening includes the explanation
    assert "[Thread #1 · 选区 x=0.00 y=0.25 w=1.00 h=0.30 · 新问题]" in p
    assert "> softmax(QK^T / sqrt(d)) V" in p  # covered text
    assert p.rstrip().endswith("为什么除以根号 d？")
    assert [i.name for i in call.images] == ["page-2.png", f"crop-{thread.id}.png"]


def test_follow_up_and_second_thread_resume(setup):
    fp = FakeProvider()

    async def steps(tutor, course):
        t1, _, j = tutor.ask(course, 2, (0, 0, 0.5, 0.5), "q1")
        await finish(j)
        _, j = tutor.follow_up(course, t1.id, "追问一下")
        await finish(j)
        t2, _, j = tutor.ask(course, 2, (0.5, 0.5, 0.5, 0.5), "q2")
        await finish(j)
        return t1, t2

    t1, t2 = run_tutor(setup, fp, steps)
    first, follow, second = fp.calls
    sid = first.session.id
    assert follow.session.id == sid and follow.session.new is False and follow.system is None
    assert follow.prompt == "[Thread #1 · 追问]\n追问一下" and follow.images == []
    assert second.session.id == sid and second.prompt.startswith("[Thread #2 · 选区 x=0.50")
    assert [i.name for i in second.images] == [f"crop-{t2.id}.png"]  # page image already in session


def test_session_rebuilt_when_lost(setup):
    fp = FakeProvider(reply="A")

    async def steps(tutor, course):
        t1, _, j = tutor.ask(course, 2, (0, 0, 0.5, 0.5), "q1")
        await finish(j)
        old = course.get_session(2)
        fp.forget.add(old)
        _, j = tutor.follow_up(course, t1.id, "again")
        evs = await finish(j)
        return old, course.get_session(2), evs, course

    old, new, evs, course = run_tutor(setup, fp, steps)
    assert new != old and evs[-1]["type"] == "done"
    assert "reset" in [e["type"] for e in evs]
    rebuild = fp.calls[-1]
    assert rebuild.session.new and rebuild.system
    p = rebuild.prompt
    assert "之前的问答记录" in p
    assert p.index("学生：[Thread #1 · 选区") < p.index("助教（Thread #1）：A") < p.index("[Thread #1 · 追问]\nagain")
    assert p.count("again") == 1  # current message not duplicated into history
    assert [i.name for i in rebuild.images] == ["page-2.png"]


def test_same_page_questions_queue(setup):
    gate = asyncio.Event()
    fp = FakeProvider(gate=gate)

    async def steps(tutor, course):
        _, _, j1 = tutor.ask(course, 1, (0, 0, 0.5, 0.5), "a")
        _, _, j2 = tutor.ask(course, 1, (0, 0, 0.5, 0.5), "b")
        await asyncio.sleep(0.05)
        statuses = (j1.status, j2.status)
        gate.set()
        await finish(j1)
        await finish(j2)
        return statuses

    assert run_tutor(setup, fp, steps) == ("running", "queued")
    assert fp.calls[0].session.new and not fp.calls[1].session.new  # second resumes the first's session


def test_error_keeps_question_and_retry_answers_it(setup):
    fp = FakeProvider(fail=True)

    async def steps(tutor, course):
        t, _, j = tutor.ask(course, 1, (0, 0, 0.5, 0.5), "q")
        evs = await finish(j)
        fp.fail = False
        await finish(tutor.retry(course, t.id))
        with pytest.raises(ValueError):
            tutor.retry(course, t.id)
        return evs, [m.role for m in course.list_messages(t.id)]

    evs, roles = run_tutor(setup, fp, steps)
    assert evs[-1] == {"type": "error", "message": "boom"}
    assert roles == ["user", "assistant"]


def test_session_log(setup):
    async def steps(tutor, course):
        _, _, j = tutor.ask(course, 1, (0, 0, 0.5, 0.5), "q")
        await finish(j)
        return tutor.session_log(course, 1)

    log = run_tutor(setup, FakeProvider(reply="A"), steps)
    assert log["session_id"]
    assert [m["role"] for m in log["messages"]] == ["user", "assistant"]
    assert log["messages"][0]["content"].startswith("[Thread #1 · 选区")


def test_empty_question_rejected(setup):
    async def steps(tutor, course):
        with pytest.raises(ValueError):
            tutor.ask(course, 1, (0, 0, 0.5, 0.5), "  ")
        return course.list_threads(1)

    assert run_tutor(setup, FakeProvider(), steps) == []


def test_covered_text():
    blocks = [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "text": "A"},
              {"x": 0.6, "y": 0.6, "w": 0.2, "h": 0.1, "text": "B"}]
    assert covered_text(blocks, (0, 0, 0.5, 0.5)) == ["A"]
    assert covered_text(blocks, (0.25, 0.1, 0.5, 0.5)) == []  # only 25% of A


def test_crop_png_size():
    import pymupdf
    img = pymupdf.open().new_page(width=400, height=200).get_pixmap().tobytes("png")
    c = pymupdf.Pixmap(crop_png(img, (0.25, 0.5, 0.5, 0.5)))
    assert (c.width, c.height) == (200, 100)


def test_question_on_explanation_text(setup):
    fp = FakeProvider(reply="A")

    async def steps(tutor, course):
        course.add_explanation(2, "第一段。\n\n除以根号 d 是为了让方差回到 1。")
        t1, _, j = tutor.ask(course, 2, None, "为什么是 1？", quote="让方差回到 1")
        await finish(j)
        _, j = tutor.follow_up(course, t1.id, "再说说")
        await finish(j)
        with pytest.raises(ValueError):
            tutor.ask(course, 2, (0, 0, .5, .5), "q", quote="x")
        with pytest.raises(ValueError):
            tutor.ask(course, 2, None, "q")
        return t1

    t1 = run_tutor(setup, fp, steps)
    assert t1.anchor_type == "explanation_text" and t1.crop is None
    first, follow = fp.calls
    assert "[Thread #1 · 讲解中选中的一段 · 新问题]\n选中的讲解文字：\n> 让方差回到 1\n为什么是 1？" in first.prompt
    assert [i.name for i in first.images] == ["page-2.png"]  # no crop
    assert follow.prompt == "[Thread #1 · 追问]\n再说说"


# --- whole-course retrieval (M6) -------------------------------------------------

def test_qa_runs_in_course_folder_with_search_tools(setup):
    from gloss.providers import ToolUse
    fp = FakeProvider(reply="见第 3 页。", tool_calls=[ToolUse("Grep", {"pattern": "Multi-head"}),
                                                       ToolUse("Read", {"file_path": "/w/page-3.png"})])

    async def steps(tutor, course):
        course.set_transcript_spans({3: "老师：多头就是并行做 h 次。"})
        _, _, j = tutor.ask(course, 2, (0, 0, .5, .5), "后面讲的多头和这个什么关系？")
        evs = await finish(j)
        await finish(tutor.explain(course, 1))
        return evs, course

    evs, course = run_tutor(setup, fp, steps)
    qa, explain = fp.calls
    folder = qa.cwd
    assert folder.name == course.id and folder.parent.name == "courses"
    assert qa.tools == ("Read", "Grep", "Glob")
    assert "整门课的材料" in qa.system and "全部 3 页" in qa.system
    md = (folder / "course.md").read_text()
    assert "## 第 3 页\n\nMulti-head attention" in md and "老师：多头就是并行做 h 次。" in md
    assert sorted(p.name for p in folder.glob("page-*.png")) == ["page-1.png", "page-2.png", "page-3.png"]
    assert [e for e in evs if e["type"] == "tool"] == [
        {"type": "tool", "name": "Grep", "target": "search:Multi-head"},
        {"type": "tool", "name": "Read", "target": "page:3"}]
    # Explanations also run inside the course folder, but with Read only.
    assert explain.cwd == folder and explain.tools == ("Read",)


def test_retrieval_off(setup):
    course, ws = setup
    fp = FakeProvider()

    async def go():
        tutor = Tutor(fp, JobManager(2), ws, retrieval=False)
        _, _, j = tutor.ask(course, 2, (0, 0, .5, .5), "q")
        await finish(j)

    asyncio.run(go())
    (call,) = fp.calls
    assert call.tools == ("Read",) and "整门课的材料" not in call.system
    assert not (call.cwd / "course.md").exists()


def test_course_md_rewritten_only_on_change(setup):
    from gloss.tutor import Scratch
    course, ws = setup
    s = Scratch(ws, course)
    s.materials()
    md = s.dir / "course.md"
    first = md.stat().st_mtime_ns
    s.materials()
    assert md.stat().st_mtime_ns == first
    course.set_transcript_spans({1: "新内容"})
    s.materials()
    assert "新内容" in md.read_text()


@pytest.mark.parametrize("name, inp, out", [
    ("Read", {"file_path": "/a/b/page-12.png"}, "page:12"),
    ("Read", {"file_path": "course.md"}, "course"),
    ("Read", {"file_path": "crop-x.png"}, "file"),
    ("Grep", {"pattern": "softmax"}, "search:softmax"),
    ("Glob", {"pattern": "*.png"}, "files"),
])
def test_tool_target(name, inp, out):
    from gloss.tutor import tool_target
    assert tool_target(name, inp) == out
