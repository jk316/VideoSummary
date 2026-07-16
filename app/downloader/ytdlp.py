"""基于 yt-dlp sidecar 可执行文件的下载器实现（subprocess 调用）。

sidecar 形态使 yt-dlp 可通过 ``yt-dlp.exe -U`` 独立自更新，
站点接口变更无需重新分发应用（见 architecture.md §5.1）。
"""

import json
import logging
import re
from collections import deque
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from app.config.schema import DownloaderConfig, NetworkConfig
from app.core.cancellation import CancellationToken
from app.core.errors import DownloadError, TaskCancelled
from app.core.events import ProgressFn
from app.core.models import Site, SubtitleTrack, VideoInfo, VideoRef
from app.downloader.base import Downloader
from app.utils.proxy import resolve_ytdlp_proxy
from app.utils.subproc import (
    RunResult,
    SubprocessCancelled,
    SubprocessTimeout,
    run_capture,
    run_streaming,
)

logger = logging.getLogger(__name__)

_SUPPORTED_HOSTS = ("youtube.com", "youtu.be", "bilibili.com", "b23.tv")
_PREFERRED_SUB_FORMATS = ("vtt", "srt", "json3", "json")
_PROGRESS_TEMPLATE = "download:[VSPROG]|%(progress._percent_str)s|%(progress._speed_str)s"
_PROGRESS_RE = re.compile(r"\[VSPROG\]\|\s*([\d.]+)%\|(.*)")
_INFO_TIMEOUT_FLOOR = 60
_UPDATE_TIMEOUT = 600
_ERROR_TAIL_LINES = 20
AUDIO_SOURCE_NAME = "audio.source"


class YtDlpDownloader(Downloader):
    """yt-dlp sidecar 实现；binary 路径由装配层从配置/默认位置解析后注入。"""

    def __init__(self, binary: Path, downloader: DownloaderConfig, network: NetworkConfig) -> None:
        self._binary = binary
        self._config = downloader
        self._network = network

    # ---------------------------------------------------------------- 接口实现

    def resolve(self, url: str) -> list[VideoRef]:
        _validate_url(url)
        data = self._dump_json(url, flat_playlist=True)
        if data.get("_type") == "playlist":
            return self._refs_from_playlist(data)
        return [_ref_from_entry(data, playlist_index=None)]

    def fetch_info(self, ref: VideoRef) -> tuple[VideoInfo, list[SubtitleTrack]]:
        data = self._dump_json(ref.url, flat_playlist=False)
        title = str(data.get("title") or ref.title or ref.video_id)
        info = VideoInfo(
            ref=replace(ref, title=title),
            title=title,
            duration=float(data.get("duration") or 0.0),
            author=str(data.get("uploader") or data.get("channel") or ""),
        )
        return info, _parse_subtitle_tracks(data)

    def download_subtitle(
        self,
        ref: VideoRef,
        track: SubtitleTrack,
        dest_dir: Path,
        cancel: CancellationToken,
    ) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        flag = "--write-auto-subs" if track.is_auto else "--write-subs"
        args = [
            *self._base_args(),
            "--skip-download",
            "--no-playlist",
            flag,
            "--sub-langs",
            track.lang,
            "--sub-format",
            f"{track.format}/best",
            "-o",
            str(dest_dir / "subtitle"),
            ref.url,
        ]
        self._run_checked(args, action="字幕下载", cancel=cancel)
        return _find_subtitle_file(dest_dir, track)

    def download_audio(
        self,
        ref: VideoRef,
        dest_dir: Path,
        progress: ProgressFn,
        cancel: CancellationToken,
    ) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / AUDIO_SOURCE_NAME
        args = [
            *self._base_args(),
            "-f",
            "bestaudio/best",
            "--no-playlist",
            "--newline",
            "--progress-template",
            _PROGRESS_TEMPLATE,
            "-o",
            str(target),
            ref.url,
        ]
        tail: deque[str] = deque(maxlen=_ERROR_TAIL_LINES)

        def on_line(line: str) -> None:
            tail.append(line)
            _report_progress_line(line, progress)

        returncode = self._stream_checked(args, on_line=on_line, cancel=cancel, action="音频下载")
        if returncode != 0:
            detail = " / ".join(tail)
            raise DownloadError(
                f"音频下载失败 (exit {returncode}): {detail}",
                user_message=_friendly_error(detail, "音频下载"),
            )
        if not target.exists():
            raise DownloadError("yt-dlp 正常退出但未生成音频文件")
        return target

    def timestamp_url(self, ref: VideoRef, seconds: int) -> str:
        base = _canonical_url(ref.site, ref.video_id)
        if ref.site is Site.YOUTUBE:
            return f"{base}&t={seconds}s"
        return f"{base}?t={seconds}"

    # ------------------------------------------------------------ 组件管理

    def check_available(self) -> str:
        """返回 yt-dlp 版本号；不可用时抛 DownloadError。"""
        result = self._run_checked(
            [str(self._require_binary()), "--version"], action="检查 yt-dlp", timeout=30
        )
        return result.stdout.strip()

    def update_binary(self) -> str:
        """运行 ``yt-dlp -U`` 自更新，返回结果说明（设置界面展示）。"""
        result = self._run_checked(
            [str(self._require_binary()), "-U"], action="更新 yt-dlp", timeout=_UPDATE_TIMEOUT
        )
        lines = result.stdout.strip().splitlines()
        return lines[-1] if lines else "已是最新版本"

    # ---------------------------------------------------------------- 内部

    def _base_args(self) -> list[str]:
        args = [
            str(self._require_binary()),
            "--no-warnings",
            "--retries",
            str(self._config.retries),
            "--socket-timeout",
            str(self._config.socket_timeout),
        ]
        proxy = resolve_ytdlp_proxy(self._network.proxy, self._network.use_system_proxy)
        if proxy is not None:
            args += ["--proxy", proxy]
        if self._config.cookies_file:
            args += ["--cookies", self._config.cookies_file]
        return args

    def _require_binary(self) -> Path:
        if not self._binary.is_file():
            raise DownloadError(
                f"yt-dlp 不存在: {self._binary}",
                user_message="未找到 yt-dlp 组件，请在设置中检查路径或更新下载组件。",
            )
        return self._binary

    def _dump_json(
        self, url: str, *, flat_playlist: bool, cancel: CancellationToken | None = None
    ) -> dict:
        args = [*self._base_args(), "--dump-single-json", "--no-download"]
        args.append("--flat-playlist" if flat_playlist else "--no-playlist")
        args.append(url)
        timeout = max(_INFO_TIMEOUT_FLOOR, self._config.socket_timeout * 3)
        result = self._run_checked(args, action="获取视频信息", cancel=cancel, timeout=timeout)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise DownloadError(f"yt-dlp 输出不是合法 JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise DownloadError(f"yt-dlp 输出结构异常: {type(data).__name__}")
        return data

    def _run_checked(
        self,
        args: list[str],
        *,
        action: str,
        cancel: CancellationToken | None = None,
        timeout: float | None = None,
    ) -> RunResult:
        should_cancel = cancel.is_cancelled if cancel is not None else None
        try:
            result = run_capture(args, timeout=timeout, should_cancel=should_cancel)
        except SubprocessCancelled:
            raise TaskCancelled() from None
        except SubprocessTimeout as exc:
            raise DownloadError(
                f"{action}超时: {exc}", user_message=f"{action}超时，请检查网络或代理设置。"
            ) from exc
        except OSError as exc:
            raise DownloadError(
                f"无法启动 yt-dlp: {exc}", user_message="无法启动 yt-dlp 组件，请检查设置。"
            ) from exc
        if result.returncode != 0:
            detail = _tail(result.stderr)
            raise DownloadError(
                f"{action}失败 (exit {result.returncode}): {detail}",
                user_message=_friendly_error(detail, action),
            )
        return result

    def _stream_checked(
        self,
        args: list[str],
        *,
        on_line,
        cancel: CancellationToken,
        action: str,
    ) -> int:
        idle_timeout = max(_INFO_TIMEOUT_FLOOR, self._config.socket_timeout * 2)
        try:
            return run_streaming(
                args,
                on_line=on_line,
                idle_timeout=idle_timeout,
                should_cancel=cancel.is_cancelled,
            )
        except SubprocessCancelled:
            raise TaskCancelled() from None
        except SubprocessTimeout as exc:
            raise DownloadError(
                f"{action}停滞: {exc}", user_message=f"{action}长时间无进展，请检查网络或代理。"
            ) from exc
        except OSError as exc:
            raise DownloadError(
                f"无法启动 yt-dlp: {exc}", user_message="无法启动 yt-dlp 组件，请检查设置。"
            ) from exc

    def _refs_from_playlist(self, data: dict) -> list[VideoRef]:
        refs = [
            _ref_from_entry(entry, playlist_index=index)
            for index, entry in enumerate(data.get("entries") or [], start=1)
            if entry
        ]
        if not refs:
            raise DownloadError(
                f"播放列表无有效条目: {data.get('id')}",
                user_message="播放列表为空或全部条目不可用。",
            )
        return refs


# -------------------------------------------------------------------- 纯函数


def _validate_url(url: str) -> None:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise DownloadError(
            f"无效 URL: {url!r}", user_message="请输入有效的视频链接（http/https）。"
        )
    host = parsed.netloc.lower().rsplit(":", 1)[0]
    if not any(host == h or host.endswith(f".{h}") for h in _SUPPORTED_HOSTS):
        raise DownloadError(
            f"不支持的站点: {host}", user_message="暂仅支持 YouTube 与 Bilibili 链接。"
        )


def _detect_site(data: dict) -> Site:
    for key in ("extractor_key", "ie_key", "extractor"):
        value = str(data.get(key) or "").lower()
        if "youtube" in value:
            return Site.YOUTUBE
        if "bilibili" in value:
            return Site.BILIBILI
    host = urlparse(str(data.get("webpage_url") or data.get("url") or "")).netloc.lower()
    if "youtu" in host:
        return Site.YOUTUBE
    if "bilibili" in host or "b23.tv" in host:
        return Site.BILIBILI
    raise DownloadError(
        f"无法识别站点: {data.get('id')}", user_message="暂仅支持 YouTube 与 Bilibili 链接。"
    )


def _ref_from_entry(entry: dict, playlist_index: int | None) -> VideoRef:
    site = _detect_site(entry)
    video_id = str(entry.get("id") or "")
    if not video_id:
        raise DownloadError(f"条目缺少视频 ID: {entry.get('title')}")
    title = entry.get("title")
    return VideoRef(
        site=site,
        video_id=video_id,
        url=_canonical_url(site, video_id),
        title=str(title) if title else None,
        playlist_index=playlist_index,
    )


def _canonical_url(site: Site, video_id: str) -> str:
    if site is Site.YOUTUBE:
        return f"https://www.youtube.com/watch?v={video_id}"
    return f"https://www.bilibili.com/video/{video_id}"


def _parse_subtitle_tracks(data: dict) -> list[SubtitleTrack]:
    tracks: list[SubtitleTrack] = []
    for source_key, is_auto in (("subtitles", False), ("automatic_captions", True)):
        for lang, formats in (data.get(source_key) or {}).items():
            exts = [str(f["ext"]) for f in formats if isinstance(f, dict) and f.get("ext")]
            if not exts:
                continue
            fmt = next((p for p in _PREFERRED_SUB_FORMATS if p in exts), exts[0])
            tracks.append(SubtitleTrack(lang=str(lang), is_auto=is_auto, format=fmt))
    return tracks


def _find_subtitle_file(dest_dir: Path, track: SubtitleTrack) -> Path:
    exact = dest_dir / f"subtitle.{track.lang}.{track.format}"
    if exact.is_file():
        return exact
    matches = sorted(dest_dir.glob(f"subtitle.{track.lang}.*"))
    if matches:
        return matches[0]
    raise DownloadError(
        f"yt-dlp 正常退出但未生成字幕文件: {track.lang}/{track.format}",
        user_message="字幕下载失败（站点未返回字幕文件）。",
    )


def _report_progress_line(line: str, progress: ProgressFn) -> None:
    match = _PROGRESS_RE.search(line)
    if not match:
        return
    percent = float(match.group(1))
    speed = match.group(2).strip()
    progress(percent / 100, f"下载音频 {percent:.1f}% {speed}".rstrip())


def _friendly_error(detail: str, action: str) -> str:
    lowered = detail.lower()
    if any(k in lowered for k in ("sign in", "login required", "premium", "membership")):
        return "该视频需要登录或会员权限，可在设置中配置 cookies 后重试。"
    if any(k in lowered for k in ("private", "removed", "not available", "unavailable")):
        return "视频不可用（可能已删除、设为私有或有地区限制）。"
    if "unsupported url" in lowered:
        return "暂不支持该链接。"
    if any(
        k in lowered
        for k in ("unable to connect", "timed out", "getaddrinfo", "connection", "proxy", "ssl")
    ):
        return f"{action}失败：网络不通，请检查网络或代理设置。"
    return f"{action}失败，详情见日志。"


def _tail(text: str, limit: int = 500) -> str:
    compact = " / ".join(line.strip() for line in text.strip().splitlines() if line.strip())
    return compact[-limit:]
