"""主窗口：URL 输入、任务列表、进度、日志、结果、设置入口。"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.errors import TaskCancelled, VideoSummaryError
from app.core.models import TaskResult
from app.ui.log_view import LogView
from app.ui.reporter import SignalReporter
from app.ui.result_view import ResultView
from app.ui.settings_dialog import SettingsDialog
from app.ui.task_list import TaskListView


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(
        self,
        config_path: Path,
        resolve_urls,
        get_config,
        reload_config,
        worker,
        reporter: SignalReporter,
    ) -> None:
        super().__init__()
        self.setWindowTitle("VideoSummary")
        self.resize(900, 650)
        self._config_path = config_path
        self._resolve_urls = resolve_urls
        self._get_config = get_config
        self._reload_config = reload_config
        self._worker = worker
        self._reporter = reporter

        # --- central widget ---
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # URL 输入栏
        url_bar = QHBoxLayout()
        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("输入 YouTube / Bilibili 视频或播放列表 URL…")
        self._url_input.returnPressed.connect(self._submit)
        self._btn_start = QPushButton("开始")
        self._btn_start.clicked.connect(self._submit)
        self._btn_cancel = QPushButton("取消当前")
        self._btn_cancel.clicked.connect(self._cancel_current)
        self._btn_cancel_all = QPushButton("取消全部")
        self._btn_cancel_all.clicked.connect(self._cancel_all)
        url_bar.addWidget(self._url_input)
        url_bar.addWidget(self._btn_start)
        url_bar.addWidget(self._btn_cancel)
        url_bar.addWidget(self._btn_cancel_all)
        root.addLayout(url_bar)

        # 主分割区：任务列表 | 结果/日志 tab
        splitter = QSplitter(Qt.Horizontal)

        self._task_list = TaskListView()
        splitter.addWidget(self._task_list)

        right = QTabWidget()
        self._result_view = ResultView()
        right.addTab(self._result_view, "结果")
        self._log_view = LogView()
        right.addTab(self._log_view, "日志")
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter)

        # 状态栏
        self._status = QStatusBar()
        self._status.showMessage("就绪")
        self.setStatusBar(self._status)

        # 连接信号
        self._reporter.progress.connect(self._on_progress)
        self._reporter.log_message.connect(self._log_view.append_message)
        worker.task_finished.connect(self._on_task_finished)
        worker.queue_empty.connect(lambda: self._status.showMessage("队列处理完成"))
        worker.finished.connect(lambda: None)

        # 设置菜单
        menu = self.menuBar().addMenu("设置")
        menu.addAction("配置…", self._open_settings)
        menu.addAction("检查 yt-dlp 更新", self._check_ytdlp_update)

        # 启动工作线程
        worker.start()

    # ------------------------------------------------------------ 操作

    def _submit(self) -> None:
        raw = self._url_input.text().strip()
        if not raw:
            return
        try:
            refs = self._resolve_urls(raw)
        except VideoSummaryError as exc:
            QMessageBox.warning(self, "链接无效", exc.user_message)
            return
        self._url_input.clear()
        for ref in refs:
            self._task_list.add_task(ref.video_id, ref.title or ref.video_id)
        self._worker.submit(refs)
        self._status.showMessage(f"已添加 {len(refs)} 个任务")

    def _cancel_current(self) -> None:
        self._worker._task_queue.cancel_current()
        self._status.showMessage("正在取消当前任务…")

    def _cancel_all(self) -> None:
        self._worker._task_queue.cancel_all()
        self._status.showMessage("已取消全部任务")

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._get_config(), self._config_path, self)
        if dlg.exec():
            self._reload_config()
            self._log_view.append_message("设置已保存")

    def _check_ytdlp_update(self) -> None:
        try:
            from app.config.schema import DownloaderConfig, NetworkConfig
            from app.downloader.ytdlp import YtDlpDownloader

            dl = YtDlpDownloader(Path("yt-dlp.exe"), DownloaderConfig(), NetworkConfig())
            result = dl.update_binary()
            QMessageBox.information(self, "yt-dlp 更新", result)
        except VideoSummaryError as exc:
            QMessageBox.warning(self, "更新失败", exc.user_message)

    # ---------------------------------------------------------------- 信号

    def _on_progress(self, event) -> None:
        task_id = event.task_id
        if task_id and event.stage:
            self._task_list.set_status(task_id, "running", event.message)

    def _on_task_finished(self, video_id, result, error) -> None:
        if error is not None:
            status = "cancelled" if isinstance(error, TaskCancelled) else "failed"
            msg = getattr(error, "user_message", str(error))
            self._task_list.set_status(video_id, status, msg)
            return
        self._task_list.set_status(video_id, "done", "完成")
        if isinstance(result, TaskResult) and result.summary:
            self._result_view.set_markdown(result.summary.markdown)
            self._status.showMessage(
                f"总结完成 | tokens: {result.summary.usage.total_tokens}"
                f" | 耗时: {result.summary.elapsed_seconds:.0f}s"
            )

    def closeEvent(self, event) -> None:
        self._worker.stop()
        self._worker.wait(3000)
        super().closeEvent(event)
