from __future__ import annotations

import time
import unittest

from backend.library import tasks


def _quick_job(update, is_cancelled):
    return {"ok": True}


def _blocking_job(update, is_cancelled):
    while not is_cancelled():
        time.sleep(0.05)
    return {"ok": "cancelled"}


class TaskLimitsTests(unittest.TestCase):
    def test_max_active_rejects_overflow(self):
        task_id = tasks.start_job(_blocking_job, max_active=1)
        self.assertIsNotNone(task_id)
        with self.assertRaises(RuntimeError) as ctx:
            tasks.start_job(_blocking_job, max_active=1)
        self.assertIn("上限", str(ctx.exception))
        tasks.cancel(task_id)
        deadline = time.time() + 3
        while tasks.active_count() > 0 and time.time() < deadline:
            time.sleep(0.05)
        # can start a new one after release
        task2 = tasks.start_job(_quick_job, max_active=1)
        self.assertIsNotNone(task2)
        deadline = time.time() + 3
        while tasks.get(task2)["status"] == "running" and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(tasks.get(task2)["status"], "done")

    def test_timeout_kills_hung_task(self):
        task_id = tasks.start_job(_blocking_job, timeout=0.3)
        deadline = time.time() + 5
        while time.time() < deadline:
            state = tasks.get(task_id)
            if state["status"] == "error":
                break
            time.sleep(0.1)
        self.assertEqual(state["status"], "error")
        self.assertIn("超时", state["error"])

    def test_active_count_goes_back_to_zero(self):
        tasks.start_job(_quick_job, max_active=2)
        deadline = time.time() + 3
        while tasks.active_count() > 0 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(tasks.active_count(), 0)

    def test_timeout_not_overwritten_by_late_success(self):
        # A Python thread cannot be killed: a job may keep running past the watchdog and then
        # return normally. The failure must stay visible instead of flipping back to "done".
        def _ignores_cancel_and_returns_late(update, is_cancelled):
            time.sleep(0.7)
            return {"ok": True}

        task_id = tasks.start_job(_ignores_cancel_and_returns_late, timeout=0.2)
        deadline = time.time() + 5
        while time.time() < deadline:
            state = tasks.get(task_id)
            if state["status"] == "error":
                break
            time.sleep(0.05)
        self.assertEqual(state["status"], "error")
        self.assertIn("超时", state["error"])
        time.sleep(0.8)  # let the late job return
        state = tasks.get(task_id)
        self.assertEqual(state["status"], "error")


if __name__ == "__main__":
    unittest.main()
