"""端到端测试：模拟完整生命周期（不使用真实 API）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from research_kit.orchestrator import Orchestrator  # noqa: E402
from research_kit.settings import Region, load_settings  # noqa: E402
from research_kit.simulate import write_simulated_station  # noqa: E402
from research_kit.states import TaskState  # noqa: E402


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.station = self.workspace / "data" / "Ngari_data" / "ngari_pm1.csv"
        write_simulated_station(self.station, "2022-06-01", "2022-06-03", lag_days=1)
        self.settings = load_settings(workspace_root=self.workspace)
        self.orch = Orchestrator(self.settings, mode="simulate", station_path=self.station)

    def tearDown(self):
        self.tmp.cleanup()

    def _init(self):
        return self.orch.initialize(
            title="端到端测试任务",
            start="2022-06-01",
            end="2022-06-03",
            region=Region(north=40, south=20, west=60, east=90),
            station={"name": "Ngari / Ali", "latitude": 33.39, "longitude": 79.70},
            research_question="研究火点活动与 PM1 的关系及输送气象条件。",
            station_variables=["PM1"],
        )

    def test_full_lifecycle(self):
        self._init()
        # 任务数：1 单层 + 4 气压层 + 1 FIRMS = 6
        self.assertEqual(len(self.orch.store.tasks), 6)

        # 第一次循环只提交 3 个
        first = self.orch.cycle()
        self.assertEqual(len(first["submitted"]), 3, "首次提交必须为 3 个")
        self.assertLessEqual(self.orch.store.active_count(), 5)

        # 跑完剩余循环
        self.orch.run_until_complete(max_cycles=20)
        self.assertTrue(self.orch.store.all_downloaded(), "所有任务应下载完成")
        for task in self.orch.store.tasks:
            self.assertEqual(task.state, TaskState.DOWNLOADED)

        # 质检
        quality = self.orch.data_check()
        self.assertEqual(quality["summary"]["fail"], 0)
        self.assertTrue(quality["summary"]["all_ok"], f"质检应全部通过：{quality['summary']['issues']}")

        # 整理
        self.orch.prepare()
        self.assertIn("fire", self.orch.prepared.capabilities)
        self.assertIn("station", self.orch.prepared.capabilities)

        # 推荐 + 绘图 + 自动读图
        recommendation = self.orch.recommend()
        self.assertTrue(recommendation["options"], "应有可用方案")
        figures = self.orch.render_figures([o["id"] for o in recommendation["recommended"][:3]],
                                           recommendation["options"])
        self.assertTrue(figures)
        for figure in figures:
            self.assertTrue(Path(figure.png).exists(), f"图片应存在：{figure.png}")
            self.assertEqual(figure.status, "ok", f"自动读图应通过：{figure.review_notes}")

        # 报告
        report = self.orch.build_report(figures, quality)
        self.assertTrue(Path(report).exists())
        text = Path(report).read_text(encoding="utf-8")
        self.assertIn("数据质量检查", text)
        self.assertIn("不确定性", text)

    def test_resume_does_not_duplicate(self):
        """断点续传：重复初始化不应产生重复任务。"""
        self._init()
        self.orch.cycle()
        before = len(self.orch.store.tasks)
        self._init()  # 再次初始化
        self.assertEqual(len(self.orch.store.tasks), before, "不应重复创建任务")

    def test_last_task_is_downloaded(self):
        """回归：最后一个任务在本周期内变为 COMPLETED 时，循环不得提前退出。

        曾经的问题：run_until_complete 的退出条件只看 active/pending，
        忽略 ready_to_download，导致单任务研究停在 COMPLETED、从未下载。
        """
        self.orch.initialize(
            title="单任务回归测试",
            start="2022-06-01",
            end="2022-06-03",
            region=Region(north=40, south=20, west=60, east=90),
            research_question="火点活动",  # 仅触发 FIRMS 一个任务
            station_variables=["PM1"],
        )
        self.assertEqual(len(self.orch.store.tasks), 1)
        self.orch.run_until_complete(max_cycles=10)
        self.assertTrue(self.orch.store.all_downloaded(), "单任务也必须完成下载")
        self.assertEqual(self.orch.store.tasks[0].state, TaskState.DOWNLOADED)
        quality = self.orch.data_check()
        self.assertEqual(quality["summary"]["fail"], 0, "单任务场景质检应通过")


if __name__ == "__main__":
    unittest.main()
