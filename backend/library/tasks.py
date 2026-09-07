from __future__ import annotations

import secrets
import threading
from typing import Any, Callable


_tasks: dict[str, dict] = {}
_lock = threading.Lock()

# Active-task counter (guards against runaway concurrency from tests/errors blowing the LLM budget)
_active: int = 0
_active_cond = threading.Condition(_lock)

# A background task: receives (update progress callback, is_cancelled cancellation check), returns the final result dict.
Job = Callable[[Callable[[dict], None], Callable[[], bool]], dict]


def active_count() -> int:
    with _lock:
        return _active


def start_job(job: Job, *, max_active: int | None = None, timeout: float | None = None,
              on_started: Callable[[str], None] | None = None,
              on_finished: Callable[[dict], None] | None = None) -> str:
    """Start a background task.

    max_active: concurrency cap; exceeding it raises RuntimeError (the caller turns it into 429);
    timeout: overall task timeout (seconds); on expiry the task is force-marked as error (prevents a single task from hanging; server-side safety net).
    """
    global _active
    if max_active is not None:
        with _active_cond:
            if _active >= max_active:
                raise RuntimeError(f"已有 {_active} 个任务在运行（上限 {max_active}），请稍后再试")
            _active += 1

    task_id = secrets.token_hex(8)
    state: dict[str, Any] = {
        "task_id": task_id,
        "status": "running",
        "current": "",
        "cancel": False,
    }
    with _lock:
        _tasks[task_id] = state
    if on_started:
        on_started(task_id)

    def notify_finished() -> None:
        if on_finished:
            with _lock:
                snapshot = dict(state)
            on_finished(snapshot)

    def update(snapshot: dict) -> None:
        with _lock:
            for key, value in snapshot.items():
                state[key] = value

    def is_cancelled() -> bool:
        with _lock:
            return state["cancel"]

    def run() -> None:
        try:
            result = job(update, is_cancelled)
            with _lock:
                if state["status"] == "error":
                    # The watchdog already failed this task (timeout). A Python thread cannot be
                    # killed, so the job may still return afterwards; keep the failure visible
                    # instead of letting a late success overwrite it with "done".
                    for key, value in (result or {}).items():
                        if key not in ("status", "error", "cancel"):
                            state[key] = value
                else:
                    state["status"] = "done"
                    for key, value in (result or {}).items():
                        state[key] = value
        except Exception as exc:  # keep the console usable while surfacing the failure
            with _lock:
                state["status"] = "error"
                state["error"] = str(exc)
        finally:
            if max_active is not None:
                with _active_cond:
                    global _active
                    _active = max(_active - 1, 0)
            notify_finished()

    def _timeout_kill() -> None:
        with _lock:
            if state["status"] == "running":
                state["status"] = "error"
                state["cancel"] = True
                state["error"] = f"任务执行超时（>{timeout:.0f}s），已请求停止，请检查逐篇完成状态"
        notify_finished()

    threading.Thread(target=run, daemon=True).start()
    if timeout is not None:
        timer = threading.Timer(timeout, _timeout_kill)
        timer.daemon = True
        timer.start()
    return task_id


def get(task_id: str) -> dict | None:
    with _lock:
        state = _tasks.get(task_id)
        return dict(state) if state else None


def cancel(task_id: str) -> None:
    with _lock:
        if task_id in _tasks:
            _tasks[task_id]["cancel"] = True
