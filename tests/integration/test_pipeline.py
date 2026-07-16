"""Pipeline + TaskQueue 集成测试：全 Fake 实现覆盖字幕/STT 两分支与缓存/取消/队列。"""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.audio.processor import AudioProcessor
from app.cache.manager import CacheManager
from app.chunking.chunker import TranscriptChunker
from app.config.schema import (
    AppConfig,
    CacheConfig,
    LlmConfig,
    PathsConfig,
    SubtitleConfig,
    SummaryConfig,
)
from app.core.cancellation import CancellationToken
from app.core.errors import TaskCancelled, VideoSummaryError
from app.core.events import ProgressEvent, Stage
from app.core.models import (
    Chunk,
    Segment,
    Site,
    SubtitleTrack,
    SummaryLanguage,
    SummaryResult,
    TokenUsage,
    Transcript,
    TranscriptSource,
    VideoInfo,
    VideoRef,
)
from app.core.pipeline import SummaryPipeline
from app.core.task_queue import TaskQueue
from app.downloader.base import Downloader
from app.llm.base import LLMClient, LLMResponse
from app.stt.base import SpeechRecognizer
from app.summarizer.summarizer import Summarizer

# ================================================================= Fake 实现


@dataclass
class _FakeProgressEvent:
    task_id: str = ""
    stage: Stage | None = None
    fraction: float | None = None
    message: str = ""


class _FakeReporter:
    def __init__(self):
        self.events: list[ProgressEvent] = []

    def report(self, event: ProgressEvent):
        self.events.append(event)


class _FakeDownloader(Downloader):
    def __init__(self, has_subtitles: bool = True):
        self.has_subtitles = has_subtitles
        self.resolve_calls: list[str] = []
        self.subtitle_downloads: list[str] = []
        self.audio_downloads: list[str] = []

    def resolve(self, url):
        self.resolve_calls.append(url)
        return [VideoRef(site=Site.YOUTUBE, video_id="test1", url=url)]

    def fetch_info(self, ref):
        return (
            VideoInfo(ref=ref, title="测试视频", duration=30.0, author="作者"),
            (
                [SubtitleTrack(lang="zh-Hans", is_auto=False, format="vtt")]
                if self.has_subtitles
                else []
            ),
        )

    def download_subtitle(self, ref, track, dest_dir, cancel):
        self.subtitle_downloads.append(track.lang)
        path = dest_dir / f"subtitle.{track.lang}.{track.format}"
        path.write_text("WEBVTT\n\n00:00.000 --> 00:05.000\n你好世界\n", encoding="utf-8")
        return path

    def download_audio(self, ref, dest_dir, progress, cancel):
        self.audio_downloads.append(ref.video_id)
        path = dest_dir / "audio.source"
        path.write_bytes(b"fake-audio-data")
        progress(0.5, "50%")
        progress(1.0, "100%")
        return path

    def timestamp_url(self, ref, seconds):
        return f"https://youtu.be/{ref.video_id}?t={seconds}"


class _FakeAudio(AudioProcessor):
    def __init__(self):
        super().__init__(Path("ffmpeg"))
        self.conversions: list[Path] = []

    def check_available(self):
        return "ffmpeg-fake 1.0"

    def to_wav_16k_mono(self, src, dest, progress, cancel, duration_hint=None):
        self.conversions.append(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"R" * 100)
        progress(1.0, "done")
        return dest


class _FakeRecognizer(SpeechRecognizer):
    def __init__(self):
        self.transcriptions: list[Path] = []

    def transcribe(self, audio_path, *, language=None, progress, cancel):
        self.transcriptions.append(audio_path)
        progress(0.5, "转写中…")
        progress(1.0, "完成")
        return Transcript(
            language="zh",
            source=TranscriptSource.STT,
            segments=(Segment(0.0, 30.0, "这是语音识别的内容文本内容文本"),),
        )


class _FakeLLM(LLMClient):
    def __init__(self):
        self.call_count = 0
        self.closed = False

    async def generate(self, messages):
        self.call_count += 1
        return LLMResponse(text=f"# 总结 #{self.call_count}", usage=TokenUsage(10, 5))

    async def acheck(self):
        pass

    async def aclose(self):
        self.closed = True


class _FakeSummarizer(Summarizer):
    def __init__(self):
        self.calls: list[list[Chunk]] = []

    async def summarize(self, info, chunks, progress, cancel):
        self.calls.append(chunks)
        cancel.raise_if_cancelled()
        return SummaryResult(
            markdown=f"# 总结（{info.title}，{len(chunks)} 块）",
            language=SummaryLanguage.ZH,
            chunk_count=len(chunks),
            usage=TokenUsage(100, 50),
            elapsed_seconds=1.0,
        )


class _CharCounter:
    def count(self, text):
        return len(text)


# ================================================================= 配置


def _make_config(*, output_dir: str = "", has_subtitles: bool = True) -> AppConfig:
    cfg = AppConfig()
    # 字幕优先语言
    cfg = AppConfig(
        subtitle=SubtitleConfig(prefer_langs=("zh-Hans", "zh", "en"), allow_auto=True),
        summary=SummaryConfig(language="zh", chunk_max_tokens=100, chunk_overlap_tokens=20),
        llm=LlmConfig(model="gpt-test"),
        paths=PathsConfig(output_dir=output_dir),
        cache=CacheConfig(keep_intermediate_audio=False),
    )
    return cfg


# ================================================================= 测试


class TestPipelineSubtitlePath:
    def test_full_subtitle_flow(self, tmp_path: Path) -> None:
        reporter = _FakeReporter()
        cache = CacheManager(tmp_path)
        chunker = TranscriptChunker(100, 20, _CharCounter())
        downloader = _FakeDownloader(has_subtitles=True)
        audio = _FakeAudio()
        recognizer = _FakeRecognizer()
        summarizer = _FakeSummarizer()
        config = _make_config(output_dir=str(tmp_path / "out"))
        pipeline = SummaryPipeline(
            downloader, audio, recognizer, chunker, summarizer, cache, config
        )

        ref = VideoRef(site=Site.YOUTUBE, video_id="test1", url="https://x")
        result = pipeline.run(ref, reporter, CancellationToken())

        assert result.info.title == "测试视频"
        assert len(result.transcript.segments) == 1
        assert result.summary.chunk_count == 1
        assert result.timings[0].stage == Stage.RESOLVE_INFO
        assert len(result.output_files) >= 3
        assert downloader.subtitle_downloads == ["zh-Hans"]
        # STT 路径未被走
        assert downloader.audio_downloads == []
        assert audio.conversions == []
        assert recognizer.transcriptions == []

    def test_cache_skip_on_second_run(self, tmp_path: Path) -> None:
        reporter = _FakeReporter()
        cache = CacheManager(tmp_path)
        chunker = TranscriptChunker(100, 20, _CharCounter())
        downloader = _FakeDownloader(has_subtitles=True)
        audio = _FakeAudio()
        recognizer = _FakeRecognizer()
        summarizer = _FakeSummarizer()
        config = _make_config(output_dir=str(tmp_path / "out"))
        pipeline = SummaryPipeline(
            downloader, audio, recognizer, chunker, summarizer, cache, config
        )

        ref = VideoRef(site=Site.YOUTUBE, video_id="test1", url="https://x")
        # 第一次
        r1 = pipeline.run(ref, reporter, CancellationToken())
        # 第二次：全部缓存命中
        downloader2 = _FakeDownloader(has_subtitles=True)
        summarizer2 = _FakeSummarizer()
        pipeline2 = SummaryPipeline(
            downloader2, audio, recognizer, chunker, summarizer2, cache, config
        )
        r2 = pipeline2.run(ref, reporter, CancellationToken())
        assert r2.output_files == r1.output_files
        assert downloader2.subtitle_downloads == []  # 字幕未重新下载

    def test_cache_meta_skip(self, tmp_path: Path) -> None:
        """仅 meta 缓存命中时仍走字幕下载。"""
        reporter = _FakeReporter()
        cache = CacheManager(tmp_path)
        vcache = cache.for_video("youtube", "test1")
        # 手动写入 meta 缓存
        info = VideoInfo(
            ref=VideoRef(site=Site.YOUTUBE, video_id="test1", url="https://x"),
            title="已缓存视频",
            duration=60.0,
            author="作者",
        )
        vcache.write_text("meta.json", json.dumps(info.to_dict(), ensure_ascii=False))
        # 也写入字幕轨缓存
        vcache.write_text(
            "subtitles.json",
            json.dumps(
                [SubtitleTrack(lang="en", is_auto=False, format="vtt").to_dict()],
                ensure_ascii=False,
            ),
        )

        downloader = _FakeDownloader(has_subtitles=True)
        pipeline = SummaryPipeline(
            downloader,
            _FakeAudio(),
            _FakeRecognizer(),
            TranscriptChunker(100, 0, _CharCounter()),
            _FakeSummarizer(),
            cache,
            _make_config(),
        )
        ref = VideoRef(site=Site.YOUTUBE, video_id="test1", url="https://x")
        result = pipeline.run(ref, reporter, CancellationToken())
        assert result.info.title == "已缓存视频"  # 缓存命中
        assert downloader.subtitle_downloads == ["en"]  # 字幕仍需要下载（transcript 缓存未命中）


class TestPipelineSttPath:
    def test_falls_back_to_stt_when_no_subtitles(self, tmp_path: Path) -> None:
        reporter = _FakeReporter()
        cache = CacheManager(tmp_path)
        chunker = TranscriptChunker(100, 20, _CharCounter())
        downloader = _FakeDownloader(has_subtitles=False)
        audio = _FakeAudio()
        recognizer = _FakeRecognizer()
        summarizer = _FakeSummarizer()
        config = _make_config(output_dir=str(tmp_path / "out"))
        pipeline = SummaryPipeline(
            downloader, audio, recognizer, chunker, summarizer, cache, config
        )

        ref = VideoRef(site=Site.YOUTUBE, video_id="test1", url="https://x")
        result = pipeline.run(ref, reporter, CancellationToken())

        assert result.transcript.source == TranscriptSource.STT
        assert downloader.audio_downloads == ["test1"]
        assert len(audio.conversions) == 1
        assert len(recognizer.transcriptions) == 1

    def test_audio_intermediates_cleaned_by_default(self, tmp_path: Path) -> None:
        cache = CacheManager(tmp_path)
        reporter = _FakeReporter()
        chunker = TranscriptChunker(100, 20, _CharCounter())
        config = _make_config(output_dir=str(tmp_path / "out"))
        # keep_intermediate_audio=False（默认）
        pipeline = SummaryPipeline(
            _FakeDownloader(has_subtitles=False),
            _FakeAudio(),
            _FakeRecognizer(),
            chunker,
            _FakeSummarizer(),
            cache,
            config,
        )
        ref = VideoRef(site=Site.YOUTUBE, video_id="test1", url="https://x")
        pipeline.run(ref, reporter, CancellationToken())
        vcache = cache.for_video("youtube", "test1")
        assert not vcache.exists("audio.source")
        assert not vcache.exists("audio.wav")


class TestPipelineCancel:
    def test_cancel_before_resolve(self, tmp_path: Path) -> None:
        pipeline = SummaryPipeline(
            _FakeDownloader(),
            _FakeAudio(),
            _FakeRecognizer(),
            TranscriptChunker(100, 0, _CharCounter()),
            _FakeSummarizer(),
            CacheManager(tmp_path),
            _make_config(output_dir=str(tmp_path / "out")),
        )
        cancel = CancellationToken()
        cancel.cancel()
        ref = VideoRef(site=Site.YOUTUBE, video_id="test1", url="https://x")
        with pytest.raises(TaskCancelled):
            pipeline.run(ref, _FakeReporter(), cancel)


class TestTaskQueue:
    def test_serial_execution_order(self, tmp_path: Path) -> None:
        results: list[tuple[str, str | None]] = []

        def on_done(ref, result, error):
            status = "ok" if result else f"err:{type(error).__name__}" if error else "nil"
            results.append((ref.video_id, status))

        pipeline = SummaryPipeline(
            _FakeDownloader(),
            _FakeAudio(),
            _FakeRecognizer(),
            TranscriptChunker(100, 0, _CharCounter()),
            _FakeSummarizer(),
            CacheManager(tmp_path),
            _make_config(output_dir=str(tmp_path / "out")),
        )
        queue = TaskQueue(pipeline)
        queue.enqueue(
            [
                VideoRef(site=Site.YOUTUBE, video_id="v1", url="https://x1"),
                VideoRef(site=Site.YOUTUBE, video_id="v2", url="https://x2"),
                VideoRef(site=Site.YOUTUBE, video_id="v3", url="https://x3"),
            ]
        )
        queue.run_all(_FakeReporter(), on_done)
        assert [v for v, s in results] == ["v1", "v2", "v3"]
        assert all(s == "ok" for _, s in results)

    def test_failure_does_not_stop_queue(self, tmp_path: Path) -> None:
        results: list[str] = []

        class _BadDownloader(_FakeDownloader):
            def fetch_info(self, ref):
                if ref.video_id == "v2":
                    raise VideoSummaryError("模拟失败", user_message="第二条失败了")
                return super().fetch_info(ref)

        pipeline = SummaryPipeline(
            _BadDownloader(),
            _FakeAudio(),
            _FakeRecognizer(),
            TranscriptChunker(100, 0, _CharCounter()),
            _FakeSummarizer(),
            CacheManager(tmp_path),
            _make_config(output_dir=str(tmp_path / "out")),
        )
        queue = TaskQueue(pipeline)
        queue.enqueue(
            [
                VideoRef(site=Site.YOUTUBE, video_id="v1", url="https://x"),
                VideoRef(site=Site.YOUTUBE, video_id="v2", url="https://x"),
                VideoRef(site=Site.YOUTUBE, video_id="v3", url="https://x"),
            ]
        )

        def on_done(ref, result, error):
            results.append("ok" if result else "fail")

        queue.run_all(_FakeReporter(), on_done)
        assert results == ["ok", "fail", "ok"]

    def test_cancel_current(self, tmp_path: Path) -> None:
        import threading

        results: list[str] = []
        started = threading.Event()

        class _SlowDownloader(_FakeDownloader):
            def fetch_info(self, ref):
                if ref.video_id == "v2":
                    started.set()
                    import time

                    time.sleep(0.5)
                return super().fetch_info(ref)

        pipeline = SummaryPipeline(
            _SlowDownloader(),
            _FakeAudio(),
            _FakeRecognizer(),
            TranscriptChunker(100, 0, _CharCounter()),
            _FakeSummarizer(),
            CacheManager(tmp_path),
            _make_config(output_dir=str(tmp_path / "out")),
        )
        queue = TaskQueue(pipeline)
        queue.enqueue(
            [
                VideoRef(site=Site.YOUTUBE, video_id="v1", url="https://x"),
                VideoRef(site=Site.YOUTUBE, video_id="v2", url="https://x"),
                VideoRef(site=Site.YOUTUBE, video_id="v3", url="https://x"),
            ]
        )

        def on_done(ref, result, error):
            results.append(ref.video_id if result else f"cancel:{ref.video_id}")

        def runner():
            queue.run_all(_FakeReporter(), on_done)

        t = threading.Thread(target=runner)
        t.start()
        assert started.wait(timeout=5)  # 等 v2 开始
        queue.cancel_current()
        t.join(timeout=5)
        assert "v1" in results
        assert any("v2" in r and "cancel" in r for r in results)
        assert "v3" in results

    def test_cancel_all(self, tmp_path: Path) -> None:
        import threading

        results: list[str] = []
        started = threading.Event()

        class _SlowDownloader(_FakeDownloader):
            def fetch_info(self, ref):
                if ref.video_id == "v1":
                    started.set()
                    import time

                    time.sleep(0.5)
                return super().fetch_info(ref)

        pipeline = SummaryPipeline(
            _SlowDownloader(),
            _FakeAudio(),
            _FakeRecognizer(),
            TranscriptChunker(100, 0, _CharCounter()),
            _FakeSummarizer(),
            CacheManager(tmp_path),
            _make_config(output_dir=str(tmp_path / "out")),
        )
        queue = TaskQueue(pipeline)
        queue.enqueue(
            [
                VideoRef(site=Site.YOUTUBE, video_id="v1", url="https://x"),
                VideoRef(site=Site.YOUTUBE, video_id="v2", url="https://x"),
                VideoRef(site=Site.YOUTUBE, video_id="v3", url="https://x"),
            ]
        )

        def on_done(ref, result, error):
            results.append(ref.video_id if result else f"cancel:{ref.video_id}")

        def runner():
            queue.run_all(_FakeReporter(), on_done)

        t = threading.Thread(target=runner)
        t.start()
        assert started.wait(timeout=5)
        queue.cancel_all()
        t.join(timeout=5)
        assert len(results) == 1
        assert "v2" not in results
        assert "v3" not in results


class TestSubtitleSelection:
    def test_prefers_matching_language(self) -> None:
        from app.core.pipeline import _select_subtitle

        tracks = [
            SubtitleTrack(lang="ja", is_auto=False, format="vtt"),
            SubtitleTrack(lang="zh-Hans", is_auto=False, format="vtt"),
        ]
        result = _select_subtitle(tracks, ("zh-Hans", "zh"), True)
        assert result is not None
        assert result.lang == "zh-Hans"

    def test_fallback_to_any_manual(self) -> None:
        from app.core.pipeline import _select_subtitle

        tracks = [SubtitleTrack(lang="ja", is_auto=False, format="vtt")]
        result = _select_subtitle(tracks, ("zh-Hans",), True)
        assert result is not None
        assert result.lang == "ja"

    def test_auto_fallback_when_allowed(self) -> None:
        from app.core.pipeline import _select_subtitle

        tracks = [SubtitleTrack(lang="ja", is_auto=True, format="vtt")]
        result = _select_subtitle(tracks, ("zh",), True)
        assert result is not None
        assert result.is_auto

    def test_auto_disallowed_returns_none(self) -> None:
        from app.core.pipeline import _select_subtitle

        tracks = [SubtitleTrack(lang="ja", is_auto=True, format="vtt")]
        result = _select_subtitle(tracks, ("zh",), False)
        assert result is None

    def test_empty_tracks_returns_none(self) -> None:
        from app.core.pipeline import _select_subtitle

        assert _select_subtitle([], ("zh",), True) is None
