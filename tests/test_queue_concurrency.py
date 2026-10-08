"""并发调度单元测试：首次 3 个，任意时刻最多 5 个。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from research_kit.queue import (  # noqa: E402
    is_initial_phase,
    mark_initial_batch,
    plan_submissions,
    queue_status,
)
from research_kit.settings import load_settings  # noqa: E402
from research_kit.states import TaskState  # noqa: E402
from research_kit.store import Task, TaskStore  # noqa: E402


def _task(index: int) -> Task:
    return Task(task_id=f"t{index:02d}", kind="era5_single", label=f"task {index}", year=2022, month=6)


class TestQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = load_settings(workspace_root=self.tmp.name)
        self.store = TaskStore(Path(self.tmp.name) / "requests.json")
        self.store.add_many(_task(i) for i in range(10))

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_batch_is_three(self):
        plan = plan_submissions(self.store, self.settings)
        self.assertEqual(len(plan.to_submit), 3, "首次提交必须最多 3 个")
        self.assertTrue(is_initial_phase(self.store))

    def test_after_initial_limit_is_five(self):
        # 首次提交 3 个
        for task in plan_submissions(self.store, self.settings).to_submit:
            task.set_state(TaskState.SUBMITTED, note="test")
            self.store.update(task)
        mark_initial_batch(self.store)
        # 已 3 个进行中 -> 只能再补 2 个到 5
        plan = plan_submissions(self.store, self.settings)
        self.assertEqual(len(plan.to_submit), 2)
        self.assertEqual(plan.active, 3)

    def test_never_exceeds_max(self):
        mark_initial_batch(self.store)
        # 手动把 5 个置为进行中
        for task in self.store.tasks[:5]:
            task.set_state(TaskState.RUNNING, note="test")
            self.store.update(task)
        plan = plan_submissions(self.store, self.settings)
        self.assertEqual(len(plan.to_submit), 0, "并发已满时不得再提交")
        self.assertEqual(self.store.active_count(), 5)

    def test_free_slot_after_download(self):
        mark_initial_batch(self.store)
        for task in self.store.tasks[:5]:
            task.set_state(TaskState.RUNNING, note="test")
            self.store.update(task)
        # 两个下载完成 -> 释放 2 个名额
        for task in self.store.tasks[:2]:
            task.set_state(TaskState.COMPLETED, note="test")
            task.set_state(TaskState.DOWNLOADING, note="test")
            task.set_state(TaskState.DOWNLOADED, note="test")
            self.store.update(task)
        plan = plan_submissions(self.store, self.settings)
        self.assertEqual(len(plan.to_submit), 2)

    def test_queue_status_fields(self):
        status = queue_status(self.store, self.settings)
        self.assertEqual(status["max_concurrent"], 5)
        self.assertEqual(status["initial_submit"], 3)
        self.assertEqual(status["phase"], "initial")


if __name__ == "__main__":
    unittest.main()
