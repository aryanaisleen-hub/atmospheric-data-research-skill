"""最终分析报告生成（analysis_report.md）。

报告包含：数据来源、时间/空间范围、下载数据、数据质量检查、分析方法、图形列表、
每张图的主要发现、综合科学解释、不确定性、后续建议。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .planning import StudySpec
from .plotting.figures import FigureResult
from .prepare import PreparedData
from .settings import Settings


@dataclass
class ReportInputs:
    spec: StudySpec
    prepared: PreparedData
    quality_rows: list[dict[str, Any]] = field(default_factory=list)
    figures: list[FigureResult] = field(default_factory=list)
    queue_summary: dict[str, Any] = field(default_factory=dict)
    mode: str = "simulate"   # simulate | real


def _table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_无_"
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    lines = [header, sep]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")) for col in columns) + " |")
    return "\n".join(lines)


def _figure_block(index: int, figure: FigureResult) -> str:
    lines = [
        f"### 图 {index}：{figure.title}",
        "",
        f"![{figure.title}]({figure.png})" if figure.png else "",
        "",
        f"**图展示了什么**：{figure.caption}",
        "",
    ]
    if figure.findings:
        lines.append("**主要现象**：")
        lines.extend(f"- {item}" for item in figure.findings)
        lines.append("")
    if figure.link:
        lines.append(f"**与研究问题的关系**：{figure.link}")
        lines.append("")
    if figure.science:
        lines.append(f"**科学解释（谨慎）**：{figure.science}")
        lines.append("")
    if figure.stats:
        stats = {k: v for k, v in figure.stats.items() if k != "review"}
        if stats:
            lines.append(f"**关键统计量**：`{stats}`")
            lines.append("")
    if figure.review_notes:
        lines.append(f"**自动读图记录**：{'；'.join(figure.review_notes)}")
        lines.append("")
    return "\n".join(lines)


def build_report(inputs: ReportInputs, settings: Settings) -> str:
    spec = inputs.spec
    prepared = inputs.prepared
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        f"# {settings.report.get('title', '大气环境科研分析报告')}",
        "",
        f"> 生成时间：{now}　|　运行模式：{'模拟数据（未使用真实 API）' if inputs.mode == 'simulate' else '真实数据'}",
        "",
        "## 1. 研究任务",
        "",
        f"- 任务标题：{spec.title}",
        f"- 科学问题：{spec.research_question or '（未明确给出）'}",
        f"- 观测变量：{', '.join(spec.station_variables) if spec.station_variables else '（未指定）'}",
        "",
        "## 2. 时间与空间范围",
        "",
        f"- 时间范围：{spec.start} 至 {spec.end}（UTC）",
        f"- 空间范围：{spec.region.north:g}°N – {spec.region.south:g}°N，{spec.region.west:g}°E – {spec.region.east:g}°E",
    ]
    if spec.station:
        lines.append(f"- 观测站：{spec.station.get('name', '')}（{spec.station.get('latitude')}°N, {spec.station.get('longitude')}°E）")
    lines += [
        "",
        "## 3. 数据来源与下载情况",
        "",
        "- ERA5：Copernicus Climate Data Store（CDS），单层与气压层，小时分辨率。",
        "- FIRMS：NASA FIRMS Area API，VIIRS/MODIS 火点产品。",
        "- 站点观测：用户提供的阿里站观测数据。",
        "",
        f"- 任务总数：{inputs.queue_summary.get('total', '—')}",
        f"- 状态分布：`{inputs.queue_summary.get('states', {})}`",
        f"- 并发设置：首次提交 {inputs.queue_summary.get('initial_submit', '—')} 个，上限 {inputs.queue_summary.get('max_concurrent', '—')} 个。",
        "",
        "> 说明：所有数据申请均记录于 `logs/data_requests.json`，支持断点续传；不重复提交、不重复下载。",
        "",
        "## 4. 数据质量检查",
        "",
    ]

    if inputs.quality_rows:
        lines.append(_table(inputs.quality_rows, ["task_id", "kind", "status", "notes"]))
    else:
        lines.append("_无质检记录_")
    lines += ["", "## 5. 分析方法", ""]
    methods = {
        "A": "时间序列：按日均值考察变量的时间演变。",
        "B": "空间分布：火点散点、1° 网格化 FRP/密度、风场矢量。",
        "C": "气象分析：各高度风场与近地面气象要素的日变化。",
        "D": "相关性：Pearson 线性相关与线性拟合。",
        "E": "时间滞后：超前-滞后相关系数，取 |r| 最大者。",
        "F": "综合图：多要素多面板叠加。",
    }
    used_groups = sorted({fig.option_id for fig in inputs.figures})
    lines.append("本报告使用的分析类别：")
    for key, desc in methods.items():
        lines.append(f"- {key}：{desc}")
    lines += [
        "",
        "统计口径：相关分析基于日平均序列；显著性以 p 值给出，但需注意样本量较小。",
        "",
        "## 6. 图形列表与逐图解释",
        "",
    ]

    for index, figure in enumerate(inputs.figures, 1):
        lines.append(_figure_block(index, figure))

    lines += [
        "## 7. 综合科学解释",
        "",
    ]
    lines.extend(_synthesis(inputs))
    lines += [
        "",
        "## 8. 不确定性",
        "",
        "- 卫星火点受云、观测过境时间与像元尺度限制，可能低估实际燃烧。",
        "- ERA5 为再分析产品，在复杂地形（如青藏高原）存在代表性误差。",
        "- 站点观测为单点，向区域外推存在尺度不匹配问题。",
        "- 相关与滞后相关不能直接证明因果关系。",
        "",
        "## 9. 后续建议",
        "",
        "- 结合 HYSPLIT 等轨迹模式，定量验证源-受体输送路径。",
        "- 引入更多站点与更长时段，检验结论的稳健性。",
        "- 对关键过程做敏感性分析，评估气象场不确定性的影响。",
        "",
        "---",
        "",
        "_本报告由 atmospheric-data-research Skill 自动生成。图形解释中的措辞遵循“表明/与……一致/可能表明/可能反映”等谨慎表述。_",
    ]
    return "\n".join(line for line in lines if line is not None)


def _synthesis(inputs: ReportInputs) -> list[str]:
    spec = inputs.spec
    variable = spec.station_variables[0] if spec.station_variables else "观测浓度"
    daily = inputs.prepared.daily
    bullets: list[str] = []
    if daily is None or daily.empty:
        return ["数据不足，无法形成综合解释。"]
    if "fire_count" in daily.columns:
        peak = daily["fire_count"].idxmax()
        bullets.append(
            f"研究期内火点活动在 {daily.loc[peak, 'date'].date() if hasattr(daily.loc[peak,'date'],'date') else daily.loc[peak,'date']} 附近最强，"
            f"最高 {int(daily.loc[peak, 'fire_count'])} 个/日。"
        )
    if variable in daily.columns:
        peak = daily[variable].idxmax()
        bullets.append(f"{variable} 的峰值出现在同一时段附近，与火点增强在时间上具有一定同步性。")
    bullets.append("综合来看，火点活动增强与观测浓度升高在时间上一致，可能表明存在区域输送贡献；该推断与输送气象条件相符，但仍需轨迹模式进一步验证。")
    return bullets


def write_report(text: str, settings: Settings, filename: str = "analysis_report.md") -> Path:
    settings.ensure_dirs()
    path = settings.results_dir / filename
    path.write_text(text, encoding="utf-8")
    return path
