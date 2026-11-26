"""providers/context.py: the shared Q&A context (CLI rebuild path = API normal path)."""

import asyncio
from pathlib import Path

from fakes import FakeProvider
from test_tutor import finish, setup  # noqa: F401  (fixture)

from gloss.jobs import JobManager
from gloss.providers import Capabilities
from gloss.providers.context import Part, flatten, qa_session
from gloss.tutor import Tutor


def test_flatten_and_order():
    parts = qa_session(Path("/w/page-2.png"), "OPEN", ("HEAD", ["Q1", "A1"], "NOW:"), "q2", [Path("/w/crop.png")])
    assert [(p.text, p.image and p.image.name, p.cache) for p in parts] == [
        (None, "page-2.png", False), ("OPEN", None, True), ("HEAD", None, False), ("Q1", None, False),
        ("A1", None, True), ("NOW:\n\nq2", None, False), (None, "crop.png", False)]
    assert flatten(parts) == ("OPEN\n\nHEAD\n\nQ1\n\nA1\n\nNOW:\n\nq2", [Path("/w/page-2.png"), Path("/w/crop.png")])
    assert [p.text for p in qa_session(Path("/p"), "O", None, "N", [])] == [None, "O", "N"]


def api_fake():
    fp = FakeProvider(reply="A", name="anthropic_api")
    fp.caps = Capabilities(True, True, True, True, supports_tools=False, supports_sessions=False)
    seen = []
    orig = fp.run

    async def run(req):
        seen.append(req)
        async for e in orig(req):
            yield e
    fp.run = run
    return fp, seen


def test_stable_prefix_across_questions(setup):  # noqa: F811
    """Each new question resends the same page prefix byte for byte, and the history only grows at the end."""
    course, ws = setup
    fp, seen = api_fake()

    async def go():
        t = Tutor(fp, JobManager(2), ws)
        t1, _, j = t.ask(course, 2, (0, 0, 0.5, 0.5), "first")
        await finish(j)
        _, j = t.follow_up(course, t1.id, "second")
        await finish(j)
        _, _, j = t.ask(course, 2, (0.5, 0.5, 0.4, 0.4), "third")
        await finish(j)

    asyncio.run(go())
    a, b, c = (r.parts for r in seen)
    assert a[:2] == b[:2] == c[:2] and a[1].cache  # page image + opening identical
    # every earlier message is its own part: the next question's context starts with the same parts
    hist_b = [p.text for p in b[2:-1]]
    hist_c = [p.text for p in c[2:-2]]
    assert hist_c[:len(hist_b)] == hist_b and len(hist_c) == len(hist_b) + 2  # grows only at the end
    assert hist_b[-1] == "助教（Thread #1）：A" and b[-2].cache and not b[-1].cache
    assert b[-1].text.startswith("## 现在的消息") and "second" in b[-1].text
    assert [p.image.name for p in c if p.image][0] == "page-2.png" and c[-1].image is not None  # crop last
    for r in seen:
        assert (r.prompt, r.images) == flatten(r.parts)  # the CLI view of the same context


def test_one_off_calls_have_parts_too(setup):  # noqa: F811
    course, ws = setup
    fp, seen = api_fake()

    async def go():
        await finish(Tutor(fp, JobManager(2), ws).explain(course, 2))

    asyncio.run(go())
    (r,) = seen
    assert r.parts[0].image.name == "page-2.png" and r.parts[1].text == r.prompt and not any(p.cache for p in r.parts)


def test_part_is_hashable_and_frozen():
    assert hash(Part(text="x")) == hash(Part(text="x"))
