"""subproc 单元测试：用真实 Python 子进程验证捕获/流式/取消/超时。"""

import sys

import pytest

from app.utils.subproc import (
    SubprocessCancelled,
    SubprocessTimeout,
    run_capture,
    run_streaming,
)

PY = sys.executable
SLEEP_SCRIPT = "import time; time.sleep(30)"


class TestRunCapture:
    def test_captures_stdout_and_returncode(self) -> None:
        result = run_capture([PY, "-c", "print('hello'); import sys; sys.exit(3)"])
        assert result.stdout.strip() == "hello"
        assert result.returncode == 3

    def test_captures_stderr_separately(self) -> None:
        result = run_capture([PY, "-c", "import sys; sys.stderr.write('boom')"])
        assert "boom" in result.stderr
        assert result.stdout == ""

    def test_timeout_kills_process(self) -> None:
        with pytest.raises(SubprocessTimeout):
            run_capture([PY, "-c", SLEEP_SCRIPT], timeout=0.5)

    def test_cancel_kills_process(self) -> None:
        with pytest.raises(SubprocessCancelled):
            run_capture([PY, "-c", SLEEP_SCRIPT], should_cancel=lambda: True)


class TestRunStreaming:
    def test_lines_delivered_in_order(self) -> None:
        script = "print('a'); print('b'); print('c')"
        lines: list[str] = []
        returncode = run_streaming([PY, "-u", "-c", script], on_line=lines.append)
        assert lines == ["a", "b", "c"]
        assert returncode == 0

    def test_stderr_merged_into_stream(self) -> None:
        script = "import sys; sys.stderr.write('err-line\\n')"
        lines: list[str] = []
        run_streaming([PY, "-u", "-c", script], on_line=lines.append)
        assert lines == ["err-line"]

    def test_idle_timeout_kills_silent_process(self) -> None:
        with pytest.raises(SubprocessTimeout):
            run_streaming([PY, "-c", SLEEP_SCRIPT], on_line=lambda _line: None, idle_timeout=0.5)

    def test_cancel_kills_process(self) -> None:
        with pytest.raises(SubprocessCancelled):
            run_streaming(
                [PY, "-c", SLEEP_SCRIPT], on_line=lambda _line: None, should_cancel=lambda: True
            )

    def test_nonzero_exit_returned(self) -> None:
        returncode = run_streaming(
            [PY, "-c", "import sys; sys.exit(7)"], on_line=lambda _line: None
        )
        assert returncode == 7
