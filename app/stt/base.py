"""语音识别抽象接口。"""

from abc import ABC, abstractmethod
from pathlib import Path

from app.core.cancellation import CancellationToken
from app.core.events import ProgressFn
from app.core.models import Transcript


class SpeechRecognizer(ABC):
    """音频 → Transcript 的统一接口（faster-whisper / FunASR / API 等实现）。"""

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
        progress: ProgressFn,
        cancel: CancellationToken,
    ) -> Transcript:
        """转写 16kHz/mono/wav 音频。

        Args:
            language: 指定语言代码；None 表示自动检测。
            progress: 进度回调（按已转写时长/总时长换算）。
            cancel: 取消令牌，逐段检查。

        Raises:
            SttError: 组件缺失、模型加载失败或识别失败。
            TaskCancelled: 用户取消。
        """
