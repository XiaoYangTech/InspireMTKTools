"""后台任务执行器：在工作线程里跑耗时操作，主线程通过 ``after`` 轮询回传结果。

为什么需要它（修复原版 B1/B8）：
* 解析 / 打包大镜像时界面不能卡死（原版同步执行，窗口“假死”）；
* 长任务要有进度提示和取消按钮；
* 任何异常都必须回到主线程，由界面弹出可读错误（绝不吞掉、不让进程崩溃）。

约定：``work`` 函数在 **工作线程** 中执行，只允许做计算/读文件，不能碰 Tk 控件；
需要更新界面的内容通过 ``ctx.progress()/ctx.log()`` 投递，或在 ``on_done`` 回调里处理。
"""

from __future__ import annotations

import queue
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable


class TaskCancelled(Exception):
    """任务被用户取消。"""


@dataclass
class TaskContext:
    """传给工作函数的上下文：进度、日志、取消检查。"""

    title: str = ""
    _cancel: threading.Event = field(default_factory=threading.Event)
    _queue: "queue.Queue[tuple[str, tuple[Any, ...]]]" = field(
        default_factory=lambda: queue.Queue()
    )
    _fraction: float | None = None
    _message: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ── 工作线程可调用 ────────────────────────────────────
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check_cancel(self) -> None:
        """在耗时循环里调用；用户取消时抛 :class:`TaskCancelled`。"""
        if self._cancel.is_set():
            raise TaskCancelled()

    def progress(self, fraction: float | None, message: str = "") -> None:
        """``fraction`` 为 0..1 时显示确定进度；``None`` 表示不确定进度（脉冲）。"""
        with self._lock:
            self._fraction = None if fraction is None else max(0.0, min(1.0, float(fraction)))
            if message:
                self._message = message
        self._queue.put(("progress", (self._fraction, message)))

    def log(self, level: str, message: str) -> None:
        self._queue.put(("log", (level, message)))

    # ── 主线程读取 ────────────────────────────────────────
    @property
    def fraction(self) -> float | None:
        with self._lock:
            return self._fraction

    @property
    def message(self) -> str:
        with self._lock:
            return self._message

    def drain(self) -> list[tuple[str, tuple[Any, ...]]]:
        events: list[tuple[str, tuple[Any, ...]]] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events


class TaskRunner:
    """单任务执行器（同一时间只允许一个任务，防止重复点击）。"""

    POLL_MS = 60

    def __init__(self, root: Any) -> None:
        self.root = root
        self._thread: threading.Thread | None = None
        self._ctx: TaskContext | None = None
        self._poll_id: str | None = None
        self._finished = False
        self._gen = 0
        self.title = ""
        self.last_error: dict[str, Any] | None = None
        self.last_result: Any = None
        # 最近一次完成类型：done / error / cancelled
        self.last_outcome: str | None = None

    # ── 状态 ─────────────────────────────────────────────
    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def context(self) -> TaskContext | None:
        return self._ctx

    # ── 执行 ─────────────────────────────────────────────
    def run(
        self,
        work: Callable[[TaskContext], Any],
        *,
        title: str = "",
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[dict[str, Any]], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
        on_progress: Callable[[float | None, str], None] | None = None,
        on_log: Callable[[str, str], None] | None = None,
    ) -> bool:
        """启动任务；已有任务在跑时返回 ``False``。"""
        if self.busy:
            return False
        self._gen += 1
        gen = self._gen
        ctx = TaskContext(title=title)
        self._ctx = ctx
        self._finished = False
        self.title = title
        self.last_error = None
        self.last_result = None
        self.last_outcome = None
        self._on_done = on_done
        self._on_error = on_error
        self._on_cancelled = on_cancelled
        self._on_progress = on_progress
        self._on_log = on_log

        def _worker() -> None:
            outcome: str = "done"
            result: Any = None
            error: dict[str, Any] | None = None
            try:
                result = work(ctx)
            except TaskCancelled:
                outcome = "cancelled"
            except BaseException as exc:  # noqa: BLE001 - 必须兜住所有异常
                outcome = "error"
                error = {
                    "message": str(exc) or type(exc).__name__,
                    "hint": getattr(exc, "hint", None),
                    "traceback": "".join(
                        traceback.format_exception(type(exc), exc, exc.__traceback__)
                    ),
                    "type": type(exc).__name__,
                }
            if outcome == "done" and ctx.cancelled():
                outcome = "cancelled"
            ctx._queue.put(("finished", (outcome, result, error)))

        thread = threading.Thread(target=_worker, name=f"logobuilder-task-{gen}", daemon=True)
        self._thread = thread
        thread.start()
        self._schedule_poll(gen)
        return True

    def cancel(self) -> None:
        """请求取消（协作式：工作函数需调用 ``ctx.check_cancel()``）。"""
        if self._ctx is not None:
            self._ctx._cancel.set()

    def wait(self, timeout: float | None = None) -> None:
        """等待线程结束（测试用；不要在 UI 主线程里长时间调用）。"""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # ── 内部轮询 ─────────────────────────────────────────
    def _schedule_poll(self, gen: int) -> None:
        if gen != self._gen:
            return
        try:
            self._poll_id = self.root.after(self.POLL_MS, lambda: self._poll(gen))
        except Exception:
            self._poll_id = None

    def _poll(self, gen: int) -> None:
        if gen != self._gen:
            return
        ctx = self._ctx
        if ctx is None:
            return
        for kind, payload in ctx.drain():
            if kind == "progress":
                fraction, message = payload
                callback = getattr(self, "_on_progress", None)
                if callback is not None:
                    self._safe_call(callback, fraction, message)
            elif kind == "log":
                callback = getattr(self, "_on_log", None)
                if callback is not None:
                    self._safe_call(callback, *payload)
            elif kind == "finished":
                outcome, result, error = payload
                self._finished = True
                self.last_outcome = outcome
                self.last_result = result
                self.last_error = error
                self._thread = None
                self._ctx = None
                if outcome == "done":
                    callback = getattr(self, "_on_done", None)
                    if callback is not None:
                        self._safe_call(callback, result)
                elif outcome == "error":
                    callback = getattr(self, "_on_error", None)
                    if callback is not None:
                        self._safe_call(callback, error or {})
                else:
                    callback = getattr(self, "_on_cancelled", None)
                    if callback is not None:
                        self._safe_call(callback)
                return
        self._schedule_poll(gen)

    @staticmethod
    def _safe_call(callback: Callable[..., Any], *args: Any) -> None:
        try:
            callback(*args)
        except Exception:
            # 回调里的异常不能再把主循环带崩：打印到 stderr 供排查
            traceback.print_exc()
