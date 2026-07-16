"""字幕清洗：空白规范化、时间排序、滚动窗口去重（YouTube 自动字幕）。

YouTube 自动字幕以滑动窗口滚动显示——相邻 cue 形如
``行A\\n行B`` → ``行B\\n行C``，直接拼接会产生大量重复行。
本模块用"上一段行尾与当前段行首的最长重叠"消除重复；
文本完全重复的段合并入前一段并延长其结束时间。

纯函数模块，无外部依赖。
"""

from collections.abc import Sequence
from dataclasses import replace

from app.core.models import Segment


def clean_segments(segments: Sequence[Segment]) -> tuple[Segment, ...]:
    """规范化 + 排序 + 滚动去重，返回可直接进入 Transcript 的段序列。"""
    normalized = [seg for seg in (_normalize(s) for s in segments) if seg is not None]
    normalized.sort(key=lambda s: (s.start, s.end))
    return _dedup_rolling(normalized)


def _normalize(segment: Segment) -> Segment | None:
    lines = [line.strip() for line in segment.text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return None
    return replace(segment, text="\n".join(lines))


def _dedup_rolling(segments: list[Segment]) -> tuple[Segment, ...]:
    result: list[Segment] = []
    prev_lines: list[str] = []
    for seg in segments:
        lines = seg.text.splitlines()
        overlap = _line_overlap(prev_lines, lines)
        fresh_lines = lines[overlap:]
        if not fresh_lines:
            # 完全重复：并入前一段，延长结束时间
            if result and seg.end > result[-1].end:
                result[-1] = replace(result[-1], end=seg.end)
            prev_lines = lines
            continue
        result.append(replace(seg, text="\n".join(fresh_lines)))
        prev_lines = lines
    return tuple(result)


def _line_overlap(prev_lines: list[str], lines: list[str]) -> int:
    """prev_lines 行尾与 lines 行首的最长重叠行数。"""
    max_overlap = min(len(prev_lines), len(lines))
    for count in range(max_overlap, 0, -1):
        if prev_lines[-count:] == lines[:count]:
            return count
    return 0
