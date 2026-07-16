"""字幕解析：vtt / srt / json3（YouTube）/ B站 JSON → 统一 Transcript。

vtt 与 srt 共用块解析器（按空行分块、识别 ``-->`` 时间行）；
json 类格式按内容分发（``events`` → json3，``body`` → B站），
比按扩展名分发更抗 yt-dlp 的命名差异。
"""

import html
import json
import re
from pathlib import Path

from app.core.errors import SubtitleError
from app.core.models import Segment, Transcript, TranscriptSource
from app.subtitle.cleaner import clean_segments

_TIMING_RE = re.compile(
    r"(?P<start>(?:\d+:)?\d{1,2}:\d{1,2}[.,]\d{1,3})\s*-->\s*"
    r"(?P<end>(?:\d+:)?\d{1,2}:\d{1,2}[.,]\d{1,3})"
)
_TIME_PARTS_RE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{1,2})[.,](\d{1,3})")
_INLINE_TAG_RE = re.compile(r"<[^>]*>")
_BLOCK_SPLIT_RE = re.compile(r"\r?\n\s*\r?\n")

_CUE_FORMATS = ("vtt", "webvtt", "srt")
_JSON_FORMATS = ("json", "json3", "bili_json")


def parse_subtitle(path: Path, fmt: str, language: str) -> Transcript:
    """解析字幕文件为 Transcript（segments 已清洗去重）。

    Args:
        path: 字幕文件路径。
        fmt: 字幕格式（yt-dlp 的 ext，如 "vtt"/"srt"/"json3"/"json"）。
        language: 字幕语言代码（写入 Transcript.language）。

    Raises:
        SubtitleError: 格式不支持、内容无法解析或解析结果为空。
    """
    text = _read_file(path)
    normalized_fmt = fmt.lower().strip()
    if normalized_fmt in _CUE_FORMATS:
        segments = _parse_cue_blocks(text)
    elif normalized_fmt in _JSON_FORMATS:
        segments = _parse_json_content(text, path)
    else:
        raise SubtitleError(
            f"不支持的字幕格式: {fmt} ({path})",
            user_message=f"暂不支持 {fmt} 格式的字幕。",
        )
    cleaned = clean_segments(segments)
    if not cleaned:
        raise SubtitleError(f"字幕解析结果为空: {path}", user_message="字幕内容为空或无法解析。")
    return Transcript(language=language, source=TranscriptSource.SUBTITLE, segments=cleaned)


def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise SubtitleError(f"无法读取字幕文件: {path}: {exc}") from exc


# ------------------------------------------------------------- vtt / srt


def _parse_cue_blocks(text: str) -> list[Segment]:
    """按空行分块解析 cue；无时间行的块（WEBVTT 头/NOTE/STYLE/序号）自然跳过。"""
    segments: list[Segment] = []
    for block in _BLOCK_SPLIT_RE.split(text):
        lines = [line for line in block.splitlines() if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if _TIMING_RE.search(line)), None)
        if timing_index is None:
            continue
        match = _TIMING_RE.search(lines[timing_index])
        assert match is not None
        content = _strip_markup("\n".join(lines[timing_index + 1 :]))
        if not content.strip():
            continue
        segments.append(
            Segment(
                start=_parse_timestamp(match.group("start")),
                end=_parse_timestamp(match.group("end")),
                text=content,
            )
        )
    return segments


def _parse_timestamp(value: str) -> float:
    match = _TIME_PARTS_RE.fullmatch(value.strip())
    if match is None:
        raise SubtitleError(f"无法解析时间戳: {value!r}")
    hours, minutes, seconds, millis = match.groups()
    return (
        int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, "0")) / 1000
    )


def _strip_markup(text: str) -> str:
    return html.unescape(_INLINE_TAG_RE.sub("", text))


# ------------------------------------------------------------- json 类


def _parse_json_content(text: str, path: Path) -> list[Segment]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SubtitleError(f"字幕 JSON 解析失败: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SubtitleError(f"字幕 JSON 结构异常: {path}")
    if "events" in data:
        return _parse_json3_events(data)
    if "body" in data:
        return _parse_bilibili_body(data)
    raise SubtitleError(f"无法识别的字幕 JSON 结构: {path}", user_message="字幕格式无法识别。")


def _parse_json3_events(data: dict) -> list[Segment]:
    segments: list[Segment] = []
    for event in data.get("events") or []:
        if not isinstance(event, dict):
            continue
        segs = event.get("segs")
        if not segs:
            continue  # 窗口定义等非文本事件
        content = "".join(str(part.get("utf8") or "") for part in segs if isinstance(part, dict))
        if not content.strip():
            continue  # aAppend 换行填充事件
        start = float(event.get("tStartMs") or 0) / 1000
        duration = float(event.get("dDurationMs") or 0) / 1000
        segments.append(Segment(start=start, end=start + duration, text=content))
    return segments


def _parse_bilibili_body(data: dict) -> list[Segment]:
    segments: list[Segment] = []
    for item in data.get("body") or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        if not content.strip():
            continue
        start = float(item.get("from") or 0)
        end = float(item.get("to") or start)
        segments.append(Segment(start=start, end=end, text=content))
    return segments
