"""QueueWorker：QThread 中运行 TaskQueue.run_all()，通过 SignalReporter 与 UI 通信。"""

import logging
import queue

from PySide6.QtCore import QThread, Signal

from app.core.models import VideoRef
from app.core.task_queue import TaskQueue

logger = logging.getLogger(__name__)


class QueueWorker(QThread):
    """串行执行任务队列的工作线程。

    主线程通过线程安全的 ``queue.Queue`` 提交 URL 列表；
    取消通过直接调用 ``cancel_current()`` / ``cancel_all()``
    （底层 ``CancellationToken`` 使用 ``threading.Event``，线程安全）。
    """

    task_started = Signal(str, str)  # video_id, title
    task_finished = Signal(str, object, object)  # video_id, TaskResult | None, error | None
    queue_empty = Signal()

    _SUBMIT_SENTINEL = object()

    def __init__(self, task_queue: TaskQueue, reporter) -> None:
        super().__init__()
        self._task_queue = task_queue
        self._reporter = reporter
        self._submit_queue: queue.Queue = queue.Queue()

    def submit(self, refs: list[VideoRef]) -> None:
        """线程安全：从主线程调用的提交入口。"""
        self._submit_queue.put(refs)

    def stop(self) -> None:
        """线程安全：请求线程退出。"""
        self._submit_queue.put(self._SUBMIT_SENTINEL)

    def run(self) -> None:
        """QThread 主循环（在工作线程运行，阻塞直到 stop）。"""
        while True:
            item = self._submit_queue.get()
            if item is self._SUBMIT_SENTINEL:
                break
            self._task_queue.enqueue(item)

            def on_done(ref, result, error):
                self.task_finished.emit(ref.video_id, result, error)

            self._task_queue.run_all(self._reporter, on_done)
            self.queue_empty.emit()
