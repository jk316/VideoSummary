"""领域数据模型：全部为不可变 dataclass，跨阶段传值不共享可变状态。

需要落盘的模型（VideoRef/VideoInfo/SubtitleTrack/Transcript）提供
``to_dict``/``from_dict``，缓存层只负责字节/文本存取。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.core.events import Stage


class Site(StrEnum):
    """支持的视频站点。"""

    YOUTUBE = "youtube"
    BILIBILI = "bilibili"


class TranscriptSource(StrEnum):
    """转写文本来源。"""

    SUBTITLE = "subtitle"
    STT = "stt"


class SummaryLanguage(StrEnum):
    """总结输出语言。"""

    ZH = "zh"
    EN = "en"
    BILINGUAL = "bilingual"


@dataclass(frozen=True)
class VideoRef:
    """``resolve()`` 的输出：视频标识 + 粗标题。

    Attributes:
        title: flat-playlist 提供的粗标题，供任务列表先行显示；
            ``fetch_info`` 后以 ``VideoInfo.title`` 为准。
    """

    site: Site
    video_id: str
    url: str
    title: str | None = None
    playlist_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "site": str(self.site),
            "video_id": self.video_id,
            "url": self.url,
            "title": self.title,
            "playlist_index": self.playlist_index,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VideoRef:
        return cls(
            site=Site(str(data["site"])),
            video_id=str(data["video_id"]),
            url=str(data["url"]),
            title=data.get("title"),
            playlist_index=data.get("playlist_index"),
        )


@dataclass(frozen=True)
class VideoInfo:
    """视频元信息。"""

    ref: VideoRef
    title: str
    duration: float
    author: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref.to_dict(),
            "title": self.title,
            "duration": self.duration,
            "author": self.author,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VideoInfo:
        return cls(
            ref=VideoRef.from_dict(data["ref"]),
            title=str(data["title"]),
            duration=float(data["duration"]),
            author=str(data["author"]),
        )


@dataclass(frozen=True)
class SubtitleTrack:
    """可用字幕轨。"""

    lang: str
    is_auto: bool
    format: str

    def to_dict(self) -> dict[str, Any]:
        return {"lang": self.lang, "is_auto": self.is_auto, "format": self.format}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SubtitleTrack:
        return cls(
            lang=str(data["lang"]),
            is_auto=bool(data["is_auto"]),
            format=str(data["format"]),
        )


@dataclass(frozen=True)
class Segment:
    """带时间戳的文本片段（秒）。"""

    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "text": self.text}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Segment:
        return cls(start=float(data["start"]), end=float(data["end"]), text=str(data["text"]))


@dataclass(frozen=True)
class Transcript:
    """统一转写结果（字幕或 STT 产出）。"""

    language: str
    source: TranscriptSource
    segments: tuple[Segment, ...]

    @property
    def text(self) -> str:
        """全文（各 segment 按行拼接）。"""
        return "\n".join(s.text for s in self.segments)

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "source": str(self.source),
            "segments": [s.to_dict() for s in self.segments],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Transcript:
        return cls(
            language=str(data["language"]),
            source=TranscriptSource(str(data["source"])),
            segments=tuple(Segment.from_dict(s) for s in data["segments"]),
        )


@dataclass(frozen=True)
class Chunk:
    """按 token 切分后的片段，保留时间轴。"""

    index: int
    start: float
    end: float
    text: str
    token_count: int


@dataclass(frozen=True)
class TokenUsage:
    """LLM token 用量（不可变累加）。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
        )


@dataclass(frozen=True)
class SummaryResult:
    """最终总结结果。"""

    markdown: str
    language: SummaryLanguage
    chunk_count: int
    usage: TokenUsage
    elapsed_seconds: float


@dataclass(frozen=True)
class StageTiming:
    """单阶段耗时（NFR-6 耗时分解）。"""

    stage: Stage
    seconds: float


@dataclass(frozen=True)
class TaskResult:
    """单任务完整产出。"""

    info: VideoInfo
    transcript: Transcript
    summary: SummaryResult
    output_files: tuple[Path, ...]
    timings: tuple[StageTiming, ...]
