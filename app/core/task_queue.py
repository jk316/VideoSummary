"""任务队列：播放列表展开后逐个串行执行，单项失败不中断，支持取消。

从 QueueWorker(QThread) 的主循环调用 ``run_all()``；
提交走 ``queue.Queue``，取消走 ``CancellationToken``（均线程安全）。
"""

import asyncio
import logging
from collections.abc import Callable

from app.core.cancellation import CancellationToken
from app.core.errors import TaskCancelled, VideoSummaryError
from app.core.events import ProgressReporter
from app.core.models import TaskResult, VideoRef
from app.core.pipeline import SummaryPipeline

logger = logging.getLogger(__name__)

TaskDoneCallback = Callable[[VideoRef, TaskResult | None, Exception | None], None]
"""单个任务完成/失败时的回调：``(ref, result_or_none, error_or_none)``。"""


class TaskQueue:
    """串行任务调度器。"""

    def __init__(self, pipeline: SummaryPipeline) -> None:
        self._pipeline = pipeline
        self._refs: list[VideoRef] = []
        self._current_cancel: CancellationToken | None = None

    def enqueue(self, refs: list[VideoRef]) -> None:
        self._refs = refs

    def run_all(self, reporter: ProgressReporter, on_done: TaskDoneCallback) -> None:
        """逐个执行队列中的任务（串行阻塞，在工作线程调用）。

        单任务取消 → 继续下一任务；单任务失败 → 记录后继续。
        ``enqueue()`` 应在调用本方法前完成。
        """
        while self._refs:
            ref = self._refs.pop(0)
            cancel = CancellationToken()
            self._current_cancel = cancel
            try:
                result = self._pipeline.run(ref, reporter, cancel)
                on_done(ref, result, None)
            except (TaskCancelled, asyncio.CancelledError):
                on_done(ref, None, TaskCancelled("任务已取消"))
            except VideoSummaryError as exc:
                logger.error("任务失败 %s: %s", ref.video_id, exc)
                on_done(ref, None, exc)
            except Exception as exc:
                logger.exception("任务异常 %s", ref.video_id)
                on_done(ref, None, VideoSummaryError(str(exc)))
            finally:
                if self._current_cancel is cancel:
                    self._current_cancel = None

    def cancel_current(self) -> None:
        """跳过当前任务，继续执行队列中下一任务。"""
        if self._current_cancel is not None:
            self._current_cancel.cancel()

    def cancel_all(self) -> None:
        """取消当前任务并清空剩余队列。"""
        self._refs.clear()
        self.cancel_current()

    @property
    def pending_count(self) -> int:
        return len(self._refs)
