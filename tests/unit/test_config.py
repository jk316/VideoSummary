"""config loader/schema 单元测试。"""

import logging
from pathlib import Path

import pytest
import yaml

from app.config.loader import load_config, save_config, validate_config
from app.config.schema import AppConfig, LlmConfig, SttConfig, SummaryConfig
from app.core.errors import ConfigError


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


class TestLoadConfig:
    def test_missing_file_creates_default(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        config = load_config(path)
        assert config == AppConfig()
        assert path.exists()

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        original = AppConfig(llm=LlmConfig(api_key="sk-test", max_concurrency=8))
        save_config(original, path)
        assert load_config(path) == original

    def test_partial_file_merges_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        _write_yaml(path, {"llm": {"model": "deepseek-chat"}})
        config = load_config(path)
        assert config.llm.model == "deepseek-chat"
        assert config.llm.timeout_seconds == AppConfig().llm.timeout_seconds
        assert config.stt == AppConfig().stt

    def test_invalid_yaml_raises_config_error(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("llm: [unclosed", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(path)

    def test_type_error_reports_dotted_path(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        _write_yaml(path, {"llm": {"max_concurrency": "four"}})
        with pytest.raises(ConfigError, match=r"llm.max_concurrency"):
            load_config(path)

    def test_bool_rejected_as_int(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        _write_yaml(path, {"downloader": {"retries": True}})
        with pytest.raises(ConfigError, match=r"downloader.retries"):
            load_config(path)

    def test_prefer_langs_list_becomes_tuple(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        _write_yaml(path, {"subtitle": {"prefer_langs": ["ja", "en"]}})
        assert load_config(path).subtitle.prefer_langs == ("ja", "en")

    def test_unknown_key_warns_and_ignored(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "config.yaml"
        _write_yaml(path, {"llm": {"modle": "typo"}})
        with caplog.at_level(logging.WARNING):
            config = load_config(path)
        assert config.llm.model == AppConfig().llm.model
        assert any("llm.modle" in r.message for r in caplog.records)


class TestValidateConfig:
    def test_invalid_model_size_lists_valid_values(self) -> None:
        config = AppConfig(stt=SttConfig(model_size="huge"))
        with pytest.raises(ConfigError, match=r"stt.model_size"):
            validate_config(config)

    def test_overlap_must_be_less_than_max(self) -> None:
        config = AppConfig(summary=SummaryConfig(chunk_max_tokens=100, chunk_overlap_tokens=100))
        with pytest.raises(ConfigError, match="chunk_overlap_tokens"):
            validate_config(config)

    def test_invalid_proxy_scheme_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        _write_yaml(path, {"network": {"proxy": "127.0.0.1:7890"}})
        with pytest.raises(ConfigError, match=r"network.proxy"):
            load_config(path)

    def test_multiple_errors_collected(self) -> None:
        config = AppConfig(
            stt=SttConfig(model_size="huge", device="tpu"),
            llm=LlmConfig(max_concurrency=0),
        )
        with pytest.raises(ConfigError) as exc_info:
            validate_config(config)
        message = str(exc_info.value)
        assert "stt.model_size" in message
        assert "stt.device" in message
        assert "llm.max_concurrency" in message

    def test_default_config_is_valid(self) -> None:
        validate_config(AppConfig())  # 不应抛出
