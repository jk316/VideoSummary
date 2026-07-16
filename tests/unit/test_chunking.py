"""tokens 计数与 TranscriptChunker 单元测试。"""

import logging
import os
from pathlib import Path

import pytest

from app.chunking.chunker import TranscriptChunker
from app.core.models import Segment, Transcript, TranscriptSource
from app.utils.tokens import HeuristicTokenCounter, get_token_counter


class _CharCounter:
    """测试用：1 字符 = 1 token，行为完全可预测。"""

    def count(self, text: str) -> int:
        return len(text)


def _transcript(*texts: str, seconds_each: float = 10.0) -> Transcript:
    segments = tuple(
        Segment(start=i * seconds_each, end=(i + 1) * seconds_each, text=text)
        for i, text in enumerate(texts)
    )
    return Transcript(language="zh", source=TranscriptSource.SUBTITLE, segments=segments)


class TestHeuristicCounter:
    def test_cjk_counted_per_char(self) -> None:
        assert HeuristicTokenCounter().count("一二三四五") == 5

    def test_ascii_counted_per_four_chars(self) -> None:
        assert HeuristicTokenCounter().count("abcdefgh") == 2

    def test_mixed_text(self) -> None:
        # 4 个 CJK + 8 个其他字符
        assert HeuristicTokenCounter().count("中文内容abcdefgh") == 4 + 2

    def test_empty_is_zero(self) -> None:
        assert HeuristicTokenCounter().count("") == 0


class TestGetTokenCounter:
    def test_fallback_when_tiktoken_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        import sys

        monkeypatch.setitem(sys.modules, "tiktoken", None)
        with caplog.at_level(logging.WARNING):
            counter = get_token_counter()
        assert isinstance(counter, HeuristicTokenCounter)
        assert any("启发式" in r.message for r in caplog.records)

    def test_cache_dir_env_set_once(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)
        get_token_counter(cache_dir=tmp_path)
        assert os.environ.get("TIKTOKEN_CACHE_DIR") == str(tmp_path)
        get_token_counter(cache_dir=tmp_path / "other")  # 已设置时不覆盖
        assert os.environ.get("TIKTOKEN_CACHE_DIR") == str(tmp_path)
        monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)

    def test_real_tiktoken_counts_if_available(self) -> None:
        pytest.importorskip("tiktoken")
        counter = get_token_counter()
        if isinstance(counter, HeuristicTokenCounter):
            pytest.skip("BPE 编码不可用（无网络且无缓存）")
        assert counter.count("hello world") >= 2
        assert counter.count("") == 0


class TestChunkerValidation:
    def test_invalid_max_tokens_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_tokens"):
            TranscriptChunker(0, 0, _CharCounter())

    def test_invalid_overlap_rejected(self) -> None:
        with pytest.raises(ValueError, match="overlap"):
            TranscriptChunker(10, 10, _CharCounter())


class TestChunkerSplit:
    def test_small_transcript_single_chunk(self) -> None:
        chunker = TranscriptChunker(100, 0, _CharCounter())
        chunks = chunker.split(_transcript("aaaaa", "bbbbb"))
        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk.index == 0
        assert chunk.text == "aaaaa\nbbbbb"
        assert chunk.token_count == 10
        assert chunk.start == 0.0
        assert chunk.end == 20.0

    def test_split_at_segment_boundary(self) -> None:
        # 每段 10 token，上限 25：两段一块
        chunker = TranscriptChunker(25, 0, _CharCounter())
        chunks = chunker.split(_transcript("a" * 10, "b" * 10, "c" * 10, "d" * 10))
        assert [c.text for c in chunks] == ["a" * 10 + "\n" + "b" * 10, "c" * 10 + "\n" + "d" * 10]
        assert [c.index for c in chunks] == [0, 1]

    def test_time_axis_preserved(self) -> None:
        chunker = TranscriptChunker(25, 0, _CharCounter())
        chunks = chunker.split(_transcript("a" * 10, "b" * 10, "c" * 10))
        assert (chunks[0].start, chunks[0].end) == (0.0, 20.0)
        assert (chunks[1].start, chunks[1].end) == (20.0, 30.0)

    def test_overlap_repeats_trailing_segments(self) -> None:
        # 上限 25、overlap 10：第二块以前一块的最后一段开头
        chunker = TranscriptChunker(25, 10, _CharCounter())
        chunks = chunker.split(_transcript("a" * 10, "b" * 10, "c" * 10))
        assert chunks[0].text == "a" * 10 + "\n" + "b" * 10
        assert chunks[1].text == "b" * 10 + "\n" + "c" * 10
        assert chunks[1].start == 10.0  # overlap 段的时间也回溯

    def test_overlap_dropped_when_no_progress_possible(self) -> None:
        # 每段 15 token，上限 25，overlap 20：带上 overlap 就放不下新段 → 放弃 overlap
        chunker = TranscriptChunker(25, 20, _CharCounter())
        chunks = chunker.split(_transcript("a" * 15, "b" * 15, "c" * 15))
        assert [c.text for c in chunks] == ["a" * 15, "b" * 15, "c" * 15]

    def test_oversized_segment_becomes_own_chunk(self, caplog: pytest.LogCaptureFixture) -> None:
        chunker = TranscriptChunker(10, 0, _CharCounter())
        with caplog.at_level(logging.WARNING):
            chunks = chunker.split(_transcript("short", "x" * 50, "tail"))
        assert [c.token_count for c in chunks] == [5, 50, 4]
        assert any("独立成块" in r.message for r in caplog.records)

    def test_empty_transcript_returns_empty(self) -> None:
        transcript = Transcript(language="zh", source=TranscriptSource.SUBTITLE, segments=())
        assert TranscriptChunker(10, 0, _CharCounter()).split(transcript) == []
