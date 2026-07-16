"""协作式取消令牌（线程安全）。"""

import threading

from app.core.errors import TaskCancelled


class CancellationToken:
    """包装 ``threading.Event`` 的取消令牌。

    主线程直接调用 ``cancel()``；工作线程/能力模块在可取消点调用
    ``raise_if_cancelled()``。
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """标记取消。可从任意线程调用，幂等。"""
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """已取消则抛 ``TaskCancelled``；能力模块在循环/回调中调用。"""
        if self._event.is_set():
            raise TaskCancelled()
