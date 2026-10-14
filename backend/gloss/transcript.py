"""Lecture transcripts: parsing, segmenting, and validating the slide alignment (DESIGN.md §5.3).

A transcript (plain text, Markdown, WebVTT or SRT) is cut into short numbered
segments. Claude maps each slide to a range of segment numbers; the text of that
range becomes the slide's `transcript_span`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

TARGET_CHARS = 400  # default segment size
MIN_TARGET, SEGMENTS_PER_SLIDE = 60, 5

ALIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer"},
                    "topic": {"type": "string"},
                    "first": {"type": "integer"},
                    "last": {"type": "integer"},
                },
                "required": ["page", "topic", "first", "last"],
            },
        },
    },
    "required": ["slides"],
}

_TIME = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,]\d{1,3})?"
_CUE = re.compile(rf"^\s*{_TIME}\s*-->\s*{_TIME}")
_SENTENCE_END = re.compile(r"(?<=[。！？!?；;])|(?<=[.!?])\s+")


@dataclass
class Segment:
    index: int
    text: str
    start: float | None = None  # seconds, if the transcript had timestamps


def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            text = data.decode(enc)
            if enc == "utf-16" and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue
            return text
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _seconds(h: str | None, m: str, s: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s)


def parse(text: str) -> list[tuple[float | None, str]]:
    """Units of (start time or None, text). Subtitle cues for VTT/SRT, paragraphs otherwise."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if any(_CUE.match(line) for line in lines[:200]):
        units: list[tuple[float | None, str]] = []
        start: float | None = None
        buf: list[str] = []
        for line in lines:
            m = _CUE.match(line)
            if m:
                if buf:
                    units.append((start, " ".join(buf)))
                start, buf = _seconds(*m.groups()[:3]), []
            elif line.strip() and start is not None and not line.strip().isdigit() \
                    and not line.startswith(("WEBVTT", "NOTE", "STYLE", "Kind:", "Language:")):
                buf.append(re.sub(r"<[^>]+>", "", line.strip()))  # drop VTT inline tags
        if buf:
            units.append((start, " ".join(buf)))
        # Auto captions repeat lines across cues; drop exact consecutive repeats.
        out: list[tuple[float | None, str]] = []
        for u in units:
            if u[1] and (not out or out[-1][1] != u[1]):
                out.append(u)
        return out
    paras = re.split(r"\n\s*\n", text)
    return [(None, " ".join(p.split())) for p in paras if p.strip()]


def target_for(text: str, pages: int) -> int:
    """Segment size giving ~SEGMENTS_PER_SLIDE segments per slide: fine enough to find slide boundaries,
    coarse enough to keep the prompt short. Works for any script (Chinese packs more speech per character)."""
    return max(MIN_TARGET, min(TARGET_CHARS, len(text) // max(1, pages * SEGMENTS_PER_SLIDE)))


def _split_long(text: str, target: int) -> list[str]:
    max_chars = target * 7 // 4
    if len(text) <= max_chars:
        return [text]
    parts, cur = [], ""
    for sent in _SENTENCE_END.split(text):
        if not sent:
            continue
        if cur and len(cur) + len(sent) > target:
            parts.append(cur.strip())
            cur = ""
        cur += sent if not cur or re.search(r"[　-鿿]$", cur) else " " + sent
        while len(cur) > max_chars:  # no sentence breaks at all
            parts.append(cur[:target].strip())
            cur = cur[target:]
    if cur.strip():
        parts.append(cur.strip())
    return parts


def segment(text: str, target: int = TARGET_CHARS) -> list[Segment]:
    """Cut a transcript into segments of about `target` chars, numbered from 0."""
    segs: list[Segment] = []
    cur_text, cur_start = "", None
    for start, unit in parse(text):
        for piece in _split_long(unit, target):
            if cur_text and len(cur_text) + len(piece) > target:
                segs.append(Segment(len(segs), cur_text, cur_start))
                cur_text, cur_start = "", None
            if not cur_text:
                cur_start = start
            joiner = "" if not cur_text or re.search(r"[　-鿿]$", cur_text) else " "
            cur_text += joiner + piece
    if cur_text:
        segs.append(Segment(len(segs), cur_text, cur_start))
    return segs


def fmt_time(t: float | None) -> str:
    if t is None:
        return ""
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def for_prompt(segs: list[Segment]) -> str:
    return "\n".join(f"[段{s.index}]{' (' + fmt_time(s.start) + ')' if s.start is not None else ''} {s.text}"
                     for s in segs)


def span_text(segs: list[Segment], first: int, last: int) -> str:
    out = []
    for s in segs[first:last + 1]:
        out.append(f"[{fmt_time(s.start)}] {s.text}" if s.start is not None else s.text)
    return "\n\n".join(out)


def alignment_problems(data: Any, n_pages: int, n_segs: int) -> list[str]:
    if not isinstance(data, dict) or not isinstance(data.get("slides"), list):
        return ["结果不是 {slides: [...]} 的结构"]
    problems, seen = [], set()
    for item in data["slides"]:
        if not isinstance(item, dict):
            continue
        p, a, b = item.get("page"), item.get("first"), item.get("last")
        if not all(isinstance(v, int) for v in (p, a, b)):
            problems.append(f"条目 {item} 缺少整数 page / first / last")
            continue
        if not 1 <= p <= n_pages:
            problems.append(f"第 {p} 页不存在（只有 1–{n_pages} 页）")
        elif p in seen:
            problems.append(f"第 {p} 页出现了不止一次")
        seen.add(p)
        if not (0 <= a <= b < n_segs):
            problems.append(f"第 {p} 页的段落范围 {a}–{b} 不合法（段落编号 0–{n_segs - 1}，first ≤ last）")
    if not seen:
        problems.append("没有任何一页对应到 transcript")
    return problems


def repair_alignment(data: Any, n_pages: int, n_segs: int) -> dict[int, tuple[int, int]]:
    """Valid entries only: page -> (first, last). First entry wins for a repeated page; ranges are clipped."""
    out: dict[int, tuple[int, int]] = {}
    items = data.get("slides") if isinstance(data, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        p, a, b = item.get("page"), item.get("first"), item.get("last")
        if not all(isinstance(v, int) for v in (p, a, b)) or not 1 <= p <= n_pages or p in out:
            continue
        a, b = max(0, min(a, b)), min(n_segs - 1, max(a, b))
        if a <= b:
            out[p] = (a, b)
    return out
