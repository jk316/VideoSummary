"""应用入口：装配全部依赖，启动 PySide6 GUI。"""

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.audio.processor import AudioProcessor
from app.cache.manager import CacheManager
from app.chunking.chunker import TranscriptChunker
from app.config.loader import load_config
from app.core.pipeline import SummaryPipeline
from app.core.task_queue import TaskQueue
from app.downloader.ytdlp import YtDlpDownloader
from app.llm.base import LLMClientFactory
from app.llm.openai_compat import make_openai_factory
from app.stt.whisper import WhisperRecognizer
from app.summarizer.summarizer import MapReduceSummarizer
from app.ui.main_window import MainWindow
from app.ui.reporter import SignalReporter
from app.ui.worker import QueueWorker
from app.utils.logging_setup import setup_logging
from app.utils.paths import (
    get_bin_dir,
    get_cache_dir,
    get_config_path,
    get_logs_dir,
    get_models_dir,
)
from app.utils.tokens import get_token_counter

logger = logging.getLogger(__name__)


def main() -> None:
    # 日志（启动即初始化，GUI 日志窗由 LogView 追加）
    setup_logging(get_logs_dir())
    logger.info("VideoSummary 启动")

    # 配置
    config_path = get_config_path()
    config = load_config(config_path)
    models_dir = get_models_dir()
    cache_dir = Path(config.paths.cache_dir) if config.paths.cache_dir else get_cache_dir()

    # 二进制路径（随包 bin 目录 → 首次运行拷贝到用户 writable 目录）
    ffmpeg = _resolve_binary("ffmpeg.exe", config.audio.ffmpeg_path)
    ytdlp = _resolve_binary("yt-dlp.exe", config.downloader.ytdlp_path)

    # --------- 装配依赖 ---------

    downloader = YtDlpDownloader(ytdlp, config.downloader, config.network)
    audio = AudioProcessor(ffmpeg)
    recognizer = WhisperRecognizer(config.stt, models_dir, config.network)
    counter = get_token_counter(cache_dir=Path(cache_dir) / "tiktoken")
    chunker = TranscriptChunker(
        config.summary.chunk_max_tokens, config.summary.chunk_overlap_tokens, counter
    )
    factory: LLMClientFactory = make_openai_factory(config.llm, config.network, models_dir)
    summarizer = MapReduceSummarizer(
        client_factory=factory,
        language=config.summary.language,
        max_concurrency=config.llm.max_concurrency,
        timestamp_url=downloader.timestamp_url,
        map_prompt=config.summary.map_prompt,
        reduce_prompt=config.summary.reduce_prompt,
    )
    cache = CacheManager(cache_dir)
    pipeline = SummaryPipeline(downloader, audio, recognizer, chunker, summarizer, cache, config)
    task_queue = TaskQueue(pipeline)

    reporter = SignalReporter()
    worker = QueueWorker(task_queue, reporter)

    # --------- 启动检查 ---------

    try:
        downloader.check_available()
    except Exception as exc:
        logger.warning("yt-dlp 不可用: %s", exc)
    try:
        audio.check_available()
    except Exception as exc:
        logger.warning("ffmpeg 不可用: %s", exc)

    # --------- GUI ---------

    app = QApplication(sys.argv)
    app.setApplicationName("VideoSummary")

    def resolve_urls(raw: str):
        return downloader.resolve(raw)

    def get_current_config():
        return config

    def reload():
        nonlocal config
        config = load_config(config_path)

    window = MainWindow(
        config_path=config_path,
        resolve_urls=resolve_urls,
        get_config=get_current_config,
        reload_config=reload,
        worker=worker,
        reporter=reporter,
    )
    window.show()

    try:
        sys.exit(app.exec())
    finally:
        worker.stop()
        worker.wait(3000)


def _resolve_binary(name: str, configured: str) -> Path:
    if configured:
        return Path(configured)
    bundled = get_bin_dir() / name
    if bundled.is_file():
        return bundled
    # 开发态：回退到 packaging/bin/
    from app.utils.paths import get_bundle_dir

    return get_bundle_dir() / "bin" / name


if __name__ == "__main__":
    main()
