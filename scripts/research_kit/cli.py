"""命令行入口。

用法（在 skill 目录下）：
    python scripts/run.py env      --study examples/study_ngari_2022.json
    python scripts/run.py plan     --study examples/study_ngari_2022.json
    python scripts/run.py submit   --study examples/study_ngari_2022.json --confirm
    python scripts/run.py cycle    --study examples/study_ngari_2022.json --confirm
    python scripts/run.py run      --study examples/study_ngari_2022.json --confirm
    python scripts/run.py check
    python scripts/run.py prepare  --study examples/study_ngari_2022.json
    python scripts/run.py options
    python scripts/run.py plot     --select 5,11,21
    python scripts/run.py report
    python scripts/run.py demo     --study examples/study_ngari_2022.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .orchestrator import Orchestrator
from .settings import Region, load_settings


def _load_study(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    region_cfg = data.get("region", {})
    data["region"] = Region(
        north=float(region_cfg["north"]),
        south=float(region_cfg["south"]),
        west=float(region_cfg["west"]),
        east=float(region_cfg["east"]),
    )
    return data


def _make(args: argparse.Namespace, *, mode: str) -> Orchestrator:
    settings = load_settings(workspace_root=args.workspace)
    orch = Orchestrator(settings, mode=mode, station_path=Path(args.station) if args.station else None)
    study = _load_study(Path(args.study))
    orch.initialize(**study)
    return orch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="atmospheric-data-research CLI")
    parser.add_argument("command", choices=[
        "env", "plan", "submit", "cycle", "run", "check", "prepare", "options", "plot", "report", "demo",
    ])
    parser.add_argument("--study", default="examples/study_ngari_2022.json")
    parser.add_argument("--station", default=None, help="站点观测 CSV 路径")
    parser.add_argument("--workspace", default=None, help="覆盖工作区根目录（便于测试）")
    parser.add_argument("--confirm", action="store_true", help="真实模式下才真正联网（默认仅模拟）")
    parser.add_argument("--real", action="store_true", help="使用真实 API（需凭据；默认模拟）")
    parser.add_argument("--select", default=None, help="绘图方案编号，如 5,11,21")
    parser.add_argument("--max-cycles", type=int, default=30)
    args = parser.parse_args(argv)

    mode = "real" if args.real else "simulate"

    if args.command == "env":
        orch = _make(args, mode=mode)
        print(json.dumps(orch.environment_report(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "plan":
        orch = _make(args, mode=mode)
        print(orch.describe_plan())
        return 0

    if args.command == "submit":
        orch = _make(args, mode=mode)
        if mode == "real" and not args.confirm:
            print("真实模式需加 --confirm 才会联网提交。")
            return 1
        print(json.dumps(orch.submit_pending(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "cycle":
        orch = _make(args, mode=mode)
        if mode == "real" and not args.confirm:
            print("真实模式需加 --confirm 才会联网。")
            return 1
        print(json.dumps(orch.cycle(), ensure_ascii=False, indent=2, default=str))
        return 0

    if args.command == "run":
        orch = _make(args, mode=mode)
        if mode == "real" and not args.confirm:
            print("真实模式需加 --confirm 才会联网。")
            return 1
        history = orch.run_until_complete(max_cycles=args.max_cycles)
        print(json.dumps({"cycles": len(history), "states": orch.store.summary()}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "check":
        orch = _make(args, mode=mode)
        result = orch.data_check()
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
        return 0

    if args.command == "prepare":
        orch = _make(args, mode=mode)
        print(json.dumps(orch.prepare()["outputs"], ensure_ascii=False, indent=2))
        return 0

    if args.command == "options":
        orch = _make(args, mode=mode)
        orch.prepare()
        print(orch.recommend()["menu"])
        return 0

    if args.command == "plot":
        orch = _make(args, mode=mode)
        orch.prepare()
        selection = [int(x) for x in args.select.split(",")] if args.select else None
        figures = orch.render_figures(selection or [])
        print(json.dumps([{"title": f.title, "png": f.png, "status": f.status} for f in figures],
                         ensure_ascii=False, indent=2))
        return 0

    if args.command == "report":
        orch = _make(args, mode=mode)
        orch.prepare()
        quality = orch.data_check()
        recommendation = orch.recommend()
        figures = orch.render_figures([o["id"] for o in recommendation["recommended"]], recommendation["options"])
        path = orch.build_report(figures, quality)
        print(str(path))
        return 0

    if args.command == "demo":
        orch = _make(args, mode="simulate")
        result = orch.run_full(max_cycles=args.max_cycles)
        print(json.dumps({
            "report": result["report"],
            "figures": [{"title": f.title, "png": f.png, "status": f.status} for f in result["figures"]],
            "queue": result["queue"],
            "quality": result["quality"]["summary"],
        }, ensure_ascii=False, indent=2, default=str))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
