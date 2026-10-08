"""图形渲染。

每个渲染函数返回 FigureResult（图 + 说明 + 主要现象 + 与研究问题的关系 + 科学解释）。
所有图默认 300 dpi，输出 PNG，并尽可能输出 PDF/SVG。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..analysis.metrics import lag_correlation, linear_fit, pearson
from ..era5.client import monthly_target
from ..planning import StudySpec
from ..prepare import PreparedData
from ..settings import Settings
from ..store import Task
from .style import annotate_units, has_cartopy, new_figure, save_figure, setup_style


@dataclass
class FigureResult:
    name: str
    option_id: int
    title: str
    files: dict[str, str] = field(default_factory=dict)
    caption: str = ""
    findings: list[str] = field(default_factory=list)
    link: str = ""
    science: str = ""
    stats: dict[str, Any] = field(default_factory=dict)
    table: str | None = None
    status: str = "ok"
    review_notes: list[str] = field(default_factory=list)

    @property
    def png(self) -> str:
        return self.files.get("png", "")


@dataclass
class RenderContext:
    prepared: PreparedData
    spec: StudySpec
    settings: Settings
    tasks: list[Task]
    figures_dir: Path
    tables_dir: Path
    index: int = 0


def _meta(settings: Settings, name: str) -> tuple[str, str, str]:
    cfg = settings.variable_meta(name)
    return cfg.get("label", name), cfg.get("unit", ""), cfg.get("color", "#2F6B8A")


def _daily(ctx: RenderContext) -> pd.DataFrame:
    return ctx.prepared.daily


# ---------------------------------------------------------------------------
# A. 时间变化
# ---------------------------------------------------------------------------
def timeseries_station(ctx: RenderContext, option: dict[str, Any]) -> FigureResult:
    daily = _daily(ctx)
    variable = ctx.spec.station_variables[0] if ctx.spec.station_variables else "PM1"
    label, unit, color = _meta(ctx.settings, variable)
    fig, ax = new_figure()  # type: ignore[misc]
    if variable in daily.columns:
        ax.plot(daily["date"], daily[variable], "-o", ms=3, lw=1.6, color=color, label=label)
        peak_idx = daily[variable].idxmax()
        peak_date = pd.Timestamp(daily.loc[peak_idx, "date"]).date()
        peak_value = float(daily.loc[peak_idx, variable])
        ax.annotate(f"峰值 {peak_value:.1f}", xy=(daily.loc[peak_idx, "date"], peak_value),
                    xytext=(6, 8), textcoords="offset points", fontsize=8, color="#333333")
        findings = [f"{variable} 日均值在 {peak_date} 达到峰值 {peak_value:.2f} {unit}。"]
    else:
        findings = [f"数据中缺少 {variable}，无法绘制。"]
    annotate_units(ax, f"{label} 日均值", unit)
    ax.set_xlabel("日期（UTC）")
    ax.set_title(f"{ctx.spec.region.north:g}–{ctx.spec.region.south:g}°N 研究时段 {label} 日均序列")
    fig.autofmt_xdate()
    name = f"Fig{ctx.index:02d}_pm1_timeseries"
    files = save_figure(fig, ctx.figures_dir / name, ctx.settings)
    return FigureResult(
        name=name, option_id=option["id"], title=option["title"], files=files,
        caption=f"展示 {ctx.spec.start} 至 {ctx.spec.end} 阿里站 {label} 的日均变化。",
        findings=findings,
        link="该序列是后续与火点、气象要素对比的观测基线。",
        science="浓度的时间演变可能反映局地排放与区域输送的共同作用。",
    )


def _fire_daily(ctx: RenderContext) -> pd.DataFrame:
    return ctx.prepared.fire_daily


def timeseries_fire_count(ctx: RenderContext, option: dict[str, Any]) -> FigureResult:
    fire = _fire_daily(ctx)
    fig, ax = new_figure()  # type: ignore[misc]
    findings: list[str] = []
    if not fire.empty:
        ax.bar(fire["date"], fire["fire_count"], color="#B23A2E", width=0.7, alpha=0.85)
        peak_idx = fire["fire_count"].idxmax()
        peak_date = pd.Timestamp(fire.loc[peak_idx, "date"]).date()
        peak_value = int(fire.loc[peak_idx, "fire_count"])
        findings.append(f"火点数量在 {peak_date} 达到峰值 {peak_value} 个/日。")
        first = pd.Timestamp(fire.loc[fire["fire_count"].idxmin(), "date"]).date()
        findings.append(f"研究期内火点最少出现在 {first}。")
    else:
        findings.append("研究期内没有火点记录。")
    ax.set_xlabel("日期（UTC）")
    annotate_units(ax, "火点数量", "count")
    ax.set_title("研究区域内 FIRMS 火点数量日变化")
    fig.autofmt_xdate()
    name = f"Fig{ctx.index:02d}_fire_count_timeseries"
    files = save_figure(fig, ctx.figures_dir / name, ctx.settings)
    return FigureResult(
        name=name, option_id=option["id"], title=option["title"], files=files,
        caption="展示研究区域内卫星探测火点数量的逐日变化。",
        findings=findings,
        link="火点数量是燃烧活动的时间代理，可用于与 PM1 对比。",
        science="火点数量的日变化可能反映农业焚烧等活动的阶段性特征。",
    )


def timeseries_frp(ctx: RenderContext, option: dict[str, Any]) -> FigureResult:
    fire = _fire_daily(ctx)
    fig, ax = new_figure()  # type: ignore[misc]
    findings: list[str] = []
    if not fire.empty:
        ax.plot(fire["date"], fire["frp_sum"], "-o", ms=3, lw=1.6, color="#D8593B")
        peak_idx = fire["frp_sum"].idxmax()
        peak_date = pd.Timestamp(fire.loc[peak_idx, "date"]).date()
        peak_value = float(fire.loc[peak_idx, "frp_sum"])
        findings.append(f"FRP 合计在 {peak_date} 达到峰值 {peak_value:.1f} MW。")
    else:
        findings.append("研究期内没有火点记录。")
    ax.set_xlabel("日期（UTC）")
    annotate_units(ax, "FRP 合计", "MW")
    ax.set_title("研究区域内 FIRMS 火辐射功率（FRP）日变化")
    fig.autofmt_xdate()
    name = f"Fig{ctx.index:02d}_frp_timeseries"
    files = save_figure(fig, ctx.figures_dir / name, ctx.settings)
    return FigureResult(
        name=name, option_id=option["id"], title=option["title"], files=files,
        caption="展示研究区域内火点 FRP 合计的逐日变化。",
        findings=findings,
        link="FRP 比单纯计数更能反映源强，是判断排放强弱的指标。",
        science="FRP 的峰值期通常与燃烧活动增强一致，可能表明该时段排放显著。",
    )


def timeseries_dual(ctx: RenderContext, option: dict[str, Any]) -> FigureResult:
    daily = _daily(ctx)
    variable = ctx.spec.station_variables[0] if ctx.spec.station_variables else "PM1"
    label, unit, color = _meta(ctx.settings, variable)
    fig, ax = new_figure()  # type: ignore[misc]
    ax2 = ax.twinx()
    findings: list[str] = []
    if "fire_count" in daily.columns:
        ax.bar(daily["date"], daily["fire_count"], color="#B23A2E", alpha=0.4, width=0.7, label="火点数量")
        annotate_units(ax, "火点数量", "count")
    if variable in daily.columns:
        ax2.plot(daily["date"], daily[variable], "-o", ms=3, lw=1.8, color=color, label=label)
        annotate_units(ax2, f"{label} 日均值", unit)
    stat = pearson(daily.get("fire_count", pd.Series(dtype=float)), daily.get(variable, pd.Series(dtype=float)))
    if np.isfinite(stat.get("r", float("nan"))):
        findings.append(f"火点数量与 {label} 的同期相关系数 r = {stat['r']:.2f}（n = {stat['n']}）。")
    ax.set_xlabel("日期（UTC）")
    ax.set_title(f"火点数量与 {label} 双时间序列")
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, loc="upper left")
    fig.autofmt_xdate()
    name = f"Fig{ctx.index:02d}_fire_pm1_dual"
    files = save_figure(fig, ctx.figures_dir / name, ctx.settings)
    return FigureResult(
        name=name, option_id=option["id"], title=option["title"], files=files, stats=stat,
        caption="在同一时间轴上叠加火点数量与站点观测浓度。",
        findings=findings,
        link="双序列的时间同步性可提示火点活动与观测浓度是否存在关联。",
        science="若二者在时间上同相，可能表明燃烧排放对观测浓度有贡献；但相关不等于因果。",
    )


# ---------------------------------------------------------------------------
# B / C. 空间分布与气象
# ---------------------------------------------------------------------------
def _load_grid(ctx: RenderContext, kind: str, level: int | None = None) -> dict[str, Any] | None:
    import xarray as xr

    for task in ctx.tasks:
        if task.kind != kind:
            continue
        path = monthly_target(task, ctx.settings)
        if not path.exists():
            continue
        with xr.open_dataset(path) as ds:
            time_name = "valid_time" if "valid_time" in ds.coords else "time"
            data = ds.mean(dim=time_name)
            lat_name = "latitude" if "latitude" in data.coords else "lat"
            lon_name = "longitude" if "longitude" in data.coords else "lon"
            result: dict[str, Any] = {
                "lat": data[lat_name].values,
                "lon": data[lon_name].values,
            }
            level_name = next((n for n in ("pressure_level", "level", "plev") if n in data.coords), None)
            for name in data.data_vars:
                da = data[name]
                if level is not None and level_name and level_name in da.dims:
                    result[name] = da.sel({level_name: level}).values
                elif level_name and level_name in da.dims:
                    # 未指定层时对全部气压层取平均，避免保留多余维度
                    result[name] = da.mean(dim=level_name).values
                else:
                    result[name] = da.values
            return result
    return None


def _grid_axes(ax, ctx: RenderContext):
    # 先画国界（若资源可用），再设定范围
    try:
        from .boundaries import draw_boundaries

        draw_boundaries(ax, ctx.settings, ctx.spec.region)
    except Exception:  # noqa: BLE001
        pass
    ax.set_xlabel("经度 (°E)")
    ax.set_ylabel("纬度 (°N)")
    ax.set_xlim(ctx.spec.region.west, ctx.spec.region.east)
    ax.set_ylim(ctx.spec.region.south, ctx.spec.region.north)
    ax.set_aspect("equal", adjustable="box")


def map_fire_points(ctx: RenderContext, option: dict[str, Any]) -> FigureResult:
    points = ctx.prepared.fire_points
    fig, ax = new_figure(6.4, 4.8)  # type: ignore[misc]
    findings: list[str] = []
    if not points.empty:
        scatter = ax.scatter(points["longitude"], points["latitude"], c=points["frp"], s=6,
                             cmap="inferno", alpha=0.7, edgecolors="none", zorder=3)
        cbar = fig.colorbar(scatter, ax=ax, pad=0.02)
        cbar.set_label("FRP (MW)")
        source_lat = points["latitude"].mean()
        source_lon = points["longitude"].mean()
        ax.plot(source_lon, source_lat, marker="*", ms=13, color="#2F6B8A", mec="white", mew=0.8)
        ax.annotate(f"火点质心\n({source_lat:.1f}°N, {source_lon:.1f}°E)", xy=(source_lon, source_lat),
                    xytext=(8, -18), textcoords="offset points", fontsize=8)
        findings.append(f"火点主要集中在约 {source_lat:.1f}°N、{source_lon:.1f}°E 附近。")
    else:
        findings.append("研究期内没有火点记录。")
    station = ctx.spec.station
    if station:
        ax.plot(station.get("longitude"), station.get("latitude"), marker="^", ms=9,
                color="#2F6B8A", mec="white", mew=0.8)
        ax.annotate(station.get("name", "站点"), xy=(station.get("longitude"), station.get("latitude")),
                    xytext=(8, 6), textcoords="offset points", fontsize=8, color="#2F6B8A")
    _grid_axes(ax, ctx)
    ax.set_title("研究区域内 FIRMS 火点空间分布")
    name = f"Fig{ctx.index:02d}_fire_map"
    files = save_figure(fig, ctx.figures_dir / name, ctx.settings)
    return FigureResult(
        name=name, option_id=option["id"], title=option["title"], files=files,
        caption="展示研究时段内全部火点的空间分布，颜色表示 FRP 强度。",
        findings=findings,
        link="火点分布定位了潜在排放源区，是判断输送路径的起点。",
        science="火点集中在特定区域，可能表明该区域是主要的生物质燃烧源。",
    )


def _gridded_fire(ctx: RenderContext, weights: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    points = ctx.prepared.fire_points
    lon_edges = np.arange(ctx.spec.region.west, ctx.spec.region.east + 0.001, 1.0)
    lat_edges = np.arange(ctx.spec.region.south, ctx.spec.region.north + 0.001, 1.0)
    grid = np.zeros((len(lat_edges) - 1, len(lon_edges) - 1))
    if not points.empty:
        lon_idx = np.clip(np.digitize(points["longitude"], lon_edges) - 1, 0, grid.shape[1] - 1)
        lat_idx = np.clip(np.digitize(points["latitude"], lat_edges) - 1, 0, grid.shape[0] - 1)
        values = points["frp"].to_numpy() if weights else np.ones(len(points))
        np.add.at(grid, (lat_idx, lon_idx), values)
    return grid, lon_edges, lat_edges


def _render_fire_grid(ctx: RenderContext, option: dict[str, Any], weights: bool) -> FigureResult:
    grid, lon_edges, lat_edges = _gridded_fire(ctx, weights)
    fig, ax = new_figure(6.4, 4.8)  # type: ignore[misc]
    mesh = ax.pcolormesh(lon_edges, lat_edges, grid, cmap="YlOrRd", shading="flat")
    cbar = fig.colorbar(mesh, ax=ax, pad=0.02)
    cbar.set_label("FRP 合计 (MW)" if weights else "火点数 (count)")
    label = "FRP 空间分布（1° 网格）" if weights else "火点密度（1° 网格）"
    total = float(grid.sum())
    peak = np.unravel_index(int(np.argmax(grid)), grid.shape)
    peak_lat = (lat_edges[peak[0]] + lat_edges[peak[0] + 1]) / 2
    peak_lon = (lon_edges[peak[1]] + lon_edges[peak[1] + 1]) / 2
    _grid_axes(ax, ctx)
    ax.set_title(label)
    name = f"Fig{ctx.index:02d}_{'frp_grid' if weights else 'fire_density'}"
    files = save_figure(fig, ctx.figures_dir / name, ctx.settings)
    return FigureResult(
        name=name, option_id=option["id"], title=option["title"], files=files,
        caption=f"以 1° 网格统计研究区域内的{'FRP 合计' if weights else '火点数量'}。",
        findings=[
            f"研究期合计 {total:.1f} {'MW' if weights else '个'}。",
            f"最强网格位于约 {peak_lat:.1f}°N、{peak_lon:.1f}°E。",
        ],
        link="网格化后可以定量比较不同子区的燃烧强度。",
        science="强度集中在少数网格，可能表明排放源具有明显的地域集中性。",
    )


def map_frp_grid(ctx: RenderContext, option: dict[str, Any]) -> FigureResult:
    return _render_fire_grid(ctx, option, weights=True)


def map_fire_density(ctx: RenderContext, option: dict[str, Any]) -> FigureResult:
    return _render_fire_grid(ctx, option, weights=False)


def _wind_quiver(ax, ctx: RenderContext, level: int | None) -> tuple[bool, float]:
    grid = _load_grid(ctx, "era5_pressure", level=level)
    if grid is None or "u" not in grid or "v" not in grid:
        return False, float("nan")
    lon = grid["lon"]
    lat = grid["lat"]
    u = np.asarray(grid["u"], dtype=float)
    v = np.asarray(grid["v"], dtype=float)
    lon2d, lat2d = np.meshgrid(lon, lat)
    speed = np.hypot(u, v)
    step = max(2, len(lat) // 9)
    mesh = ax.pcolormesh(lon, lat, speed, cmap="viridis", shading="auto", alpha=0.85, zorder=0)
    ax.quiver(lon2d[::step, ::step], lat2d[::step, ::step], u[::step, ::step], v[::step, ::step],
              color="#1F2D3D", scale=220, width=0.0035, zorder=4)
    return True, float(np.nanmean(speed))


def map_wind(ctx: RenderContext, option: dict[str, Any]) -> FigureResult:
    levels = sorted(ctx.prepared.era5_pressure.keys(), reverse=True)
    level = levels[0] if levels else None
    fig, ax = new_figure(6.4, 4.8)  # type: ignore[misc]
    ok, speed = _wind_quiver(ax, ctx, level)
    findings = [f"研究时段内 {level} hPa 平均风速约 {speed:.1f} m s⁻¹。"] if ok else ["缺少风场数据。"]
    _grid_axes(ax, ctx)
    ax.set_title(f"{level} hPa 平均风场" if level else "平均风场")
    name = f"Fig{ctx.index:02d}_wind_map"
    files = save_figure(fig, ctx.figures_dir / name, ctx.settings)
    return FigureResult(
        name=name, option_id=option["id"], title=option["title"], files=files,
        caption=f"展示研究时段内 {level} hPa 的平均风场（填色为风速，箭头为风向）。",
        findings=findings,
        link="平均风场刻画了研究时段的输送背景，可判断源区烟羽的可能去向。",
        science="风向与风速的空间分布可能反映该高度层的输送通道。",
    )


def map_wind_level(ctx: RenderContext, option: dict[str, Any]) -> FigureResult:
    level = None
    for requirement in option["requires"]:
        if requirement.startswith("era5_pressure:"):
            level = int(requirement.split(":")[1])
    if level is None:
        level = sorted(ctx.prepared.era5_pressure.keys(), reverse=True)[0]
    fig, ax = new_figure(6.4, 4.8)  # type: ignore[misc]
    ok, speed = _wind_quiver(ax, ctx, level)
    findings = [f"{level} hPa 平均风速约 {speed:.1f} m s⁻¹。"] if ok else [f"缺少 {level} hPa 风场数据。"]
    _grid_axes(ax, ctx)
    ax.set_title(f"{level} hPa 平均风场")
    name = f"Fig{ctx.index:02d}_wind_{level}hpa"
    files = save_figure(fig, ctx.figures_dir / name, ctx.settings)
    role = {850: "低层", 700: "中低层", 600: "中低层", 500: "中层"}.get(level, "")
    return FigureResult(
        name=name, option_id=option["id"], title=option["title"], files=files,
        caption=f"展示 {level} hPa（{role}）的平均风场。",
        findings=findings,
        link="该高度层的风场是判断区域输送方向的重要依据。",
        science=f"{level} hPa 的风场可能代表{role}输送条件，与污染物输送过程相关。",
    )


def map_fire_wind(ctx: RenderContext, option: dict[str, Any]) -> FigureResult:
    points = ctx.prepared.fire_points
    fig, ax = new_figure(6.4, 4.8)  # type: ignore[misc]
    ok, speed = _wind_quiver(ax, ctx, None)
    findings: list[str] = []
    if not points.empty:
        ax.scatter(points["longitude"], points["latitude"], c="#B23A2E", s=5, alpha=0.55, edgecolors="none", zorder=3)
        findings.append(f"叠加显示 {len(points)} 个火点。")
    if ok:
        findings.append(f"背景平均风速约 {speed:.1f} m s⁻¹。")
    _grid_axes(ax, ctx)
    ax.set_title("火点分布与平均风场叠加")
    name = f"Fig{ctx.index:02d}_fire_wind"
    files = save_figure(fig, ctx.figures_dir / name, ctx.settings)
    return FigureResult(
        name=name, option_id=option["id"], title=option["title"], files=files,
        caption="把火点位置与平均风场叠加，用于判断输送方向。",
        findings=findings,
        link="若风场把源区指向观测站，则火点排放有可能被输送到站点。",
        science="火点位于上风向时，其排放可能被输送至下风向的观测站；这需要结合具体风向判断。",
    )


def timeseries_single_met(ctx: RenderContext, option: dict[str, Any]) -> FigureResult:
    variable = None
    for requirement in option["requires"]:
        if requirement.startswith("era5_single:"):
            variable = requirement.split(":")[1]
    variable = variable or "t2m"
    label, unit, color = _meta(ctx.settings, variable)
    daily = _daily(ctx)
    fig, ax = new_figure()  # type: ignore[misc]
    findings: list[str] = []
    if variable in daily.columns:
        series = daily[variable]
        if variable == "tp":
            series = series * 1000.0
            unit = "mm"
            ax.bar(daily["date"], series, color=color, alpha=0.85)
        else:
            ax.plot(daily["date"], series, "-o", ms=3, lw=1.6, color=color)
        findings.append(f"{label} 日均范围 {float(series.min()):.2f} – {float(series.max()):.2f} {unit}。")
    else:
        findings.append(f"缺少 {label} 数据。")
    ax.set_xlabel("日期（UTC）")
    annotate_units(ax, f"{label} 日均值", unit)
    ax.set_title(f"{label} 日变化（区域平均）")
    fig.autofmt_xdate()
    name = f"Fig{ctx.index:02d}_met_{variable}"
    files = save_figure(fig, ctx.figures_dir / name, ctx.settings)
    return FigureResult(
        name=name, option_id=option["id"], title=option["title"], files=files,
        caption=f"展示研究区域内 {label} 的日均变化。",
        findings=findings,
        link=f"{label} 是影响气溶胶浓度与输送的气象条件之一。",
        science=f"{label} 的变化可能与观测浓度的波动相关，具体机制需要结合其他要素共同判断。",
    )


# ---------------------------------------------------------------------------
# D. 相关性
# ---------------------------------------------------------------------------
_PAIR_MAP: dict[int, tuple[str, str]] = {
    16: ("fire_count", "PM1"),
    17: ("frp_sum", "PM1"),
    18: ("wind_speed", "PM1"),
    19: ("tp", "PM1"),
    20: ("blh", "PM1"),
}


def scatter_fit(ctx: RenderContext, option: dict[str, Any]) -> FigureResult:
    x_name, y_name = _PAIR_MAP.get(option["id"], ("fire_count", "PM1"))
    if ctx.spec.station_variables:
        y_name = ctx.spec.station_variables[0]
    daily = _daily(ctx)
    x_label, x_unit, _ = _meta(ctx.settings, x_name)
    y_label, y_unit, _ = _meta(ctx.settings, y_name)
    fig, ax = new_figure()  # type: ignore[misc]
    findings: list[str] = []
    stats: dict[str, Any] = {}
    if x_name in daily.columns and y_name in daily.columns:
        fit = linear_fit(daily[x_name], daily[y_name])
        stats = fit
        ax.scatter(daily[x_name], daily[y_name], s=22, color="#2F6B8A", alpha=0.75, edgecolors="white", linewidths=0.5)
        clean = daily[[x_name, y_name]].dropna().sort_values(x_name)
        if np.isfinite(fit["slope"]) and len(clean):
            ax.plot(clean[x_name], fit["slope"] * clean[x_name] + fit["intercept"], color="#B23A2E", lw=1.8)
        findings.append(f"Pearson r = {fit['r']:.2f}（p = {fit['p']:.3f}，n = {fit['n']}）。")
        if np.isfinite(fit["r"]) and abs(fit["r"]) >= 0.5:
            findings.append("相关强度中等以上。")
        elif np.isfinite(fit["r"]):
            findings.append("相关强度偏弱。")
    else:
        findings.append("缺少所需变量，无法计算相关。")
    annotate_units(ax, y_label, y_unit)
    ax.set_xlabel(f"{x_label} ({x_unit})" if x_unit else x_label)
    ax.set_title(f"{y_label} 与 {x_label} 的关系（日均）")
    name = f"Fig{ctx.index:02d}_scatter_{x_name}_vs_{y_name}"
    files = save_figure(fig, ctx.figures_dir / name, ctx.settings)
    return FigureResult(
        name=name, option_id=option["id"], title=option["title"], files=files, stats=stats,
        caption=f"以日均值考察 {x_label} 与 {y_label} 的线性关系。",
        findings=findings,
        link="相关分析量化了二者在统计上的联系强度。",
        science="相关关系表明二者可能受共同过程调制；但不能据此推断因果关系。",
    )


_LAG_MAP: dict[int, tuple[str, str]] = {
    21: ("fire_count", "PM1"),
    22: ("frp_sum", "PM1"),
    23: ("wind_speed", "PM1"),
}


def lag_correlation_figure(ctx: RenderContext, option: dict[str, Any]) -> FigureResult:
    x_name, y_name = _LAG_MAP.get(option["id"], ("fire_count", "PM1"))
    if ctx.spec.station_variables:
        y_name = ctx.spec.station_variables[0]
    daily = _daily(ctx)
    x_label, x_unit, _ = _meta(ctx.settings, x_name)
    y_label, y_unit, _ = _meta(ctx.settings, y_name)
    fig, ax = new_figure()  # type: ignore[misc]
    findings: list[str] = []
    stats: dict[str, Any] = {}
    table_path: str | None = None
    if x_name in daily.columns and y_name in daily.columns:
        lag_frame = lag_correlation(daily[x_name], daily[y_name], max_lag=7)
        valid = lag_frame.dropna(subset=["r"])
        ax.axhline(0, color="#999999", lw=0.8)
        ax.bar(valid["lag"], valid["r"], color="#4E79A7", width=0.6)
        if not valid.empty:
            best = valid.loc[valid["r"].abs().idxmax()]
            ax.axvline(best["lag"], color="#B23A2E", ls="--", lw=1.2)
            stats = {"best_lag": int(best["lag"]), "best_r": float(best["r"])}
            findings.append(f"最佳滞后为 {int(best['lag'])} 天，r = {best['r']:.2f}。")
            if best["lag"] > 0:
                findings.append(f"提示 {x_label} 变化可能超前 {y_label} 约 {int(best['lag'])} 天。")
        table = ctx.tables_dir / f"lag_{x_name}_vs_{y_name}.csv"
        table.parent.mkdir(parents=True, exist_ok=True)
        lag_frame.to_csv(table, index=False, encoding="utf-8-sig")
        table_path = str(table)
    else:
        findings.append("缺少所需变量，无法计算滞后相关。")
    ax.set_xlabel("滞后天数（正=左侧变量超前）")
    ax.set_ylabel("相关系数 r")
    ax.set_title(f"{x_label} 与 {y_label} 的滞后相关")
    name = f"Fig{ctx.index:02d}_lag_{x_name}_vs_{y_name}"
    files = save_figure(fig, ctx.figures_dir / name, ctx.settings)
    return FigureResult(
        name=name, option_id=option["id"], title=option["title"], files=files, stats=stats, table=table_path,
        caption=f"计算 {x_label} 相对 {y_label} 的超前-滞后相关系数。",
        findings=findings,
        link="最佳滞后时间反映了从源区排放到站点响应可能所需的时间尺度。",
        science="滞后相关表明源-受体之间可能存在时间上的先后关系，与输送和老化过程一致；但需结合气象场进一步验证。",
    )


# ---------------------------------------------------------------------------
# F. 综合图
# ---------------------------------------------------------------------------
def composite_fire_wind_pm1(ctx: RenderContext, option: dict[str, Any]) -> FigureResult:
    daily = _daily(ctx)
    variable = ctx.spec.station_variables[0] if ctx.spec.station_variables else "PM1"
    label, unit, color = _meta(ctx.settings, variable)
    fig, axes = __import__("matplotlib.pyplot", fromlist=["subplots"]).subplots(2, 1, figsize=(7.6, 7.2))
    findings: list[str] = []
    ax0 = axes[0]
    ok, speed = _wind_quiver(ax0, ctx, None)
    if not ctx.prepared.fire_points.empty:
        ax0.scatter(ctx.prepared.fire_points["longitude"], ctx.prepared.fire_points["latitude"],
                    c="#B23A2E", s=4, alpha=0.5, edgecolors="none", zorder=3)
    _grid_axes(ax0, ctx)
    ax0.set_title("(a) 火点分布与平均风场")
    ax1 = axes[1]
    if "fire_count" in daily.columns:
        ax1.bar(daily["date"], daily["fire_count"], color="#B23A2E", alpha=0.4, width=0.7, label="火点数量")
    if variable in daily.columns:
        ax1b = ax1.twinx()
        ax1b.plot(daily["date"], daily[variable], "-o", ms=3, lw=1.8, color=color, label=label)
        ax1b.set_ylabel(f"{label} ({unit})")
    ax1.set_ylabel("火点数量")
    ax1.set_xlabel("日期（UTC）")
    ax1.set_title("(b) 火点数量与观测浓度")
    ax1b.legend(loc="upper left")
    fig.autofmt_xdate()
    stat = pearson(daily.get("fire_count", pd.Series(dtype=float)), daily.get(variable, pd.Series(dtype=float)))
    if np.isfinite(stat.get("r", float("nan"))):
        findings.append(f"火点与 {label} 同期相关 r = {stat['r']:.2f}。")
        stats = stat
    else:
        stats = {}
    fig.suptitle("火点—风场—观测浓度综合图", y=0.995)
    fig.tight_layout()
    name = f"Fig{ctx.index:02d}_composite_fire_wind_pm1"
    files = save_figure(fig, ctx.figures_dir / name, ctx.settings)
    return FigureResult(
        name=name, option_id=option["id"], title=option["title"], files=files, stats=stats,
        caption="上图为火点与风场空间叠加，下图为火点数量与观测浓度的时间对比。",
        findings=findings,
        link="该图把源、输送与受体放在同一框架下，形成污染过程的整体图景。",
        science="若火点位于上风向且时间上领先于浓度峰值，可能表明存在源-受体输送联系。",
    )


def composite_full(ctx: RenderContext, option: dict[str, Any]) -> FigureResult:
    daily = _daily(ctx)
    variable = ctx.spec.station_variables[0] if ctx.spec.station_variables else "PM1"
    label, unit, color = _meta(ctx.settings, variable)
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(3, 1, figsize=(7.6, 9.0), sharex=True)
    findings: list[str] = []
    if "fire_count" in daily.columns:
        axes[0].bar(daily["date"], daily["fire_count"], color="#B23A2E", alpha=0.85, width=0.7)
    annotate_units(axes[0], "火点数量", "count")
    axes[0].set_title("(a) 火点数量")
    if variable in daily.columns:
        axes[1].plot(daily["date"], daily[variable], "-o", ms=3, lw=1.8, color=color)
        peak = daily[variable].idxmax()
        findings.append(f"{label} 峰值 {float(daily[variable].max()):.2f} {unit} 出现在 {pd.Timestamp(daily.loc[peak,'date']).date()}。")
    annotate_units(axes[1], f"{label} 日均值", unit)
    axes[1].set_title(f"(b) {label}")
    if "blh" in daily.columns:
        axes[2].plot(daily["date"], daily["blh"], "-", lw=1.6, color="#5B8E55", label="边界层高度")
    if "tp" in daily.columns:
        ax3b = axes[2].twinx()
        ax3b.bar(daily["date"], daily["tp"] * 1000, color="#2F6B8A", alpha=0.5, width=0.6, label="降水")
        ax3b.set_ylabel("降水 (mm)")
    annotate_units(axes[2], "边界层高度", "m")
    axes[2].set_title("(c) 边界层高度与降水")
    axes[2].set_xlabel("日期（UTC）")
    fig.suptitle("火点—ERA5—观测浓度综合图", y=0.995)
    fig.autofmt_xdate()
    fig.tight_layout()
    name = f"Fig{ctx.index:02d}_composite_full"
    files = save_figure(fig, ctx.figures_dir / name, ctx.settings)
    return FigureResult(
        name=name, option_id=option["id"], title=option["title"], files=files,
        caption="三面板依次为火点数量、观测浓度、边界层高度与降水。",
        findings=findings,
        link="把源强与环境背景放在同一时间轴上，便于判断浓度变化的主导因素。",
        science="浓度峰值可能由源强增强与不利扩散条件共同导致，需要结合多个要素综合判断。",
    )


def composite_episode(ctx: RenderContext, option: dict[str, Any]) -> FigureResult:
    daily = _daily(ctx)
    variable = ctx.spec.station_variables[0] if ctx.spec.station_variables else "PM1"
    label, unit, color = _meta(ctx.settings, variable)
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(9.4, 7.2))
    findings: list[str] = []
    if variable in daily.columns:
        peak = daily[variable].idxmax()
        peak_date = pd.Timestamp(daily.loc[peak, "date"])
        window = daily[(daily["date"] >= peak_date - pd.Timedelta(days=5)) & (daily["date"] <= peak_date + pd.Timedelta(days=5))]
        axes[0, 0].plot(window["date"], window[variable], "-o", ms=3, color=color)
        findings.append(f"污染过程中心日约为 {peak_date.date()}。")
    axes[0, 0].set_title(f"(a) {label} 峰值前后")
    axes[0, 0].tick_params(axis="x", rotation=30)
    if "fire_count" in daily.columns:
        axes[0, 1].bar(daily["date"], daily["fire_count"], color="#B23A2E", alpha=0.85, width=0.7)
    axes[0, 1].set_title("(b) 火点数量")
    axes[0, 1].tick_params(axis="x", rotation=30)
    if not ctx.prepared.fire_points.empty:
        axes[1, 0].scatter(ctx.prepared.fire_points["longitude"], ctx.prepared.fire_points["latitude"],
                           c=ctx.prepared.fire_points["frp"], s=5, cmap="inferno", alpha=0.7, zorder=3)
    _grid_axes(axes[1, 0], ctx)
    axes[1, 0].set_title("(c) 火点分布")
    _wind_quiver(axes[1, 1], ctx, None)
    _grid_axes(axes[1, 1], ctx)
    axes[1, 1].set_title("(d) 平均风场")
    fig.suptitle("污染过程综合分析", y=0.995)
    fig.tight_layout()
    name = f"Fig{ctx.index:02d}_composite_episode"
    files = save_figure(fig, ctx.figures_dir / name, ctx.settings)
    return FigureResult(
        name=name, option_id=option["id"], title=option["title"], files=files,
        caption="四面板总览：浓度峰值、火点时间、火点空间、风场。",
        findings=findings,
        link="该图作为报告总结图，把污染过程的各个侧面集中呈现。",
        science="综合多要素可以更完整地刻画污染过程，但仍需注意观测与模式的代表性限制。",
    )


RENDERERS: dict[str, Callable[[RenderContext, dict[str, Any]], FigureResult]] = {
    "timeseries_station": timeseries_station,
    "timeseries_fire_count": timeseries_fire_count,
    "timeseries_frp": timeseries_frp,
    "timeseries_dual": timeseries_dual,
    "map_fire_points": map_fire_points,
    "map_frp_grid": map_frp_grid,
    "map_fire_density": map_fire_density,
    "map_wind": map_wind,
    "map_wind_level": map_wind_level,
    "map_fire_wind": map_fire_wind,
    "timeseries_single_met": timeseries_single_met,
    "scatter_fit": scatter_fit,
    "lag_correlation": lag_correlation_figure,
    "composite_fire_wind_pm1": composite_fire_wind_pm1,
    "composite_full": composite_full,
    "composite_episode": composite_episode,
}


def render_option(ctx: RenderContext, option: dict[str, Any], *, attempt: int = 0) -> FigureResult:
    """渲染一个分析方案。

    ``attempt`` > 0 表示这是自动读图失败后的重绘，会启用更稳健的样式
    （加大字号与留白），确保重绘结果与上一版不同，从而具备修复能力。
    """
    setup_style(ctx.settings, robust=attempt > 0)
    renderer = RENDERERS.get(option["renderer"])
    if renderer is None:
        return FigureResult(name=f"Fig{ctx.index:02d}_unknown", option_id=option["id"],
                            title=option["title"], status="error",
                            findings=[f"未实现渲染器：{option['renderer']}"])
    return renderer(ctx, option)
