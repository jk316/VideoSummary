"""配置读写：yaml <-> dataclass，含默认生成、类型/取值校验。

类型推断基于字段默认值（所有配置字段均有默认值），错误信息携带
``llm.max_concurrency`` 这样的点路径，fail fast。未知键记录 WARNING
后忽略（兼容版本升级后废弃的键）。
"""

import logging
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from app.config.schema import (
    VALID_COMPUTE_TYPES,
    VALID_DEVICES,
    VALID_LOG_LEVELS,
    VALID_MODEL_SIZES,
    VALID_PROXY_SCHEMES,
    VALID_SUMMARY_LANGUAGES,
    AppConfig,
)
from app.core.errors import ConfigError
from app.utils.fs import atomic_write_text

logger = logging.getLogger(__name__)


def load_config(path: Path) -> AppConfig:
    """读取配置；文件不存在时生成默认配置文件并返回默认值。"""
    if not path.exists():
        config = AppConfig()
        save_config(config, path)
        logger.info("已生成默认配置: %s", path)
        return config
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"配置文件解析失败: {path}: {exc}",
            user_message="配置文件格式有误（YAML 解析失败），请检查或删除后重新生成。",
        ) from exc
    if raw is None:
        raw = {}
    config = _build_dataclass(AppConfig, raw, "")
    validate_config(config)
    return config


def save_config(config: AppConfig, path: Path) -> None:
    """原子写回 yaml（GUI 设置界面保存时调用）。"""
    content = yaml.safe_dump(
        _to_plain_dict(config), allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    atomic_write_text(path, content)


def validate_config(config: AppConfig) -> None:
    """取值级校验；收集全部问题后一次性抛出 ConfigError。"""
    errors: list[str] = []
    _validate_network(config, errors)
    _validate_numbers(config, errors)
    _validate_enums(config, errors)
    if errors:
        raise ConfigError(
            "配置校验失败: " + "; ".join(errors),
            user_message=f"配置有误：{errors[0]}",
        )


def _validate_network(config: AppConfig, errors: list[str]) -> None:
    proxy = config.network.proxy
    if proxy and not proxy.startswith(VALID_PROXY_SCHEMES):
        errors.append(f"network.proxy 需以 {'/'.join(VALID_PROXY_SCHEMES)} 开头，实际 '{proxy}'")


def _validate_numbers(config: AppConfig, errors: list[str]) -> None:
    checks = (
        ("downloader.retries", config.downloader.retries >= 0, "需 >= 0"),
        ("downloader.socket_timeout", config.downloader.socket_timeout > 0, "需 > 0"),
        ("llm.timeout_seconds", config.llm.timeout_seconds > 0, "需 > 0"),
        ("llm.max_retries", config.llm.max_retries >= 0, "需 >= 0"),
        ("llm.max_concurrency", config.llm.max_concurrency >= 1, "需 >= 1"),
        ("summary.chunk_max_tokens", config.summary.chunk_max_tokens > 0, "需 > 0"),
        (
            "summary.chunk_overlap_tokens",
            0 <= config.summary.chunk_overlap_tokens < config.summary.chunk_max_tokens,
            "需 >= 0 且 < chunk_max_tokens",
        ),
    )
    errors.extend(f"{name} {hint}" for name, ok, hint in checks if not ok)


def _validate_enums(config: AppConfig, errors: list[str]) -> None:
    checks = (
        ("stt.model_size", config.stt.model_size, VALID_MODEL_SIZES),
        ("stt.device", config.stt.device, VALID_DEVICES),
        ("stt.compute_type", config.stt.compute_type, VALID_COMPUTE_TYPES),
        ("summary.language", config.summary.language, VALID_SUMMARY_LANGUAGES),
        ("logging.level", config.logging.level, VALID_LOG_LEVELS),
    )
    errors.extend(
        f"{name} 取值 '{value}' 无效，可选: {', '.join(valid)}"
        for name, value, valid in checks
        if value not in valid
    )


def _build_dataclass[T](cls: type[T], data: Any, path: str) -> T:
    """按字段默认值推断类型，递归构建 dataclass。"""
    if not isinstance(data, Mapping):
        raise ConfigError(f"配置节 '{path or '<root>'}' 应为键值映射，实际为 {_type_name(data)}")
    defaults = cls()
    known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    for key in data:
        if key not in known:
            logger.warning("忽略未知配置项: %s", _join(path, str(key)))
    kwargs: dict[str, Any] = {}
    for f in fields(cls):  # type: ignore[arg-type]
        if f.name in data:
            default_value = getattr(defaults, f.name)
            kwargs[f.name] = _convert(default_value, data[f.name], _join(path, f.name))
    return cls(**kwargs)


def _convert(default_value: Any, value: Any, path: str) -> Any:
    if is_dataclass(default_value):
        return _build_dataclass(type(default_value), value, path)
    if isinstance(default_value, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"配置项 '{path}' 应为布尔值，实际为 {_type_name(value)}")
        return value
    if isinstance(default_value, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"配置项 '{path}' 应为整数，实际为 {_type_name(value)}")
        return value
    if isinstance(default_value, str):
        if not isinstance(value, str):
            raise ConfigError(f"配置项 '{path}' 应为字符串，实际为 {_type_name(value)}")
        return value
    if isinstance(default_value, tuple):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"配置项 '{path}' 应为字符串列表，实际为 {_type_name(value)}")
        return tuple(value)
    raise ConfigError(f"配置项 '{path}' 类型不受支持")


def _to_plain_dict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_plain_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, tuple):
        return [_to_plain_dict(v) for v in obj]
    return obj


def _join(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


def _type_name(value: Any) -> str:
    return type(value).__name__
