# VideoSummary 打包指南

## 前置条件

1. **Python 3.12+** + 项目依赖已安装（`uv sync`）
2. **ffmpeg.exe** 放于 `packaging/bin/`
   - 下载: https://www.gyan.dev/ffmpeg/builds/ （ffmpeg-release-essentials.zip）
   - 解压后取 `bin/ffmpeg.exe`
3. **yt-dlp.exe** 放于 `packaging/bin/`
   - 下载: https://github.com/yt-dlp/yt-dlp/releases
4. **tiktoken BPE 已预下载**（构建脚本会自动触发）
5. （可选）**faster-whisper** 已安装（`uv add faster-whisper`）
   - 若无字幕场景不需要 STT，可不安装，应用会在首次转写时报错提示

## 构建

```powershell
# PowerShell
.\packaging\build.ps1
```

或手动执行：

```bash
# 预下载 tiktoken BPE
uv run python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

# PyInstaller
uv run pyinstaller --clean packaging/videosummary.spec
```

## 产物

```
dist/VideoSummary/
├── VideoSummary.exe          # 入口（windowed 模式，无控制台）
├── bin/
│   └── yt-dlp.exe            # 种子副本（首次运行拷贝到 %APPDATA%）
├── _internal/                # Python 运行时 + 依赖
│   ├── python3.dll
│   ├── PySide6/
│   ├── ...
│   └── bin/
│       └── ffmpeg.exe        # 随包 ffmpeg
└── ...
```

## 运行时行为

- 首次运行：`bin/yt-dlp.exe` 拷贝到 `%APPDATA%/VideoSummary/bin/`（可写位置才能自更新 `yt-dlp -U`）
- Whisper 模型**不打包**，首次 STT 时从 HF 镜像/代理下载到 `%APPDATA%/VideoSummary/models/`
- tiktoken BPE 随包分发，**断网环境 CHUNK 阶段可用**
- 配置文件自动生成于 `%APPDATA%/VideoSummary/config.yaml`
- 日志输出到 `%APPDATA%/VideoSummary/logs/app.log`

## 打包验证清单

- [ ] 断网环境启动不崩溃
- [ ] URL 输入 → 字幕路径通（yt-dlp 下载字幕 + LLM 总结）
- [ ] 无字幕 → STT 路径通（faster-whisper 模型首次下载后可用）
- [ ] VAD 路径可用（转写时 vad_filter=True 不崩溃）
- [ ] CHUNK 阶段断网可用（tiktoken BPE 离线）
- [ ] 设置界面可正常打开、编辑、保存
- [ ] 配置热重载（改设置后任务使用新配置）
- [ ] 任务取消（当前 / 全部）
- [ ] 导出：Summary（md/json）、Transcript（txt/srt）
- [ ] 进程退出时工作线程正常关闭

## 已知限制

- **yt-dlp 需定期更新**：站点接口频繁变更，通过设置菜单「检查 yt-dlp 更新」或手动替换 `%APPDATA%/VideoSummary/bin/yt-dlp.exe` 更新
- **无签名 exe**：可能被 Windows SmartScreen / Defender 提示，需手动允许运行
- **首次 Whisper 模型下载**：需网络 + 代理配置正确，约 484 MB（small 模型），视网络可能需要数分钟
- **体积**：打包目录约 300-500 MB（含 PySide6 + Python 运行时）
