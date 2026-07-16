"""文件系统工具：统一的原子落盘（tmp + os.replace）。"""

import os
from pathlib import Path

TMP_SUFFIX = ".tmp"


def atomic_write_bytes(path: Path, data: bytes) -> Path:
    """先写 ``*.tmp`` 再 ``os.replace`` 原子落盘。

    取消/崩溃只会留下 tmp 文件，不会产生被误判为有效缓存的半成品。
    Windows 下 rename 目标已存在会失败，统一使用 ``os.replace``。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tmp_path_for(path)
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> Path:
    return atomic_write_bytes(path, content.encode(encoding))


def tmp_path_for(path: Path) -> Path:
    """约定的临时文件路径（``<name>.tmp``），供 ffmpeg 等外部进程输出使用。"""
    return path.parent / (path.name + TMP_SUFFIX)


def remove_stale_tmp(root: Path) -> int:
    """递归清理目录下遗留的 ``*.tmp``，返回清理数量。"""
    if not root.is_dir():
        return 0
    count = 0
    for tmp in root.rglob(f"*{TMP_SUFFIX}"):
        tmp.unlink(missing_ok=True)
        count += 1
    return count
