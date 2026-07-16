"""下载器抽象接口（能力模块只依赖 core 共享内核）。"""

from abc import ABC, abstractmethod
from pathlib import Path

from app.core.cancellation import CancellationToken
from app.core.events import ProgressFn
from app.core.models import SubtitleTrack, VideoInfo, VideoRef


class Downloader(ABC):
    """站点解析、元信息/字幕/音频获取的统一接口。"""

    @abstractmethod
    def resolve(self, url: str) -> list[VideoRef]:
        """校验并规范化 URL；播放列表/合集展开为多个 VideoRef。

        Raises:
            DownloadError: URL 无效、站点不支持或解析失败。
        """

    @abstractmethod
    def fetch_info(self, ref: VideoRef) -> tuple[VideoInfo, list[SubtitleTrack]]:
        """获取元信息与可用字幕轨列表（人工与自动字幕均含）。"""

    @abstractmethod
    def download_subtitle(
        self,
        ref: VideoRef,
        track: SubtitleTrack,
        dest_dir: Path,
        cancel: CancellationToken,
    ) -> Path:
        """下载指定字幕轨到 ``dest_dir``，返回字幕文件路径。"""

    @abstractmethod
    def download_audio(
        self,
        ref: VideoRef,
        dest_dir: Path,
        progress: ProgressFn,
        cancel: CancellationToken,
    ) -> Path:
        """仅下载 bestaudio 原始流（不转码，转换交给 AudioProcessor）。"""

    @abstractmethod
    def timestamp_url(self, ref: VideoRef, seconds: int) -> str:
        """生成带时间参数的跳转 URL（供总结中的章节时间戳链接）。"""
