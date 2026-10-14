import asyncio

import pytest
from fakes import FakeProvider
from test_tutor import finish, run_tutor, setup  # noqa: F401  (fixture)

from gloss.tutor import TREE_SCHEMA, repair_tree, tree_problems

GOOD = {"title": "Attention", "subtitle": "第 7 讲", "children": [
    {"title": "引入", "summary": "标题", "slides": [1]},
    {"title": "注意力", "summary": "公式和多头", "slides": [2, 3]},
]}


def test_valid_tree_has_no_problems():
    assert tree_problems(GOOD, 3) == []


@pytest.mark.parametrize("data, needle", [
    (None, "结构"),
    ({"title": "x", "children": []}, "没有任何主题"),
    ({"children": [{"title": "A", "slides": [1, 2]}, {"title": "B", "slides": [2, 3]}]}, "第 2 页同时出现在「A」和「B」"),
    ({"children": [{"title": "A", "slides": [1, 2]}]}, "没有被分到任何主题：3"),
    ({"children": [{"title": "A", "slides": [1, 2, 3, 9]}]}, "第 9 页不存在"),
    ({"children": [{"title": "A", "slides": [1, 2, 3]}, {"title": "B", "slides": []}]}, "「B」没有任何页"),
])
def test_problems_are_described(data, needle):
    assert any(needle in p for p in tree_problems(data, 3))


def test_repair():
    bad = {"title": "", "children": [
        {"title": "A", "summary": "s", "slides": [3, 1, 1, 99]},
        {"title": "B", "slides": [3, 5]},
        {"title": "C", "slides": []},
        "junk",
    ]}
    t = repair_tree(bad, 7, fallback_title="Lec")
    assert t["title"] == "Lec" and t["subtitle"] == ""
    # 1,3 -> A; 5 -> B (3 already taken); C dropped; missing 2 -> A (nearest earlier page 1),
    # 4 -> A (nearest earlier 3), 6, 7 -> B (nearest earlier 5)
    assert [(c["title"], c["slides"]) for c in t["children"]] == [("A", [1, 2, 3, 4]), ("B", [5, 6, 7])]
    assert tree_problems(t, 7) == []


def test_repair_garbage():
    t = repair_tree("nonsense", 2, fallback_title="Lec")
    assert t["children"] == [{"title": "全部 slides", "summary": "", "slides": [1, 2]}]


def test_build_tree_retries_with_feedback_then_saves(setup):  # noqa: F811
    first = {"title": "Attention", "children": [{"title": "A", "summary": "", "slides": [1, 2]}]}  # missing 3
    fp = FakeProvider(structured=[first, GOOD])

    async def steps(tutor, course):
        evs = await finish(tutor.build_tree(course))
        again = tutor.build_tree(course)
        return evs, again, course.get_tree()

    evs, again, tree = run_tutor(setup, fp, steps)
    assert evs[-1] == {"type": "done", "tree": tree} and again is None
    assert tree == GOOD
    a, b = fp.calls
    assert a.json_schema == TREE_SCHEMA and a.session is None and a.images == []
    assert "### 第 2 页\nScaled dot-product" in a.prompt and "共 3 页" in a.prompt
    assert "上一次的结果有问题" not in a.prompt
    assert "没有被分到任何主题：3" in b.prompt


def test_build_tree_repairs_after_second_failure(setup):  # noqa: F811
    bad = {"title": "T", "children": [{"title": "A", "summary": "", "slides": [1]}]}
    fp = FakeProvider(structured=[bad, bad])

    async def steps(tutor, course):
        await finish(tutor.build_tree(course))
        return course.get_tree()

    tree = run_tutor(setup, fp, steps)
    assert len(fp.calls) == 2
    assert tree["children"] == [{"title": "A", "summary": "", "slides": [1, 2, 3]}]


def test_regenerate_replaces(setup):  # noqa: F811
    other = {"title": "X", "children": [{"title": "全部", "summary": "", "slides": [1, 2, 3]}]}
    fp = FakeProvider(structured=[GOOD, other])

    async def steps(tutor, course):
        await finish(tutor.build_tree(course))
        await finish(tutor.build_tree(course, regenerate=True))
        return course.get_tree()

    assert run_tutor(setup, fp, steps)["title"] == "X"


def test_concurrent_requests_share_one_job(setup):  # noqa: F811
    gate = asyncio.Event()
    fp = FakeProvider(structured=[GOOD], gate=gate)

    async def steps(tutor, course):
        a = tutor.build_tree(course)
        b = tutor.build_tree(course)
        gate.set()
        await finish(a)
        return a is b

    assert run_tutor(setup, fp, steps) is True
    assert len(fp.calls) == 1
