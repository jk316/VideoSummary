"""日志初始化：轮转文件 + 敏感信息脱敏；GUI 日志窗 Handler 由 ui 层追加。"""

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

MAX_LOG_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_MASK = "***"
_OWNED_FLAG = "_videosummary_handler"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(cookies?[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
)


def redact_secrets(text: str) -> str:
    """脱敏 API Key / Bearer token / cookies 等敏感值。"""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_replace_match, text)
    return text


class SecretRedactingFilter(logging.Filter):
    """在 Handler 层对最终消息做脱敏，保证敏感值不落盘、不进 GUI。"""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_secrets(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


def setup_logging(logs_dir: Path, level: str = "INFO") -> None:
    """初始化根日志：轮转文件 + （有控制台时）stderr 输出。幂等可重复调用。"""
    root = logging.getLogger()
    root.setLevel(level)
    _remove_owned_handlers(root)
    formatter = logging.Formatter(LOG_FORMAT)

    logs_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        logs_dir / "app.log",
        maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    _attach(root, file_handler, formatter)

    # windowed（无控制台）模式下 sys.stderr 为 None，不加控制台输出
    if sys.stderr is not None:
        _attach(root, logging.StreamHandler(sys.stderr), formatter)


def _attach(root: logging.Logger, handler: logging.Handler, formatter: logging.Formatter) -> None:
    handler.setFormatter(formatter)
    handler.addFilter(SecretRedactingFilter())
    setattr(handler, _OWNED_FLAG, True)
    root.addHandler(handler)


def _remove_owned_handlers(root: logging.Logger) -> None:
    for handler in list(root.handlers):
        if getattr(handler, _OWNED_FLAG, False):
            root.removeHandler(handler)
            handler.close()


def _replace_match(match: re.Match[str]) -> str:
    prefix = match.group(1) if match.lastindex else ""
    return prefix + _MASK
