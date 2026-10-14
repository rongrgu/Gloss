"""Export a whole course as Markdown (DESIGN.md §4): tree, explanations, Q&A, notes."""

from __future__ import annotations

from .i18n import DEFAULT_LOCALE, tr
from .store import Course


def _page_title(text: str, page: int, locale: str = DEFAULT_LOCALE) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[:80] or tr(locale, "page", p=page)


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


def export_markdown(course: Course, locale: str = DEFAULT_LOCALE) -> str:
    def t(key: str, **kw: object) -> str:
        return tr(locale, key, **kw)

    n = course.slide_count()
    tree = course.get_tree()
    topics = tree["children"] if tree else [{"title": "", "summary": "", "slides": list(range(1, n + 1))}]
    texts = dict(course.slide_texts())

    out = [f"# {course.title or (tree or {}).get('title') or 'Gloss'}", ""]
    if tree and tree.get("subtitle"):
        out += [tree["subtitle"], ""]
    out += [t("page_count", n=n), ""]
    if tree:
        out += [f"## {t('contents')}", ""]
        for topic in topics:
            pages = topic["slides"]
            span = t("pages", a=pages[0], b=pages[-1]) if len(pages) > 1 else t("page", p=pages[0])
            span = f"（{span}）" if locale == "zh" else f" ({span}) "
            out.append(f"- **{topic['title']}**{span}{topic.get('summary', '')}".rstrip())
        out.append("")

    for topic in topics:
        level = "###" if tree else "##"
        nest = len(level)  # generated headings go below the page heading
        if tree:
            out += [f"## {topic['title']}", ""]
            if topic.get("summary"):
                out += [f"*{topic['summary']}*", ""]
        for p in topic["slides"]:
            out += [f"{level} {t('page', p=p)} · {_page_title(texts.get(p, ''), p, locale)}", ""]
            e = course.latest_explanation(p)
            if e:
                out += [f"**{t('explanation')}**", "", demote(e.content.strip(), nest), ""]
            threads = course.list_threads(p)
            if threads:
                out += [f"**{t('qa')}**", ""]
                for th in threads:
                    msgs = course.list_messages(th.id)
                    for i, m in enumerate(msgs):
                        if m.role == "user":
                            label = t("question", n=th.n) if i == 0 else t("follow_up")
                            out += [_quote(f"**{label}{t('colon').rstrip()}** {m.content}"), ""]
                        else:
                            out += [demote(m.content.strip(), nest), ""]
            note = course.get_note(p).strip()
            if note:
                out += [f"**{t('notes')}**", "", note, ""]
    return "\n".join(out).rstrip() + "\n"
