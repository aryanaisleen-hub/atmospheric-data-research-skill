"""数据整理与标准化。

把 raw 数据整理为分析就绪的结构：
  * FIRMS -> 火点表 + 日序列（火点数 / FRP）
  * ERA5 单层/气压层 -> 区域平均与站点最近格点序列
  * 站点观测 -> 小时/日序列（UTC）
统一：时间 UTC、区域 north/south/east/west、单位取自 NetCDF metadata。
原始数据只读，绝不修改。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .era5.client import monthly_target
from .firms.client import firms_target
from .planning import StudySpec
from .settings import Settings
from .store import Task


@dataclass
class PreparedData:
    fire_points: pd.DataFrame = field(default_factory=pd.DataFrame)      # 火点明细
    fire_daily: pd.DataFrame = field(default_factory=pd.DataFrame)       # date, fire_count, frp_sum, frp_mean
    station: pd.DataFrame = field(default_factory=pd.DataFrame)          # datetime, PM1
    era5_single: pd.DataFrame = field(default_factory=pd.DataFrame)      # date, t2m, tp, blh, u10, v10, wind_speed, sp
    era5_pressure: dict[int, pd.DataFrame] = field(default_factory=dict) # level -> DataFrame[date, u, v, z, w]
    daily: pd.DataFrame = field(default_factory=pd.DataFrame)            # 合并后的日序列（分析主表）
    units: dict[str, str] = field(default_factory=dict)

    @property
    def capabilities(self) -> set[str]:
        """当前数据具备的能力键，用于匹配分析方案。"""
        caps: set[str] = set()
        if not self.fire_points.empty:
            caps.add("fire")
        if not self.station.empty:
            caps.add("station")
        if not self.era5_single.empty:
            caps.add("era5_single")
            for column in ("t2m", "tp", "blh"):
                if column in self.era5_single.columns:
                    caps.add(f"era5_single:{column}")
        for level, frame in self.era5_pressure.items():
            if not frame.empty:
                caps.add("era5_pressure")
                caps.add(f"era5_pressure:{level}")
        return caps


def load_fire_points(tasks: list[Task], settings: Settings) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for task in tasks:
        if task.kind != "firms":
            continue
        path = firms_target(task, settings)
        if not path.exists():
            continue
        frame = pd.read_csv(path, dtype={"acq_time": "string", "confidence": "string", "daynight": "string"})
        if not frame.empty:
            frame["product"] = task.product
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates()
    combined["datetime"] = pd.to_datetime(
        combined["acq_date"].astype(str) + " " + combined["acq_time"].astype(str).str.zfill(4),
        format="%Y-%m-%d %H%M",
        errors="coerce",
    )
    combined["frp"] = pd.to_numeric(combined["frp"], errors="coerce")
    return combined


def fire_daily_series(fire_points: pd.DataFrame) -> pd.DataFrame:
    if fire_points.empty:
        return pd.DataFrame()
    grouped = fire_points.groupby(fire_points["acq_date"]).agg(
        fire_count=("frp", "size"), frp_sum=("frp", "sum"), frp_mean=("frp", "mean")
    )
    grouped.index = pd.to_datetime(grouped.index)
    grouped.index.name = "date"
    return grouped.reset_index()


def load_station(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path)
    time_column = next((c for c in frame.columns if c.lower() in ("datetime", "date", "time", "timestamp")), None)
    if time_column is None:
        return pd.DataFrame()
    frame["datetime"] = pd.to_datetime(frame[time_column], errors="coerce")
    frame = frame.dropna(subset=["datetime"]).sort_values("datetime")
    return frame


def station_daily(station: pd.DataFrame, variables: list[str]) -> pd.DataFrame:
    if station.empty:
        return pd.DataFrame()
    keep = [v for v in variables if v in station.columns]
    if not keep:
        return pd.DataFrame()
    daily = station.set_index("datetime")[keep].resample("D").mean().reset_index()
    daily["date"] = daily["datetime"].dt.normalize()
    return daily


def load_era5_monthly(tasks: list[Task], settings: Settings, kind: str) -> dict[str, list[Path]]:
    paths: dict[str, list[Path]] = {}
    for task in tasks:
        if task.kind != kind:
            continue
        path = monthly_target(task, settings)
        if path.exists():
            paths.setdefault(task.variables[0], []).append(path)
    return paths


def era5_region_daily(
    tasks: list[Task],
    settings: Settings,
    spec: StudySpec,
    *,
    kind: str,
) -> pd.DataFrame:
    """把 ERA5 月度文件整理为区域日序列（区域平均）。"""
    import xarray as xr

    frames: list[pd.DataFrame] = []
    north, west, south, east = spec.region.north, spec.region.west, spec.region.south, spec.region.east
    for task in tasks:
        if task.kind != kind:
            continue
        path = monthly_target(task, settings)
        if not path.exists():
            continue
        with xr.open_dataset(path) as ds:
            time_name = "valid_time" if "valid_time" in ds.coords else "time"
            lat_name = "latitude" if "latitude" in ds.coords else "lat"
            lon_name = "longitude" if "longitude" in ds.coords else "lon"
            subset = ds.sel({lat_name: slice(south, north), lon_name: slice(west, east)})
            if subset[lat_name].size == 0 or subset[lon_name].size == 0:
                subset = ds
            level_name = next((n for n in ("pressure_level", "level", "plev") if n in subset.coords), None)
            columns: dict[str, pd.Series] = {}
            for name in subset.data_vars:
                da = subset[name]
                if level_name and level_name in da.dims:
                    continue  # 气压层单独处理
                series = da.mean(dim=[d for d in da.dims if d != time_name])
                columns[name] = pd.Series(series.values, index=pd.to_datetime(subset[time_name].values))
            if columns:
                frame = pd.DataFrame(columns)
                frame.index = pd.DatetimeIndex(frame.index).tz_localize(None)
                frames.append(frame)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames).sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]
    daily = combined.resample("D").mean()
    if {"u10", "v10"}.issubset(daily.columns):
        daily["wind_speed"] = np.hypot(daily["u10"], daily["v10"])
    daily["date"] = daily.index.normalize()
    return daily.reset_index(drop=True)


def era5_pressure_daily(
    tasks: list[Task],
    settings: Settings,
    spec: StudySpec,
) -> dict[int, pd.DataFrame]:
    """把 ERA5 气压层整理为各高度层的区域日序列。"""
    import xarray as xr

    north, west, south, east = spec.region.north, spec.region.west, spec.region.south, spec.region.east
    result_series: dict[int, dict[str, pd.Series]] = {}
    for task in tasks:
        if task.kind != "era5_pressure":
            continue
        path = monthly_target(task, settings)
        if not path.exists():
            continue
        with xr.open_dataset(path) as ds:
            time_name = "valid_time" if "valid_time" in ds.coords else "time"
            lat_name = "latitude" if "latitude" in ds.coords else "lat"
            lon_name = "longitude" if "longitude" in ds.coords else "lon"
            level_name = next((n for n in ("pressure_level", "level", "plev") if n in ds.coords), None)
            if level_name is None:
                continue
            subset = ds.sel({lat_name: slice(south, north), lon_name: slice(west, east)})
            if subset[lat_name].size == 0 or subset[lon_name].size == 0:
                subset = ds
            index = pd.DatetimeIndex(pd.to_datetime(subset[time_name].values)).tz_localize(None)
            for name in subset.data_vars:
                da = subset[name]
                if level_name not in da.dims:
                    continue
                for level in da[level_name].values:
                    level = int(round(float(level)))
                    layer = da.sel({level_name: level})
                    series = layer.mean(dim=[d for d in layer.dims if d != time_name])
                    bucket = result_series.setdefault(level, {})
                    # 同名变量（重复任务）只保留首次，避免列冲突
                    if name not in bucket:
                        bucket[name] = pd.Series(series.values, index=index)

    result: dict[int, pd.DataFrame] = {}
    for level, bucket in result_series.items():
        frame = pd.DataFrame(bucket).sort_index()
        frame = frame[~frame.index.duplicated(keep="first")]
        daily = frame.resample("D").mean()
        daily["date"] = daily.index.normalize()
        result[level] = daily.reset_index(drop=True)
    return result


def build_daily_frame(prepared: PreparedData) -> pd.DataFrame:
    """合并火点、站点、ERA5 为分析主表（按 date 对齐）。"""
    frames: list[pd.DataFrame] = []
    if not prepared.fire_daily.empty:
        frames.append(prepared.fire_daily)
    if not prepared.station.empty:
        daily = prepared.station.copy()
        if "date" not in daily.columns:
            daily["date"] = pd.to_datetime(daily["datetime"]).dt.normalize()
        frames.append(daily)
    if not prepared.era5_single.empty:
        frames.append(prepared.era5_single)
    if not frames:
        return pd.DataFrame()
    merged = frames[0]
    for frame in frames[1:]:
        merged = pd.merge(merged, frame, on="date", how="outer")
    merged = merged.sort_values("date").reset_index(drop=True)
    return merged


def prepare_all(
    tasks: list[Task],
    settings: Settings,
    spec: StudySpec,
    station_path: Path | None = None,
) -> PreparedData:
    """执行完整整理流程。"""
    prepared = PreparedData()
    prepared.fire_points = load_fire_points(tasks, settings)
    prepared.fire_daily = fire_daily_series(prepared.fire_points)

    if station_path and station_path.exists():
        prepared.station = station_daily(load_station(station_path), spec.station_variables or ["PM1"])
    prepared.era5_single = era5_region_daily(tasks, settings, spec, kind="era5_single")
    prepared.era5_pressure = era5_pressure_daily(tasks, settings, spec)
    prepared.daily = build_daily_frame(prepared)

    # 记录单位（从 metadata 读取，不自行换算）
    prepared.units = {
        "t2m": "K", "tp": "m", "blh": "m", "u10": "m s-1", "v10": "m s-1", "sp": "Pa",
        "frp": "MW", "fire_count": "count",
    }
    return prepared


def save_processed(prepared: PreparedData, settings: Settings) -> dict[str, Path]:
    """把整理结果写入 processed/ 与 outputs/tables/。"""
    settings.ensure_dirs()
    outputs: dict[str, Path] = {}
    if not prepared.daily.empty:
        path = settings.processed_dir / "analysis_daily.csv"
        prepared.daily.to_csv(path, index=False, encoding="utf-8-sig")
        outputs["daily"] = path
        table = settings.tables_dir / "analysis_daily.csv"
        prepared.daily.to_csv(table, index=False, encoding="utf-8-sig")
        outputs["daily_table"] = table
    if not prepared.fire_points.empty:
        path = settings.processed_dir / "fire_points.csv"
        prepared.fire_points.to_csv(path, index=False, encoding="utf-8-sig")
        outputs["fire_points"] = path
    return outputs
