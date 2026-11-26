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
        "digest": "学习总结",
        "digest_covered": "这一讲讲了什么",
        "digest_questions": "你问了什么",
        "digest_weak": "还没弄懂的",
        "digest_understood": "已经弄懂的",
        "digest_open": "还没解决的问题",
        "digest_review": "建议复习",
        "digest_empty": "（无）",
        "question_ref": "第 {p} 页 #{n}",
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
        "digest": "Study summary",
        "digest_covered": "What this lecture covered",
        "digest_questions": "What you asked",
        "digest_weak": "Still unclear",
        "digest_understood": "Now understood",
        "digest_open": "Open questions",
        "digest_review": "Worth reviewing",
        "digest_empty": "(none)",
        "question_ref": "slide {p} #{n}",
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
        data = data if isinstance(data, dict) else {}
        lang = data.get("ui_language")
        self.ui_language: str | None = lang if lang in LOCALES else None  # None: not chosen yet
        # The CLI the user picked when both claude and codex are installed (DESIGN.md §6.2); None: not chosen.
        prov = data.get("provider")
        self.provider: str | None = prov if isinstance(prov, str) and prov else None

    @property
    def locale(self) -> str:
        return self.ui_language or DEFAULT_LOCALE

    def set_ui_language(self, lang: str) -> None:
        if lang not in LOCALES:
            raise ValueError(f"ui_language must be one of {LOCALES}")
        self.ui_language = lang
        self._save()

    def set_provider(self, name: str | None) -> None:
        self.provider = name
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        data = {k: v for k, v in {"ui_language": self.ui_language, "provider": self.provider}.items() if v}
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self.path)
