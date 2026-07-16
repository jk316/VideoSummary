"""AudioProcessor 单元测试：mock subprocess 层 + 可选的真实 ffmpeg 冒烟测试。"""

import shutil
import wave
from pathlib import Path

import pytest

from app.audio import processor as processor_module
from app.audio.processor import AudioProcessor
from app.core.cancellation import CancellationToken
from app.core.errors import AudioError, TaskCancelled
from app.utils.subproc import RunResult, SubprocessCancelled, SubprocessTimeout


@pytest.fixture
def ffmpeg_stub(tmp_path: Path) -> Path:
    path = tmp_path / "ffmpeg.exe"
    path.write_bytes(b"stub")
    return path


def _no_progress(_fraction: float | None, _message: str) -> None:
    pass


class TestEstimateWavSize:
    def test_one_second(self) -> None:
        assert AudioProcessor.estimate_wav_size(1.0) == 32000 + 44

    def test_one_hour_about_110mb(self) -> None:
        size = AudioProcessor.estimate_wav_size(3600.0)
        assert size == 3600 * 32000 + 44  # ≈ 110 MB

    def test_negative_duration_clamped(self) -> None:
        assert AudioProcessor.estimate_wav_size(-5.0) == 44


class TestCheckAvailable:
    def test_returns_version_line(self, monkeypatch: pytest.MonkeyPatch, ffmpeg_stub: Path) -> None:
        monkeypatch.setattr(
            processor_module,
            "run_capture",
            lambda args, **kw: RunResult(0, "ffmpeg version 7.1\nbuilt with gcc\n", ""),
        )
        assert AudioProcessor(ffmpeg_stub).check_available() == "ffmpeg version 7.1"

    def test_missing_binary_raises(self, tmp_path: Path) -> None:
        with pytest.raises(AudioError, match="ffmpeg"):
            AudioProcessor(tmp_path / "nonexistent.exe").check_available()

    def test_nonzero_exit_raises(self, monkeypatch: pytest.MonkeyPatch, ffmpeg_stub: Path) -> None:
        monkeypatch.setattr(
            processor_module, "run_capture", lambda args, **kw: RunResult(1, "", "boom")
        )
        with pytest.raises(AudioError):
            AudioProcessor(ffmpeg_stub).check_available()


class TestToWav:
    def _patch_streaming(self, monkeypatch: pytest.MonkeyPatch, lines: list[str], code: int = 0):
        """替换 run_streaming：回放 lines 并往输出 tmp 路径写入有效内容。"""
        calls: list[list[str]] = []

        def fake(args, *, on_line, idle_timeout=None, should_cancel=None):
            calls.append(list(args))
            for line in lines:
                on_line(line)
            if code == 0:
                Path(args[-1]).write_bytes(b"R" * 100)  # 大于 wav 头
            return code

        monkeypatch.setattr(processor_module, "run_streaming", fake)
        return calls

    def test_success_atomic_and_progress(
        self, monkeypatch: pytest.MonkeyPatch, ffmpeg_stub: Path, tmp_path: Path
    ) -> None:
        src = tmp_path / "audio.source"
        src.write_bytes(b"fake-audio")
        dest = tmp_path / "audio.wav"
        lines = ["out_time_ms=30000000", "progress=continue", "out_time_ms=60000000"]
        calls = self._patch_streaming(monkeypatch, lines)
        events: list[tuple[float | None, str]] = []

        result = AudioProcessor(ffmpeg_stub).to_wav_16k_mono(
            src, dest, lambda f, m: events.append((f, m)), CancellationToken(), duration_hint=60.0
        )
        assert result == dest
        assert dest.is_file()
        assert not (tmp_path / "audio.wav.tmp").exists()
        assert events[0][0] == pytest.approx(0.5)
        assert events[-1][0] == pytest.approx(1.0)
        args = calls[0]
        assert args[args.index("-ar") + 1] == "16000"
        assert args[args.index("-ac") + 1] == "1"
        assert args[-1].endswith("audio.wav.tmp")  # 输出到 tmp 而非最终路径

    def test_out_time_fallback_and_no_hint(
        self, monkeypatch: pytest.MonkeyPatch, ffmpeg_stub: Path, tmp_path: Path
    ) -> None:
        src = tmp_path / "audio.source"
        src.write_bytes(b"x")
        self._patch_streaming(monkeypatch, ["out_time=00:01:30.500000"])
        events: list[tuple[float | None, str]] = []
        AudioProcessor(ffmpeg_stub).to_wav_16k_mono(
            src, tmp_path / "o.wav", lambda f, m: events.append((f, m)), CancellationToken()
        )
        assert events == [(None, "转换音频 90s")]

    def test_failure_cleans_tmp_and_reports_stderr(
        self, monkeypatch: pytest.MonkeyPatch, ffmpeg_stub: Path, tmp_path: Path
    ) -> None:
        src = tmp_path / "audio.source"
        src.write_bytes(b"x")
        dest = tmp_path / "audio.wav"
        self._patch_streaming(
            monkeypatch, ["[mp3 @ 0x1] Header missing", "out_time_ms=100"], code=1
        )
        with pytest.raises(AudioError, match="Header missing"):
            AudioProcessor(ffmpeg_stub).to_wav_16k_mono(
                src, dest, _no_progress, CancellationToken()
            )
        assert not (tmp_path / "audio.wav.tmp").exists()
        assert not dest.exists()

    def test_cancel_translated_and_tmp_cleaned(
        self, monkeypatch: pytest.MonkeyPatch, ffmpeg_stub: Path, tmp_path: Path
    ) -> None:
        src = tmp_path / "audio.source"
        src.write_bytes(b"x")
        dest = tmp_path / "audio.wav"

        def fake(args, *, on_line, idle_timeout=None, should_cancel=None):
            Path(args[-1]).write_bytes(b"partial")  # 模拟被中断的半成品
            raise SubprocessCancelled("cancelled")

        monkeypatch.setattr(processor_module, "run_streaming", fake)
        with pytest.raises(TaskCancelled):
            AudioProcessor(ffmpeg_stub).to_wav_16k_mono(
                src, dest, _no_progress, CancellationToken()
            )
        assert not (tmp_path / "audio.wav.tmp").exists()

    def test_stall_raises_audio_error(
        self, monkeypatch: pytest.MonkeyPatch, ffmpeg_stub: Path, tmp_path: Path
    ) -> None:
        src = tmp_path / "audio.source"
        src.write_bytes(b"x")

        def fake(args, **kwargs):
            raise SubprocessTimeout("no output")

        monkeypatch.setattr(processor_module, "run_streaming", fake)
        with pytest.raises(AudioError, match="无输出"):
            AudioProcessor(ffmpeg_stub).to_wav_16k_mono(
                src, tmp_path / "o.wav", _no_progress, CancellationToken()
            )

    def test_missing_source_rejected_without_subprocess(
        self, monkeypatch: pytest.MonkeyPatch, ffmpeg_stub: Path, tmp_path: Path
    ) -> None:
        calls = self._patch_streaming(monkeypatch, [])
        with pytest.raises(AudioError, match="不存在"):
            AudioProcessor(ffmpeg_stub).to_wav_16k_mono(
                tmp_path / "missing.source", tmp_path / "o.wav", _no_progress, CancellationToken()
            )
        assert calls == []

    def test_empty_output_raises(
        self, monkeypatch: pytest.MonkeyPatch, ffmpeg_stub: Path, tmp_path: Path
    ) -> None:
        src = tmp_path / "audio.source"
        src.write_bytes(b"x")

        def fake(args, *, on_line, idle_timeout=None, should_cancel=None):
            Path(args[-1]).write_bytes(b"tiny")  # 小于 wav 头
            return 0

        monkeypatch.setattr(processor_module, "run_streaming", fake)
        with pytest.raises(AudioError, match="为空"):
            AudioProcessor(ffmpeg_stub).to_wav_16k_mono(
                src, tmp_path / "o.wav", _no_progress, CancellationToken()
            )

    def test_insufficient_disk_space_raises(
        self, monkeypatch: pytest.MonkeyPatch, ffmpeg_stub: Path, tmp_path: Path
    ) -> None:
        src = tmp_path / "audio.source"
        src.write_bytes(b"x")
        usage = shutil.disk_usage(tmp_path)._replace(free=1024)
        monkeypatch.setattr(processor_module.shutil, "disk_usage", lambda _p: usage)
        with pytest.raises(AudioError, match="磁盘"):
            AudioProcessor(ffmpeg_stub).to_wav_16k_mono(
                src, tmp_path / "o.wav", _no_progress, CancellationToken(), duration_hint=3600.0
            )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="本机未安装 ffmpeg")
class TestRealFfmpegSmoke:
    def test_real_conversion_produces_16k_mono_wav(self, tmp_path: Path) -> None:
        src = tmp_path / "input.wav"
        _write_test_wav(src, sample_rate=44100, channels=2, seconds=0.3)
        dest = tmp_path / "out.wav"

        processor = AudioProcessor(Path(shutil.which("ffmpeg")))  # type: ignore[arg-type]
        processor.check_available()
        result = processor.to_wav_16k_mono(
            src, dest, _no_progress, CancellationToken(), duration_hint=0.3
        )

        with wave.open(str(result), "rb") as out:
            assert out.getframerate() == 16000
            assert out.getnchannels() == 1
            assert out.getsampwidth() == 2


def _write_test_wav(path: Path, sample_rate: int, channels: int, seconds: float) -> None:
    frames = int(sample_rate * seconds)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x01" * frames * channels)
