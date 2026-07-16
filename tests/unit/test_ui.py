"""UI 组件单元测试：SignalReporter 信号桥接、MainWindow 构造。"""

# 确保 QApplication 存在（pytest-qt 提供 qapp fixture）


class TestSignalReporter:
    def test_progress_signal_emitted(self, qapp) -> None:
        from app.core.events import ProgressEvent, Stage
        from app.ui.reporter import SignalReporter

        reporter = SignalReporter()
        events: list[ProgressEvent] = []
        reporter.progress.connect(lambda e: events.append(e))
        event = ProgressEvent("test123", Stage.CHUNK, 0.5, "正在切块")
        reporter.report(event)
        assert len(events) == 1
        assert events[0].stage == Stage.CHUNK

    def test_log_message_also_emitted(self, qapp) -> None:
        from app.core.events import ProgressEvent, Stage
        from app.ui.reporter import SignalReporter

        reporter = SignalReporter()
        messages: list[str] = []
        reporter.log_message.connect(lambda m: messages.append(m))
        reporter.report(ProgressEvent("t1", Stage.RESOLVE_INFO, None, "hello"))
        assert "hello" in messages


class TestLogView:
    def test_append_and_scroll(self, qapp) -> None:
        from app.ui.log_view import LogView

        view = LogView()
        view.append_message("line 1")
        view.append_message("line 2")
        assert "line 1" in view.toPlainText()
        assert "line 2" in view.toPlainText()


class TestTaskList:
    def test_add_and_set_status(self, qapp) -> None:
        from app.ui.task_list import TaskListView

        view = TaskListView()
        view.add_task("v1", "测试视频")
        view.set_status("v1", "running", "下载中…")
        assert view.topLevelItemCount() == 1


class TestResultView:
    def test_set_markdown(self, qapp) -> None:
        from app.ui.result_view import ResultView

        view = ResultView()
        view.set_markdown("## 标题\n内容")
        assert "标题" in view._browser.toPlainText()
