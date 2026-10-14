"""Export a whole course as Markdown (DESIGN.md §4): tree, explanations, Q&A, notes."""

from __future__ import annotations

from .store import Course


def _page_title(text: str, page: int) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[:80] or f"第 {page} 页"


def demote(md: str, below: int) -> str:
    """Push Markdown headings in generated text below heading level `below`, so they nest under the page heading.
    Fenced code blocks are left alone."""
    out, fence = [], None
    for line in md.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            mark = stripped[:3]
            fence = None if fence == mark else (fence or mark)
        elif fence is None and stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            if 1 <= hashes <= 6 and stripped[hashes:hashes + 1] in (" ", ""):
                line = "#" * min(6, hashes + below) + stripped[hashes:]
        out.append(line)
    return "\n".join(out)


def _quote(text: str) -> str:
    return "\n".join("> " + line if line else ">" for line in text.strip().splitlines())


def export_markdown(course: Course) -> str:
    n = course.slide_count()
    tree = course.get_tree()
    topics = tree["children"] if tree else [{"title": "", "summary": "", "slides": list(range(1, n + 1))}]
    texts = dict(course.slide_texts())

    out = [f"# {course.title or (tree or {}).get('title') or 'Gloss'}", ""]
    if tree and tree.get("subtitle"):
        out += [tree["subtitle"], ""]
    out += [f"{n} 页。由 Gloss 导出。", ""]
    if tree:
        out += ["## 目录", ""]
        for t in topics:
            pages = t["slides"]
            span = f"第 {pages[0]}–{pages[-1]} 页" if len(pages) > 1 else f"第 {pages[0]} 页"
            out.append(f"- **{t['title']}**（{span}）{t.get('summary', '')}")
        out.append("")

    for t in topics:
        level = "###" if tree else "##"
        nest = len(level)  # generated headings go below the page heading
        if tree:
            out += [f"## {t['title']}", ""]
            if t.get("summary"):
                out += [f"*{t['summary']}*", ""]
        for p in t["slides"]:
            out += [f"{level} 第 {p} 页 · {_page_title(texts.get(p, ''), p)}", ""]
            e = course.latest_explanation(p)
            if e:
                out += ["**讲解**", "", demote(e.content.strip(), nest), ""]
            threads = course.list_threads(p)
            if threads:
                out += ["**问答**", ""]
                for th in threads:
                    msgs = course.list_messages(th.id)
                    for i, m in enumerate(msgs):
                        if m.role == "user":
                            label = f"问题 {th.n}" if i == 0 else "追问"
                            out += [_quote(f"**{label}：** {m.content}"), ""]
                        else:
                            out += [demote(m.content.strip(), nest), ""]
            note = course.get_note(p).strip()
            if note:
                out += ["**笔记**", "", note, ""]
    return "\n".join(out).rstrip() + "\n"
