"""Interface language (DESIGN.md §9.3): the zh / en preference and the few user-visible backend strings."""

from __future__ import annotations

import json
import os
from pathlib import Path

LOCALES = ("zh", "en")
DEFAULT_LOCALE = "en"
# Claude's explanation / answer language for each interface language, unless config.toml pins `language`.
LANGUAGE_FOR = {"zh": "中文", "en": "English"}

STRINGS: dict[str, dict[str, str]] = {
    "zh": {
        "page": "第 {p} 页",
        "pages": "第 {a}–{b} 页",
        "page_count": "{n} 页。由 Gloss 导出。",
        "contents": "目录",
        "explanation": "讲解",
        "qa": "问答",
        "question": "问题 {n}",
        "follow_up": "追问",
        "notes": "笔记",
        "colon": "：",
        "untitled_topic": "未命名主题",
        "all_slides": "全部 slides",
    },
    "en": {
        "page": "Slide {p}",
        "pages": "Slides {a}–{b}",
        "page_count": "{n} slides. Exported from Gloss.",
        "contents": "Contents",
        "explanation": "Explanation",
        "qa": "Q&A",
        "question": "Question {n}",
        "follow_up": "Follow-up",
        "notes": "Notes",
        "colon": ": ",
        "untitled_topic": "Untitled topic",
        "all_slides": "All slides",
    },
}


def tr(locale: str, key: str, **kw: object) -> str:
    return STRINGS.get(locale, STRINGS[DEFAULT_LOCALE])[key].format(**kw)


class Prefs:
    """App-wide preferences in `prefs.json` next to `recent.json` (not inside any .gloss)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        lang = data.get("ui_language") if isinstance(data, dict) else None
        self.ui_language: str | None = lang if lang in LOCALES else None  # None: not chosen yet

    @property
    def locale(self) -> str:
        return self.ui_language or DEFAULT_LOCALE

    def set_ui_language(self, lang: str) -> None:
        if lang not in LOCALES:
            raise ValueError(f"ui_language must be one of {LOCALES}")
        self.ui_language = lang
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ui_language": lang}))
        os.replace(tmp, self.path)
