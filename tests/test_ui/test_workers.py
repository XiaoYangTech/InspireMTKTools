"""后台任务执行器测试（进度 / 完成 / 错误 / 取消）。"""

from __future__ import annotations

import time

import pytest

from logobuilder.ui.workers import TaskCancelled, TaskRunner


def _wait_until(root, predicate, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update_idletasks()
        root.update()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_runner_done(tk_root):
    runner = TaskRunner(tk_root)
    results = []
    assert runner.run(lambda ctx: 21 * 2, on_done=results.append)
    assert _wait_until(tk_root, lambda: not runner.busy and results)
    assert results == [42]
    assert runner.last_outcome == "done"
    assert runner.last_result == 42


def test_runner_error_is_reported_not_raised(tk_root):
    runner = TaskRunner(tk_root)
    errors = []

    def boom(ctx):
        raise ValueError("模拟错误")

    assert runner.run(boom, on_error=errors.append)
    assert _wait_until(tk_root, lambda: not runner.busy and errors)
    assert runner.last_outcome == "error"
    assert "模拟错误" in errors[0]["message"]
    assert "ValueError" in errors[0]["traceback"]


def test_runner_progress_events(tk_root):
    runner = TaskRunner(tk_root)
    progress = []

    def work(ctx):
        for index in range(4):
            ctx.progress(index / 4, f"step {index}")
        return "ok"

    runner.run(work, on_done=lambda _r: None, on_progress=lambda fraction, message: progress.append((fraction, message)))
    assert _wait_until(tk_root, lambda: not runner.busy and len(progress) >= 4)
    assert progress[0][0] == pytest.approx(0.0)
    assert progress[-1][0] == pytest.approx(0.75)


def test_runner_cancel(tk_root):
    runner = TaskRunner(tk_root)
    events = []

    def slow(ctx):
        for _ in range(500):
            ctx.check_cancel()
            time.sleep(0.005)
        return "finished"

    runner.run(slow, on_cancelled=lambda: events.append("cancelled"), on_done=lambda _r: events.append("done"))
    time.sleep(0.05)
    runner.cancel()
    assert _wait_until(tk_root, lambda: not runner.busy and events)
    assert events == ["cancelled"]
    assert runner.last_outcome == "cancelled"


def test_runner_rejects_concurrent_task(tk_root):
    runner = TaskRunner(tk_root)

    def slow(ctx):
        time.sleep(0.2)
        return 1

    assert runner.run(slow)
    assert runner.run(lambda ctx: 2) is False  # 已有任务在跑
    assert _wait_until(tk_root, lambda: not runner.busy)


def test_task_context_check_cancel_raises():
    from logobuilder.ui.workers import TaskContext

    context = TaskContext()
    context.check_cancel()
    context._cancel.set()
    with pytest.raises(TaskCancelled):
        context.check_cancel()
