"""WhisperRecognizer 单元测试：以假 faster_whisper 模块注入 sys.modules。"""

import os
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.config.schema import NetworkConfig, SttConfig
from app.core.cancellation import CancellationToken
from app.core.errors import SttError, TaskCancelled
from app.core.models import TranscriptSource
from app.stt.whisper import WhisperRecognizer


@dataclass(frozen=True)
class _FakeSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class _FakeInfo:
    language: str | None = "zh"
    duration: float = 10.0


@dataclass
class _Recorder:
    init_calls: list[dict] = field(default_factory=list)
    transcribe_calls: list[dict] = field(default_factory=list)


def _install_fake_whisper(
    monkeypatch: pytest.MonkeyPatch,
    segments,
    info: _FakeInfo,
    recorder: _Recorder,
    load_error: Exception | None = None,
) -> None:
    module = types.ModuleType("faster_whisper")

    class FakeWhisperModel:
        def __init__(self, model_size, device=None, compute_type=None, download_root=None):
            recorder.init_calls.append(
                {
                    "model_size": model_size,
                    "device": device,
                    "compute_type": compute_type,
                    "download_root": download_root,
                    "hf_endpoint": os.environ.get("HF_ENDPOINT"),
                    "https_proxy": os.environ.get("HTTPS_PROXY"),
                }
            )
            if load_error is not None:
                raise load_error

        def transcribe(self, path, **kwargs):
            recorder.transcribe_calls.append({"path": path, **kwargs})
            return iter(segments), info

    module.WhisperModel = FakeWhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    path = tmp_path / "audio.wav"
    path.write_bytes(b"R" * 100)
    return path


def _make_recognizer(
    tmp_path: Path,
    config: SttConfig | None = None,
    network: NetworkConfig | None = None,
) -> WhisperRecognizer:
    models_dir = tmp_path / "models"
    models_dir.mkdir(exist_ok=True)
    return WhisperRecognizer(
        config=config or SttConfig(), models_dir=models_dir, network=network or NetworkConfig()
    )


def test_module_import_does_not_load_faster_whisper() -> None:
    """延迟导入：仅 import 本模块不应加载 faster_whisper。"""
    assert "app.stt.whisper" in sys.modules
    assert "faster_whisper" not in sys.modules


class TestTranscribe:
    def test_success_collects_segments_and_progress(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audio_file: Path
    ) -> None:
        recorder = _Recorder()
        segments = [
            _FakeSegment(0.0, 5.0, " 第一段 "),
            _FakeSegment(5.0, 10.0, "第二段"),
        ]
        _install_fake_whisper(monkeypatch, segments, _FakeInfo(), recorder)
        events: list[tuple[float | None, str]] = []

        transcript = _make_recognizer(tmp_path).transcribe(
            audio_file, progress=lambda f, m: events.append((f, m)), cancel=CancellationToken()
        )

        assert transcript.source is TranscriptSource.STT
        assert transcript.language == "zh"
        assert [s.text for s in transcript.segments] == ["第一段", "第二段"]
        fractions = [f for f, _ in events if f is not None]
        assert fractions == [pytest.approx(0.5), pytest.approx(1.0)]

    def test_model_init_args_and_transcribe_args(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audio_file: Path
    ) -> None:
        recorder = _Recorder()
        _install_fake_whisper(monkeypatch, [_FakeSegment(0, 1, "x")], _FakeInfo(), recorder)
        config = SttConfig(model_size="medium", device="cpu", compute_type="int8", vad_filter=True)

        _make_recognizer(tmp_path, config=config).transcribe(
            audio_file, progress=lambda f, m: None, cancel=CancellationToken()
        )

        init = recorder.init_calls[0]
        assert init["model_size"] == "medium"
        assert init["compute_type"] == "int8"
        assert init["download_root"] == str(tmp_path / "models")
        call = recorder.transcribe_calls[0]
        assert call["vad_filter"] is True
        assert call["language"] is None  # 空配置 → 自动检测

    def test_explicit_language_passed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audio_file: Path
    ) -> None:
        recorder = _Recorder()
        _install_fake_whisper(monkeypatch, [_FakeSegment(0, 1, "x")], _FakeInfo(), recorder)
        _make_recognizer(tmp_path).transcribe(
            audio_file, language="ja", progress=lambda f, m: None, cancel=CancellationToken()
        )
        assert recorder.transcribe_calls[0]["language"] == "ja"

    def test_model_loaded_once_across_calls(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audio_file: Path
    ) -> None:
        recorder = _Recorder()
        _install_fake_whisper(monkeypatch, [_FakeSegment(0, 1, "x")], _FakeInfo(), recorder)
        recognizer = _make_recognizer(tmp_path)
        for _ in range(2):
            recognizer.transcribe(
                audio_file, progress=lambda f, m: None, cancel=CancellationToken()
            )
        assert len(recorder.init_calls) == 1
        assert len(recorder.transcribe_calls) == 2

    def test_cancel_between_segments_raises_task_cancelled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audio_file: Path
    ) -> None:
        recorder = _Recorder()
        cancel = CancellationToken()

        def rolling_segments():
            yield _FakeSegment(0.0, 5.0, "第一段")
            cancel.cancel()
            yield _FakeSegment(5.0, 10.0, "不应处理")

        _install_fake_whisper(monkeypatch, rolling_segments(), _FakeInfo(), recorder)
        with pytest.raises(TaskCancelled):
            _make_recognizer(tmp_path).transcribe(
                audio_file, progress=lambda f, m: None, cancel=cancel
            )

    def test_empty_result_raises_stt_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audio_file: Path
    ) -> None:
        _install_fake_whisper(monkeypatch, [], _FakeInfo(), _Recorder())
        with pytest.raises(SttError, match="为空"):
            _make_recognizer(tmp_path).transcribe(
                audio_file, progress=lambda f, m: None, cancel=CancellationToken()
            )

    def test_missing_audio_rejected_early(self, tmp_path: Path) -> None:
        with pytest.raises(SttError, match="不存在"):
            _make_recognizer(tmp_path).transcribe(
                tmp_path / "nope.wav", progress=lambda f, m: None, cancel=CancellationToken()
            )


class TestModelLoading:
    def test_load_failure_maps_to_friendly_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audio_file: Path
    ) -> None:
        _install_fake_whisper(
            monkeypatch, [], _FakeInfo(), _Recorder(), load_error=RuntimeError("download failed")
        )
        with pytest.raises(SttError) as exc_info:
            _make_recognizer(tmp_path).transcribe(
                audio_file, progress=lambda f, m: None, cancel=CancellationToken()
            )
        assert "镜像" in exc_info.value.user_message

    def test_missing_package_maps_to_stt_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audio_file: Path
    ) -> None:
        monkeypatch.setitem(sys.modules, "faster_whisper", None)  # import 时抛 ImportError
        with pytest.raises(SttError, match="未安装"):
            _make_recognizer(tmp_path).transcribe(
                audio_file, progress=lambda f, m: None, cancel=CancellationToken()
            )

    def test_hf_env_set_during_load_and_restored(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audio_file: Path
    ) -> None:
        for key in ("HF_ENDPOINT", "HTTPS_PROXY", "HTTP_PROXY"):
            monkeypatch.delenv(key, raising=False)
        recorder = _Recorder()
        _install_fake_whisper(monkeypatch, [_FakeSegment(0, 1, "x")], _FakeInfo(), recorder)
        config = SttConfig(hf_endpoint="https://hf-mirror.com")
        network = NetworkConfig(proxy="http://127.0.0.1:7890")

        _make_recognizer(tmp_path, config=config, network=network).transcribe(
            audio_file, progress=lambda f, m: None, cancel=CancellationToken()
        )

        init = recorder.init_calls[0]
        assert init["hf_endpoint"] == "https://hf-mirror.com"  # 加载期间生效
        assert init["https_proxy"] == "http://127.0.0.1:7890"
        assert "HF_ENDPOINT" not in os.environ  # 加载结束后恢复
        assert "HTTPS_PROXY" not in os.environ

    def test_first_download_message_mentions_size(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audio_file: Path
    ) -> None:
        recorder = _Recorder()
        _install_fake_whisper(monkeypatch, [_FakeSegment(0, 1, "x")], _FakeInfo(), recorder)
        messages: list[str] = []
        _make_recognizer(tmp_path).transcribe(
            audio_file, progress=lambda f, m: messages.append(m), cancel=CancellationToken()
        )
        assert any("下载" in m and "484" in m for m in messages)  # small ≈ 484MB

    def test_cached_model_message_says_loading(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audio_file: Path
    ) -> None:
        recorder = _Recorder()
        _install_fake_whisper(monkeypatch, [_FakeSegment(0, 1, "x")], _FakeInfo(), recorder)
        models_dir = tmp_path / "models"
        models_dir.mkdir(exist_ok=True)
        (models_dir / "models--Systran--faster-whisper-small").mkdir()
        messages: list[str] = []
        _make_recognizer(tmp_path).transcribe(
            audio_file, progress=lambda f, m: messages.append(m), cancel=CancellationToken()
        )
        assert any("加载" in m for m in messages)
        assert not any("下载" in m for m in messages)
