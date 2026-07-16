"""配置数据模型：嵌套不可变 dataclass，默认值即 config.yaml 的默认内容。"""

from dataclasses import dataclass, field

VALID_MODEL_SIZES = ("tiny", "base", "small", "medium", "large-v3")
VALID_DEVICES = ("cpu", "cuda")
VALID_COMPUTE_TYPES = ("int8", "int8_float16", "float16", "float32")
VALID_SUMMARY_LANGUAGES = ("zh", "en", "bilingual")
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
VALID_PROXY_SCHEMES = ("http://", "https://", "socks5://", "socks5h://", "socks4://")


@dataclass(frozen=True)
class NetworkConfig:
    proxy: str = ""
    use_system_proxy: bool = True


@dataclass(frozen=True)
class DownloaderConfig:
    ytdlp_path: str = ""
    cookies_file: str = ""
    retries: int = 3
    socket_timeout: int = 30


@dataclass(frozen=True)
class AudioConfig:
    ffmpeg_path: str = ""


@dataclass(frozen=True)
class SubtitleConfig:
    prefer_langs: tuple[str, ...] = ("zh-Hans", "zh", "en")
    allow_auto: bool = True


@dataclass(frozen=True)
class SttConfig:
    model_size: str = "small"
    device: str = "cpu"
    compute_type: str = "int8"
    vad_filter: bool = True
    language: str = ""
    hf_endpoint: str = "https://hf-mirror.com"


@dataclass(frozen=True)
class LlmConfig:
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    timeout_seconds: int = 120
    max_retries: int = 3
    max_concurrency: int = 4


@dataclass(frozen=True)
class SummaryConfig:
    language: str = "zh"
    chunk_max_tokens: int = 3000
    chunk_overlap_tokens: int = 200
    map_prompt: str = ""
    reduce_prompt: str = ""


@dataclass(frozen=True)
class PathsConfig:
    output_dir: str = ""
    cache_dir: str = ""


@dataclass(frozen=True)
class CacheConfig:
    keep_intermediate_audio: bool = False


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"


@dataclass(frozen=True)
class AppConfig:
    """应用配置根节点；空字符串路径项表示使用默认位置（见 utils/paths.py）。"""

    network: NetworkConfig = field(default_factory=NetworkConfig)
    downloader: DownloaderConfig = field(default_factory=DownloaderConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    subtitle: SubtitleConfig = field(default_factory=SubtitleConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    summary: SummaryConfig = field(default_factory=SummaryConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
