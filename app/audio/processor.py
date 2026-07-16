"""音频处理：ffmpeg 转 16kHz/mono/wav，含磁盘预检与原子落盘。"""

import logging
import os
import re
import shutil
from collections import deque
from pathlib import Path

from app.core.cancellation import CancellationToken
from app.core.errors import AudioError, TaskCancelled
from app.core.events import ProgressFn
from app.utils.fs import tmp_path_for
from app.utils.subproc import (
    SubprocessCancelled,
    SubprocessTimeout,
    run_capture,
    run_streaming,
)

logger = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 16000
_BYTES_PER_SECOND = TARGET_SAMPLE_RATE * 2  # pcm_s16le 单声道
_WAV_HEADER_BYTES = 44
_DISK_MARGIN = 1.1
_IDLE_TIMEOUT_SECONDS = 60
_VERSION_TIMEOUT_SECONDS = 15
_ERROR_TAIL_LINES = 20

# -progress pipe:1 输出形如 out_time_ms=5000000 的 kv 行；其余行视为错误信息
_PROGRESS_KV_RE = re.compile(r"^[A-Za-z_][\w.]*=")
_OUT_TIME_US_RE = re.compile(r"^out_time_(?:ms|us)=(\d+)")
_OUT_TIME_RE = re.compile(r"^out_time=(\d+):(\d{2}):(\d{2})\.(\d+)")


class AudioProcessor:
    """ffmpeg 封装；binary 路径由装配层从配置/随包 bin 解析后注入。"""

    def __init__(self, ffmpeg: Path) -> None:
        self._ffmpeg = ffmpeg

    def check_available(self) -> str:
        """返回 ffmpeg 版本行；不可用时抛 AudioError。

        应用启动时自检一次，任务进入 STT 分支前再检一次。
        """
        try:
            result = run_capture(
                [str(self._require_binary()), "-version"], timeout=_VERSION_TIMEOUT_SECONDS
            )
        except (OSError, SubprocessTimeout) as exc:
            raise AudioError(
                f"无法运行 ffmpeg: {self._ffmpeg}: {exc}",
                user_message="无法运行 ffmpeg，请检查安装或在设置中指定路径。",
            ) from exc
        if result.returncode != 0:
            raise AudioError(f"ffmpeg -version 退出码 {result.returncode}: {result.stderr}")
        return result.stdout.splitlines()[0] if result.stdout else "ffmpeg"

    @staticmethod
    def estimate_wav_size(duration: float) -> int:
        """估算 16k/mono/s16le wav 的字节数（磁盘预检用）。"""
        return int(max(duration, 0.0) * _BYTES_PER_SECOND) + _WAV_HEADER_BYTES

    def to_wav_16k_mono(
        self,
        src: Path,
        dest: Path,
        progress: ProgressFn,
        cancel: CancellationToken,
        duration_hint: float | None = None,
    ) -> Path:
        """转换为 16kHz 单声道 wav，原子落盘到 dest。

        Args:
            duration_hint: 源时长（秒），用于换算进度百分比与磁盘预检；
                None 时进度为不确定模式。
        """
        if not src.is_file():
            raise AudioError(f"音频源文件不存在: {src}")
        self._check_disk_space(dest.parent, duration_hint)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = tmp_path_for(dest)
        args = self._build_args(src, tmp)
        error_tail: deque[str] = deque(maxlen=_ERROR_TAIL_LINES)

        def on_line(line: str) -> None:
            if _PROGRESS_KV_RE.match(line):
                _report_progress(line, progress, duration_hint)
            elif line.strip():
                error_tail.append(line)

        returncode = self._stream(args, on_line, cancel, tmp)
        if returncode != 0:
            tmp.unlink(missing_ok=True)
            detail = " / ".join(error_tail)
            raise AudioError(
                f"ffmpeg 转换失败 (exit {returncode}): {detail}",
                user_message="音频转换失败，详情见日志。",
            )
        if not tmp.is_file() or tmp.stat().st_size <= _WAV_HEADER_BYTES:
            tmp.unlink(missing_ok=True)
            raise AudioError("ffmpeg 正常退出但输出为空")
        os.replace(tmp, dest)
        return dest

    # ---------------------------------------------------------------- 内部

    def _build_args(self, src: Path, tmp: Path) -> list[str]:
        return [
            str(self._require_binary()),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostats",
            "-progress",
            "pipe:1",
            "-y",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(TARGET_SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(tmp),
        ]

    def _stream(self, args: list[str], on_line, cancel: CancellationToken, tmp: Path) -> int:
        try:
            return run_streaming(
                args,
                on_line=on_line,
                idle_timeout=_IDLE_TIMEOUT_SECONDS,
                should_cancel=cancel.is_cancelled,
            )
        except SubprocessCancelled:
            tmp.unlink(missing_ok=True)
            raise TaskCancelled() from None
        except SubprocessTimeout as exc:
            tmp.unlink(missing_ok=True)
            raise AudioError(
                f"ffmpeg 长时间无输出: {exc}", user_message="音频转换停滞，已中止。"
            ) from exc
        except OSError as exc:
            raise AudioError(
                f"无法启动 ffmpeg: {exc}",
                user_message="无法运行 ffmpeg，请检查安装或在设置中指定路径。",
            ) from exc

    def _require_binary(self) -> Path:
        if not self._ffmpeg.is_file():
            raise AudioError(
                f"ffmpeg 不存在: {self._ffmpeg}",
                user_message="未找到 ffmpeg 组件，请检查安装或在设置中指定路径。",
            )
        return self._ffmpeg

    def _check_disk_space(self, target_dir: Path, duration_hint: float | None) -> None:
        if not duration_hint:
            return
        required = int(self.estimate_wav_size(duration_hint) * _DISK_MARGIN)
        probe_dir = target_dir if target_dir.exists() else target_dir.parent
        free = shutil.disk_usage(probe_dir).free
        if free < required:
            raise AudioError(
                f"磁盘空间不足: 需要约 {required} 字节，剩余 {free} 字节 ({target_dir})",
                user_message=(
                    f"磁盘空间不足：转换约需 {required // (1024 * 1024)} MB，"
                    "请清理磁盘或更换缓存目录。"
                ),
            )


def _report_progress(line: str, progress: ProgressFn, duration_hint: float | None) -> None:
    seconds = _parse_out_time_seconds(line)
    if seconds is None:
        return
    if duration_hint and duration_hint > 0:
        fraction = min(seconds / duration_hint, 1.0)
        progress(fraction, f"转换音频 {fraction * 100:.0f}%")
    else:
        progress(None, f"转换音频 {seconds:.0f}s")


def _parse_out_time_seconds(line: str) -> float | None:
    match = _OUT_TIME_US_RE.match(line)
    if match:  # out_time_ms 实为微秒（ffmpeg 已知命名问题）
        return int(match.group(1)) / 1_000_000
    match = _OUT_TIME_RE.match(line)
    if match:
        hours, minutes, seconds, frac = match.groups()
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(frac) / (10 ** len(frac))
    return None
