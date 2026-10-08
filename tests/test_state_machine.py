"""状态机单元测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from research_kit.states import (  # noqa: E402
    ACTIVE_STATES,
    TaskState,
    assert_transition,
    can_transition,
    is_active,
    is_terminal,
    map_provider_status,
)


class TestStateMachine(unittest.TestCase):
    def test_legal_transitions(self):
        self.assertTrue(can_transition(TaskState.PENDING, TaskState.SUBMITTED))
        self.assertTrue(can_transition(TaskState.SUBMITTED, TaskState.QUEUED))
        self.assertTrue(can_transition(TaskState.QUEUED, TaskState.RUNNING))
        self.assertTrue(can_transition(TaskState.RUNNING, TaskState.COMPLETED))
        self.assertTrue(can_transition(TaskState.COMPLETED, TaskState.DOWNLOADING))
        self.assertTrue(can_transition(TaskState.DOWNLOADING, TaskState.DOWNLOADED))
        self.assertTrue(can_transition(TaskState.FAILED, TaskState.RETRY))
        self.assertTrue(can_transition(TaskState.RETRY, TaskState.SUBMITTED))

    def test_illegal_transitions(self):
        self.assertFalse(can_transition(TaskState.DOWNLOADED, TaskState.PENDING))
        self.assertFalse(can_transition(TaskState.PENDING, TaskState.DOWNLOADED))
        self.assertFalse(can_transition(TaskState.RUNNING, TaskState.SUBMITTED))
        with self.assertRaises(Exception):
            assert_transition(TaskState.DOWNLOADED, TaskState.RUNNING)

    def test_provider_mapping(self):
        self.assertEqual(map_provider_status("accepted"), TaskState.SUBMITTED)
        self.assertEqual(map_provider_status("queued"), TaskState.QUEUED)
        self.assertEqual(map_provider_status("running"), TaskState.RUNNING)
        self.assertEqual(map_provider_status("successful"), TaskState.COMPLETED)
        self.assertEqual(map_provider_status("rejected"), TaskState.FAILED)
        # 瞬时查询失败不应误判
        self.assertIsNone(map_provider_status("lookup_error"))
        self.assertIsNone(map_provider_status("unknown"))

    def test_active_and_terminal(self):
        self.assertTrue(is_active(TaskState.SUBMITTED))
        self.assertTrue(is_active(TaskState.QUEUED))
        self.assertTrue(is_active(TaskState.RUNNING))
        self.assertFalse(is_active(TaskState.COMPLETED))
        self.assertFalse(is_active(TaskState.PENDING))
        self.assertTrue(is_terminal(TaskState.DOWNLOADED))
        self.assertEqual(ACTIVE_STATES, {TaskState.SUBMITTED, TaskState.QUEUED, TaskState.RUNNING})


if __name__ == "__main__":
    unittest.main()
