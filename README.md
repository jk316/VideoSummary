# VideoSummary

Windows 桌面视频内容总结工具：输入 YouTube / Bilibili 视频或播放列表 URL，
自动获取官方字幕（无字幕时下载音频并用 faster-whisper 转写），
调用 OpenAI 兼容 LLM 生成带时间戳章节的 Markdown 总结。

```
URL（单个视频 / 播放列表）
  ↓ 解析规范化（播放列表展开为任务队列）
  ↓ 官方字幕? ── 有 → 下载字幕 → 解析
  │            └ 无 → 下载音频流 → 16k wav → faster-whisper STT
  ↓ Transcript（可导出 txt/json/srt）
  ↓ 按 token 切块（保留时间轴）
  ↓ LLM Map-Reduce 总结（中/英/双语）
  ↓ Markdown 输出（含时间戳跳转链接）
```

## 文档

| 文档 | 内容 |
|------|------|
| [docs/requirements.md](docs/requirements.md) | 需求规格（FR/NFR、关键决策、风险） |
| [docs/architecture.md](docs/architecture.md) | 系统架构（模块、接口、线程模型、缓存、打包） |

## 开发环境

```bash
# Python 3.12+，使用 uv 管理项目
uv sync                   # 自动创建 venv + 安装依赖 + editable install

# 测试（live 用例默认跳过）
uv run pytest --cov=app

# 代码检查
uv run ruff check app tests
uv run black --check app tests

# 格式化（提交前）
uv run black app tests
uv run ruff check app tests --fix

# 添加新依赖
uv add <package>
uv add --group dev <package>
```

## 项目结构

```
app/
├── core/        # 领域模型、事件、取消令牌、异常（共享内核）+ 编排（后续阶段）
├── config/      # yaml <-> dataclass 配置（schema + loader）
├── cache/       # 分阶段缓存（原子落盘、skey 含 transcript_hash）
├── utils/       # paths（%APPDATA%/frozen 解析）、fs（原子写）、日志（脱敏+轮转）
├── downloader/  # （阶段3）yt-dlp sidecar
├── subtitle/    # （阶段4）字幕解析
├── audio/       # （阶段5）ffmpeg
├── stt/         # （阶段6）faster-whisper
├── chunking/    # （阶段7）token 切块
├── llm/         # （阶段8）OpenAI 兼容客户端
├── summarizer/  # （阶段9）Map-Reduce 总结
├── export/      # （阶段9）md/txt/json/srt 导出
└── ui/          # （阶段11）PySide6 GUI
tests/           # unit / integration / fixtures
packaging/       # （阶段13）PyInstaller spec 与 ffmpeg/yt-dlp 二进制
```

配置文件与运行时数据位于 `%APPDATA%/VideoSummary/`（config.yaml、logs、
cache、models、bin），默认输出目录为 `~/Documents/VideoSummary`。
完整配置项见 [config.example.yaml](config.example.yaml)。

## 开发进度

| 阶段 | 内容 | 状态 |
|------|------|------|
| 1 | 需求梳理 + 架构设计（含 architect 评审） | ✅ |
| 2 | 项目骨架与基础设施（models/errors/config/logging/cache） | ✅ 49 tests，覆盖率 97% |
| 3 | Downloader（yt-dlp sidecar） | ✅ 累计 95 tests，覆盖率 95% |
| 4 | SubtitleParser | ✅ 累计 114 tests，覆盖率 95% |
| 5 | AudioProcessor（ffmpeg） | ✅ 累计 128 tests，覆盖率 95% |
| 6 | STT（faster-whisper） | ✅ 累计 141 tests，覆盖率 96% |
| 7 | Chunker（tiktoken） | ✅ 累计 157 tests，覆盖率 96% |
| 8 | LLMClient（OpenAI 兼容） | ✅ 累计 175 tests，覆盖率 96% |
| 9 | Summarizer + Export | ✅ 累计 194 tests，覆盖率 96% |
| 10 | Pipeline + TaskQueue | ✅ 累计 213 tests，覆盖率 96% |
| 11 | GUI（PySide6） | ✅ 累计 218 tests，覆盖率 79% |
| 12 | 端到端集成测试 | ✅ 累计 223 tests，核心 96% / 整体 81% |
| 13 | PyInstaller 打包 | ✅ spec 配置 + build.ps1 + 打包验证清单 |
