"""subprocess 统一封装：窗口抑制、取消/超时看护、行流式输出。

utils 为最底层，不依赖 core——取消通过 ``should_cancel`` 回调注入，
调用方负责把 ``SubprocessCancelled`` 翻译为业务层的 ``TaskCancelled``。

所有子进程统一：``CREATE_NO_WINDOW``（windowed 模式防闪黑框）、
``stdin=DEVNULL``（防交互挂起）、utf-8 容错解码。
"""

import contextlib
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import IO

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_POLL_INTERVAL = 0.2
_KILL_WAIT_SECONDS = 5

ShouldCancel = Callable[[], bool]


class SubprocessCancelled(Exception):
    """``should_cancel`` 触发，进程已被终止。"""


class SubprocessTimeout(Exception):
    """总超时或无输出看护超时，进程已被终止。"""


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str


def run_capture(
    args: Sequence[str],
    *,
    timeout: float | None = None,
    should_cancel: ShouldCancel | None = None,
) -> RunResult:
    """运行并捕获全部输出；轮询期间响应取消与总超时。"""
    proc = _popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + timeout if timeout is not None else None
    while True:
        _check_guards(proc, args, deadline, should_cancel)
        try:
            stdout, stderr = proc.communicate(timeout=_POLL_INTERVAL)
        except subprocess.TimeoutExpired:
            continue
        return RunResult(returncode=proc.returncode, stdout=stdout, stderr=stderr)


def run_streaming(
    args: Sequence[str],
    *,
    on_line: Callable[[str], None],
    idle_timeout: float | None = None,
    should_cancel: ShouldCancel | None = None,
) -> int:
    """运行并逐行回调输出（stderr 合并入 stdout）。

    Args:
        on_line: 每行输出的回调（已去除行尾换行）。
        idle_timeout: 连续无输出看护——超过该秒数无任何输出则终止进程。

    Returns:
        进程退出码。
    """
    proc = _popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    lines: queue.Queue[str | None] = queue.Queue()
    assert proc.stdout is not None
    threading.Thread(target=_pump_lines, args=(proc.stdout, lines), daemon=True).start()
    last_output = time.monotonic()
    while True:
        if should_cancel is not None and should_cancel():
            _kill(proc)
            raise SubprocessCancelled(f"已取消: {args[0]}")
        if idle_timeout is not None and time.monotonic() - last_output > idle_timeout:
            _kill(proc)
            raise SubprocessTimeout(f"进程超过 {idle_timeout}s 无输出: {args[0]}")
        try:
            line = lines.get(timeout=_POLL_INTERVAL)
        except queue.Empty:
            continue
        if line is None:  # EOF
            break
        last_output = time.monotonic()
        on_line(line.rstrip("\r\n"))
    return proc.wait()


def _pump_lines(stream: IO[str], lines: "queue.Queue[str | None]") -> None:
    try:
        for line in stream:
            lines.put(line)
    finally:
        lines.put(None)


def _popen(args: Sequence[str], **kwargs: object) -> subprocess.Popen[str]:
    return subprocess.Popen(  # type: ignore[call-overload]
        list(args),
        stdin=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
        text=True,
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )


def _check_guards(
    proc: subprocess.Popen[str],
    args: Sequence[str],
    deadline: float | None,
    should_cancel: ShouldCancel | None,
) -> None:
    if should_cancel is not None and should_cancel():
        _kill(proc)
        raise SubprocessCancelled(f"已取消: {args[0]}")
    if deadline is not None and time.monotonic() > deadline:
        _kill(proc)
        raise SubprocessTimeout(f"进程超时: {args[0]}")


def _kill(proc: subprocess.Popen[str]) -> None:
    with contextlib.suppress(OSError):
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=_KILL_WAIT_SECONDS)
