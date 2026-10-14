import pytest

from gloss.transcript import (
    alignment_problems, decode, fmt_time, for_prompt, parse, repair_alignment, segment, span_text, target_for,
)

VTT = """WEBVTT
Kind: captions

1
00:00:01.000 --> 00:00:04.000
Welcome to lecture <c>seven</c>.

2
00:00:04.000 --> 00:00:07.500
Welcome to lecture seven.

3
00:01:05.000 --> 00:01:09.000
Today: attention.
"""

SRT = """1
00:00:01,000 --> 00:00:04,000
大家好，

2
01:02:03,500 --> 01:02:05,000
今天讲注意力。
"""


def test_parse_vtt_drops_tags_numbers_and_repeats():
    assert parse(VTT) == [(1, "Welcome to lecture seven."), (65, "Today: attention.")]


def test_parse_srt_hours():
    assert parse(SRT) == [(1, "大家好，"), (3723, "今天讲注意力。")]


def test_parse_plain_paragraphs():
    assert parse("First para\nwraps here.\n\n\nSecond.") == [(None, "First para wraps here."), (None, "Second.")]


def test_segment_sizes_and_numbering():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 30 for i in range(40))
    segs = segment(text)
    assert [s.index for s in segs] == list(range(len(segs)))
    assert all(len(s.text) <= 700 for s in segs)
    assert 10 < len(segs) < 40  # paragraphs are merged up to ~400 chars
    assert " ".join(s.text for s in segs).count("Paragraph") == 40


def test_segment_splits_long_chinese_at_sentence_ends():
    text = "这是一句话。" * 300  # one 1800-char paragraph, no blank lines
    segs = segment(text)
    assert len(segs) >= 4
    assert all(s.text.endswith("。") for s in segs)
    assert "".join(s.text for s in segs) == text


def test_segment_without_any_punctuation():
    segs = segment("x" * 2000)
    assert "".join(s.text for s in segs) == "x" * 2000 and all(len(s.text) <= 700 for s in segs)


def test_target_scales_with_deck():
    assert target_for("x" * 1700, 6) == 60  # short Chinese lecture: one cue ≈ one segment
    assert target_for("x" * 20000, 50) == 80
    assert target_for("x" * 200000, 10) == 400  # capped
    segs = segment("。".join(["一句话" * 10] * 40) + "。", target_for("一句话" * 400, 6))
    assert 15 <= len(segs) <= 40


def test_segment_keeps_first_timestamp():
    segs = segment(VTT)
    assert segs[0].start == 1 and segs[0].text == "Welcome to lecture seven. Today: attention."


def test_prompt_and_span_formats():
    segs = segment(VTT)
    assert for_prompt(segs) == "[段0] (0:01) Welcome to lecture seven. Today: attention."
    assert span_text(segs, 0, 0) == "[0:01] Welcome to lecture seven. Today: attention."
    assert fmt_time(3723) == "1:02:03" and fmt_time(None) == ""


def test_decode_variants():
    assert decode("中文".encode("utf-8-sig")) == "中文"
    assert decode("中文".encode("gb18030")) == "中文"
    assert decode("abc".encode("utf-16")) == "abc"


@pytest.mark.parametrize("data, needle", [
    ({"slides": [{"page": 9, "first": 0, "last": 1}]}, "第 9 页不存在"),
    ({"slides": [{"page": 1, "first": 3, "last": 1}]}, "不合法"),
    ({"slides": [{"page": 1, "first": 0, "last": 1}, {"page": 1, "first": 2, "last": 2}]}, "不止一次"),
    ({"slides": []}, "没有任何一页"),
    ([], "结构"),
])
def test_alignment_problems(data, needle):
    assert any(needle in p for p in alignment_problems(data, 3, 5))


def test_alignment_valid_and_repair():
    good = {"slides": [{"page": 1, "first": 0, "last": 1}, {"page": 3, "first": 2, "last": 4}]}
    assert alignment_problems(good, 3, 5) == []
    bad = {"slides": [{"page": 1, "first": 3, "last": 1}, {"page": 1, "first": 0, "last": 0},
                      {"page": 2, "first": 4, "last": 99}, {"page": 7, "first": 0, "last": 0}, "x"]}
    assert repair_alignment(bad, 3, 5) == {1: (1, 3), 2: (4, 4)}
