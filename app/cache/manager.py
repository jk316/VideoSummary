"""分阶段缓存：目录 key = {site}_{video_id}，产物统一原子落盘。

摘要类缓存 key（skey）由 ``make_summary_key`` 计算，包含 transcript 内容
哈希与生效 Prompt 全文——换模型/改 Prompt/重新转写均不会误命中旧结果。
"""

import hashlib
import re
import shutil
from pathlib import Path

from app.utils.fs import TMP_SUFFIX, atomic_write_bytes, atomic_write_text, remove_stale_tmp

_KEY_SEPARATOR = "\x1f"
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_\-]")
SUMMARY_KEY_LENGTH = 12


def sha256_text(text: str) -> str:
    """文本的 sha256 十六进制摘要（用于 transcript_hash）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_summary_key(
    transcript_sha: str,
    model: str,
    prompt_text: str,
    language: str,
    chunk_max_tokens: int,
    chunk_overlap_tokens: int,
) -> str:
    """摘要类缓存 key：任一输入变化都会得到新 key。

    Args:
        transcript_sha: transcript 内容哈希（``sha256_text(transcript.text)``）。
        prompt_text: 生效的完整模板全文（内置默认或用户自定义），
            而非配置值——内置模板随版本变化时旧缓存自然失效。
    """
    payload = _KEY_SEPARATOR.join(
        (
            transcript_sha,
            model,
            prompt_text,
            language,
            str(chunk_max_tokens),
            str(chunk_overlap_tokens),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:SUMMARY_KEY_LENGTH]


class VideoCache:
    """单个视频的缓存目录访问器；写入均为原子落盘。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        remove_stale_tmp(self.root)  # 上次取消/崩溃遗留的半成品

    def path(self, name: str) -> Path:
        return self.root / name

    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    def read_text(self, name: str, encoding: str = "utf-8") -> str:
        return self.path(name).read_text(encoding=encoding)

    def read_bytes(self, name: str) -> bytes:
        return self.path(name).read_bytes()

    def write_text(self, name: str, content: str) -> Path:
        return atomic_write_text(self.path(name), content)

    def write_bytes(self, name: str, data: bytes) -> Path:
        return atomic_write_bytes(self.path(name), data)

    def delete(self, name: str) -> None:
        self.path(name).unlink(missing_ok=True)


class CacheManager:
    """缓存根目录管理：视频子目录分配、按类别统计、一键清空。"""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def for_video(self, site: str, video_id: str) -> VideoCache:
        key = f"{_sanitize(site)}_{_sanitize(video_id)}"
        return VideoCache(self.cache_dir / key)

    def total_size(self) -> dict[str, int]:
        """按类别统计字节数，含 ``total``（供设置界面展示）。"""
        sizes: dict[str, int] = {}
        for file in self.cache_dir.rglob("*"):
            if not file.is_file():
                continue
            category = _categorize(file.relative_to(self.cache_dir))
            sizes[category] = sizes.get(category, 0) + file.stat().st_size
        sizes["total"] = sum(sizes.values())
        return sizes

    def clear(self) -> None:
        for child in self.cache_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)


def _categorize(relative: Path) -> str:
    name = relative.name.removesuffix(TMP_SUFFIX)
    if len(relative.parts) >= 3 and relative.parts[1] == "chunks":
        return "summary"
    if name.startswith("summary."):
        return "summary"
    if name.startswith("audio."):
        return "audio"
    if name.startswith("subtitle."):
        return "subtitle"
    if name == "transcript.json":
        return "transcript"
    if name == "meta.json":
        return "meta"
    return "other"


def _sanitize(value: str) -> str:
    return _UNSAFE_CHARS.sub("_", value)
