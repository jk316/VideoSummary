# -*- mode: python ; coding: utf-8 -*-
"""VideoSummary PyInstaller spec（onedir 模式，排除未用 Qt 模块，收集 tiktoken/faster-whisper 资产）。"""

import os
import sys
from pathlib import Path

# --------- 项目根目录 ---------
_PROJECT = Path(SPECPATH).parent  # packaging/ 的父目录

# --------- tiktoken BPE 资产 ---------
# tiktoken 首次使用会联网下载 BPE 编码文件。为支持断网环境：
# 1. 构建前必须在开发环境执行一次 tiktoken.get_encoding('cl100k_base')
#    触发下载到 TIKTOKEN_CACHE_DIR（通常是 ~/.cache/tiktoken 或环境变量指定）
# 2. 将缓存目录作为数据文件收集
# 3. 运行时通过 TIKTOKEN_CACHE_DIR 环境变量指向打包后的路径

_TIKTOKEN_CACHE = os.environ.get("TIKTOKEN_CACHE_DIR")
if not _TIKTOKEN_CACHE:
    _TIKTOKEN_CACHE = str(Path.home() / ".cache" / "tiktoken")

_tiktoken_datas = []
if os.path.isdir(_TIKTOKEN_CACHE):
    _tiktoken_datas = [
        (str(p.parent), str(p.parent.relative_to(Path(_TIKTOKEN_CACHE).parent)))
        for p in Path(_TIKTOKEN_CACHE).rglob("*")
        if p.is_file()
    ]
    # 去重
    _tiktoken_datas = list(dict.fromkeys(_tiktoken_datas))

# --------- faster-whisper 资产 ---------
# CTranslate2 / onnxruntime 的动态库和 Silero VAD 资产默认不会被 PyInstaller 收集。
# 从 pip 包中显式收集二进制文件。
_ctranslate2_datas = []
_onnxruntime_datas = []
_fw_datas = []

try:
    import ctranslate2

    _ct2_dir = Path(ctranslate2.__file__).parent
    _ctranslate2_datas = [
        (str(p), str(p.parent.relative_to(_ct2_dir.parent)))
        for p in _ct2_dir.rglob("*.dll")
    ]
except ImportError:
    pass

try:
    import onnxruntime

    _ort_dir = Path(onnxruntime.__file__).parent
    _onnxruntime_datas = [
        (str(p), str(p.parent.relative_to(_ort_dir.parent)))
        for p in _ort_dir.rglob("*.dll")
    ]
except ImportError:
    pass

try:
    import faster_whisper

    _fw_dir = Path(faster_whisper.__file__).parent
    _fw_datas = [
        (str(p), str(p.parent.relative_to(_fw_dir.parent)))
        for p in _fw_dir.rglob("*")
        if p.is_file() and not p.suffix == ".py"
    ]
except ImportError:
    pass

# --------- 二进制资产 ---------
_bin_dir = _PROJECT / "packaging" / "bin"
_bin_datas = []
if _bin_dir.is_dir():
    _bin_datas = [(str(p), "bin") for p in _bin_dir.iterdir() if p.is_file()]

# --------- 收集所有数据 ---------
all_datas = []
all_datas.extend(_tiktoken_datas)
all_datas.extend(_ctranslate2_datas)
all_datas.extend(_onnxruntime_datas)
all_datas.extend(_fw_datas)
all_datas.extend(_bin_datas)

# --------- 排除未用 PySide6 子模块 ---------
_pyside6_excludes = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtXml",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtHttpServer",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSpatialAudio",
    "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtWebSockets",
]

# ===================================================================
a = Analysis(
    [str(_PROJECT / "app" / "main.py")],
    pathex=[str(_PROJECT)],
    binaries=[],
    datas=all_datas,
    hiddenimports=[
        "tiktoken_ext",
        "tiktoken_ext.openai_public",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_pyside6_excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VideoSummary",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # windowed 模式：不显示控制台窗口
    icon=None,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="VideoSummary",
)
