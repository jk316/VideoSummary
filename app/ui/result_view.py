"""结果窗：Markdown 渲染预览 + 一键复制。"""

from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class ResultView(QWidget):
    """Markdown 渲染结果窗（用 QTextBrowser 内置 Markdown 支持，不引入 WebEngine）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(True)

        btn_copy = QPushButton("复制到剪贴板")
        btn_copy.clicked.connect(self._copy)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._browser)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btn_copy)
        layout.addLayout(btn_row)

    def set_markdown(self, text: str) -> None:
        self._browser.setMarkdown(text)

    def clear_result(self) -> None:
        self._browser.clear()

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self._browser.toPlainText())
