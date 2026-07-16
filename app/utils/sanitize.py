"""Windows 文件名清洗：非法字符、保留名、长度限制。"""

import re

DEFAULT_MAX_LENGTH = 120
_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_FALLBACK_NAME = "untitled"


def sanitize_filename(name: str, max_length: int = DEFAULT_MAX_LENGTH) -> str:
    """清洗为合法的 Windows 文件名（不含扩展名或含扩展名均可）。

    非法字符替换为下划线，压缩空白，去除结尾的点/空格（Windows 不允许），
    规避 CON/PRN 等保留名，超长截断。
    """
    cleaned = _WHITESPACE.sub(" ", name)  # 先折叠空白（\t\n 属于控制字符，需在替换前处理）
    cleaned = _INVALID_CHARS.sub("_", cleaned)
    cleaned = cleaned.strip().rstrip(". ")
    if not cleaned.strip("_ ."):  # 清洗后只剩占位符号视为无效
        return _FALLBACK_NAME
    if cleaned.split(".", 1)[0].upper() in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    truncated = cleaned[:max_length].rstrip(". ")
    return truncated or _FALLBACK_NAME
