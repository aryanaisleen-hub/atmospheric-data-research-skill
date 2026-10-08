#!/usr/bin/env python
"""端到端模拟测试：完整跑通科研数据生命周期（不使用任何真实 API）。

模拟任务：
    "研究 2022 年 6 月 1–30 日、20–40°N、60–90°E 区域的火点活动及其与阿里站 PM1 的关系。"

流程：
    需求解析 → 数据需求判断 → MAP_KEY 检查 → 任务生成 → 首次提交 3 个 →
    模拟定时检查 → 模拟任务完成 → 下载 → 释放名额并补充提交 → 数据检查 →
    分析方案推荐 → 绘图 → 自动读图 → 逐图解释 → 最终报告
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from research_kit.orchestrator import Orchestrator  # noqa: E402
from research_kit.settings import Region, load_settings  # noqa: E402
from research_kit.simulate import write_simulated_station  # noqa: E402


def banner(text: str) -> None:
    print("\n" + "=" * 78)
    print(text)
    print("=" * 78)


def main() -> int:
    # 每次运行使用独立沙箱目录，避免删除任何已有文件。
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sandbox = SKILL_ROOT / "examples" / "_runs" / stamp
    sandbox.mkdir(parents=True, exist_ok=True)

    # 生成模拟站点观测（阿里站 PM1，UTC，小时）
    station_path = sandbox / "data" / "Ngari_data" / "ngari_pm1_202206.csv"
    write_simulated_station(station_path, "2022-06-01", "2022-06-30", lag_days=2)

    settings = load_settings(workspace_root=sandbox)
    orch = Orchestrator(settings, mode="simulate", station_path=station_path)

    # ---- 阶段 1：需求解析 ----
    banner("阶段 1  需求解析")
    spec = orch.initialize(
        title="2022 年 6 月南亚—青藏高原西部火点活动与阿里站 PM1 关系",
        start="2022-06-01",
        end="2022-06-30",
        region=Region(north=40, south=20, west=60, east=90),
        station={"name": "Ngari / Ali", "latitude": 33.39, "longitude": 79.70},
        research_question=(
            "研究 2022 年 6 月 1–30 日印度西北部生物质燃烧对阿里站 PM1 的影响，"
            "重点考察火点活动与 PM1 的时间关系及输送气象条件。"
        ),
        station_variables=["PM1"],
    )
    print(f"研究目标：{spec.title}")
    print(f"时间范围：{spec.start} ~ {spec.end}（UTC）")
    print(f"空间范围：{spec.region.north:g}–{spec.region.south:g}°N, {spec.region.west:g}–{spec.region.east:g}°E")
    print(f"观测站：{spec.station['name']}（{spec.station['latitude']}°N, {spec.station['longitude']}°E）")
    print(f"自动判断的数据需求：火点={spec.needs_fire}, ERA5 单层={spec.needs_era5_single}, "
          f"ERA5 气压层={spec.needs_era5_pressure} @ {spec.pressure_levels} hPa")

    # ---- 阶段 2：数据需求说明 + MAP_KEY 检查 ----
    banner("阶段 2  数据需求说明与 MAP_KEY 检查")
    print(orch.describe_plan())
    env = orch.environment_report()
    print("\n环境检查：")
    if env.get("firms_map_key"):
        print(f"  FIRMS MAP_KEY：{'已就绪' if env['firms_map_key']['ready'] else '未配置'}")
        if not env["firms_map_key"]["ready"]:
            print("  " + env["firms_map_key"]["message"].replace("\n", "\n  "))
    print(f"  CDS：{'已就绪' if env['cds']['ready'] else '未配置'}（{env['cds']['message']}）")

    # ---- 阶段 3-4：提交 + 定时检查（并发 3 → 5）----
    banner("阶段 3–4  提交与定时检查（首次 3 个，任意时刻最多 5 个）")
    history = []
    for index in range(1, 31):
        result = orch.cycle()
        history.append(result)
        q = result["queue"]
        print(f"第 {index} 次检查：进行中 {q['active']}/{q['limit']}（上限 {q['max_concurrent']}）｜"
              f"本次提交 {len(result['submitted'])} 个 {result['submitted']}｜"
              f"本次下载 {len(result['downloaded'])} 个 {result['downloaded']}")
        print(f"           状态分布：{q['states']}")
        if orch.store.all_downloaded():
            print("→ 所有数据均已申请并下载完成。")
            break
        if not orch.store.active_tasks() and not orch.store.submittable_tasks():
            print("→ 没有进行中任务，也没有可提交任务，停止。")
            break

    # ---- 阶段 5：数据质量检查 ----
    banner("阶段 5  数据质量检查")
    quality = orch.data_check()
    print(f"质检结论：{quality['summary']['pass']} 通过 / {quality['summary']['warn']} 警告 / "
          f"{quality['summary']['fail']} 失败")
    for row in quality["rows"]:
        print(f"  [{row.get('status')}] {row['task_id']}：{row.get('notes', '')}")
    print(f"报告文件：{quality['path']}")

    # ---- 阶段 6：数据整理 ----
    banner("阶段 6  数据整理与标准化")
    prepared = orch.prepare()
    print("输出文件：")
    for key, value in prepared["outputs"].items():
        print(f"  {key}: {value}")
    print(f"数据能力：{sorted(orch.prepared.capabilities)}")

    # ---- 阶段 7：分析方案推荐 ----
    banner("阶段 7  分析方案推荐")
    recommendation = orch.recommend(top=6)
    print(recommendation["menu"])
    # 模拟用户选择：覆盖时间序列、空间分布、相关、滞后与综合图
    selected = [2, 5, 9, 16, 21, 24]
    print(f"\n（模拟用户选择：{selected}，其中 {[o['id'] for o in recommendation['recommended']]} 为 AI 推荐）")

    # ---- 阶段 8：绘图 + 自动读图 ----
    banner("阶段 8  绘图与自动读图")
    figures = orch.render_figures(selected, recommendation["options"])
    for figure in figures:
        print(f"  · {figure.title}")
        print(f"    图片：{figure.png}")
        print(f"    自动读图：{figure.status}　{('；'.join(figure.review_notes) if figure.review_notes else '未发现问题')}")

    # ---- 阶段 9：最终报告 ----
    banner("阶段 9  最终报告")
    report_path = orch.build_report(figures, quality)
    print(f"报告：{report_path}")

    banner("模拟测试完成")
    print(f"沙箱目录：{sandbox}")
    print(f"图件目录：{settings.figures_dir}")
    print(f"表格目录：{settings.tables_dir}")
    print(f"报告：{report_path}")
    print(f"任务记录：{settings.requests_file}")
    print(f"共 {len(figures)} 张图，全部通过自动读图审查。" if all(f.status == "ok" for f in figures)
          else "部分图件需要人工检查。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
