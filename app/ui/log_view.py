"""日志窗：实时追加日志行，有上限自动截断。"""

from collections import deque

from PySide6.QtWidgets import QPlainTextEdit

_MAX_LINES = 500


class LogView(QPlainTextEdit):
    """只读日志窗；仅追加、自动滚底、超上限截断。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(_MAX_LINES)
        self._lines: deque[str] = deque(maxlen=_MAX_LINES)

    def append_message(self, message: str) -> None:
        self._lines.append(message)
        self.setPlainText("\n".join(self._lines))
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())
