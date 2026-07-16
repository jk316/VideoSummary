"""进度与状态事件定义（core 共享内核，不依赖任何能力模块）。"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class Stage(StrEnum):
    """流水线阶段。"""

    RESOLVE_INFO = "resolve_info"
    GET_TRANSCRIPT = "get_transcript"
    CHUNK = "chunk"
    SUMMARIZE = "summarize"
    EXPORT = "export"


class TaskStatus(StrEnum):
    """任务生命周期状态。"""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ProgressEvent:
    """一次进度上报。

    Attributes:
        task_id: 任务标识。
        stage: 当前阶段。
        fraction: 0.0~1.0 的进度；None 表示不确定进度（spinner）。
        message: 面向用户的进度描述。
    """

    task_id: str
    stage: Stage
    fraction: float | None
    message: str


class ProgressReporter(Protocol):
    """core 层对外的进度上报协议；GUI/CLI/测试各自实现。"""

    def report(self, event: ProgressEvent) -> None: ...


ProgressFn = Callable[[float | None, str], None]
"""能力模块内部的轻量进度回调 (fraction, message)；由 Pipeline 包装为 ProgressEvent。"""
