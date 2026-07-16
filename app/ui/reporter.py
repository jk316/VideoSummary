"""SignalReporter：把 core 的 ProgressEvent 桥接为 Qt 信号（跨线程自动 QueuedConnection）。"""

from PySide6.QtCore import QObject, Signal

from app.core.events import ProgressEvent


class SignalReporter(QObject):
    """实现 ProgressReporter 协议（duck-typed），将事件转为 Qt 信号。

    在 QueueWorker 线程中调用 ``report()``，Qt 自动使用
    QueuedConnection 将信号投递到主线程的事件循环。
    Qt 的 Shiboken 元类与 Protocol 的 ABCMeta 无法共存，
    因此不显式继承 Protocol，通过结构类型匹配。
    """

    progress = Signal(ProgressEvent)
    log_message = Signal(str)

    def report(self, event: ProgressEvent) -> None:
        self.progress.emit(event)
        self.log_message.emit(event.message)
