"""subtitle parser + cleaner 单元测试。"""

from pathlib import Path

import pytest

from app.core.errors import SubtitleError
from app.core.models import Segment, TranscriptSource
from app.subtitle.cleaner import clean_segments
from app.subtitle.parser import parse_subtitle

FIXTURES = Path(__file__).parents[1] / "fixtures" / "subtitles"


class TestVttParsing:
    def test_parses_cues_and_skips_header_blocks(self) -> None:
        transcript = parse_subtitle(FIXTURES / "sample.vtt", "vtt", "en")
        assert len(transcript.segments) == 3
        assert transcript.language == "en"
        assert transcript.source is TranscriptSource.SUBTITLE

    def test_strips_tags_and_unescapes_entities(self) -> None:
        transcript = parse_subtitle(FIXTURES / "sample.vtt", "vtt", "en")
        assert transcript.segments[0].text == "Hello world & friends"
        assert transcript.segments[1].text == "This is the second cue"

    def test_timestamps_with_and_without_hours(self) -> None:
        transcript = parse_subtitle(FIXTURES / "sample.vtt", "vtt", "en")
        first, _, third = transcript.segments
        assert first.start == 0.0
        assert first.end == 2.5
        assert third.start == pytest.approx(3723.4)  # 01:02:03.400

    def test_bom_tolerated(self, tmp_path: Path) -> None:
        content = "﻿WEBVTT\n\n00:00.000 --> 00:01.000\nhi\n"
        path = tmp_path / "bom.vtt"
        path.write_text(content, encoding="utf-8")
        transcript = parse_subtitle(path, "vtt", "en")
        assert transcript.segments[0].text == "hi"


class TestSrtParsing:
    def test_parses_comma_timestamps_and_multiline(self) -> None:
        transcript = parse_subtitle(FIXTURES / "sample.srt", "srt", "zh")
        assert len(transcript.segments) == 2
        assert transcript.segments[0].start == 1.0
        assert transcript.segments[0].end == 3.5
        assert transcript.segments[1].text == "第二句带标签\n跨两行"


class TestJsonParsing:
    def test_json3_events_parsed_and_fillers_skipped(self) -> None:
        transcript = parse_subtitle(FIXTURES / "youtube.json3", "json3", "zh-Hans")
        assert [s.text for s in transcript.segments] == ["大家好，欢迎收看", "本期内容"]
        assert transcript.segments[0].start == pytest.approx(0.1)
        assert transcript.segments[0].end == pytest.approx(2.5)

    def test_bilibili_body_parsed_via_content_detection(self) -> None:
        # B站字幕 ext 为 "json"，按内容（body 键）识别
        transcript = parse_subtitle(FIXTURES / "bilibili.json", "json", "zh-Hans")
        assert [s.text for s in transcript.segments] == ["第一句B站字幕", "第二句B站字幕"]
        assert transcript.segments[1].end == 5.0

    def test_unrecognized_json_structure_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "weird.json"
        path.write_text('{"foo": []}', encoding="utf-8")
        with pytest.raises(SubtitleError, match="无法识别"):
            parse_subtitle(path, "json", "zh")

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json3"
        path.write_text("{broken", encoding="utf-8")
        with pytest.raises(SubtitleError, match="JSON"):
            parse_subtitle(path, "json3", "zh")


class TestParseSubtitleErrors:
    def test_unsupported_format_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "sub.ass"
        path.write_text("x", encoding="utf-8")
        with pytest.raises(SubtitleError, match="不支持"):
            parse_subtitle(path, "ass", "en")

    def test_empty_result_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.vtt"
        path.write_text("WEBVTT\n\n", encoding="utf-8")
        with pytest.raises(SubtitleError, match="为空"):
            parse_subtitle(path, "vtt", "en")

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SubtitleError, match="读取"):
            parse_subtitle(tmp_path / "nope.vtt", "vtt", "en")


class TestRollingDedup:
    def test_youtube_auto_rolling_window_deduplicated(self) -> None:
        transcript = parse_subtitle(FIXTURES / "youtube_auto_rolling.vtt", "vtt", "en")
        assert [s.text for s in transcript.segments] == [
            "hello world",
            "this is",
            "a rolling test",
        ]

    def test_full_duplicate_extends_previous_end(self) -> None:
        transcript = parse_subtitle(FIXTURES / "youtube_auto_rolling.vtt", "vtt", "en")
        # 最后一个 cue 与倒数第二个完全重复，其时间并入前段
        assert transcript.segments[-1].end == 8.0


class TestCleanSegments:
    def test_empty_and_whitespace_dropped(self) -> None:
        cleaned = clean_segments(
            [
                Segment(0.0, 1.0, "  \n "),
                Segment(1.0, 2.0, "有效内容"),
            ]
        )
        assert [s.text for s in cleaned] == ["有效内容"]

    def test_out_of_order_sorted_by_time(self) -> None:
        cleaned = clean_segments(
            [
                Segment(5.0, 6.0, "后"),
                Segment(1.0, 2.0, "前"),
            ]
        )
        assert [s.text for s in cleaned] == ["前", "后"]

    def test_inner_lines_stripped(self) -> None:
        cleaned = clean_segments([Segment(0.0, 1.0, "  a  \n\n  b  ")])
        assert cleaned[0].text == "a\nb"

    def test_partial_overlap_keeps_fresh_lines_only(self) -> None:
        cleaned = clean_segments(
            [
                Segment(0.0, 2.0, "A\nB"),
                Segment(2.0, 4.0, "B\nC\nD"),
            ]
        )
        assert [s.text for s in cleaned] == ["A\nB", "C\nD"]

    def test_no_overlap_kept_intact(self) -> None:
        cleaned = clean_segments(
            [
                Segment(0.0, 2.0, "A"),
                Segment(2.0, 4.0, "B"),
            ]
        )
        assert [s.text for s in cleaned] == ["A", "B"]
