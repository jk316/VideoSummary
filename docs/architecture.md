# VideoSummary 系统架构设计

> 版本：v1.1（第一阶段交付物；已按 architect 评审意见修订全部 HIGH/MEDIUM 项）
> 日期：2026-07-15
> 依据：docs/requirements.md（需求定稿 v1.0）

## 1. 设计目标与原则

- **低耦合**：外部能力（下载、STT、LLM）一律通过抽象接口调用，实现可替换。
- **GUI 零业务逻辑**：GUI 只做输入、进度展示、结果展示；编排逻辑全部在
  `core/` 层，保证无 GUI 也能跑通（可测试、可加 CLI）。
- **可断点续跑**：管道每个阶段产物落盘缓存，失败/取消后从最近完成阶段继续。
- **不可变数据**：领域模型使用 `@dataclass(frozen=True)`，阶段之间只传值，
  不共享可变状态。
- **依赖注入**：Pipeline 在构造时注入 Downloader/Recognizer/LLMClient 等接口
  实现，测试时注入 Fake 实现即可跑通集成测试。

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────┐
│ ui/  (PySide6)                                           │
│   MainWindow · TaskListView · ResultView · SettingsDialog│
│   LogView · QueueWorker(QThread) · SignalReporter        │
└───────────────▲─────────────────────────┬───────────────┘
        Qt signals（进度/日志/结果）        │ 提交任务/取消
┌───────────────┴─────────────────────────▼───────────────┐
│ core/  （编排层，无 Qt 依赖）                              │
│   TaskQueue ─→ SummaryPipeline（阶段状态机）              │
│   models · events · errors · cancellation                │
└──┬──────┬──────┬──────┬──────┬──────┬──────┬─────────────┘
   │      │      │      │      │      │      │  （均为接口调用）
┌──▼──┐┌──▼───┐┌─▼───┐┌─▼──┐┌─▼───┐┌─▼──┐┌──▼──────┐
│down-││subti-││audio││stt ││chunk││llm ││summarizer│
│loader││tle   ││     ││    ││ing  ││    ││+ export  │
└──┬──┘└──────┘└─┬───┘└─┬──┘└─────┘└─┬──┘└──────────┘
   │             │       │            │
 yt-dlp.exe   ffmpeg  faster-      OpenAI 兼容 API
 (subprocess)  .exe   whisper      (httpx / openai SDK)
─────────────────────────────────────────────────────────
横切模块：config/（yaml+dataclass） cache/（CacheManager）
          utils/（logging·paths·proxy·sanitize·tokens）
```

依赖方向：`ui → core → 各能力模块 → utils/config/cache`。
反向依赖禁止：core 不 import ui；能力模块可 import core 的**共享内核**
（`models` / `events` / `cancellation` / `errors`），
禁止 import 编排逻辑（`pipeline` / `task_queue`）。

## 3. 目录结构

```
VideoSummary/
├── app/
│   ├── main.py                  # 入口：装配依赖 → 启动 GUI
│   ├── core/
│   │   ├── models.py            # 领域模型（全部 frozen dataclass）
│   │   ├── pipeline.py          # SummaryPipeline：单任务阶段状态机
│   │   ├── task_queue.py        # TaskQueue：多任务串行调度
│   │   ├── events.py            # ProgressEvent / Stage / TaskStatus
│   │   ├── cancellation.py      # CancellationToken
│   │   └── errors.py            # 异常层次（含 user_message）
│   ├── downloader/
│   │   ├── base.py              # Downloader 抽象接口
│   │   └── ytdlp.py             # YtDlpDownloader（subprocess 调 yt-dlp.exe）
│   ├── subtitle/
│   │   ├── parser.py            # vtt/srt/json3/B站json → Transcript
│   │   └── cleaner.py           # 自动字幕去重、滚动字幕清洗
│   ├── audio/
│   │   └── processor.py         # AudioProcessor（ffmpeg subprocess）
│   ├── stt/
│   │   ├── base.py              # SpeechRecognizer 抽象接口
│   │   └── whisper.py           # WhisperRecognizer（faster-whisper）
│   ├── chunking/
│   │   └── chunker.py           # TranscriptChunker（tiktoken 计数）
│   ├── llm/
│   │   ├── base.py              # LLMClient 抽象接口 + Message/LLMResponse
│   │   └── openai_compat.py     # OpenAICompatClient（base_url 可配）
│   ├── summarizer/
│   │   ├── summarizer.py        # MapReduceSummarizer
│   │   └── prompts.py           # 默认 Prompt 模板（Map/Reduce×中/英/双语）
│   ├── export/
│   │   └── exporters.py         # Summary→md/txt/json；Transcript→txt/json/srt
│   ├── cache/
│   │   └── manager.py           # CacheManager：分阶段产物缓存
│   ├── config/
│   │   ├── schema.py            # AppConfig 及子配置 dataclass
│   │   └── loader.py            # yaml 读写、默认生成、校验、迁移
│   ├── utils/
│   │   ├── logging_setup.py     # 日志初始化、轮转、脱敏 Filter
│   │   ├── paths.py             # %APPDATA% 等路径解析
│   │   ├── proxy.py             # 代理配置 → 环境变量/参数
│   │   ├── sanitize.py          # Windows 文件名清洗
│   │   ├── subproc.py           # run_subprocess()：CREATE_NO_WINDOW/DEVNULL/watchdog
│   │   └── tokens.py            # tiktoken 封装（离线 BPE，见 §11）
│   └── ui/
│       ├── main_window.py
│       ├── task_list.py         # 任务队列视图
│       ├── result_view.py       # Markdown 渲染 + 复制/导出
│       ├── log_view.py
│       ├── settings_dialog.py   # 含"检查 yt-dlp 更新"、缓存管理入口
│       ├── worker.py            # QueueWorker(QThread)：core 与 Qt 的桥
│       └── reporter.py          # SignalReporter：ProgressEvent → Qt signal
├── tests/
│   ├── unit/                    # 每模块一个测试文件，Mock 外部依赖
│   ├── integration/             # Fake 实现全链路集成测试
│   └── fixtures/                # 样例 vtt/srt/json3、3 秒 wav、假 API 响应
├── packaging/
│   ├── videosummary.spec        # PyInstaller onedir 配置
│   ├── bin/                     # ffmpeg.exe、yt-dlp.exe（随包分发）
│   └── build.ps1
├── docs/
├── config.example.yaml
├── pyproject.toml               # ruff/black/pytest 配置
└── README.md
```

与 task.md 原始架构的差异及理由见 §13。

## 4. 核心数据模型（core/models.py）

全部 `@dataclass(frozen=True)`，跨阶段传值不可变。

```python
class Site(StrEnum):
    YOUTUBE = "youtube"
    BILIBILI = "bilibili"

@dataclass(frozen=True)
class VideoRef:                     # resolve() 的输出：标识 + 粗标题
    site: Site
    video_id: str
    url: str                        # 规范化后的 URL
    title: str | None = None        # flat-playlist 的粗标题，供任务列表先行显示
    playlist_index: int | None = None

@dataclass(frozen=True)
class VideoInfo:
    ref: VideoRef
    title: str
    duration: float                 # 秒
    author: str

@dataclass(frozen=True)
class SubtitleTrack:
    lang: str                       # "zh-Hans" / "en" ...
    is_auto: bool                   # 自动字幕 or 人工字幕
    format: str                     # "vtt" | "srt" | "json3" | "bili_json"

@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str

class TranscriptSource(StrEnum):
    SUBTITLE = "subtitle"
    STT = "stt"

@dataclass(frozen=True)
class Transcript:
    language: str
    source: TranscriptSource
    segments: tuple[Segment, ...]

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.segments)

@dataclass(frozen=True)
class Chunk:
    index: int
    start: float
    end: float
    text: str
    token_count: int

@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # __add__ 返回新实例（不可变累加）

class SummaryLanguage(StrEnum):
    ZH = "zh"
    EN = "en"
    BILINGUAL = "bilingual"

@dataclass(frozen=True)
class SummaryResult:
    markdown: str
    language: SummaryLanguage
    chunk_count: int
    usage: TokenUsage
    elapsed_seconds: float

@dataclass(frozen=True)
class StageTiming:
    stage: Stage
    seconds: float

@dataclass(frozen=True)
class TaskResult:
    info: VideoInfo
    transcript: Transcript
    summary: SummaryResult
    output_files: tuple[Path, ...]
    timings: tuple[StageTiming, ...]    # 各阶段耗时分解（NFR-6）
```

## 5. 能力模块接口

### 5.1 Downloader（downloader/base.py）

```python
class Downloader(ABC):
    @abstractmethod
    def resolve(self, url: str) -> list[VideoRef]:
        """校验并规范化 URL；播放列表/合集展开为多个 VideoRef。"""

    @abstractmethod
    def fetch_info(self, ref: VideoRef) -> tuple[VideoInfo, list[SubtitleTrack]]:
        """获取元信息与可用字幕轨列表。"""

    @abstractmethod
    def download_subtitle(
        self, ref: VideoRef, track: SubtitleTrack, dest_dir: Path,
        cancel: CancellationToken,
    ) -> Path: ...

    @abstractmethod
    def download_audio(
        self, ref: VideoRef, dest_dir: Path,
        progress: ProgressFn, cancel: CancellationToken,
    ) -> Path:
        """仅下载 bestaudio 原始流；不加 -x 转码，转换交给 AudioProcessor。"""

    @abstractmethod
    def timestamp_url(self, ref: VideoRef, seconds: int) -> str:
        """带时间参数的跳转 URL（YouTube ?t=Ns / B 站 ?t=N），供 FR-8.6。"""
```

**实现决策：yt-dlp 以 sidecar 独立 exe 通过 subprocess 调用**，而非 pip 包内嵌：

- 站点接口频繁变化，内嵌 pip 包意味着 yt-dlp 一旧、整个应用必须重新打包分发；
  sidecar exe 支持 `yt-dlp.exe -U` 自更新，应用内提供"更新下载组件"按钮。
- 元信息：`--dump-single-json`（播放列表加 `--flat-playlist`）；
  音频：`-f bestaudio --progress-template` 按行解析进度
  （不用 `-x`，避免多余的一次 ffmpeg 转码）；
  字幕：`--write-subs --write-auto-subs --sub-langs ... --skip-download`。
- exe 副本放于用户可写目录（`%APPDATA%/VideoSummary/bin/`），
  安装目录内的只作首次运行的种子拷贝。
- 代理经 `--proxy`、cookies 经 `--cookies`、超时经 `--socket-timeout` 传入；
  所有 yt-dlp 调用统一经 `utils/subproc.run_subprocess()` 执行
  （`CREATE_NO_WINDOW` + `stdin=DEVNULL` + 无输出 watchdog，`fetch_info`
  同样受其超时保护）。
- 测试只需 mock subprocess，无需网络。

### 5.2 SubtitleParser（subtitle/parser.py）

```python
def parse_subtitle(path: Path, fmt: str, language: str) -> Transcript
```

- 各格式解析后统一走 `cleaner.clean_segments()`：
  合并 YouTube 自动字幕的滚动重复行、去除空段、按时间排序。
- 纯函数模块，无外部依赖，测试用 fixtures 中的样例文件。

### 5.3 AudioProcessor（audio/processor.py）

```python
class AudioProcessor:
    def check_available(self) -> None            # ffmpeg 不可用则抛 AudioError
    def estimate_wav_size(self, duration: float) -> int
    def to_wav_16k_mono(
        self, src: Path, dest: Path,
        progress: ProgressFn, cancel: CancellationToken,
    ) -> Path
```

- ffmpeg subprocess，`-progress pipe:1` 按行解析进度；
  转换前用 `estimate_wav_size` + `shutil.disk_usage` 做磁盘预检。
- 输出先写 `audio.wav.tmp`，成功后 `os.replace` 原子落盘；
  取消/失败时 terminate 进程并删除 tmp，杜绝半截文件被缓存误判命中。
- `check_available()` 在应用启动时自检一次，任务进入 STT 分支前再检一次。

### 5.4 SpeechRecognizer（stt/base.py）

```python
class SpeechRecognizer(ABC):
    @abstractmethod
    def transcribe(
        self, audio_path: Path, *, language: str | None,
        progress: ProgressFn, cancel: CancellationToken,
    ) -> Transcript: ...
```

- `WhisperRecognizer`：faster-whisper，模型规格/`device`/`compute_type`/
  VAD 开关来自配置；`transcribe()` 返回的是 segment 生成器，
  逐段消费即可上报进度（已转写秒数/总时长）并检查取消。
- 模型下载到 `%APPDATA%/VideoSummary/models/`，
  经 `HF_ENDPOINT`（镜像）与代理配置；下载进度经 huggingface_hub 回调上报。
- **faster-whisper 延迟导入**（在 WhisperRecognizer 内部 import）：
  字幕命中场景不加载 CTranslate2，加快启动。

### 5.5 Chunker（chunking/chunker.py）

```python
class TranscriptChunker:
    def __init__(self, max_tokens: int, overlap_tokens: int, encoding: str): ...
    def split(self, transcript: Transcript) -> list[Chunk]
```

- 以 Segment 为最小单元聚合（不切断句子），tiktoken 计数；
  overlap 取上一 chunk 末尾若干 segment。

### 5.6 LLMClient（llm/base.py）

```python
@dataclass(frozen=True)
class Message:
    role: str          # "system" | "user" | "assistant"
    content: str

@dataclass(frozen=True)
class LLMResponse:
    text: str
    usage: TokenUsage

class LLMClient(ABC):
    @abstractmethod
    async def generate(self, messages: list[Message]) -> LLMResponse: ...
    @abstractmethod
    async def check(self) -> None:
        """轻量连通性校验（任务开始前快速失败）。"""
    @abstractmethod
    async def aclose(self) -> None: ...

LLMClientFactory = Callable[[], LLMClient]   # 每次 summarize 创建新实例
```

- `OpenAICompatClient`：官方 `openai` SDK 的 `AsyncOpenAI`，
  `base_url`/`api_key`/`model`/`timeout`/`max_retries` 来自配置；
  代理经 httpx transport 注入。
- **生命周期约束**：AsyncOpenAI 的连接池绑定创建时的事件循环，而每个任务
  各有一次 `asyncio.run()`——跨任务复用同一实例会抛
  `RuntimeError: Event loop is closed`。因此注入的是 `LLMClientFactory`，
  Summarizer 在每次 `summarize()` 内创建、`finally` 中 `aclose()`。
- 重试：SDK 自带指数退避，之上仅补充对 429/5xx 的整体任务级提示。

### 5.7 Summarizer（summarizer/summarizer.py）

```python
class Summarizer(ABC):                       # Pipeline 依赖此抽象
    @abstractmethod
    async def summarize(
        self, info: VideoInfo, chunks: list[Chunk],
        progress: ProgressFn, cancel: CancellationToken,
    ) -> SummaryResult: ...

class MapReduceSummarizer(Summarizer):
    def __init__(self, client_factory: LLMClientFactory, prompts: PromptSet,
                 language: SummaryLanguage, max_concurrency: int): ...
```

- 单 chunk：直接一次总结（跳过 Map-Reduce）。
- 多 chunk：`asyncio.Semaphore(max_concurrency)` 并发 Map → Reduce。
- **取消响应**：summarize 内起 watcher 协程（每 0.5s 轮询 CancellationToken），
  触发即 `task.cancel()` 全部 in-flight LLM 调用并关闭 client——
  避免取消要等 HTTP 超时（最坏 120s）才生效。
- Prompt 模板含占位符：`{title}` `{author}` `{chunk_text}`
  `{chunk_summaries}` `{start_time}` `{end_time}`；
  Reduce 模板要求输出"整体摘要 + 分章节要点（含时间戳）+ 关键结论"。
- 时间戳链接由 Summarizer 后处理生成：`[mm:ss](url&t=Ns)`
  （YouTube/B 站的 t 参数格式差异在 downloader 提供的 URL 模板中处理）。

### 5.8 Export（export/exporters.py）

纯函数：`Transcript → txt/json/srt`、`SummaryResult → md/txt/json`。
文件名 = 清洗后的视频标题 + 后缀，写入配置的输出目录。

## 6. 编排层：Pipeline 与 TaskQueue

### 6.1 阶段状态机（core/pipeline.py）

```
RESOLVE_INFO → GET_TRANSCRIPT → CHUNK → SUMMARIZE → EXPORT
                ├─ 字幕分支: download_subtitle → parse
                └─ STT 分支: download_audio → to_wav → transcribe
```

| 阶段 | 缓存产物（命中即跳过） | 可取消点 |
|------|------------------------|----------|
| RESOLVE_INFO | `meta.json`（VideoInfo+字幕轨） | 请求前后 |
| GET_TRANSCRIPT | `transcript.json`；中间产物 `subtitle.*` / `audio.wav` | 下载分片间、ffmpeg/whisper 逐段间 |
| CHUNK | 不缓存（内存计算，毫秒级） | — |
| SUMMARIZE | `chunks/{i}.{key}.md`（逐 chunk）+ `summary.{key}.md` | 每次 LLM 调用间 |
| EXPORT | 输出目录成品 | — |

- 字幕选择策略：目标语言人工字幕 > 任意人工字幕 > 目标语言自动字幕 >
  视频原语言自动字幕；全部没有 → STT 分支。策略独立成纯函数便于测试。
- Pipeline 只依赖抽象接口，构造函数注入：

```python
class SummaryPipeline:
    def __init__(self, downloader: Downloader, audio: AudioProcessor,
                 recognizer: SpeechRecognizer, chunker: TranscriptChunker,
                 summarizer: Summarizer, cache: CacheManager,
                 config: AppConfig): ...
    def run(self, ref: VideoRef, reporter: ProgressReporter,
            cancel: CancellationToken) -> TaskResult
```

`run()` 是同步阻塞函数（在工作线程执行）；内部 SUMMARIZE 阶段用
`asyncio.run()` 驱动并发 LLM 调用。补充约定：

- 全部依赖为抽象/可注入——集成测试注入 Fake 即可覆盖字幕与 STT 两条分支。
- 阶段边界计时，产出 `TaskResult.timings`（NFR-6）。
- GET_TRANSCRIPT 完成即发布 `transcript_ready` 事件，GUI 据此启用
  "导出转写"入口（FR-5.2，不必等总结完成）。
- **配置快照语义**：每个任务启动时快照 AppConfig，运行中任务不受设置变更
  影响，新配置自下一个任务生效（设置保存时 GUI 给出提示）。

### 6.2 TaskQueue（core/task_queue.py）

- 播放列表 `resolve()` 展开后入队，**串行执行**（CPU STT 与带宽都不适合并行）。
- 任务状态：`PENDING → RUNNING(stage) → DONE | FAILED | CANCELLED`。
- 单任务失败：记录错误，继续下一任务（FR-9.3）。
- 取消：`cancel_current()`（跳过当前，继续队列）与 `cancel_all()`。

### 6.3 进度与事件（core/events.py）

```python
class Stage(StrEnum): ...
class TaskStatus(StrEnum): ...

@dataclass(frozen=True)
class ProgressEvent:
    task_id: str
    stage: Stage
    fraction: float | None      # None = 不确定进度（spinner）
    message: str

class ProgressReporter(Protocol):
    def report(self, event: ProgressEvent) -> None: ...

ProgressFn = Callable[[float | None, str], None]
# 能力模块内部的轻量进度回调（fraction, message）；
# Pipeline 负责把它包装/换算为带 task_id 与 Stage 的 ProgressEvent
```

core 层只认识 `ProgressReporter` 协议；GUI 提供 `SignalReporter`
（转 Qt signal），测试/CLI 提供 logging 实现。

### 6.4 取消（core/cancellation.py）

```python
class CancellationToken:
    def cancel(self) -> None
    def is_cancelled(self) -> bool
    def raise_if_cancelled(self) -> None    # 抛 TaskCancelled
```

包装 `threading.Event`。各能力模块在循环/回调中调用
`raise_if_cancelled()`；subprocess（yt-dlp/ffmpeg）取消时 terminate 进程。

## 7. 线程与异步模型

**决策：QThread 工作线程 + 线程内局部 asyncio，不引入 qasync。**

```
Main Thread (Qt event loop)
   │  submit(urls) / cancel()
   ▼
QueueWorker (QThread, 常驻)
   └─ TaskQueue.run_next() → SummaryPipeline.run()   # 同步阻塞
        ├─ yt-dlp / ffmpeg: subprocess，逐行读进度
        ├─ faster-whisper: CPU 计算，逐 segment 让出
        └─ SUMMARIZE: asyncio.run(summarizer.summarize())  # 并发 LLM
   │
   └─ SignalReporter.report(event) → Qt Signal（跨线程自动 queued）→ UI 更新
```

理由：

- 管道主体是**阻塞型**工作（subprocess、CPU 推理），asyncio 对其无益，
  强行全异步只会引入 `run_in_executor` 噪音。
- 唯一真正受益于并发的是 Map 阶段的多次 LLM 调用——
  在工作线程内用一个局部事件循环即可，范围小、易测。
- qasync 把 Qt 与 asyncio 事件循环合并，增加打包与调试复杂度，收益不成比例。
- Qt 跨线程 signal/slot 自动排队，UI 更新天然线程安全。

跨线程控制通道（评审修订后明确）：

- **任务提交**：主线程 → 线程安全 `queue.Queue` → QueueWorker 循环消费。
  不走 signal→slot——QThread 的 `run()` 阻塞期间其事件循环不运行，
  投递到该线程的 slot 会静默失效。
- **取消**：主线程**直接调用** `CancellationToken.cancel()`
  （threading.Event 线程安全），不经任何事件循环。
- **LLM 客户端生命周期**：事件循环随每次 `asyncio.run()` 创建/销毁，
  故 LLMClient 按次创建（工厂注入，见 §5.6），杜绝跨循环复用。

## 8. 缓存设计（cache/manager.py）

```
%APPDATA%/VideoSummary/cache/
└── {site}_{video_id}/
    ├── meta.json                  # VideoInfo + 字幕轨列表
    ├── subtitle.{lang}.{fmt}      # 原始字幕文件
    ├── audio.source               # bestaudio 原始下载（m4a/webm）
    ├── audio.wav                  # 16k mono wav
    ├── transcript.json            # 统一 Transcript
    ├── chunks/{i}.{skey}.md       # 单 chunk 摘要
    └── summary.{skey}.md          # 最终摘要
```

- 目录 key = `{site}_{video_id}`（URL 规范化后），FR-11.1。
- **摘要类缓存 key（skey）** = `hash(transcript_sha256 + model +
  生效Prompt全文 + language + chunk参数)[:12]`：
  - 含 **transcript_hash**——transcript 重新生成（换 STT 模型、换字幕轨）后，
    旧 chunk 摘要/最终摘要不会误命中；
  - 参与 hash 的是**生效模板全文**而非配置值——`map_prompt: ""` 表示用内置
    默认模板，应用升级修改内置模板后旧缓存自然失效。
- **写入原子性（全阶段统一）**：所有产物先写 `*.tmp` 再 `os.replace`
  （Windows 下 rename 目标已存在会失败，统一用 replace）；
  ffmpeg 输出同样走 tmp（§5.3）；字幕文件以"解析成功"为有效判据，
  解析失败即删除重下。
- `transcript.json` 记录来源元数据（source / 字幕轨 lang / STT 模型规格），
  GUI 提供"重新获取转写"强制失效入口。
- **中间音频自动清理**：`transcript.json` 落盘成功后删除
  `audio.source` / `audio.wav`（1 小时视频 wav 约 110MB+）；
  配置项 `cache.keep_intermediate_audio` 可保留。
- `CacheManager` 提供 `total_size()`（按类别统计）/ `clear()` 供设置界面使用。

## 9. 配置设计（config/schema.py + loader.py）

位置：`%APPDATA%/VideoSummary/config.yaml`，首次运行自动生成默认值。

```yaml
network:
  proxy: ""                      # "http://127.0.0.1:7890" | "socks5://..." | ""
  use_system_proxy: true
downloader:
  ytdlp_path: ""                 # 空=用户目录 bin/yt-dlp.exe
  cookies_file: ""
  retries: 3
  socket_timeout: 30
audio:
  ffmpeg_path: ""                # 空=随包 bin/ffmpeg.exe
subtitle:
  prefer_langs: ["zh-Hans", "zh", "en"]
  allow_auto: true
stt:
  model_size: "small"            # tiny/base/small/medium/large-v3
  device: "cpu"                  # 预留 "cuda"
  compute_type: "int8"
  vad_filter: true
  language: ""                   # 空=自动检测
  hf_endpoint: "https://hf-mirror.com"
llm:
  base_url: "https://api.openai.com/v1"
  api_key: ""
  model: "gpt-4o-mini"
  timeout_seconds: 120
  max_retries: 3
  max_concurrency: 4
summary:
  language: "zh"                 # zh/en/bilingual
  chunk_max_tokens: 3000
  chunk_overlap_tokens: 200
  map_prompt: ""                 # 空=内置默认模板
  reduce_prompt: ""
paths:
  output_dir: ""                 # 空=~/Documents/VideoSummary
  cache_dir: ""                  # 空=%APPDATA%/VideoSummary/cache
cache:
  keep_intermediate_audio: false # transcript 生成后自动删除音频中间产物
logging:
  level: "INFO"
```

- `schema.py`：嵌套 frozen dataclass；`loader.py` 负责
  yaml↔dataclass、默认值合并、类型/取值校验（fail fast，给出具体路径），
  以及 GUI 设置界面的保存回写。
- API Key 仅存本地配置文件，运行时不进日志（logging Filter 脱敏，§10）；
  后续版本可选 Windows DPAPI 加密存储（ADR 备忘，v1 不做）。

## 10. 错误处理与日志

异常层次（core/errors.py）：

```python
class VideoSummaryError(Exception):
    user_message: str            # 面向用户的提示（GUI 弹窗/任务列表显示）

class ConfigError(VideoSummaryError): ...
class DownloadError(VideoSummaryError): ...      # 含"请检查代理设置"类提示
class SubtitleError(VideoSummaryError): ...
class AudioError(VideoSummaryError): ...         # ffmpeg 缺失/磁盘不足
class SttError(VideoSummaryError): ...           # 模型下载失败等
class LlmError(VideoSummaryError): ...           # Key 无效/超时/配额
class TaskCancelled(Exception): ...              # 控制流信号，非错误
```

- Pipeline 捕获 `VideoSummaryError` → 任务标记 FAILED（展示 user_message，
  堆栈进日志）；捕获 `TaskCancelled` → CANCELLED；
  其他异常 → FAILED + "未知错误，详见日志"。
- 日志：`RotatingFileHandler`（10MB×5）写
  `%APPDATA%/VideoSummary/logs/app.log`；`QueueHandler` → GUI 日志窗；
  自定义 Filter 对 `api_key`、`cookies` 值做正则脱敏。

## 11. 打包方案

- PyInstaller **onedir** 模式（onefile 解压启动过慢且易触发杀软误报）。
- 分发目录：

```
VideoSummary/
├── VideoSummary.exe
├── _internal/                    # PySide6、faster-whisper 等依赖
└── bin/
    ├── ffmpeg.exe                # 静态构建版
    └── yt-dlp.exe                # 种子副本
```

- 首次运行：`bin/yt-dlp.exe` 拷贝到 `%APPDATA%/VideoSummary/bin/`
  （可写位置才能自更新）；ffmpeg 原地使用，路径可在配置覆盖。
- Whisper 模型**不打包**，首次 STT 时按配置的镜像/代理下载到用户目录。
- 体积控制：排除 PySide6 未用子模块（QtWebEngine 等）；
  结果窗 Markdown 渲染用 `QTextBrowser.setMarkdown()`，不引入 WebEngine。
- **tiktoken 离线化**：spec 加
  `hiddenimports=['tiktoken_ext', 'tiktoken_ext.openai_public']`；
  BPE 编码文件随包分发并设 `TIKTOKEN_CACHE_DIR`——否则 tiktoken 首次使用
  会联网下载，断网用户在 CHUNK 阶段直接失败。
- **faster-whisper 资产收集**：`collect_data_files('faster_whisper')`
  （含 Silero VAD onnx 资产）+ `collect_dynamic_libs` 收集
  ctranslate2 / onnxruntime DLL——默认不收集时 VAD 路径要到运行期才崩。
- **路径解析（frozen/非 frozen）**：`utils/paths.get_bundle_dir()`：
  `sys.frozen` 时为 `Path(sys.executable).parent`（onedir 模式，
  不是 onefile 的 `_MEIPASS`）；开发态指向仓库 `packaging/` 目录。
- **windowed 模式防护**：无控制台时 `sys.stdout/stderr` 为 `None`，
  启动时兜底重定向；所有 subprocess 统一经 `utils/subproc.run_subprocess()`
  （`CREATE_NO_WINDOW` + `stdin=DEVNULL`，防挂起与闪黑框）；
  HF 模型下载用回调型进度，不依赖 tqdm 默认输出。
- **打包验证清单**：断网环境 CHUNK 可用、VAD 路径可用、
  字幕/STT 两分支各一次端到端冒烟。
- 已知风险：无签名 exe 可能被 SmartScreen/Defender 提示，README 说明。

## 12. 测试策略

| 层 | 方式 |
|----|------|
| downloader | mock subprocess（预录 yt-dlp JSON 输出 fixture），测 URL 规范化、播放列表展开、进度解析、代理参数拼装 |
| subtitle | 真实解析 fixtures（vtt/srt/json3/B站json + 自动字幕重复样例） |
| audio | mock subprocess 测参数与进度解析；1 个 3 秒 wav fixture 做真实转换冒烟测试（本地有 ffmpeg 时） |
| stt | mock faster-whisper 模块，测进度/取消/Transcript 组装 |
| chunking | 纯逻辑直测：边界、overlap、不切断 segment |
| llm | mock AsyncOpenAI / respx 拦截 httpx，测重试、超时、usage 累加 |
| summarizer | 注入 FakeLLMClient，测单 chunk 直通、并发 Map、取消传播 |
| cache | tmp_path 直测：命中/失效（skey、transcript_hash 变化）/原子写/**半成品 tmp 不命中**/clear |
| config | 直测：默认生成、校验失败信息、往返读写 |
| utils | paths 的 frozen/非 frozen 分支（monkeypatch `sys.frozen`）；subproc 的 watchdog 超时 |
| core | FakeDownloader/FakeRecognizer/FakeLLM 注入 Pipeline，测状态机、缓存跳过、断点续跑、取消语义；TaskQueue 测失败不中断 |
| ui | QueueWorker 与 SignalReporter 用 pytest-qt 做信号级测试；纯视图不强求单测 |
| 集成 | 全 Fake 实现走通 URL→Summary→Export 全链路（含字幕分支与 STT 分支各一条）；另设 `-m live` 标记的真实网络用例，CI 默认跳过 |

覆盖率目标 ≥ 80%。统计口径：`app/` 全部计入，豁免 `app/ui/` 中的纯视图
文件（`worker.py` / `reporter.py` 仍计入，以 pytest-qt 覆盖）。

## 13. 与 task.md 原架构的差异说明

| task.md | 本设计 | 理由 |
|---------|--------|------|
| `models/` 顶级模块 | 并入 `core/models.py` | 模型与编排同属核心层；"models"易与 ML 模型混淆 |
| 无编排层 | 新增 `core/pipeline.py` + `task_queue.py` | 业务流程必须有归属，否则会泄漏进 GUI，违反"GUI 无业务逻辑" |
| 无字幕模块 | 新增 `subtitle/` | 字幕优先（决策 D1）是独立解析域 |
| 无导出模块 | 新增 `export/` | md/txt/json/srt 导出是独立职责，GUI 只调用 |
| `tests/` 在 `app/` 内 | 顶级 `tests/` | Python 社区惯例；打包时天然排除 |
| `cache/` 模块含义模糊 | `app/cache/` 是代码，缓存数据在 `%APPDATA%` | 程序目录（Program Files）无写权限 |
| "推荐 asyncio"（全局） | 仅 LLM 并发用 asyncio，主体为线程模型 | 管道主体是 subprocess/CPU 阻塞型，见 §7 |
| yt-dlp 未指定形态 | sidecar exe + 自更新 | 站点变更不应迫使应用重新分发，见 §5.1 |

## 14. 开发阶段规划（修订 task.md 第二~十阶段）

| 阶段 | 内容 | 交付 |
|------|------|------|
| 2 | 项目骨架 + 基础设施：pyproject、core/models、errors、events、cancellation、config、logging、paths、cache | 单测 |
| 3 | Downloader（resolve/info/字幕/音频下载、播放列表、代理） | 单测 |
| 4 | SubtitleParser + cleaner | 单测 |
| 5 | AudioProcessor | 单测 |
| 6 | STT（WhisperRecognizer + 模型下载管理） | 单测 |
| 7 | Chunker | 单测 |
| 8 | LLMClient（OpenAI 兼容） | 单测 |
| 9 | Summarizer + Export | 单测 |
| 10 | Pipeline + TaskQueue 编排 | 单测 + Fake 集成测试 |
| 11 | GUI（主窗、任务列表、设置、日志、结果） | pytest-qt |
| 12 | 端到端集成测试 + live 用例 | 集成测试 |
| 13 | PyInstaller 打包 + 分发验证 | exe |

每阶段完成后：设计说明 → 代码 → 单测 → 跑测 → 修复 → 更新 README →
等待确认（沿用 task.md 约定）。
