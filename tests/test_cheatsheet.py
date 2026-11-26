"""Cheatsheets (DESIGN.md §16): storage, input assembly, generate and revise jobs, the API."""

import pytest
from make_pdfs import make_deck
from test_course_tools import library  # noqa: F401  (fixture)
from test_quiz import app  # noqa: F401  (fixture)
from test_server import sse

from gloss.cheatsheet import (
    REPLY_MARK, SheetStore, budget_text, clean_markdown, materials_text, split_reply, title_of,
)
from gloss.course_tools import Scope
from gloss.exams import Exams

SHEET = "# ML 速查\n\n## 缩放点积\n- $\\mathrm{softmax}(QK^T/\\sqrt d)V$\n- 除以 $\\sqrt d$：方差保持在 1 附近\n"


def test_store_versions_messages_and_merged_hand_edits(tmp_path):
    st = SheetStore(tmp_path)
    assert st.list() == [] and not st.path.exists()
    sid = st.create("ML 速查", {"pages": 1}, SHEET)
    s = st.get(sid)
    assert s["content"] == SHEET and [v["source"] for v in s["versions"]] == ["generate"]
    v1 = s["version"]
    e1 = st.save(sid, SHEET + "- 手改一\n", "edit", base=v1)
    e2 = st.save(sid, SHEET + "- 手改二\n", "edit", base=e1)  # consecutive hand edits: one version
    assert e1 == e2 and len(st.get(sid)["versions"]) == 2
    a = st.save(sid, SHEET.replace("ML 速查", "Attention 速查"), "ai", note="改标题")
    assert st.get(sid)["title"] == "Attention 速查"  # the title follows the first heading
    st.add_message(sid, "user", "改标题")
    st.add_message(sid, "assistant", "已改", a)
    e3 = st.save(sid, "# x\n\nhand", "edit", base=a)  # after an AI change, a hand edit is a new version
    assert e3 != a
    st.save(sid, "# x\n\nstale", "edit", base=v1)  # an editor that saw an older version never overwrites
    assert len(st.get(sid)["versions"]) == 5
    st.restore(sid, v1)
    s = st.get(sid)
    assert s["content"] == SHEET and s["versions"][-1]["source"] == "restore"
    assert [m["version_id"] for m in s["messages"]] == [None, a]
    with pytest.raises(KeyError):
        st.version(sid, 9999)
    st.delete(sid)
    assert st.list() == []


def test_text_helpers():
    assert title_of(SHEET) == "ML 速查" and title_of("no heading") == ""
    assert clean_markdown("```markdown\n# T\n- a\n```") == "# T\n- a"
    assert clean_markdown("Here is your cheatsheet:\n\n# T\n- a") == "# T\n- a"
    assert split_reply(f"改了标题\n{REPLY_MARK}\n# New\n- a") == ("改了标题", "# New\n- a")
    assert split_reply("# Only\n- a") == ("", "# Only\n- a")
    assert "1500 个汉字" in budget_text(1, "中文") and "1800" in budget_text(2, "English")
    assert "不限" in budget_text(0, "English")


def test_materials_cover_notes_questions_and_exams(library):  # noqa: F811
    root, cid = library
    Exams(root / "CMU" / "ML").add("mid.pdf", make_deck(slides=[["Midterm", "Why sqrt(d)?"]]))
    with Scope(root, "CMU/ML", [cid]) as s:
        text = materials_text(s, ["E1"])
    assert "位置编码：sin/cos" in text and "为什么要除以 sqrt(d)？" in text and "因为点积的方差随 d 增长" in text
    assert "缩放点积" in text and "Why sqrt(d)?" in text and "Transformers" not in text  # only the chosen lecture


def test_api_generate_edit_revise_restore(app):  # noqa: F811
    client, provider, root, cid = app
    provider.reply = "Sure, here it is:\n\n" + SHEET
    r = client.post("/api/library/cheatsheets", json={"folder": "CMU/ML", "lectures": ["CMU/ML/b.gloss"], "pages": 1,
                                                      "instructions": "多写公式"})
    assert r.status_code == 200, r.text
    done = sse(client, r.json()["job"]["id"])[-1]
    assert done["type"] == "done", done
    sid = done["sheet_id"]
    gen = provider.calls[-1]
    assert "位置编码：sin/cos" in gen.prompt and "多写公式" in gen.prompt and "Encoder decoder" not in gen.prompt
    assert "要能打印在 1 页 A4 上" in gen.prompt and gen.course_tools.only == (cid,) and gen.json_schema is None
    listing = client.get("/api/library/cheatsheets", params={"folder": "CMU/ML"}).json()
    assert listing["job"] is None and listing["sheets"][0]["title"] == "ML 速查"
    s = client.get(f"/api/library/cheatsheets/{sid}", params={"folder": "CMU/ML"}).json()
    assert s["content"] == SHEET.strip()  # the preamble is gone

    e = client.put(f"/api/library/cheatsheets/{sid}", params={"folder": "CMU/ML"},
                   json={"content": SHEET + "- 手加的一行\n", "base": s["version"]}).json()
    assert e["title"] == "ML 速查"
    assert client.put(f"/api/library/cheatsheets/{sid}", params={"folder": "CMU/ML"},
                      json={"content": " \n", "base": e["version"]}).status_code == 422  # an empty save is a bug, not an edit

    provider.reply = f"把标题改成了 Attention\n{REPLY_MARK}\n# Attention\n\n## 缩放点积\n- 除以 $\\sqrt d$\n"
    r = client.post(f"/api/library/cheatsheets/{sid}/revise", params={"folder": "CMU/ML"},
                    json={"request": "标题改成 Attention", "selection": "ML 速查"})
    done = sse(client, r.json()["job"]["id"])[-1]
    assert done["type"] == "done", done
    p = provider.calls[-1].prompt
    assert "手加的一行" in p and "标题改成 Attention" in p and "## 只改这一部分" in p and "> ML 速查" in p
    s = client.get(f"/api/library/cheatsheets/{sid}", params={"folder": "CMU/ML"}).json()
    assert s["title"] == "Attention" and [m["role"] for m in s["messages"]] == ["user", "assistant"]
    assert s["messages"][1]["content"] == "把标题改成了 Attention" and s["messages"][1]["version_id"] == s["version"]
    assert [v["source"] for v in s["versions"]] == ["generate", "edit", "ai"]

    provider.reply = "抱歉，我没法改。"  # no sheet in the answer: nothing changes
    r = client.post(f"/api/library/cheatsheets/{sid}/revise", params={"folder": "CMU/ML"}, json={"request": "再短一点"})
    assert sse(client, r.json()["job"]["id"])[-1]["type"] == "error"
    assert client.get(f"/api/library/cheatsheets/{sid}", params={"folder": "CMU/ML"}).json()["version"] == s["version"]

    first = s["versions"][0]["id"]
    assert client.get(f"/api/library/cheatsheets/{sid}/versions/{first}", params={"folder": "CMU/ML"}).json()["content"] == SHEET.strip()
    back = client.post(f"/api/library/cheatsheets/{sid}/restore", params={"folder": "CMU/ML"}, json={"version": first}).json()
    assert back["title"] == "ML 速查" and back["versions"][-1]["source"] == "restore"
    md = client.get(f"/api/library/cheatsheets/{sid}/export.md", params={"folder": "CMU/ML"})
    assert md.text == SHEET.strip() and "ML%20%E9%80%9F%E6%9F%A5.md" in md.headers["content-disposition"]

    for body in ({"folder": "CMU/ML", "pages": 5}, {"folder": "CMU/ML", "lectures": ["x.gls"]},
                 {"folder": "CMU/ML", "instructions": "x" * 2001}):
        assert client.post("/api/library/cheatsheets", json=body).status_code == 422, body
    assert client.post(f"/api/library/cheatsheets/{sid}/revise", params={"folder": "CMU/ML"}, json={"request": " "}).status_code == 422
    assert client.get("/api/library/cheatsheets/nope", params={"folder": "CMU/ML"}).status_code == 404
    client.delete(f"/api/library/cheatsheets/{sid}", params={"folder": "CMU/ML"})
    assert client.get("/api/library/cheatsheets", params={"folder": "CMU/ML"}).json()["sheets"] == []


def test_api_empty_answer_is_an_error(app):  # noqa: F811
    client, provider, *_ = app
    provider.reply = "ok"
    r = client.post("/api/library/cheatsheets", json={"folder": "CMU/ML", "pages": 0})
    events = sse(client, r.json()["job"]["id"])
    assert events[-1]["type"] == "error" and "cheatsheet" in events[-1]["message"]
    assert "篇幅不限" in provider.calls[-1].prompt


# --- Word export (§16.4) -------------------------------------------------------------------------

DOCX_MD = ("# ML 速查\n\n## Attention\n\n$$\n\\frac{QK^T}{\\sqrt{d}}\n$$\n\n- **Why** $\\sqrt{d}$ & <not> d\n  - nested\n"
           "1. first\n\n| Term | Meaning |\n| --- | --- |\n| $d_k$ | width |\n\nPlain *italic* `code`.\n")


def _xml(data: bytes) -> str:
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read("word/document.xml").decode()


def test_builtin_docx_writer():
    from gloss.docx_export import builtin_docx
    xml = _xml(builtin_docx(DOCX_MD))
    assert 'w:val="Title"' in xml and "ML 速查" in xml and 'w:val="Heading1"' in xml
    assert "\\frac{QK^T}{\\sqrt{d}}" in xml and 'w:val="Formula"' in xml
    assert "• " in xml and "&amp; &lt;not&gt; d" in xml and "<w:b/>" in xml and "<w:i/>" in xml  # escaped, styled
    assert xml.count("<w:tr>") == 2 and "width" in xml and '<w:cols w:num="2"' in xml


def test_to_docx_falls_back_without_pandoc(monkeypatch):
    from gloss import docx_export
    monkeypatch.setattr(docx_export.shutil, "which", lambda _: None)
    data, equations = docx_export.to_docx(DOCX_MD)
    assert data[:2] == b"PK" and equations is False


@pytest.mark.skipif(__import__("shutil").which("pandoc") is None, reason="pandoc not installed")
def test_to_docx_with_pandoc_makes_equations():
    from gloss.docx_export import to_docx
    data, equations = to_docx(DOCX_MD + "\n![x](/etc/hosts)\n")
    xml = _xml(data)
    assert equations and "<m:oMath" in xml and "速查" in xml  # pandoc splits text into several runs


def test_api_docx(app):  # noqa: F811
    client, provider, *_ = app
    provider.reply = SHEET
    sid = sse(client, client.post("/api/library/cheatsheets", json={"folder": "CMU/ML"}).json()["job"]["id"])[-1]["sheet_id"]
    r = client.get(f"/api/library/cheatsheets/{sid}/export.docx", params={"folder": "CMU/ML"})
    assert r.status_code == 200 and r.content[:2] == b"PK" and r.headers["x-gloss-equations"] in ("word", "text")
    assert "ML%20%E9%80%9F%E6%9F%A5.docx" in r.headers["content-disposition"]


def test_api_deep_listing(app):  # noqa: F811
    """A quiz on a group folder can open the cheatsheets of the courses under it (§16.9)."""
    client, provider, *_ = app
    provider.reply = SHEET
    sid = sse(client, client.post("/api/library/cheatsheets", json={"folder": "CMU/ML"}).json()["job"]["id"])[-1]["sheet_id"]
    assert client.get("/api/library/cheatsheets", params={"folder": "CMU"}).json()["sheets"] == []
    deep = client.get("/api/library/cheatsheets", params={"folder": "CMU", "deep": "true"}).json()["sheets"]
    assert [(s["id"], s["folder"]) for s in deep] == [(sid, "CMU/ML")]


def test_api_rename_exam_quiz_and_sheet(app):  # noqa: F811
    """Uploaded and generated files can all be renamed (lectures and folders already could)."""
    from test_quiz import q
    client, provider, *_ = app
    exam = client.post("/api/library/exams", data={"folder": "CMU/ML"},
                       files={"file": ("mid.pdf", make_deck(), "application/pdf")}).json()
    r = client.patch(f"/api/library/exams/{exam['id']}", params={"folder": "CMU/ML"}, json={"title": "  Midterm   2025 "})
    assert r.json()["title"] == "Midterm 2025"
    assert client.get("/api/library/exams", params={"folder": "CMU/ML"}).json()["exams"][0]["title"] == "Midterm 2025"

    provider.structured = [{"title": "t", "questions": [q()]}, None]
    qid = sse(client, client.post("/api/library/quizzes", json={"folder": "CMU/ML", "count": 1, "types": ["mcq"]}).json()["job"]["id"])[-1]["quiz_id"]
    assert client.patch(f"/api/library/quizzes/{qid}", params={"folder": "CMU/ML"}, json={"title": "Week 3 practice"}).status_code == 200
    assert client.get("/api/library/quizzes", params={"folder": "CMU/ML"}).json()["quizzes"][0]["title"] == "Week 3 practice"
    assert client.patch("/api/library/quizzes/nope", params={"folder": "CMU/ML"}, json={"title": "x"}).status_code == 404

    provider.reply = SHEET
    sid = sse(client, client.post("/api/library/cheatsheets", json={"folder": "CMU/ML"}).json()["job"]["id"])[-1]["sheet_id"]
    s = client.patch(f"/api/library/cheatsheets/{sid}", params={"folder": "CMU/ML"}, json={"title": "Final sheet"}).json()
    assert s["title"] == "Final sheet" and s["content"].startswith("# Final sheet\n") and s["versions"][-1]["source"] == "rename"
    assert "## 缩放点积" in s["content"]  # only the title line changed

    for url in (f"/api/library/exams/{exam['id']}", f"/api/library/quizzes/{qid}", f"/api/library/cheatsheets/{sid}"):
        for bad in ("", "   ", "x" * 201):
            assert client.patch(url, params={"folder": "CMU/ML"}, json={"title": bad}).status_code == 422, (url, bad)
