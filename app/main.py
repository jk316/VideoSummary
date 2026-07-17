"""应用入口：装配全部依赖，启动 PySide6 GUI。"""

import logging
import sys
from dataclasses import replace
from pathlib import Path

from dotenv import dotenv_values
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
    get_bundle_dir,
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

    # 配置（config.yaml）
    config_path = get_config_path()
    config = load_config(config_path)

    # .env 文件覆盖（API Key 等敏感信息建议通过 .env 管理，不入 git）
    config = _merge_dotenv(config)
    logger.info("LLM base_url=%s model=%s", config.llm.base_url, config.llm.model)

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
        ytdlp_path=ytdlp,
    )
    window.show()

    try:
        sys.exit(app.exec())
    finally:
        worker.stop()
        worker.wait(3000)


def _merge_dotenv(config):
    """从项目根目录或 exe 同级目录加载 .env，覆盖 config.yaml 中的 LLM 配置。

    只有 .env 中非空的值才会覆盖，config.yaml 中已配置的值优先保留。
    """
    from app.utils.paths import is_frozen

    if is_frozen():
        env_path = Path(sys.executable).resolve().parent / ".env"
    else:
        env_path = Path(__file__).resolve().parents[1] / ".env"

    if not env_path.is_file():
        logger.debug("未找到 .env 文件，使用 config.yaml 中的 LLM 配置")
        return config

    values = dotenv_values(str(env_path))
    llm = config.llm
    updated = False

    url = values.get("LLM_BASE_URL", "").strip()
    if url and not llm.base_url:
        llm = replace(llm, base_url=url)
        updated = True

    key = values.get("LLM_API_KEY", "").strip()
    if key and not llm.api_key:
        llm = replace(llm, api_key=key)
        updated = True

    model = values.get("LLM_MODEL", "").strip()
    if model and llm.model == "gpt-4o-mini":
        llm = replace(llm, model=model)
        updated = True

    if updated:
        logger.info("已从 .env 加载 LLM 配置: %s", env_path)
        config = replace(config, llm=llm)
    return config


def _resolve_binary(name: str, configured: str) -> Path:
    import shutil

    if configured:
        return Path(configured)
    # 优先用户 writable 目录（已被拷贝到 %APPDATA%/bin/）
    user_bin = get_bin_dir() / name
    if user_bin.is_file():
        return user_bin
    # 首次运行：从随包/开发 bin 目录拷贝到用户 writable 目录

    source = get_bundle_dir() / "bin" / name
    if source.is_file():
        user_bin.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, user_bin)
        logger.info("首次运行：已拷贝 %s → %s", name, user_bin)
        return user_bin
    return source  # 开发态回退，后续 check_available 会报错


if __name__ == "__main__":
    main()
