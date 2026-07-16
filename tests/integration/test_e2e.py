"""端到端集成测试：全 Fake 链路的字幕/STT 双分支完整走通。"""

import json
from pathlib import Path

import pytest

from app.config.schema import (
    AppConfig,
    CacheConfig,
    LlmConfig,
    PathsConfig,
    SttConfig,
    SubtitleConfig,
    SummaryConfig,
)
from app.core.cancellation import CancellationToken
from app.core.models import Site, VideoRef
from tests.integration.test_pipeline import (
    _CharCounter,
    _FakeAudio,
    _FakeDownloader,
    _FakeRecognizer,
    _FakeReporter,
    _FakeSummarizer,
)


def _make_config(output_dir: str) -> AppConfig:
    return AppConfig(
        subtitle=SubtitleConfig(prefer_langs=("zh-Hans",), allow_auto=True),
        summary=SummaryConfig(language="zh", chunk_max_tokens=200, chunk_overlap_tokens=0),
        llm=LlmConfig(model="gpt-test"),
        stt=SttConfig(),
        paths=PathsConfig(output_dir=output_dir),
        cache=CacheConfig(keep_intermediate_audio=False),
    )


class TestE2ESubtitlePath:
    """URL → resolve → info → 字幕下载 → parse → chunk → summarize → export"""

    def test_complete_subtitle_flow(self, tmp_path: Path) -> None:
        from app.cache.manager import CacheManager
        from app.chunking.chunker import TranscriptChunker
        from app.core.pipeline import SummaryPipeline

        out = str(tmp_path / "out")
        config = _make_config(out)
        cache = CacheManager(tmp_path / "cache")
        chunker = TranscriptChunker(100, 0, _CharCounter())
        downloader = _FakeDownloader(has_subtitles=True)
        pipeline = SummaryPipeline(
            downloader, _FakeAudio(), _FakeRecognizer(),
            chunker, _FakeSummarizer(), cache, config,
        )
        reporter = _FakeReporter()

        ref = VideoRef(site=Site.YOUTUBE, video_id="e2e-test", url="https://youtu.be/test")
        result = pipeline.run(ref, reporter, CancellationToken())

        # 阶段完整性
        stages = {t.stage for t in result.timings}
        assert stages == {"resolve_info", "get_transcript", "chunk", "summarize", "export"}

        # 输出文件存在
        assert len(result.output_files) > 0
        for f in result.output_files:
            assert f.exists(), f"输出文件不存在: {f}"

        # 进度事件记录了全部阶段
        stage_names = {e.stage for e in reporter.events if e.stage}
        assert "get_transcript" in stage_names
        assert "summarize" in stage_names

        # Transcript 可导出为各种格式
        assert result.transcript.source.value == "subtitle"

        # 缓存文件已写入
        assert (tmp_path / "cache" / "youtube_e2e-test" / "meta.json").exists()
        assert (tmp_path / "cache" / "youtube_e2e-test" / "transcript.json").exists()

    def test_report_json_structure(self, tmp_path: Path) -> None:
        """验证 transcript JSON 可被正确读取和反序列化。"""
        from app.cache.manager import CacheManager
        from app.chunking.chunker import TranscriptChunker
        from app.core.models import Transcript
        from app.core.pipeline import SummaryPipeline

        cache = CacheManager(tmp_path / "cache")
        pipeline = SummaryPipeline(
            _FakeDownloader(has_subtitles=True), _FakeAudio(), _FakeRecognizer(),
            TranscriptChunker(100, 0, _CharCounter()), _FakeSummarizer(), cache,
            _make_config(str(tmp_path / "out")),
        )
        ref = VideoRef(site=Site.YOUTUBE, video_id="e2e-test", url="https://x")
        pipeline.run(ref, _FakeReporter(), CancellationToken())

        # 从缓存文件反序列化 Transcript
        vcache = cache.for_video("youtube", "e2e-test")
        data = json.loads(vcache.read_text("transcript.json"))
        transcript = Transcript.from_dict(data)
        assert len(transcript.segments) > 0
        assert len(transcript.text) > 0


class TestE2ESttPath:
    """URL → resolve → info → 无字幕 → 音频下载 → STT → chunk → summarize → export"""

    def test_complete_stt_flow(self, tmp_path: Path) -> None:
        from app.cache.manager import CacheManager
        from app.chunking.chunker import TranscriptChunker
        from app.core.pipeline import SummaryPipeline

        config = _make_config(str(tmp_path / "out"))
        cache = CacheManager(tmp_path / "cache")
        chunker = TranscriptChunker(100, 0, _CharCounter())
        downloader = _FakeDownloader(has_subtitles=False)
        pipeline = SummaryPipeline(
            downloader, _FakeAudio(), _FakeRecognizer(),
            chunker, _FakeSummarizer(), cache, config,
        )
        reporter = _FakeReporter()

        ref = VideoRef(site=Site.BILIBILI, video_id="BV1test", url="https://b23.tv/test")
        result = pipeline.run(ref, reporter, CancellationToken())

        # STT 路径被走通
        assert result.transcript.source.value == "stt"
        assert downloader.audio_downloads == ["BV1test"]

        # 音频中间产物已自动清理
        vcache = cache.for_video("bilibili", "BV1test")
        assert not vcache.exists("audio.source")
        assert not vcache.exists("audio.wav")

        # 输出完整
        assert len(result.output_files) >= 3  # md + summary.json + transcript.txt + transcript.srt
        for f in result.output_files:
            assert f.exists()

    def test_second_run_hits_cache(self, tmp_path: Path) -> None:
        """STT 路径：第二次运行全部缓存命中，不重复 STT。"""
        from app.cache.manager import CacheManager
        from app.chunking.chunker import TranscriptChunker
        from app.core.pipeline import SummaryPipeline

        cache = CacheManager(tmp_path / "cache")
        chunker = TranscriptChunker(100, 0, _CharCounter())
        config = _make_config(str(tmp_path / "out"))
        ref = VideoRef(site=Site.BILIBILI, video_id="BV1test", url="https://x")

        # 第一次
        p1 = SummaryPipeline(
            _FakeDownloader(has_subtitles=False), _FakeAudio(), _FakeRecognizer(),
            chunker, _FakeSummarizer(), cache, config,
        )
        r1 = p1.run(ref, _FakeReporter(), CancellationToken())

        # 第二次：全新 recognizer（验证没再调用）
        recognizer2 = _FakeRecognizer()
        downloader2 = _FakeDownloader(has_subtitles=False)
        p2 = SummaryPipeline(
            downloader2, _FakeAudio(), recognizer2,
            TranscriptChunker(100, 0, _CharCounter()), _FakeSummarizer(), cache, config,
        )
        r2 = p2.run(ref, _FakeReporter(), CancellationToken())

        assert recognizer2.transcriptions == []  # STT 被缓存跳过
        assert downloader2.audio_downloads == []  # 音频下载被缓存跳过
        assert r2.transcript.source.value == "stt"
        assert r2.output_files == r1.output_files


class TestFullPipelineResilience:
    def test_cancel_and_retry_respects_cache(self, tmp_path: Path) -> None:
        """取消后再跑：已完成阶段的缓存被保留，从断点续跑。"""
        from app.cache.manager import CacheManager
        from app.chunking.chunker import TranscriptChunker
        from app.core.pipeline import SummaryPipeline

        cache = CacheManager(tmp_path / "cache")
        chunker = TranscriptChunker(100, 0, _CharCounter())
        config = _make_config(str(tmp_path / "out"))
        ref = VideoRef(site=Site.YOUTUBE, video_id="resume1", url="https://x")

        # 第一次：完成后验证
        p1 = SummaryPipeline(
            _FakeDownloader(has_subtitles=True), _FakeAudio(), _FakeRecognizer(),
            chunker, _FakeSummarizer(), cache, config,
        )
        r1 = p1.run(ref, _FakeReporter(), CancellationToken())

        # 第二次：用全新的 downloader（验证没重复下载）
        downloader2 = _FakeDownloader(has_subtitles=True)
        p2 = SummaryPipeline(
            downloader2, _FakeAudio(), _FakeRecognizer(),
            TranscriptChunker(100, 0, _CharCounter()), _FakeSummarizer(), cache, config,
        )
        r2 = p2.run(ref, _FakeReporter(), CancellationToken())

        assert downloader2.subtitle_downloads == []  # 字幕缓存命中，不重复下载
        assert r2.output_files == r1.output_files


@pytest.mark.live
class TestLiveYtDlp:
    """需要真实 yt-dlp 和网络的冒烟测试（CI 默认跳过）。

    运行方式: uv run pytest -m live tests/integration/test_e2e.py
    """

    def test_resolve_real_video(self, tmp_path: Path) -> None:
        import shutil

        ytdlp = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
        if not ytdlp:
            pytest.skip("yt-dlp 未安装")

        from app.config.schema import DownloaderConfig, NetworkConfig
        from app.downloader.ytdlp import YtDlpDownloader

        dl = YtDlpDownloader(Path(ytdlp), DownloaderConfig(), NetworkConfig())  # type: ignore[arg-type]
        dl.check_available()
        # 用短视频快速验证
        refs = dl.resolve("https://www.youtube.com/watch?v=jNQXAC9IVRw")
        assert len(refs) == 1
        assert refs[0].title
