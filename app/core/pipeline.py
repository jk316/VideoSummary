"""单任务流水线：阶段状态机，含字幕选择、分阶段缓存、配置快照、耗时统计。

GET_TRANSCRIPT 阶段分两条分支：
  - 字幕分支: download_subtitle → parse_subtitle
  - STT 分支: download_audio → to_wav_16k_mono → transcribe
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from app.audio.processor import AudioProcessor
from app.cache.manager import CacheManager, make_summary_key, sha256_text
from app.chunking.chunker import TranscriptChunker
from app.config.schema import AppConfig
from app.core.cancellation import CancellationToken
from app.core.events import ProgressEvent, ProgressReporter, Stage
from app.core.models import (
    StageTiming,
    SubtitleTrack,
    SummaryLanguage,
    SummaryResult,
    TaskResult,
    TokenUsage,
    Transcript,
    VideoInfo,
    VideoRef,
)
from app.downloader.base import Downloader
from app.export.exporters import (
    export_summary_json,
    export_summary_md,
    export_transcript_srt,
    export_transcript_txt,
)
from app.stt.base import SpeechRecognizer
from app.subtitle.parser import parse_subtitle
from app.summarizer.summarizer import Summarizer
from app.utils.paths import get_default_output_dir
from app.utils.sanitize import sanitize_filename

logger = logging.getLogger(__name__)


def _mk_report(
    reporter: ProgressReporter,
    task_id: str,
    stage: Stage,
    fraction: float | None,
    message: str | None,
):
    """生成一次性进度上报回调，供能力模块的 progress 参数使用。"""

    def _report(fraction_override: float | None = fraction, message_override: str | None = message):
        reporter.report(ProgressEvent(task_id, stage, fraction_override, message_override or ""))

    return _report


META = "meta.json"
TRANSCRIPT = "transcript.json"
AUDIO_SOURCE = "audio.source"
AUDIO_WAV = "audio.wav"


class SummaryPipeline:
    """单视频处理流水线；全部依赖注入，工作线程中同步调用 ``run()``。"""

    def __init__(
        self,
        downloader: Downloader,
        audio: AudioProcessor,
        recognizer: SpeechRecognizer,
        chunker: TranscriptChunker,
        summarizer: Summarizer,
        cache: CacheManager,
        config: AppConfig,
    ) -> None:
        self._downloader = downloader
        self._audio = audio
        self._recognizer = recognizer
        self._chunker = chunker
        self._summarizer = summarizer
        self._cache = cache
        self._config = config

    def run(
        self,
        ref: VideoRef,
        reporter: ProgressReporter,
        cancel: CancellationToken,
    ) -> TaskResult:
        """执行完整流水线（同步阻塞，在工作线程中调用）。"""
        timings: list[StageTiming] = []
        task_id = ref.video_id[:12]

        # -------- RESOLVE_INFO --------
        pre_info = time.monotonic()
        reporter.report(ProgressEvent(task_id, Stage.RESOLVE_INFO, None, "获取视频信息…"))
        cancel.raise_if_cancelled()
        vcache = self._cache.for_video(str(ref.site), ref.video_id)
        info = self._resolve_info(ref, vcache)
        timings.append(StageTiming(Stage.RESOLVE_INFO, time.monotonic() - pre_info))

        # -------- GET_TRANSCRIPT --------
        pre_ts = time.monotonic()
        reporter.report(ProgressEvent(task_id, Stage.GET_TRANSCRIPT, None, "获取转写文本…"))
        transcript = self._get_transcript(ref, info, vcache, task_id, reporter, cancel)
        timings.append(StageTiming(Stage.GET_TRANSCRIPT, time.monotonic() - pre_ts))

        # -------- CHUNK --------
        pre_ch = time.monotonic()
        reporter.report(ProgressEvent(task_id, Stage.CHUNK, None, "正在切块…"))
        cancel.raise_if_cancelled()
        chunks = self._chunker.split(transcript)
        timings.append(StageTiming(Stage.CHUNK, time.monotonic() - pre_ch))

        # -------- SUMMARIZE --------
        pre_sm = time.monotonic()
        reporter.report(ProgressEvent(task_id, Stage.SUMMARIZE, None, "生成总结…"))
        cancel.raise_if_cancelled()
        summary = asyncio.run(self._summarize(info, transcript, chunks, vcache, reporter, cancel))
        timings.append(StageTiming(Stage.SUMMARIZE, time.monotonic() - pre_sm))

        # -------- EXPORT --------
        pre_ex = time.monotonic()
        reporter.report(ProgressEvent(task_id, Stage.EXPORT, None, "导出文件…"))
        cancel.raise_if_cancelled()
        output_files = self._export(info, transcript, summary)
        timings.append(StageTiming(Stage.EXPORT, time.monotonic() - pre_ex))

        reporter.report(ProgressEvent(task_id, Stage.EXPORT, 1.0, "完成"))
        return TaskResult(
            info=info,
            transcript=transcript,
            summary=summary,
            output_files=output_files,
            timings=tuple(timings),
        )

    # --------------------------------------------------------- RESOLVE_INFO

    def _resolve_info(self, ref: VideoRef, vcache) -> VideoInfo:
        if vcache.exists(META):
            logger.info("命中缓存: 元信息 (%s)", ref.video_id)
            data = json.loads(vcache.read_text(META))
            return VideoInfo.from_dict(data)
        info, tracks = self._downloader.fetch_info(ref)
        vcache.write_text(META, json.dumps(info.to_dict(), ensure_ascii=False))
        if tracks:
            vcache.write_text(
                "subtitles.json",
                json.dumps([t.to_dict() for t in tracks], ensure_ascii=False),
            )
        return info

    # -------------------------------------------------------- GET_TRANSCRIPT

    def _get_transcript(self, ref, info, vcache, task_id, reporter, cancel):
        if vcache.exists(TRANSCRIPT):
            logger.info("命中缓存: Transcript (%s)", ref.video_id)
            data = json.loads(vcache.read_text(TRANSCRIPT))
            return Transcript.from_dict(data)

        tracks = _load_subtitle_tracks(vcache)
        subtitles_config = self._config.subtitle
        selected = _select_subtitle(
            tracks, subtitles_config.prefer_langs, subtitles_config.allow_auto
        )
        if selected is not None:
            return self._transcript_from_subtitle(ref, selected, vcache, reporter, cancel)
        return self._transcript_from_stt(ref, info, vcache, task_id, reporter, cancel)

    def _transcript_from_subtitle(self, ref, track, vcache, reporter, cancel):
        reporter.report(ProgressEvent("", Stage.GET_TRANSCRIPT, None, f"下载字幕 ({track.lang})…"))
        cancel.raise_if_cancelled()
        sub_path = self._downloader.download_subtitle(ref, track, vcache.root, cancel)
        cancel.raise_if_cancelled()
        transcript = parse_subtitle(sub_path, track.format, track.lang)
        vcache.write_text(TRANSCRIPT, json.dumps(transcript.to_dict(), ensure_ascii=False))
        reporter.report(ProgressEvent("", Stage.GET_TRANSCRIPT, 1.0, "字幕已解析"))
        return transcript

    def _transcript_from_stt(self, ref, info, vcache, task_id, reporter, cancel):
        reporter.report(ProgressEvent(task_id, Stage.GET_TRANSCRIPT, None, "无可用字幕，下载音频…"))
        _cleanup_subtitle_tracks(vcache)
        audio_src = self._downloader.download_audio(
            ref,
            vcache.root,
            progress=lambda f, m: reporter.report(
                ProgressEvent(task_id, Stage.GET_TRANSCRIPT, f, m)
            ),
            cancel=cancel,
        )
        cancel.raise_if_cancelled()
        reporter.report(ProgressEvent(task_id, Stage.GET_TRANSCRIPT, None, "转换音频格式…"))
        wav_path = self._audio.to_wav_16k_mono(
            audio_src,
            vcache.path(AUDIO_WAV),
            progress=lambda f, m: reporter.report(
                ProgressEvent(task_id, Stage.GET_TRANSCRIPT, f, m)
            ),
            cancel=cancel,
            duration_hint=info.duration,
        )
        cancel.raise_if_cancelled()
        reporter.report(ProgressEvent(task_id, Stage.GET_TRANSCRIPT, None, "语音识别中…"))
        transcript = self._recognizer.transcribe(
            wav_path,
            progress=lambda f, m: reporter.report(
                ProgressEvent(task_id, Stage.GET_TRANSCRIPT, f, m)
            ),
            cancel=cancel,
        )
        vcache.write_text(TRANSCRIPT, json.dumps(transcript.to_dict(), ensure_ascii=False))
        _cleanup_audio_intermediates(vcache, self._config.cache.keep_intermediate_audio)
        reporter.report(ProgressEvent(task_id, Stage.GET_TRANSCRIPT, 1.0, "语音识别完成"))
        return transcript

    # ------------------------------------------------------------- SUMMARIZE

    async def _summarize(self, info, transcript, chunks, vcache, reporter, cancel):
        skey = make_summary_key(
            transcript_sha=sha256_text(transcript.text),
            model=self._config.llm.model,
            prompt_text=_effective_prompt_text(self._config),
            language=str(self._config.summary.language),
            chunk_max_tokens=self._config.summary.chunk_max_tokens,
            chunk_overlap_tokens=self._config.summary.chunk_overlap_tokens,
        )
        final_name = f"summary.{skey}.md"
        if vcache.exists(final_name):
            logger.info("命中缓存: 最终摘要 (%s)", skey)
            summary = SummaryResult(
                markdown=vcache.read_text(final_name),
                language=SummaryLanguage(self._config.summary.language),
                chunk_count=len(chunks),
                usage=TokenUsage(),
                elapsed_seconds=0.0,
            )
            return summary

        def ts_url(seconds):
            return self._downloader.timestamp_url(info.ref, seconds)

        result = await self._summarizer.summarize(
            info,
            chunks,
            progress=lambda f, m: reporter.report(
                ProgressEvent(info.ref.video_id[:12], Stage.SUMMARIZE, f, m)
            ),
            cancel=cancel,
        )
        vcache.write_text(final_name, result.markdown)
        return result

    # --------------------------------------------------------------- EXPORT

    def _export(self, info, transcript, summary):
        out_dir = self._resolve_output_dir()
        safe = sanitize_filename(info.title)
        files: list[Path] = []
        files.append(export_summary_md(summary, out_dir / f"{safe}.md"))
        files.append(export_summary_json(summary, out_dir / f"{safe}.summary.json"))
        files.append(export_transcript_txt(transcript, out_dir / f"{safe}.transcript.txt"))
        files.append(export_transcript_srt(transcript, out_dir / f"{safe}.transcript.srt"))
        return tuple(files)

    def _resolve_output_dir(self):
        configured = self._config.paths.output_dir
        return Path(configured) if configured else get_default_output_dir()


# ---------------------------------------------------------------- 字幕选择


def _select_subtitle(tracks, prefer_langs, allow_auto):
    if not tracks:
        return None
    pref = [p for p in prefer_langs]
    manual = [t for t in tracks if not t.is_auto]
    auto = [t for t in tracks if t.is_auto]

    # 目标语言人工字幕 > 任意人工字幕 > 目标语言自动字幕 > 任意自动字幕
    for lang in pref:
        match = next((t for t in manual if t.lang == lang), None) or (
            next((t for t in auto if t.lang == lang), None) if allow_auto else None
        )
        if match:
            return match
    if manual:
        return manual[0]
    return auto[0] if (allow_auto and auto) else None


def _load_subtitle_tracks(vcache):
    try:
        data = json.loads(vcache.read_text("subtitles.json"))
        return [SubtitleTrack.from_dict(d) for d in data]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _cleanup_subtitle_tracks(vcache):
    vcache.delete("subtitles.json")


def _cleanup_audio_intermediates(vcache, keep):
    if keep:
        return
    vcache.delete(AUDIO_SOURCE)
    vcache.delete(AUDIO_WAV)
    tmp = vcache.path("audio.wav.tmp")
    tmp.unlink(missing_ok=True)


def _effective_prompt_text(config):
    from app.summarizer.prompts import get_default_prompts

    lang = str(config.summary.language)
    defaults = get_default_prompts(lang)
    return (
        config.summary.map_prompt
        or defaults["map"] + config.summary.reduce_prompt
        or defaults["reduce"]
    )
