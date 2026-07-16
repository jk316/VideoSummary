"""utils（paths/fs/logging_setup）单元测试。"""

import logging
import sys
from pathlib import Path

import pytest

from app.utils import paths
from app.utils.fs import atomic_write_text, remove_stale_tmp, tmp_path_for
from app.utils.logging_setup import redact_secrets, setup_logging


class TestPaths:
    def test_bundle_dir_frozen_is_exe_parent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        exe = tmp_path / "dist" / "VideoSummary.exe"
        exe.parent.mkdir(parents=True)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))
        assert paths.get_bundle_dir() == exe.parent.resolve()

    def test_bundle_dir_dev_points_to_packaging(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr(sys, "frozen", raising=False)
        bundle = paths.get_bundle_dir()
        assert bundle.name == "packaging"
        assert (bundle.parent / "app").is_dir()

    def test_app_data_dir_uses_appdata_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("APPDATA", str(tmp_path))
        data_dir = paths.get_app_data_dir()
        assert data_dir == tmp_path / paths.APP_NAME
        assert data_dir.is_dir()

    def test_sub_dirs_created_under_app_data(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert paths.get_logs_dir().is_dir()
        assert paths.get_cache_dir().name == "cache"
        assert paths.get_config_path().parent == paths.get_app_data_dir()


class TestAtomicWrite:
    def test_write_leaves_no_tmp(self, tmp_path: Path) -> None:
        target = tmp_path / "out" / "data.json"
        atomic_write_text(target, "{}")
        assert target.read_text(encoding="utf-8") == "{}"
        assert not tmp_path_for(target).exists()

    def test_overwrite_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "data.txt"
        atomic_write_text(target, "old")
        atomic_write_text(target, "new")
        assert target.read_text(encoding="utf-8") == "new"

    def test_remove_stale_tmp(self, tmp_path: Path) -> None:
        (tmp_path / "audio.wav.tmp").write_bytes(b"partial")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "transcript.json.tmp").write_text("{", encoding="utf-8")
        (tmp_path / "keep.txt").write_text("ok", encoding="utf-8")
        assert remove_stale_tmp(tmp_path) == 2
        assert (tmp_path / "keep.txt").exists()


class TestSecretRedaction:
    @pytest.mark.parametrize(
        ("raw", "leaked"),
        [
            ("llm api_key: sk-abc123456789xyz", "sk-abc123456789xyz"),
            ('config {"api-key": "secret-value"}', "secret-value"),
            ("Authorization: Bearer eyJhbGciOi.abc-123", "eyJhbGciOi.abc-123"),
            ("using cookies=SESSDATA%2Cabcdef", "SESSDATA%2Cabcdef"),
        ],
    )
    def test_secrets_masked(self, raw: str, leaked: str) -> None:
        redacted = redact_secrets(raw)
        assert leaked not in redacted
        assert "***" in redacted

    def test_normal_text_untouched(self) -> None:
        text = "开始下载视频: https://example.com/watch?v=abc"
        assert redact_secrets(text) == text


class TestSetupLogging:
    def test_writes_redacted_log_file(self, tmp_path: Path) -> None:
        setup_logging(tmp_path, level="INFO")
        logging.getLogger("test").info("api_key: sk-verysecret12345")
        for handler in logging.getLogger().handlers:
            handler.flush()
        content = (tmp_path / "app.log").read_text(encoding="utf-8")
        assert "sk-verysecret12345" not in content
        assert "***" in content

    def test_idempotent_no_duplicate_handlers(self, tmp_path: Path) -> None:
        setup_logging(tmp_path)
        first_count = len(logging.getLogger().handlers)
        setup_logging(tmp_path)
        assert len(logging.getLogger().handlers) == first_count
