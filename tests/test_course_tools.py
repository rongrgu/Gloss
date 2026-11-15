import base64
import io
import json
import subprocess

import pytest
from test_claude_cli import cli, run  # noqa: F401  (fixture)

from gloss.course_tools import TOOL_NAMES, Scope, ToolError, call
from gloss.mcp_server import allowed_tools, config, handle, serve
from gloss.providers import CourseToolSpec
from gloss.store import Course, Slide
from gloss.tutor import tool_target

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 30


@pytest.fixture
def library(tmp_path):
    """CMU/ML (course: two lectures, imported in reverse name order) and CMU/Stats (course: one)."""
    root = tmp_path / "lib"
    (root / "CMU" / "ML").mkdir(parents=True)
    (root / "CMU" / "Stats").mkdir(parents=True)
    with Course.create(root / "CMU" / "ML" / "b.gloss", "Attention") as c:
        c.set_meta("created_at", "2026-09-01T10:00:00+00:00")
        c.replace_slides([Slide(1, "Attention\nsoftmax(QK^T / sqrt(d)) V", PNG, [], "老师说 softmax 让权重归一化"),
                          Slide(2, "Multi-head attention", PNG, []),
                          Slide(3, "位置编码 positional encoding", PNG, [])])
        c.set_tree({"title": "Attention", "children": [
            {"title": "缩放点积", "summary": "QKV", "slides": [1, 2]}, {"title": "位置编码", "slides": [3]}]})
        c.add_explanation(1, "旧讲解")
        c.add_explanation(1, "为什么除以 sqrt(d)：防止点积过大。")
        t = c.create_thread(1, (0.1, 0.5, 0.6, 0.2))
        c.add_message(t.id, "user", "为什么要除以 sqrt(d)？")
        c.add_message(t.id, "assistant", "因为点积的方差随 d 增长。")
        c.add_message(t.id, "user", "那 d 很小的时候呢？")
        c.add_message(t.id, "assistant", "影响不大。")
        t2 = c.create_thread(1, None, anchor_type="explanation_text", quote="防止点积过大")
        c.add_message(t2.id, "user", "过大会怎样？")  # unanswered
        c.set_note(3, "位置编码：sin/cos")
        cid_b = c.id
    with Course.create(root / "CMU" / "ML" / "a.gloss", "Transformers") as c:
        c.set_meta("created_at", "2026-09-08T10:00:00+00:00")
        c.replace_slides([Slide(1, "Encoder decoder", PNG, [])])
    with Course.create(root / "CMU" / "Stats" / "s.gloss", "Bayes") as c:
        c.replace_slides([Slide(1, "Bayes rule", PNG, [])])
    return root, cid_b


def test_scope_labels_follow_course_order(library):
    root, cid = library
    with Scope(root, "CMU") as s:
        assert [(lec.label, lec.reader.title, lec.course, lec.number) for lec in s.lectures] == [
            ("L1", "Attention", "CMU/ML", 1), ("L2", "Transformers", "CMU/ML", 2), ("L3", "Bayes", "CMU/Stats", 1)]
        assert s.by_course_id(cid).label == "L1"
    with Scope(root, "CMU/ML", only=[cid]) as s:
        assert [lec.label for lec in s.lectures] == ["L1"]


def test_tools_text(library):
    root, _ = library
    with Scope(root, "CMU/ML") as s:
        listing = s.list_lectures()
        assert "L1（CMU/ML 第 1 讲：Attention）：3 页，2 个问题；主题：缩放点积；位置编码" in listing
        assert "缩放点积（第 1–2 页）QKV" in s.get_outline("l1")  # labels are case-insensitive
        slide = s.get_slide("L1", 1)
        assert "softmax(QK^T" in slide and "老师说" in slide and "防止点积过大" in slide and "旧讲解" not in slide
        qa = s.get_questions("L1")
        assert "学生：为什么要除以 sqrt(d)？" in qa and "学生追问：那 d 很小的时候呢？" in qa
        assert "> 防止点积过大" in qa and "框选的区域 x=0.10" in qa
        assert "sin/cos" in s.get_notes("L1") and "没有笔记" in s.get_notes("L2")
        with pytest.raises(ToolError, match="可用的有：L1, L2"):
            s.get_slide("L9", 1)
        with pytest.raises(ToolError, match="没有第 9 页"):
            s.get_slide("L1", 9)


def test_search_ranks_slide_text_and_handles_chinese(library):
    root, _ = library
    with Scope(root, "CMU") as s:
        r = s.search_course("positional")
        assert r.splitlines()[1].startswith("- L1 第 3 页 · 页面文字")
        r = s.search_course("位置编码")
        assert "L1 第 3 页 · 页面文字" in r and "L1 第 3 页 · 笔记" in r
        assert "没有找到" in s.search_course("quantum")
        assert s.search_course("bayes").splitlines()[1].startswith("- L3 第 1 页 · 页面文字")
        assert "L1" not in s.search_course("bayes", lecture="L3")


def test_study_profile(library):
    root, _ = library
    with Scope(root, "CMU") as s:
        sig = s.study_profile()
        top = sig[0]
        assert (top.lecture, top.page, top.questions, top.follow_ups, top.unanswered) == ("L1", 1, 2, 1, 1)
        assert top.score == 2 * 2 + 1 and top.topic == "缩放点积"
        assert (sig[1].lecture, sig[1].page, sig[1].has_note, sig[1].score) == ("L1", 3, True, 1)
        text = s.study_profile_text()
        assert "L1 第 1 页（缩放点积）：2 个问题，1 次追问，1 个没答完。问过：「为什么要除以 sqrt(d)？」" in text


def test_call_returns_mcp_blocks_and_truncates(library):
    root, _ = library
    with Scope(root, "CMU/ML") as s:
        blocks = call(s, "get_slide", {"lecture": "L1", "page": 1, "image": True})
        assert blocks[0]["type"] == "text" and blocks[1]["type"] == "image"
        assert base64.b64decode(blocks[1]["data"]) == PNG
        big = s.lectures[0]
        big.reader.notes = lambda page=None: {3: "x" * 20000}
        out = call(s, "get_notes", {"lecture": "L1"})[0]["text"]
        assert len(out) < 8200 and "已截断" in out


def test_mcp_handle_and_errors(library):
    root, _ = library
    with Scope(root, "CMU") as s:
        init = handle(s, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
        assert init["result"]["protocolVersion"] == "2025-03-26"
        assert handle(s, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
        names = [t["name"] for t in handle(s, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
        assert names == TOOL_NAMES
        bad = handle(s, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_slide", "arguments": {"page": 1}}})
        assert bad["result"]["isError"] and "lecture" in bad["result"]["content"][0]["text"]
        assert handle(s, {"jsonrpc": "2.0", "id": 4, "method": "nope"})["error"]["code"] == -32601


def test_serve_over_stdio_streams(library):
    root, _ = library
    reqs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_lectures", "arguments": {}}}]
    out = io.StringIO()
    serve(root, "CMU/Stats", None, io.StringIO("not json\n" + "\n".join(json.dumps(r) for r in reqs) + "\n"), out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [x["id"] for x in lines] == [1, 2] and "Bayes" in lines[1]["result"]["content"][0]["text"]


def test_mcp_serve_subprocess(library):
    """The exact command claude will run (from mcp_server.config) speaks MCP on stdio."""
    root, _ = library
    server = config(root, "CMU/ML")["mcpServers"]["gloss"]
    reqs = "\n".join(json.dumps(r) for r in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "search_course", "arguments": {"query": "softmax"}}},
    ]) + "\n"
    p = subprocess.run([server["command"], *server["args"]], input=reqs, capture_output=True, text=True, timeout=30)
    lines = [json.loads(x) for x in p.stdout.splitlines()]
    assert "L1 第 1 页" in lines[1]["result"]["content"][0]["text"], p.stderr


def test_cli_provider_adds_mcp_flags(cli, monkeypatch, library):  # noqa: F811
    root, _ = library
    spec = CourseToolSpec(root, "CMU/ML", ("L1",))
    evs = run(cli, "ok", monkeypatch, cwd=cli.workspace / "scopes" / "x", course_tools=spec)
    args = cli.calls()[-1]["args"]
    assert args[args.index("--mcp-config") + 1].endswith("scopes/x/mcp.json") and "--strict-mcp-config" in args
    allowed = args[args.index("--allowedTools") + 1: args.index("--mcp-config")]
    assert allowed == ["Read", *allowed_tools()] and args[args.index("--tools") + 1] == "Read"
    cfg = json.loads((cli.workspace / "scopes" / "x" / "mcp.json").read_text())
    assert cfg["mcpServers"]["gloss"]["args"][-2:] == ["--only", "L1"]
    assert evs[-1].text
    # without course tools nothing changes
    run(cli, "ok", monkeypatch)
    assert "--mcp-config" not in cli.calls()[-1]["args"]


def test_tool_targets_for_progress():
    assert tool_target("mcp__gloss__get_slide", {"lecture": "L2", "page": 7}) == "slide:L2:7"
    assert tool_target("mcp__gloss__search_course", {"query": "softmax"}) == "search:softmax"
    assert tool_target("mcp__gloss__get_questions", {"lecture": "L1"}) == "questions:L1"
    assert tool_target("mcp__gloss__list_lectures", {}) == "list_lectures"


def test_readers_never_write(library, tmp_path):
    root, _ = library
    f = root / "CMU" / "ML" / "b.gloss"
    before = f.read_bytes()
    with Scope(root, "") as s:
        for name in TOOL_NAMES:
            args = {"lecture": "L1", "page": 1, "query": "attention"} if name not in ("list_lectures", "study_profile") else {}
            call(s, name, args)
    assert f.read_bytes() == before
