"""任务列表视图：展示队列中各任务的状态与进度。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

_STATUS_MAP = {
    "pending": "⏳ 等待",
    "running": "🔄 进行中",
    "done": "✅ 完成",
    "failed": "❌ 失败",
    "cancelled": "🚫 已取消",
}


class TaskListView(QTreeWidget):
    """单列任务列表（视频标题 + 状态），支持右键取消。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderLabels(["任务"])
        self.setRootIsDecorated(False)
        self.header().setStretchLastSection(True)
        self._items: dict[str, QTreeWidgetItem] = {}

    def add_task(self, video_id: str, title: str) -> None:
        item = QTreeWidgetItem([f"{_STATUS_MAP['pending']}  {title}"])
        item.setData(0, Qt.UserRole, video_id)
        self.addTopLevelItem(item)
        self._items[video_id] = item

    def set_status(self, video_id: str, status: str, message: str = "") -> None:
        item = self._items.get(video_id)
        if item is None:
            return
        label = _STATUS_MAP.get(status, status)
        item.setText(0, f"{label}  {message}" if message else label)

    def clear_all(self) -> None:
        self.clear()
        self._items.clear()
