"""异常层次：所有业务异常携带面向用户的提示文案。"""


class VideoSummaryError(Exception):
    """业务异常基类。

    Attributes:
        user_message: 面向用户的提示（GUI 弹窗/任务列表显示）；
            技术细节保留在 ``str(exception)`` 并写入日志。
    """

    default_user_message = "发生错误，详情见日志。"

    def __init__(self, message: str, *, user_message: str | None = None) -> None:
        super().__init__(message)
        self.user_message = user_message or self.default_user_message


class ConfigError(VideoSummaryError):
    """配置缺失、解析失败或校验不通过。"""

    default_user_message = "配置有误，请检查设置。"


class DownloadError(VideoSummaryError):
    """视频/字幕/音频下载失败。"""

    default_user_message = "下载失败，请检查网络或代理设置。"


class SubtitleError(VideoSummaryError):
    """字幕解析失败。"""

    default_user_message = "字幕解析失败。"


class AudioError(VideoSummaryError):
    """ffmpeg 缺失、磁盘不足或转换失败。"""

    default_user_message = "音频处理失败，请检查 ffmpeg 是否可用。"


class SttError(VideoSummaryError):
    """语音识别或模型下载失败。"""

    default_user_message = "语音识别失败。"


class LlmError(VideoSummaryError):
    """LLM 接口调用失败。"""

    default_user_message = "调用大模型接口失败，请检查 API 设置。"


class ExportError(VideoSummaryError):
    """结果导出失败。"""

    default_user_message = "导出文件失败。"


class TaskCancelled(Exception):
    """任务被用户取消；属于控制流信号，不继承 VideoSummaryError。"""
