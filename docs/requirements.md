# VideoSummary 需求规格说明

> 版本：v1.0（定稿）
> 日期：2026-07-15
> 来源：task.md + 需求梳理确认

## 1. 项目概述

Windows 桌面程序（PySide6 GUI，PyInstaller 打包为 exe）。用户输入视频 URL
（YouTube / Bilibili，可扩展），程序自动获取字幕或语音转写，调用 LLM 生成
Markdown 总结。普通 Windows 用户无需安装 Python 即可使用。

## 2. 范围

### v1 范围内

- 单个视频 URL 处理
- 播放列表 / 合集 URL 批量处理（任务队列，逐个执行）
- 官方字幕优先，无字幕时走 STT（faster-whisper，仅 CPU）
- 网络代理支持（GUI 可配置，作用于下载、模型下载、LLM 请求）
- OpenAI 兼容 LLM API（base_url 可配置，覆盖 DeepSeek/Qwen/中转站等）
- Map-Reduce 总结，中文 / 英文 / 双语
- 各阶段缓存与断点续跑
- GUI 设置界面（API Key、模型、代理、路径等，无需手改 yaml）

### v1 范围外（预留接口，不实现）

- 本地视频/音频文件输入
- GPU（CUDA）加速——代码保留 device 配置项，exe 仅保证 CPU
- FunASR / SenseVoice / OpenAI Whisper API 等其他 STT 实现
- Claude / Gemini / Ollama 原生客户端（OpenAI 兼容协议已可覆盖 Ollama）

## 3. 核心流程

```
输入 URL（单个视频 或 播放列表/合集）
    ↓
URL 解析与规范化
    ├─ 播放列表 → 展开为 N 个视频任务，进入任务队列
    └─ 单视频   → 单任务
    ↓（对每个任务）
获取视频元信息（标题、时长、作者）+ 探测可用字幕
    ├─ 有官方字幕/CC → 下载字幕 → 解析为 Transcript（秒级完成）
    └─ 无字幕 → 下载音频流（bestaudio，不下视频画面）
                → ffmpeg 转 16kHz/mono/wav
                → faster-whisper 转写 → Transcript
    ↓
Chunk 切分（按 token 计数，保留时间轴，句子边界优先，可配置 overlap）
    ↓
LLM 总结
    ├─ 单 chunk → 直接一次总结
    └─ 多 chunk → Map（并发总结各 chunk）→ Reduce（汇总为最终总结）
    ↓
输出 Markdown（含章节时间戳跳转链接）
导出：Summary（md/txt/json）、Transcript（txt/json/srt）
```

## 4. 功能需求（按模块）

### FR-1 下载模块（Downloader）

- FR-1.1 基于 yt-dlp，支持 YouTube、Bilibili；站点扩展通过接口预留
- FR-1.2 URL 校验：非法 URL、不支持站点给出友好错误提示
- FR-1.3 URL 规范化：短链、带跟踪参数等不同形式的同一视频命中同一缓存
- FR-1.4 播放列表/合集识别与展开，返回条目列表（含各条目标题）
- FR-1.5 元信息获取：`VideoInfo(video_id, url, title, duration, author, ...)`
- FR-1.6 字幕探测与下载：列出可用字幕轨（人工字幕优先于自动字幕），
  下载为 vtt/srt/json 格式
- FR-1.7 无字幕时仅下载音频流（bestaudio），不下载完整视频
- FR-1.8 下载进度回调（百分比 + 速度）；失败重试（可配置次数）；超时控制
- FR-1.9 代理支持：HTTP/SOCKS5，可从配置读取
- FR-1.10 Windows 文件名非法字符清洗（标题含 `\/:*?"<>|`）
- FR-1.11 （低优先）支持配置 cookies 文件路径，用于会员/受限视频

### FR-2 字幕解析（SubtitleParser）※ 字幕优先决策新增

- FR-2.1 解析 vtt / srt / json3（YouTube）/ B 站字幕 JSON 为统一 Transcript
- FR-2.2 保留每条字幕的 start/end 时间戳
- FR-2.3 自动字幕的重复行/滚动字幕去重（YouTube 自动 CC 常见问题）

### FR-3 音频模块（AudioProcessor）

- FR-3.1 ffmpeg 提取/转换音频，统一输出 16kHz、mono、wav
- FR-3.2 启动时检测 ffmpeg 可用性，缺失时明确提示
- FR-3.3 转换前磁盘空间预检查

### FR-4 STT 模块（SpeechRecognizer）

- FR-4.1 抽象接口 `SpeechRecognizer.transcribe(audio_path) -> Transcript`；
  默认实现 WhisperRecognizer（faster-whisper）
- FR-4.2 Transcript 数据结构：`text, language, segments[{start, end, text}]`
- FR-4.3 模型规格可配置（tiny/base/small/medium/large-v3），默认兼顾速度与
  质量（建议 small 或 medium + int8 量化）
- FR-4.4 device 配置项保留（cpu/cuda），v1 打包仅保证 CPU
- FR-4.5 模型首次下载：进度提示；支持配置 HF 镜像源（HF_ENDPOINT）与代理
- FR-4.6 语言：默认自动检测，可手动指定
- FR-4.7 启用 VAD 静音过滤（可配置开关）
- FR-4.8 转写进度回调（已处理时长 / 总时长）

### FR-5 Transcript 导出

- FR-5.1 保存为 txt / json / srt
- FR-5.2 GUI 中可独立导出 Transcript（不依赖总结完成）

### FR-6 Chunk 模块

- FR-6.1 按 token 数切分（tiktoken 计数），禁止按字符切分
- FR-6.2 保留时间轴：`Chunk(start_time, end_time, text, token_count)`
- FR-6.3 chunk 大小与 overlap 可配置
- FR-6.4 优先在 segment/句子边界切分，不切断句子

### FR-7 LLM 模块（LLMClient）

- FR-7.1 统一接口 `generate(messages, ...) -> str`
- FR-7.2 默认 OpenAI 兼容实现；`base_url`、`api_key`、`model` 均可配置
- FR-7.3 预留接口以便未来新增 Claude / Gemini 原生客户端
- FR-7.4 超时、重试（指数退避）、并发上限可配置
- FR-7.5 token 用量统计（prompt/completion tokens），任务完成后汇总展示
- FR-7.6 API Key 校验缺失时快速失败并提示（不进入下载/STT 后才报错）

### FR-8 Summarizer

- FR-8.1 Map-Reduce：多 chunk 并发 Map，Reduce 汇总
- FR-8.2 单 chunk 直通（跳过 Map-Reduce）
- FR-8.3 Prompt 模板可配置（Map 与 Reduce 分别可配）
- FR-8.4 输出语言：中文 / 英文 / 双语
- FR-8.5 默认总结结构：整体摘要 + 分章节要点（含时间戳）+ 关键结论
- FR-8.6 章节时间戳生成可点击链接（如 `[12:34](https://youtu.be/xx?t=754)`）

### FR-9 任务队列 ※ 播放列表决策新增

- FR-9.1 播放列表展开后逐个排队处理（串行执行，避免资源争抢）
- FR-9.2 GUI 显示任务列表：每项状态（等待/进行中/完成/失败/已取消）
- FR-9.3 单项失败不中断队列，记录错误后继续下一项
- FR-9.4 支持取消当前任务 / 取消整个队列
- FR-9.5 每个视频独立输出一份总结文件；（可选）队列完成后生成合集汇总

### FR-10 GUI

- FR-10.1 主界面：URL 输入框、开始、取消、任务列表、
  分阶段进度条（下载 / STT / LLM）、实时日志窗、结果窗
- FR-10.2 结果窗支持 Markdown 渲染预览 + 一键复制
- FR-10.3 导出：Summary（md/txt/json）、Transcript（txt/json/srt）
- FR-10.4 设置界面：API Key（掩码显示）、base_url、模型名、代理、
  Whisper 模型规格、输出/缓存目录、总结语言、Prompt 模板
- FR-10.5 所有耗时操作在工作线程执行，GUI 不冻结
- FR-10.6 取消语义：可中断任一阶段；已完成阶段的缓存保留，可续跑
- FR-10.7 错误提示面向用户（如"无法连接 YouTube，请检查代理设置"），
  技术细节进日志

### FR-11 缓存

- FR-11.1 缓存 key 基于规范化的 video_id（站点+ID），非原始 URL 字符串
- FR-11.2 分阶段缓存：字幕文件 / 音频 / Transcript / chunk summaries /
  final summary，任一阶段命中即跳过
- FR-11.3 失败或取消后重跑可从最近完成阶段继续
- FR-11.4 缓存管理：查看占用大小、一键清空

### FR-12 配置

- FR-12.1 config.yaml：ffmpeg 路径、API 配置、模型名、代理、
  输出目录、缓存目录、Whisper 配置、chunk 参数、Prompt 模板
- FR-12.2 首次运行自动生成默认配置
- FR-12.3 配置写入用户目录（%APPDATA%/VideoSummary），不写程序安装目录
- FR-12.4 启动时校验配置，缺失/非法项给出明确提示
- FR-12.5 GUI 设置界面与 yaml 双向同步

### FR-13 日志

- FR-13.1 logging 分级（INFO/WARNING/ERROR），保存至 logs/（用户目录下）
- FR-13.2 GUI 实时日志窗
- FR-13.3 日志轮转（按大小或天数）
- FR-13.4 敏感信息脱敏：API Key、cookies 不得进入日志

## 5. 非功能需求

- NFR-1 代码规范：Python 3.12+、类型注解、dataclass、Google Docstring、
  Ruff 通过、Black 格式化
- NFR-2 架构：模块低耦合；模型/外部服务一律走抽象接口；
  业务逻辑不写入 GUI 层
- NFR-3 测试：每模块单元测试，Mock 外部 API（网络/LLM/STT）；
  最终提供 Downloader→STT→LLM→Summary 集成测试；覆盖率 ≥ 80%
- NFR-4 打包：PyInstaller，onedir 模式（onefile 启动过慢）；
  随包分发或引导下载 ffmpeg 与 yt-dlp 二进制；
  Whisper 模型不打包，首次运行下载到用户目录
- NFR-5 异步：耗时 IO 使用 asyncio / 工作线程，GUI 保持响应
- NFR-6 可观测：每个任务结束展示耗时分解与 LLM token 用量

## 6. 关键决策记录（ADR 摘要）

| # | 决策 | 结论 | 影响 |
|---|------|------|------|
| D1 | 官方字幕处理 | 字幕优先，无字幕才 STT | 新增字幕探测/下载/解析链路；常见场景处理时间从几十分钟降到秒级 |
| D2 | 网络代理 | 支持，GUI 可配置 | 代理配置贯穿 yt-dlp、HF 模型下载、LLM 请求 |
| D3 | 输入范围 | 单 URL + 播放列表/合集批量 | 新增任务队列与任务列表 UI；本地文件输入 v1 不做 |
| D4 | STT 硬件 | 仅 CPU（首版） | 打包体积可控；device 留配置项，GPU 后续版本再议 |

## 7. 主要风险

| 风险 | 说明 | 缓解 |
|------|------|------|
| yt-dlp 失效 | 站点接口频繁变更，旧版 yt-dlp 会下载失败 | yt-dlp 独立为可更新组件（binary 或 pip 包），提供更新入口 |
| 打包体积与误报 | PySide6+CTranslate2 体积大；无签名 exe 可能被 Defender 误报 | onedir 模式；README 说明；后续可考虑签名 |
| CPU STT 速度 | medium 模型 1 小时视频约 20-40 分钟 | 字幕优先绕开多数场景；默认 small+int8+VAD；进度可视 |
| 自动字幕质量 | YouTube 自动 CC 有重复/断句问题 | 解析时去重清洗；人工字幕优先于自动字幕 |
| 长上下文成本 | 超长视频 Map-Reduce 多次调用 LLM | chunk 并发 + token 用量展示；chunk 大小可调 |

## 8. 遗留开放问题（不阻塞设计，开发中确认）

- LLM 流式输出到结果窗（体验加分项，v1 可选）
- 队列完成后的"合集级汇总"是否需要
- 历史记录持久化（v1 以输出目录 + 缓存代替）
