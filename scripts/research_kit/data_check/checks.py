"""下载完成后的数据整理与质量控制。

严格只读原始数据（raw），只输出报告，绝不修改原始文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..era5.client import monthly_target, variable_aliases
from ..firms.client import firms_target
from ..settings import Settings
from ..store import Task, TaskStore


def _missing_pct(ds: Any, variables: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for name in variables:
        if name in ds:
            arr = ds[name].values
            total = arr.size
            result[name] = float(np.isnan(arr).sum() / total * 100) if total else 0.0
    return result


def check_era5_task(task: Task, settings: Settings) -> dict[str, Any]:
    """对单个 ERA5 任务做完整质检。"""
    path = monthly_target(task, settings)
    row: dict[str, Any] = {
        "task_id": task.task_id,
        "kind": task.kind,
        "file": str(path),
        "exists": path.exists(),
    }
    if not path.exists():
        row.update({"status": "FAIL", "notes": "文件不存在"})
        return row

    row["size_bytes"] = path.stat().st_size
    if row["size_bytes"] == 0:
        row.update({"status": "FAIL", "notes": "文件大小为 0"})
        return row

    try:
        import xarray as xr

        with xr.open_dataset(path, decode_times=True) as ds:
            time_name = "valid_time" if "valid_time" in ds.coords else "time"
            times = pd.DatetimeIndex(pd.to_datetime(ds[time_name].values)).tz_localize(None)
            expected = pd.date_range(task.start_time, task.end_time, freq="h")
            row["time_start"] = str(times.min())
            row["time_end"] = str(times.max())
            row["n_time"] = len(times)
            row["expected_time"] = len(expected)
            row["time_ok"] = len(times) == len(expected) and times.equals(expected)
            row["duplicate_times"] = int(len(times) - times.nunique())
            # 时间分辨率
            if len(times) > 2:
                deltas = np.diff(times.values).astype("timedelta64[m]").astype(int)
                row["time_resolution_min"] = int(np.median(deltas))
                row["resolution_ok"] = bool(np.all(deltas == 60))
            # 经纬度
            lat_name = "latitude" if "latitude" in ds.coords else "lat"
            lon_name = "longitude" if "longitude" in ds.coords else "lon"
            lat = ds[lat_name].values
            lon = ds[lon_name].values
            row["lat_min"], row["lat_max"] = float(np.min(lat)), float(np.max(lat))
            row["lon_min"], row["lon_max"] = float(np.min(lon)), float(np.max(lon))
            north, west, south, east = task.area
            row["spatial_ok"] = (
                row["lat_min"] <= south + 0.001 and row["lat_max"] >= north - 0.001
                and row["lon_min"] <= west + 0.001 and row["lon_max"] >= east - 0.001
            )
            # 变量
            aliases = variable_aliases(settings, task.kind, task.variables)
            present = set(ds.data_vars)
            missing_vars = [label for label, names in aliases.items() if not (present & names)]
            row["variables"] = ";".join(sorted(present))
            row["variables_ok"] = not missing_vars
            row["missing_variables"] = ";".join(missing_vars)
            # 单位（从 metadata 读取）
            units: list[str] = []
            for name in sorted(present):
                unit = ds[name].attrs.get("units", "?")
                units.append(f"{name}:{unit}")
            row["units"] = ";".join(units)
            # 缺失
            miss = _missing_pct(ds, sorted(present))
            row["missing_pct"] = ";".join(f"{k}:{v:.3f}%" for k, v in miss.items())
            row["max_missing_pct"] = max(miss.values()) if miss else 0.0
            row["missing_ok"] = row["max_missing_pct"] < 5.0
            # 异常值（以 6σ 粗判；对"以零为主"的累计型变量如降水不做此判据）
            outliers: list[str] = []
            for name in sorted(present):
                arr = ds[name].values.astype("float64")
                finite = arr[np.isfinite(arr)]
                if finite.size == 0:
                    continue
                zero_fraction = float(np.mean(finite == 0.0))
                if zero_fraction > 0.5:
                    continue  # 稀疏事件型变量（如降水），6σ 判据不适用
                mu, sd = float(np.mean(finite)), float(np.std(finite))
                if sd > 0:
                    n_out = int(np.sum(np.abs(finite - mu) > 6 * sd))
                    if n_out:
                        outliers.append(f"{name}:{n_out}")
            row["outliers"] = ";".join(outliers)
            row["outliers_ok"] = not outliers
            # 气压层
            if task.levels:
                level_name = next(
                    (n for n in ("pressure_level", "level", "plev") if n in ds.coords or n in ds.variables),
                    None,
                )
                if level_name:
                    found = sorted({int(round(float(v))) for v in ds[level_name].values.ravel()})
                    row["levels_found"] = ";".join(str(v) for v in found)
                    row["levels_ok"] = set(task.levels).issubset(set(found))
    except Exception as exc:  # noqa: BLE001 — NetCDF 读取可能因后端/库差异抛多种异常，统一判 FAIL
        row.update({"status": "FAIL", "notes": f"无法读取为完整 NetCDF: {exc}"})
        return row

    checks = [
        row.get("time_ok"), row.get("resolution_ok"), row.get("spatial_ok"),
        row.get("variables_ok"), row.get("missing_ok"), row.get("outliers_ok"),
    ]
    if task.levels:
        checks.append(row.get("levels_ok"))
    row["status"] = "PASS" if all(bool(c) for c in checks) else "WARN"
    notes = []
    if not row.get("time_ok"):
        notes.append("时间序列不完整")
    if not row.get("spatial_ok"):
        notes.append("空间范围不完整")
    if not row.get("variables_ok"):
        notes.append(f"缺变量 {row.get('missing_variables')}")
    if not row.get("missing_ok"):
        notes.append("缺失比例偏高")
    if not row.get("outliers_ok"):
        notes.append(f"存在 6σ 离群值 {row.get('outliers')}")
    row["notes"] = "；".join(notes) if notes else "全部通过"
    return row


def check_firms_task(task: Task, settings: Settings) -> dict[str, Any]:
    """对单个 FIRMS 任务做质检。"""
    path = firms_target(task, settings)
    row: dict[str, Any] = {"task_id": task.task_id, "kind": "firms", "file": str(path), "exists": path.exists()}
    if not path.exists():
        row.update({"status": "FAIL", "notes": "文件不存在"})
        return row
    row["size_bytes"] = path.stat().st_size
    try:
        frame = pd.read_csv(path, dtype={"acq_time": "string", "confidence": "string", "daynight": "string"})
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError, OSError) as exc:
        row.update({"status": "FAIL", "notes": f"无法读取 CSV: {exc}"})
        return row

    row["row_count"] = int(len(frame))
    if frame.empty:
        row.update({"status": "PASS", "notes": "该范围无火点记录"})
        return row

    lat = pd.to_numeric(frame["latitude"], errors="coerce")
    lon = pd.to_numeric(frame["longitude"], errors="coerce")
    row["lat_min"], row["lat_max"] = float(lat.min()), float(lat.max())
    row["lon_min"], row["lon_max"] = float(lon.min()), float(lon.max())
    north, west, south, east = task.area
    row["spatial_ok"] = bool(lat.between(south, north).all() and lon.between(west, east).all())
    row["latlon_ok"] = bool(lat.notna().all() and lon.notna().all())

    dates = pd.to_datetime(frame["acq_date"], errors="coerce")
    row["date_min"], row["date_max"] = str(dates.min().date()), str(dates.max().date())
    row["date_ok"] = bool(dates.notna().all() and dates.min() >= pd.Timestamp(task.start_time[:10]) and dates.max() <= pd.Timestamp(task.end_time[:10]))

    frp = pd.to_numeric(frame.get("frp"), errors="coerce")
    row["frp_min"], row["frp_max"] = float(frp.min()), float(frp.max())
    row["frp_negative"] = int((frp < 0).sum())
    row["frp_ok"] = bool(row["frp_negative"] == 0)

    if "confidence" in frame.columns:
        row["confidence_values"] = ";".join(sorted(frame["confidence"].astype(str).unique()))
        row["confidence_ok"] = True
    duplicates = int(frame.duplicated(subset=[c for c in ("latitude", "longitude", "acq_date", "acq_time") if c in frame.columns]).sum())
    row["duplicates"] = duplicates
    row["duplicate_ok"] = duplicates == 0

    checks = [row.get("spatial_ok"), row.get("latlon_ok"), row.get("date_ok"), row.get("frp_ok"), row.get("duplicate_ok")]
    row["status"] = "PASS" if all(bool(c) for c in checks) else "WARN"
    notes = []
    if not row.get("spatial_ok"):
        notes.append("存在区域外火点")
    if not row.get("date_ok"):
        notes.append("存在时间范围外火点")
    if not row.get("frp_ok"):
        notes.append("存在负 FRP")
    if not row.get("duplicate_ok"):
        notes.append(f"存在 {duplicates} 条重复记录")
    row["notes"] = "；".join(notes) if notes else f"全部通过（{len(frame)} 条火点）"
    return row


def check_all(store: TaskStore, settings: Settings) -> list[dict[str, Any]]:
    """对所有已下载任务做质检。"""
    rows: list[dict[str, Any]] = []
    for task in store.tasks:
        if task.kind == "firms":
            rows.append(check_firms_task(task, settings))
        else:
            rows.append(check_era5_task(task, settings))
    return rows


def write_quality_report(rows: list[dict[str, Any]], settings: Settings) -> Path:
    """输出质检报告到 outputs/tables/data_quality_report.csv。"""
    settings.ensure_dirs()
    path = settings.tables_dir / "data_quality_report.csv"
    frame = pd.DataFrame(rows)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """质检结论摘要。"""
    statuses = [row.get("status", "FAIL") for row in rows]
    return {
        "total": len(rows),
        "pass": statuses.count("PASS"),
        "warn": statuses.count("WARN"),
        "fail": statuses.count("FAIL"),
        "all_ok": all(status == "PASS" for status in statuses) and bool(rows),
        "issues": [
            {"task_id": row["task_id"], "status": row["status"], "notes": row.get("notes", "")}
            for row in rows
            if row.get("status") != "PASS"
        ],
    }
