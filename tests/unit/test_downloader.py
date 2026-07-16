"""YtDlpDownloader 单元测试：mock subprocess 层，fixtures 为预录 yt-dlp JSON。"""

import json
from pathlib import Path

import pytest

from app.config.schema import DownloaderConfig, NetworkConfig
from app.core.cancellation import CancellationToken
from app.core.errors import DownloadError, TaskCancelled
from app.core.models import Site, SubtitleTrack, VideoRef
from app.downloader import ytdlp as ytdlp_module
from app.downloader.ytdlp import YtDlpDownloader
from app.utils.subproc import RunResult, SubprocessCancelled

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ytdlp"


def _load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def binary(tmp_path: Path) -> Path:
    path = tmp_path / "yt-dlp.exe"
    path.write_bytes(b"stub")
    return path


def _make_downloader(
    binary: Path,
    downloader: DownloaderConfig | None = None,
    network: NetworkConfig | None = None,
) -> YtDlpDownloader:
    return YtDlpDownloader(
        binary=binary,
        downloader=downloader or DownloaderConfig(),
        network=network or NetworkConfig(),
    )


def _patch_capture(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str = "",
    returncode: int = 0,
    stderr: str = "",
) -> list[list[str]]:
    """替换 run_capture，记录调用参数并返回预置结果。"""
    calls: list[list[str]] = []

    def fake(args, *, timeout=None, should_cancel=None):
        calls.append(list(args))
        return RunResult(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(ytdlp_module, "run_capture", fake)
    return calls


class TestResolve:
    def test_single_video(self, monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
        calls = _patch_capture(monkeypatch, stdout=_load_fixture("youtube_video.json"))
        refs = _make_downloader(binary).resolve("https://youtu.be/dQw4w9WgXcQ")
        assert refs == [
            VideoRef(
                site=Site.YOUTUBE,
                video_id="dQw4w9WgXcQ",
                url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                title="Never Gonna Give You Up",
            )
        ]
        assert "--dump-single-json" in calls[0]
        assert "--flat-playlist" in calls[0]

    def test_playlist_expanded_with_index_and_title(
        self, monkeypatch: pytest.MonkeyPatch, binary: Path
    ) -> None:
        _patch_capture(monkeypatch, stdout=_load_fixture("youtube_playlist_flat.json"))
        refs = _make_downloader(binary).resolve("https://www.youtube.com/playlist?list=PLtest123")
        assert len(refs) == 2  # null 条目被跳过
        assert refs[0].playlist_index == 1
        assert refs[0].title == "第一集：入门"
        assert refs[1].video_id == "video0000002"
        assert refs[1].playlist_index == 3  # 保留原始位置

    def test_bilibili_video(self, monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
        _patch_capture(monkeypatch, stdout=_load_fixture("bilibili_video.json"))
        refs = _make_downloader(binary).resolve("https://b23.tv/abc123")
        assert refs[0].site is Site.BILIBILI
        assert refs[0].url == "https://www.bilibili.com/video/BV1GJ411x7h7"

    def test_invalid_url_rejected_without_subprocess(
        self, monkeypatch: pytest.MonkeyPatch, binary: Path
    ) -> None:
        calls = _patch_capture(monkeypatch)
        with pytest.raises(DownloadError):
            _make_downloader(binary).resolve("不是链接")
        assert calls == []

    def test_unsupported_site_rejected(self, monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
        calls = _patch_capture(monkeypatch)
        with pytest.raises(DownloadError, match="不支持"):
            _make_downloader(binary).resolve("https://vimeo.com/12345")
        assert calls == []

    def test_network_failure_maps_to_friendly_message(
        self, monkeypatch: pytest.MonkeyPatch, binary: Path
    ) -> None:
        _patch_capture(monkeypatch, returncode=1, stderr="ERROR: Unable to connect to proxy")
        with pytest.raises(DownloadError) as exc_info:
            _make_downloader(binary).resolve("https://youtu.be/abc")
        assert "代理" in exc_info.value.user_message

    def test_missing_binary_raises(self, tmp_path: Path) -> None:
        downloader = _make_downloader(tmp_path / "nonexistent.exe")
        with pytest.raises(DownloadError, match="yt-dlp"):
            downloader.resolve("https://youtu.be/abc")


class TestFetchInfo:
    def test_parses_info_and_subtitle_tracks(
        self, monkeypatch: pytest.MonkeyPatch, binary: Path
    ) -> None:
        _patch_capture(monkeypatch, stdout=_load_fixture("youtube_video.json"))
        ref = VideoRef(site=Site.YOUTUBE, video_id="dQw4w9WgXcQ", url="https://x")
        info, tracks = _make_downloader(binary).fetch_info(ref)
        assert info.title == "Never Gonna Give You Up"
        assert info.duration == 213.0
        assert info.author == "Rick Astley"
        assert info.ref.title == info.title  # 粗标题被正式标题覆盖
        assert SubtitleTrack(lang="en", is_auto=False, format="vtt") in tracks  # vtt 优先
        assert SubtitleTrack(lang="zh-Hans", is_auto=True, format="json3") in tracks

    def test_no_playlist_flag_used(self, monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
        calls = _patch_capture(monkeypatch, stdout=_load_fixture("youtube_video.json"))
        ref = VideoRef(site=Site.YOUTUBE, video_id="dQw4w9WgXcQ", url="https://x")
        _make_downloader(binary).fetch_info(ref)
        assert "--no-playlist" in calls[0]
        assert "--flat-playlist" not in calls[0]


class TestDownloadSubtitle:
    def _ref(self) -> VideoRef:
        return VideoRef(site=Site.YOUTUBE, video_id="abc", url="https://x")

    def test_manual_track_uses_write_subs(
        self, monkeypatch: pytest.MonkeyPatch, binary: Path, tmp_path: Path
    ) -> None:
        dest = tmp_path / "cache"
        calls: list[list[str]] = []

        def fake(args, *, timeout=None, should_cancel=None):
            calls.append(list(args))
            (dest / "subtitle.en.vtt").write_text("WEBVTT", encoding="utf-8")
            return RunResult(0, "", "")

        monkeypatch.setattr(ytdlp_module, "run_capture", fake)
        track = SubtitleTrack(lang="en", is_auto=False, format="vtt")
        path = _make_downloader(binary).download_subtitle(
            self._ref(), track, dest, CancellationToken()
        )
        assert path == dest / "subtitle.en.vtt"
        assert "--write-subs" in calls[0]
        assert "--write-auto-subs" not in calls[0]
        assert calls[0][calls[0].index("--sub-langs") + 1] == "en"

    def test_auto_track_uses_write_auto_subs(
        self, monkeypatch: pytest.MonkeyPatch, binary: Path, tmp_path: Path
    ) -> None:
        dest = tmp_path / "cache"
        calls: list[list[str]] = []

        def fake(args, *, timeout=None, should_cancel=None):
            calls.append(list(args))
            (dest / "subtitle.zh-Hans.json3").write_text("{}", encoding="utf-8")
            return RunResult(0, "", "")

        monkeypatch.setattr(ytdlp_module, "run_capture", fake)
        track = SubtitleTrack(lang="zh-Hans", is_auto=True, format="json3")
        path = _make_downloader(binary).download_subtitle(
            self._ref(), track, dest, CancellationToken()
        )
        assert path.name == "subtitle.zh-Hans.json3"
        assert "--write-auto-subs" in calls[0]

    def test_missing_output_file_raises(
        self, monkeypatch: pytest.MonkeyPatch, binary: Path, tmp_path: Path
    ) -> None:
        _patch_capture(monkeypatch)  # 正常退出但不生成文件
        track = SubtitleTrack(lang="en", is_auto=False, format="vtt")
        with pytest.raises(DownloadError, match="字幕"):
            _make_downloader(binary).download_subtitle(
                self._ref(), track, tmp_path / "cache", CancellationToken()
            )


class TestDownloadAudio:
    def _ref(self) -> VideoRef:
        return VideoRef(site=Site.YOUTUBE, video_id="abc", url="https://x")

    def test_progress_parsed_and_file_returned(
        self, monkeypatch: pytest.MonkeyPatch, binary: Path, tmp_path: Path
    ) -> None:
        dest = tmp_path / "cache"
        recorded_args: list[str] = []

        def fake_streaming(args, *, on_line, idle_timeout=None, should_cancel=None):
            recorded_args.extend(args)
            on_line("[VSPROG]|  42.0%|1.20MiB/s")
            on_line("[VSPROG]| 100.0%|2.00MiB/s")
            (dest / "audio.source").write_bytes(b"audio-bytes")
            return 0

        monkeypatch.setattr(ytdlp_module, "run_streaming", fake_streaming)
        events: list[tuple[float | None, str]] = []
        path = _make_downloader(binary).download_audio(
            self._ref(), dest, lambda f, m: events.append((f, m)), CancellationToken()
        )
        assert path == dest / "audio.source"
        assert events[0][0] == pytest.approx(0.42)
        assert events[-1][0] == pytest.approx(1.0)
        assert "bestaudio/best" in recorded_args
        assert "-x" not in recorded_args  # 不做多余转码

    def test_nonzero_exit_raises_download_error(
        self, monkeypatch: pytest.MonkeyPatch, binary: Path, tmp_path: Path
    ) -> None:
        def fake_streaming(args, *, on_line, idle_timeout=None, should_cancel=None):
            on_line("ERROR: HTTP Error 403: Forbidden")
            return 1

        monkeypatch.setattr(ytdlp_module, "run_streaming", fake_streaming)
        with pytest.raises(DownloadError, match="403"):
            _make_downloader(binary).download_audio(
                self._ref(), tmp_path, lambda f, m: None, CancellationToken()
            )

    def test_cancel_translated_to_task_cancelled(
        self, monkeypatch: pytest.MonkeyPatch, binary: Path, tmp_path: Path
    ) -> None:
        def fake_streaming(args, **kwargs):
            raise SubprocessCancelled("cancelled")

        monkeypatch.setattr(ytdlp_module, "run_streaming", fake_streaming)
        with pytest.raises(TaskCancelled):
            _make_downloader(binary).download_audio(
                self._ref(), tmp_path, lambda f, m: None, CancellationToken()
            )


class TestArgsBuilding:
    def test_explicit_proxy_passed(self, monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
        calls = _patch_capture(monkeypatch, stdout=_load_fixture("youtube_video.json"))
        network = NetworkConfig(proxy="http://127.0.0.1:7890")
        _make_downloader(binary, network=network).resolve("https://youtu.be/abc")
        args = calls[0]
        assert args[args.index("--proxy") + 1] == "http://127.0.0.1:7890"

    def test_system_proxy_omits_flag(self, monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
        calls = _patch_capture(monkeypatch, stdout=_load_fixture("youtube_video.json"))
        _make_downloader(binary).resolve("https://youtu.be/abc")
        assert "--proxy" not in calls[0]

    def test_direct_connection_forced_when_system_proxy_disabled(
        self, monkeypatch: pytest.MonkeyPatch, binary: Path
    ) -> None:
        calls = _patch_capture(monkeypatch, stdout=_load_fixture("youtube_video.json"))
        network = NetworkConfig(proxy="", use_system_proxy=False)
        _make_downloader(binary, network=network).resolve("https://youtu.be/abc")
        args = calls[0]
        assert args[args.index("--proxy") + 1] == ""

    def test_cookies_and_timeouts_passed(
        self, monkeypatch: pytest.MonkeyPatch, binary: Path
    ) -> None:
        calls = _patch_capture(monkeypatch, stdout=_load_fixture("youtube_video.json"))
        config = DownloaderConfig(cookies_file="C:/cookies.txt", retries=5, socket_timeout=15)
        _make_downloader(binary, downloader=config).resolve("https://youtu.be/abc")
        args = calls[0]
        assert args[args.index("--cookies") + 1] == "C:/cookies.txt"
        assert args[args.index("--retries") + 1] == "5"
        assert args[args.index("--socket-timeout") + 1] == "15"


class TestTimestampUrl:
    def test_youtube_format(self, binary: Path) -> None:
        ref = VideoRef(site=Site.YOUTUBE, video_id="abc", url="https://x")
        url = _make_downloader(binary).timestamp_url(ref, 754)
        assert url == "https://www.youtube.com/watch?v=abc&t=754s"

    def test_bilibili_format(self, binary: Path) -> None:
        ref = VideoRef(site=Site.BILIBILI, video_id="BV1x", url="https://x")
        url = _make_downloader(binary).timestamp_url(ref, 90)
        assert url == "https://www.bilibili.com/video/BV1x?t=90"


class TestComponentManagement:
    def test_check_available_returns_version(
        self, monkeypatch: pytest.MonkeyPatch, binary: Path
    ) -> None:
        _patch_capture(monkeypatch, stdout="2026.07.01\n")
        assert _make_downloader(binary).check_available() == "2026.07.01"

    def test_update_binary_returns_last_line(
        self, monkeypatch: pytest.MonkeyPatch, binary: Path
    ) -> None:
        _patch_capture(monkeypatch, stdout="Updating...\nUpdated to 2026.07.15\n")
        assert _make_downloader(binary).update_binary() == "Updated to 2026.07.15"


class TestJsonEdgeCases:
    def test_malformed_json_raises(self, monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
        _patch_capture(monkeypatch, stdout="not-json{")
        with pytest.raises(DownloadError, match="JSON"):
            _make_downloader(binary).resolve("https://youtu.be/abc")

    def test_empty_playlist_raises(self, monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
        data = {"_type": "playlist", "id": "PL1", "entries": [None, None]}
        _patch_capture(monkeypatch, stdout=json.dumps(data))
        with pytest.raises(DownloadError, match="播放列表"):
            _make_downloader(binary).resolve("https://www.youtube.com/playlist?list=PL1")
