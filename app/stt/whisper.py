"""faster-whisper 实现：延迟导入、HF 镜像/代理、逐段进度与取消。

``faster_whisper`` 在首次 transcribe 时才导入——字幕命中的任务
完全不加载 CTranslate2，缩短启动时间（architecture.md §5.4）。
"""

import contextlib
import logging
import os
from collections.abc import Iterable, Iterator
from pathlib import Path

from app.config.schema import NetworkConfig, SttConfig
from app.core.cancellation import CancellationToken
from app.core.errors import SttError, TaskCancelled
from app.core.events import ProgressFn
from app.core.models import Segment, Transcript, TranscriptSource
from app.stt.base import SpeechRecognizer
from app.utils.proxy import resolve_httpx_proxy

logger = logging.getLogger(__name__)

_BEAM_SIZE = 5
_MODEL_SIZES_MB = {
    "tiny": 75,
    "base": 145,
    "small": 484,
    "medium": 1530,
    "large-v3": 3090,
}


class WhisperRecognizer(SpeechRecognizer):
    """faster-whisper 封装；模型实例加载后复用。

    注意：模型下载在工作线程内阻塞进行，下载期间无法取消，
    取消检查在下载前与逐段转写间进行。
    """

    def __init__(self, config: SttConfig, models_dir: Path, network: NetworkConfig) -> None:
        self._config = config
        self._models_dir = models_dir
        self._network = network
        self._model: object | None = None

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
        progress: ProgressFn,
        cancel: CancellationToken,
    ) -> Transcript:
        if not audio_path.is_file():
            raise SttError(f"音频文件不存在: {audio_path}")
        cancel.raise_if_cancelled()
        model = self._ensure_model(progress)
        effective_language = language or self._config.language or None
        try:
            segments_iter, info = model.transcribe(  # type: ignore[attr-defined]
                str(audio_path),
                language=effective_language,
                vad_filter=self._config.vad_filter,
                beam_size=_BEAM_SIZE,
            )
            segments = _collect_segments(segments_iter, info, progress, cancel)
        except (TaskCancelled, SttError):
            raise
        except Exception as exc:
            raise SttError(
                f"语音识别失败: {audio_path}: {exc}",
                user_message="语音识别失败，详情见日志。",
            ) from exc
        if not segments:
            raise SttError(
                f"识别结果为空: {audio_path}",
                user_message="未能从音频中识别出语音内容。",
            )
        detected = str(getattr(info, "language", None) or effective_language or "")
        return Transcript(language=detected, source=TranscriptSource.STT, segments=tuple(segments))

    # ---------------------------------------------------------------- 内部

    def _ensure_model(self, progress: ProgressFn) -> object:
        if self._model is not None:
            return self._model
        self._report_load_message(progress)
        proxy = resolve_httpx_proxy(self._network.proxy, self._network.use_system_proxy)
        with _hf_env(self._config.hf_endpoint, proxy):
            model_cls = _import_whisper_model()
            try:
                self._model = model_cls(
                    self._config.model_size,
                    device=self._config.device,
                    compute_type=self._config.compute_type,
                    download_root=str(self._models_dir),
                )
            except Exception as exc:
                raise SttError(
                    f"Whisper 模型加载失败 ({self._config.model_size}): {exc}",
                    user_message="语音模型下载/加载失败，请检查网络、代理或镜像设置。",
                ) from exc
        logger.info("Whisper 模型已就绪: %s (%s)", self._config.model_size, self._config.device)
        return self._model

    def _report_load_message(self, progress: ProgressFn) -> None:
        size = self._config.model_size
        if self._is_model_downloaded(size):
            progress(None, f"加载 Whisper 模型 ({size})…")
            return
        estimate = _MODEL_SIZES_MB.get(size)
        hint = f"约 {estimate} MB，" if estimate else ""
        progress(None, f"首次下载 Whisper 模型 {size}（{hint}视网络可能需要几分钟）…")

    def _is_model_downloaded(self, size: str) -> bool:
        # huggingface_hub 缓存目录形如 models--Systran--faster-whisper-small
        return any(self._models_dir.glob(f"models--*faster-whisper-{size}*"))


def _import_whisper_model() -> type:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SttError(
            f"faster-whisper 未安装: {exc}",
            user_message="语音识别组件（faster-whisper）未安装，无法转写无字幕视频。",
        ) from exc
    return WhisperModel


def _collect_segments(
    segments_iter: Iterator[object] | Iterable[object],
    info: object,
    progress: ProgressFn,
    cancel: CancellationToken,
) -> list[Segment]:
    total = float(getattr(info, "duration", 0.0) or 0.0)
    collected: list[Segment] = []
    for raw in segments_iter:
        cancel.raise_if_cancelled()
        text = str(getattr(raw, "text", "")).strip()
        end = float(getattr(raw, "end", 0.0))
        if text:
            collected.append(Segment(start=float(getattr(raw, "start", 0.0)), end=end, text=text))
        if total > 0:
            fraction = min(end / total, 1.0)
            progress(fraction, f"语音识别 {fraction * 100:.0f}%")
        else:
            progress(None, f"语音识别 {end:.0f}s")
    return collected


@contextlib.contextmanager
def _hf_env(endpoint: str, proxy: str | None):
    """模型下载期间临时设置 HF 镜像与代理环境变量，退出时恢复。"""
    overrides: dict[str, str] = {}
    if endpoint:
        overrides["HF_ENDPOINT"] = endpoint
    if proxy:
        overrides["HTTPS_PROXY"] = proxy
        overrides["HTTP_PROXY"] = proxy
    saved = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
