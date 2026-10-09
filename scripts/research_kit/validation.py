"""通用校验与安全落盘工具（重构自现有项目 scripts/common.py，已参数化）。

设计原则：
  * 绝不覆盖已有文件：安全落盘用 os.replace，且目标已存在时拒绝。
  * 校验失败时保留文件供排查，不自动删除。
  * 时间统一 UTC；经纬度统一 latitude/longitude；区域统一 north/south/east/west。
"""

from __future__ import annotations

import calendar
import os
from pathlib import Path

import pandas as pd


def month_days(year: int, month: int) -> list[str]:
    return [f"{day:02d}" for day in range(1, calendar.monthrange(year, month)[1] + 1)]


def expected_hourly_index(start: str, end: str) -> pd.DatetimeIndex:
    """[start, end]（含）之间的小时序列（UTC）。"""
    return pd.date_range(pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(hours=23), freq="h")


def _coord_name(ds: object, candidates: tuple[str, ...]) -> str:
    for name in candidates:
        if name in ds.coords or name in ds.variables:  # type: ignore[attr-defined]
            return name
    raise ValueError(f"找不到坐标：候选名为 {candidates}")


def safe_publish(temp_path: Path, final_path: Path) -> None:
    """把临时文件安全落盘；目标已存在则拒绝覆盖。"""
    if final_path.exists():
        raise FileExistsError(f"目标已存在，拒绝覆盖：{final_path}")
    os.replace(temp_path, final_path)


def refuse_invalid_existing(path: Path, reason: str) -> None:
    raise RuntimeError(
        f"已有文件未通过检查，为避免覆盖已停止：{path}\n"
        f"原因：{reason}\n请手动检查并移动或改名后再运行；脚本不会自动删除它。"
    )


def validate_era5_file(
    path: Path,
    *,
    start: str | None = None,
    end: str | None = None,
    variable_aliases: dict[str, set[str]] | None = None,
    region: tuple[float, float, float, float] | None = None,   # north, south, west, east
    pressure_levels: list[int] | None = None,
    check_time: bool = True,
) -> tuple[bool, str]:
    """校验 ERA5 NetCDF。返回 (是否通过, 原因)。"""
    if not path.exists():
        return False, "文件不存在"
    if path.stat().st_size == 0:
        return False, "文件大小为 0"
    try:
        import xarray as xr

        with xr.open_dataset(path, decode_times=True) as ds:
            if check_time and start and end:
                time_name = _coord_name(ds, ("valid_time", "time"))
                actual = pd.DatetimeIndex(pd.to_datetime(ds[time_name].values)).tz_localize(None)
                expected = expected_hourly_index(start, end)
                if len(actual) != len(expected) or not actual.equals(expected):
                    missing = len(expected.difference(actual))
                    extra = len(actual.difference(expected))
                    return False, f"小时序列不完整（缺 {missing}，多 {extra}）"

            present = set(ds.data_vars)
            if variable_aliases:
                missing_vars = [
                    label for label, names in variable_aliases.items() if not (present & names)
                ]
                if missing_vars:
                    return False, f"缺少变量: {', '.join(missing_vars)}；实际变量: {sorted(present)}"

            if region is not None:
                north, south, west, east = region
                lat_name = _coord_name(ds, ("latitude", "lat"))
                lon_name = _coord_name(ds, ("longitude", "lon"))
                lat_min, lat_max = float(ds[lat_name].min()), float(ds[lat_name].max())
                lon_min, lon_max = float(ds[lon_name].min()), float(ds[lon_name].max())
                if lat_min > south + 0.001 or lat_max < north - 0.001 or lon_min > west + 0.001 or lon_max < east - 0.001:
                    return False, (
                        f"空间范围不完整: lat={lat_min:g}..{lat_max:g}, lon={lon_min:g}..{lon_max:g}"
                    )

            if pressure_levels:
                level_name = _coord_name(ds, ("pressure_level", "level", "plev"))
                found = {int(round(float(v))) for v in ds[level_name].values.ravel()}
                wanted = set(pressure_levels)
                if not wanted.issubset(found):
                    return False, f"压力层不符: 期望包含 {sorted(wanted)}, 实际 {sorted(found)}"
    except Exception as exc:  # noqa: BLE001 — NetCDF 读取可能因后端/库差异抛多种异常，统一返回不通过
        return False, f"无法读取为完整 NetCDF: {exc}"
    return True, "完整"


def validate_firms_csv(
    path: Path,
    *,
    columns: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    region: tuple[float, float, float, float] | None = None,   # west, south, east, north
) -> tuple[bool, str]:
    """校验 FIRMS 火点 CSV。返回 (是否通过, 原因)。"""
    if not path.exists():
        return False, "文件不存在"
    if path.stat().st_size == 0:
        return False, "文件大小为 0"
    try:
        frame = pd.read_csv(
            path, dtype={"acq_time": "string", "confidence": "string", "daynight": "string"}
        )
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError, OSError) as exc:
        return False, f"无法读取 CSV: {exc}"
    if columns and list(frame.columns) != list(columns):
        return False, f"列名不符；实际为 {list(frame.columns)}"
    if frame.empty:
        return True, "完整（该范围无火点记录）"
    if "acq_date" in frame.columns:
        parsed = pd.to_datetime(frame["acq_date"], errors="coerce")
        if parsed.isna().any():
            return False, "acq_date 中有无法解析的日期"
        if start and (parsed < pd.Timestamp(start)).any():
            return False, f"包含早于 {start} 的记录"
        if end and (parsed > pd.Timestamp(end)).any():
            return False, f"包含晚于 {end} 的记录"
    if region is not None:
        west, south, east, north = region
        lat = pd.to_numeric(frame["latitude"], errors="coerce")
        lon = pd.to_numeric(frame["longitude"], errors="coerce")
        if lat.isna().any() or lon.isna().any():
            return False, "经纬度中有非数字值"
        if (~lat.between(south, north)).any() or (~lon.between(west, east)).any():
            return False, "包含目标区域外的记录"
    return True, f"完整（{len(frame)} 条火点）"
