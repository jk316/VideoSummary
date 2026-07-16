"""应用路径解析：用户数据目录（%APPDATA%）与打包资源目录（frozen/开发态）。"""

import os
import sys
from pathlib import Path

APP_NAME = "VideoSummary"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包环境。"""
    return bool(getattr(sys, "frozen", False))


def get_bundle_dir() -> Path:
    """随包资源目录（bin/ffmpeg.exe、yt-dlp.exe 种子副本所在）。

    onedir 模式下为 exe 所在目录（非 onefile 的 ``_MEIPASS``）；
    开发态指向仓库 ``packaging/`` 目录。
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _repo_root() / "packaging"


def get_app_data_dir() -> Path:
    """用户数据根目录：``%APPDATA%/VideoSummary``（自动创建）。"""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) / APP_NAME if appdata else Path.home() / f".{APP_NAME.lower()}"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_config_path() -> Path:
    return get_app_data_dir() / "config.yaml"


def get_logs_dir() -> Path:
    return _sub_dir("logs")


def get_cache_dir() -> Path:
    return _sub_dir("cache")


def get_models_dir() -> Path:
    return _sub_dir("models")


def get_bin_dir() -> Path:
    """用户可写的二进制目录（yt-dlp.exe 自更新副本所在）。"""
    return _sub_dir("bin")


def get_default_output_dir() -> Path:
    """默认输出目录：``~/Documents/VideoSummary``（自动创建）。"""
    out = Path.home() / "Documents" / APP_NAME
    out.mkdir(parents=True, exist_ok=True)
    return out


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sub_dir(name: str) -> Path:
    sub = get_app_data_dir() / name
    sub.mkdir(parents=True, exist_ok=True)
    return sub
