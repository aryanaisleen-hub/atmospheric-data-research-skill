"""需求解析与数据需求判断。

AI 负责把用户自然语言解析成 StudySpec（区域/时间/对象/科学问题）；
本模块负责：
  * 校验必填项（区域、时间缺失时必须报错，交由 AI 向用户追问）
  * 根据研究关键词推断需要哪些数据（供 AI 参考，最终仍需用户确认）
  * 依据数据目录生成具体的申请/下载任务（Task）
"""

from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass, field
from typing import Any

from .settings import Region, Settings
from .states import TaskState
from .store import Task


class MissingRequirementError(ValueError):
    """缺少必须由用户提供的需求（区域或时间）。"""


@dataclass
class StudySpec:
    """一次科研任务的结构化描述。"""

    title: str
    start: str                              # ISO8601 日期，UTC
    end: str
    region: Region
    station: dict[str, Any] = field(default_factory=dict)
    research_question: str = ""
    station_variables: list[str] = field(default_factory=list)   # 观测变量，如 ["PM1"]
    needs_fire: bool = True
    needs_era5_single: bool = True
    needs_era5_pressure: bool = True
    pressure_levels: list[int] = field(default_factory=list)
    single_variables: list[str] = field(default_factory=list)   # CDS 变量名；空=用默认
    firms_products: list[str] = field(default_factory=list)
    confirmed_by_user: bool = False

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["region"] = self.region.as_dict()
        return data


# ---- 关键词 -> 数据需求（启发式，供 AI 参考）----
_NEED_KEYWORDS: dict[str, tuple[str, ...]] = {
    "needs_fire": ("火点", "火情", "生物质燃烧", "燃烧", "fire", "frp", "野火", "秸秆"),
    "needs_era5_single": ("降水", "温度", "边界层", "湿度", "气象", "地面", "清除", "稀释"),
    "needs_era5_pressure": ("风场", "输送", "环流", "高空", "850", "700", "500", "垂直", "下沉", "上升"),
}

_LEVEL_KEYWORDS: dict[int, tuple[str, ...]] = {
    850: ("850",),
    700: ("700",),
    600: ("600",),
    500: ("500",),
    925: ("925",),
    1000: ("1000",),
}


def infer_data_needs(question: str, settings: Settings) -> dict[str, Any]:
    """根据研究问题推断数据需求（仅作建议，不代替用户确认）。"""
    text = (question or "").lower()
    needs = {key: any(word.lower() in text for word in words) for key, words in _NEED_KEYWORDS.items()}

    # 提到 PM1 / 污染 / 气溶胶 时通常需要火点作为潜在源
    if any(word in text for word in ("pm1", "pm2.5", "pm₂.₅", "气溶胶", "污染")):
        needs["needs_fire"] = True

    levels = [level for level, words in _LEVEL_KEYWORDS.items() if any(w in text for w in words)]
    if not levels and needs["needs_era5_pressure"]:
        levels = settings.default_pressure_levels()

    needs["pressure_levels"] = sorted(levels, reverse=True)
    return needs


def build_spec(
    *,
    title: str,
    start: str,
    end: str,
    region: Region | None = None,
    station: dict[str, Any] | None = None,
    research_question: str = "",
    settings: Settings,
    **overrides: Any,
) -> StudySpec:
    """构造 StudySpec；区域缺失时抛错，要求 AI 先向用户询问。"""
    if region is None:
        raise MissingRequirementError("缺少经纬度范围。必须先向用户询问研究区域，不能擅自猜测。")
    if not start or not end:
        raise MissingRequirementError("缺少时间范围。必须先向用户询问起止时间，不能擅自猜测。")

    inferred = infer_data_needs(research_question, settings)
    spec = StudySpec(
        title=title,
        start=start,
        end=end,
        region=region,
        station=station or {},
        research_question=research_question,
    )
    for key, value in inferred.items():
        setattr(spec, key, value)
    if not spec.pressure_levels:
        spec.pressure_levels = settings.default_pressure_levels()
    if not spec.firms_products:
        spec.firms_products = [settings.datasets.get("firms", {}).get("default_product", "VIIRS_SNPP_SP")]
    for key, value in overrides.items():
        if hasattr(spec, key) and value is not None:
            setattr(spec, key, value)
    return spec


# ---- 任务生成 ----
def _month_range(start: str, end: str) -> list[tuple[int, int]]:
    """把 [start, end] 拆成 (year, month) 列表（含首尾月）。"""
    import pandas as pd

    index = pd.date_range(start, end, freq="MS")
    last = pd.Timestamp(end).to_period("M").to_timestamp()
    months = {pd.Timestamp(item) for item in index}
    months.add(last)
    return sorted((ts.year, ts.month) for ts in months)


def _month_bounds(year: int, month: int, start: str, end: str) -> tuple[str, str]:
    first = f"{year}-{month:02d}-01"
    last_day = calendar.monthrange(year, month)[1]
    last = f"{year}-{month:02d}-{last_day:02d}"
    lo = max(first, start)
    hi = min(last, end)
    return f"{lo}T00:00:00", f"{hi}T23:00:00"


def plan_tasks(spec: StudySpec, settings: Settings) -> list[Task]:
    """根据 StudySpec 生成全部数据任务（PENDING）。"""
    tasks: list[Task] = []
    area = spec.region.as_cds_area()
    months = _month_range(spec.start, spec.end)
    era5_cfg = settings.datasets.get("era5", {})
    single_catalog = era5_cfg.get("single_variables", {})
    pressure_catalog = era5_cfg.get("pressure_variables", {})
    default_single = [name for name, cfg in single_catalog.items() if not cfg.get("optional")]
    single_variables = spec.single_variables or default_single
    firms_cfg = settings.datasets.get("firms", {})

    for year, month in months:
        start_time, end_time = _month_bounds(year, month, spec.start, spec.end)

        # --- ERA5 single levels：每月一个任务，包含全部单层变量 ---
        if spec.needs_era5_single and single_variables:
            tasks.append(
                Task(
                    task_id=f"era5-single-{year}-{month:02d}",
                    kind="era5_single",
                    label=f"ERA5 单层 {year}-{month:02d}（{len(single_variables)} 变量）",
                    dataset="reanalysis-era5-single-levels",
                    year=year,
                    month=month,
                    start_time=start_time,
                    end_time=end_time,
                    variables=list(single_variables),
                    area=area,
                )
            )

        # --- ERA5 pressure levels：每月 × 每变量一个任务（规避 CDS 体积限制）---
        if spec.needs_era5_pressure and spec.pressure_levels:
            for variable in pressure_catalog:
                tasks.append(
                    Task(
                        task_id=f"era5-pressure-{year}-{month:02d}-{variable}",
                        kind="era5_pressure",
                        label=f"ERA5 气压层 {year}-{month:02d} {variable}",
                        dataset="reanalysis-era5-pressure-levels",
                        year=year,
                        month=month,
                        start_time=start_time,
                        end_time=end_time,
                        variables=[variable],
                        levels=list(spec.pressure_levels),
                        area=area,
                    )
                )

        # --- FIRMS：每月每产品一个任务 ---
        if spec.needs_fire:
            for product in spec.firms_products:
                tasks.append(
                    Task(
                        task_id=f"firms-{product}-{year}-{month:02d}",
                        kind="firms",
                        label=f"FIRMS {product} {year}-{month:02d}",
                        product=product,
                        year=year,
                        month=month,
                        start_time=start_time,
                        end_time=end_time,
                        variables=list(firms_cfg.get("default_fields", [])),
                        area=area,
                    )
                )
    return tasks


def describe_plan(spec: StudySpec, tasks: list[Task]) -> str:
    """生成给用户确认的"我建议下载以下数据"说明。"""
    lines = [
        f"研究任务：{spec.title}",
        f"时间范围：{spec.start} 至 {spec.end}（UTC）",
        f"空间范围：{spec.region.north}°N – {spec.region.south}°N, {spec.region.west}°E – {spec.region.east}°E",
        "",
        f"共生成 {len(tasks)} 个数据任务：",
    ]
    groups: dict[str, list[Task]] = {}
    for task in tasks:
        groups.setdefault(task.kind, []).append(task)
    kind_labels = {"era5_single": "ERA5 单层", "era5_pressure": "ERA5 气压层", "firms": "FIRMS 火点"}
    for kind, items in groups.items():
        lines.append(f"  [{kind_labels.get(kind, kind)}] {len(items)} 个")
        for task in items[:6]:
            lines.append(f"    - {task.label}")
        if len(items) > 6:
            lines.append(f"    ... 其余 {len(items) - 6} 个")
    lines.append("")
    lines.append("请确认是否按上述方案申请数据（首次最多提交 3 个，任意时刻最多 5 个并发）。")
    return "\n".join(lines)
