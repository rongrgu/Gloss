from gloss.export import export_markdown
from gloss.store import Course, Slide


def make(tmp_path, tree=True):
    c = Course.create(tmp_path / "c.gloss", "Attention")
    c.replace_slides([Slide(1, "Attention\nLecture 7", b"i", []), Slide(2, "Scaled dot-product\nsoftmax", b"i", []),
                      Slide(3, "", b"i", [])])
    if tree:
        c.set_tree({"title": "Attention", "subtitle": "第 7 讲", "children": [
            {"title": "引入", "summary": "标题页", "slides": [1]},
            {"title": "公式", "summary": "缩放点积", "slides": [2, 3]}]})
    return c


def test_export_full(tmp_path):
    c = make(tmp_path)
    c.add_explanation(2, "old")
    c.add_explanation(2, "这一页讲 $\\sqrt{d}$。")
    t = c.create_thread(2, (0, 0, 0.5, 0.5))
    c.add_message(t.id, "user", "为什么除以根号 d？")
    c.add_message(t.id, "assistant", "因为方差。\n\n第二段。")
    c.add_message(t.id, "user", "不除呢？")
    c.add_message(t.id, "assistant", "会饱和。")
    c.set_note(3, "记住这个")
    md = export_markdown(c)
    assert md.startswith("# Attention\n\n第 7 讲\n\n3 页。")
    assert "- **公式**（第 2–3 页）缩放点积" in md
    assert "## 公式\n\n*缩放点积*\n\n### 第 2 页 · Scaled dot-product" in md
    assert "**讲解**\n\n这一页讲 $\\sqrt{d}$。" in md and "old" not in md
    assert "> **问题 1：** 为什么除以根号 d？\n\n因为方差。\n\n第二段。\n\n> **追问：** 不除呢？\n\n会饱和。" in md
    assert "### 第 3 页 · 第 3 页\n\n**笔记**\n\n记住这个" in md
    # Ordered by topic, then page.
    assert md.index("第 1 页 ·") < md.index("第 2 页 ·") < md.index("第 3 页 ·")
    c.close()


def test_export_without_tree(tmp_path):
    c = make(tmp_path, tree=False)
    md = export_markdown(c)
    assert "## 目录" not in md
    assert "## 第 1 页 · Attention" in md and "## 第 3 页 · 第 3 页" in md
    c.close()


def test_generated_headings_nest_under_page(tmp_path):
    from gloss.export import demote
    assert demote("## 要点\n文字\n#不是标题\n```\n# 代码注释\n```\n# 一级", 3) == \
        "##### 要点\n文字\n#不是标题\n```\n# 代码注释\n```\n#### 一级"
    assert demote("##### 深", 3) == "###### 深"
    c = make(tmp_path)
    c.add_explanation(2, "## 这一页在讲什么\n内容")
    assert "##### 这一页在讲什么" in export_markdown(c)
    c.close()
