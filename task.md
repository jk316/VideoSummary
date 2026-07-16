# Role

你是一位资深 Python 软件架构师和 AI 应用工程师。

请帮助我开发一个可以在 Windows 上运行的视频内容总结工具。

在开始编码之前，请先完成系统设计，再逐步实现。

整个开发过程必须遵循模块化、可测试、易维护的原则。

------

# 项目目标

开发一个 Windows 桌面程序。

用户输入一个视频 URL（例如：

- Youtube
- Bilibili

程序自动完成以下流程：

```
输入 URL
    ↓
下载视频
    ↓
提取音频
    ↓
语音识别(STT)
    ↓
生成完整 Transcript
    ↓
调用 LLM 总结
    ↓
输出 Markdown 总结
```

最终能够打包成一个 Windows exe。

------

# 技术要求

语言：

Python 3.12+

GUI：

PySide6

视频下载：

yt-dlp

音频处理：

ffmpeg

STT：

默认使用 faster-whisper。

设计时需要保留接口，方便未来替换为：

- FunASR
- SenseVoice
- OpenAI Whisper API

LLM：

默认兼容 OpenAI API。

要求封装统一接口，未来可切换：

- OpenAI
- Ollama
- Claude API
- Gemini

配置：

yaml

日志：

logging

异步：

推荐 asyncio。

------

# 软件架构

请采用模块化设计。

例如：

```
app/

    ui/

    downloader/

    audio/

    stt/

    llm/

    summarizer/

    models/

    config/

    utils/

    cache/

    tests/
```

要求：

各模块之间低耦合。

任何模型均通过接口调用。

不要把业务逻辑写在 GUI 中。

GUI 仅负责：

- 接收输入
- 显示进度
- 展示结果

------

# 下载模块

封装 Downloader。

要求：

download(url)

返回：

```
VideoInfo

video_path

title

duration

author
```

支持：

Youtube

Bilibili

预留接口方便增加更多网站。

------

# 音频模块

封装 AudioProcessor。

提供：

extract_audio()

convert_to_wav()

统一输出：

```
16kHz

mono

wav
```

内部调用 ffmpeg。

------

# STT 模块

定义：

SpeechRecognizer 抽象类。

例如：

```
transcribe(audio_path)

↓

Transcript
```

Transcript 包含：

```
text

language

segments

start_time

end_time
```

实现：

WhisperRecognizer。

以后方便新增：

FunASRRecognizer。

------

# Transcript

支持：

保存 txt

保存 json

保存 srt

------

# Chunk 模块

长视频必须支持自动切块。

要求：

按照 token 数切分。

不要按字符切分。

保留时间轴。

例如：

Chunk

start_time

end_time

text

------

# LLM 模块

封装：

LLMClient

统一：

generate()

支持：

OpenAI

未来：

Claude

Gemini

Ollama

无需修改业务代码。

------

# Summarizer

采用 Map-Reduce。

流程：

Chunk

↓

多个 Chunk Summary

↓

Final Summary

Prompt 可配置。

支持：

中文总结

英文总结

双语总结

------

# GUI

PySide6。

页面包括：

URL 输入框

开始按钮

取消按钮

日志窗口

下载进度

STT 进度

LLM 进度

结果窗口

导出 Markdown

导出 txt

导出 json

------

# 缓存

同一个 URL：

不要重复下载。

同一个 Transcript：

不要重复 STT。

缓存目录：

cache/

------

# 配置

使用：

config.yaml

包括：

ffmpeg 路径

API Key

模型名称

输出目录

缓存目录

------

# 日志

日志分级：

INFO

WARNING

ERROR

保存：

logs/

GUI 可实时查看日志。

------

# 测试

每个模块必须提供：

单元测试。

Mock 外部 API。

最后提供：

集成测试。

要求：

Downloader

↓

STT

↓

LLM

↓

Summary

能够完整跑通。

------

# 打包

使用：

PyInstaller。

生成：

VideoSummary.exe

确保：

普通 Windows 用户无需安装 Python。

------

# 编码规范

必须遵循：

- 类型注解
- dataclass
- 清晰命名
- Google Style Docstring
- Ruff 可通过
- Black 格式化
- 避免重复代码

------

# 开发方式

不要一次生成整个项目。

请按照以下顺序开发：

第一阶段：

完成整体架构设计。

第二阶段：

实现 Downloader。

第三阶段：

实现 Audio。

第四阶段：

实现 STT。

第五阶段：

实现 Chunk。

第六阶段：

实现 LLM。

第七阶段：

实现 Summarizer。

第八阶段：

实现 GUI。

第九阶段：

完成测试。

第十阶段：

完成打包。

每完成一个阶段：

1. 说明设计思路。
2. 编写代码。
3. 编写单元测试。
4. 运行测试。
5. 修复发现的问题。
6. 更新 README。
7. 等待我确认后再进入下一阶段。

在整个过程中，请优先保证代码质量、可维护性和可扩展性，而不是追求一次性完成所有功能。