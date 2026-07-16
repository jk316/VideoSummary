"""Transcript 切块：按 token 贪心聚合 segment，保留时间轴与 overlap。

以 Segment 为最小切分单元（不切断句子）；单个 segment 超过
max_tokens 时独立成块并告警。overlap 取前一 chunk 尾部若干
segment；若 overlap 加新段即超限，丢弃 overlap 以保证前进。
"""

import logging

from app.core.models import Chunk, Transcript
from app.utils.tokens import TokenCounter

logger = logging.getLogger(__name__)


class TranscriptChunker:
    """按 token 数切块；counter 注入便于测试与离线回退。"""

    def __init__(self, max_tokens: int, overlap_tokens: int, counter: TokenCounter) -> None:
        if max_tokens <= 0:
            raise ValueError(f"max_tokens 需 > 0，实际 {max_tokens}")
        if not 0 <= overlap_tokens < max_tokens:
            raise ValueError(f"overlap_tokens 需在 [0, max_tokens)，实际 {overlap_tokens}")
        self._max_tokens = max_tokens
        self._overlap_tokens = overlap_tokens
        self._counter = counter

    def split(self, transcript: Transcript) -> list[Chunk]:
        """切分为带时间轴的 Chunk 列表；单 chunk 即可容纳时返回单元素列表。"""
        segments = transcript.segments
        if not segments:
            return []
        seg_tokens = [self._counter.count(s.text) for s in segments]
        chunks: list[Chunk] = []
        current: list[int] = []
        current_tokens = 0
        for index, tokens in enumerate(seg_tokens):
            if current and current_tokens + tokens > self._max_tokens:
                chunks.append(self._build_chunk(len(chunks), current, transcript, seg_tokens))
                current, current_tokens = self._carry_overlap(current, seg_tokens, tokens)
            if not current and tokens > self._max_tokens:
                logger.warning(
                    "单个 segment 超过 chunk 上限（%d > %d tokens），独立成块",
                    tokens,
                    self._max_tokens,
                )
            current.append(index)
            current_tokens += tokens
        chunks.append(self._build_chunk(len(chunks), current, transcript, seg_tokens))
        return chunks

    def _carry_overlap(
        self, previous: list[int], seg_tokens: list[int], next_tokens: int
    ) -> tuple[list[int], int]:
        """计算带入新 chunk 的 overlap 段；无法容纳新段时放弃 overlap 保证前进。"""
        overlap: list[int] = []
        total = 0
        for index in reversed(previous):
            if total + seg_tokens[index] > self._overlap_tokens:
                break
            overlap.insert(0, index)
            total += seg_tokens[index]
        if overlap and total + next_tokens > self._max_tokens:
            return [], 0
        return overlap, total

    @staticmethod
    def _build_chunk(
        index: int, seg_indices: list[int], transcript: Transcript, seg_tokens: list[int]
    ) -> Chunk:
        segments = [transcript.segments[i] for i in seg_indices]
        return Chunk(
            index=index,
            start=segments[0].start,
            end=segments[-1].end,
            text="\n".join(s.text for s in segments),
            token_count=sum(seg_tokens[i] for i in seg_indices),
        )
