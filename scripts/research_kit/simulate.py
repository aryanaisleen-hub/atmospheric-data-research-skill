"""模拟后端：不访问真实 API，用于测试与完整流程演示。

生成物理上自洽的合成数据：
  * ERA5 单层/气压层：温度、降水、边界层、风场等具有日变化与空间结构
  * FIRMS：源区火点，其强度与观测 PM1 存在滞后相关
  * 站点观测：阿里站 PM1，在污染期出现峰值

模拟后端严格实现与真实后端相同的接口，因此编排层无需区分。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .settings import Settings
from .store import Task
from .validation import safe_publish

# 模拟源区（印度西北部/恒河平原方向）与站点
SOURCE_BOX = {"lat": (25.0, 35.0), "lon": (70.0, 80.0)}
STATION = {"lat": 33.39, "lon": 79.70, "name": "Ngari / Ali"}


def _rng(task: Task, salt: int = 0) -> np.random.Generator:
    seed = int(hashlib.sha256(f"{task.task_id}:{salt}".encode()).hexdigest()[:8], 16)
    return np.random.default_rng(seed)


def _daily_fire_forcing(start: str, end: str) -> pd.Series:
    """构造一条"火点活动"日序列：6 月上旬出现峰值。"""
    index = pd.date_range(start, end, freq="D")
    day_of_month = index.day.to_numpy(dtype=float)
    # 峰值在每月 6 日附近
    peak = np.exp(-((day_of_month - 6.0) ** 2) / (2 * 3.0 ** 2))
    baseline = 0.25
    signal = baseline + 0.75 * peak
    noise = np.random.default_rng(20220601).normal(0, 0.06, size=len(index))
    return pd.Series(np.clip(signal + noise, 0.02, None), index=index, name="fire_forcing")


def _pm1_forcing(start: str, end: str, lag_days: int = 2) -> pd.Series:
    """PM1 日序列：由火点活动滞后驱动 + 本地噪声。"""
    fire = _daily_fire_forcing(start, end)
    shifted = fire.shift(lag_days).fillna(fire.iloc[0] * 0.5)
    noise = pd.Series(
        np.random.default_rng(79).normal(0, 0.05, size=len(fire)), index=fire.index
    )
    pm1 = 2.0 + 10.0 * shifted.to_numpy() + noise.to_numpy()
    return pd.Series(np.clip(pm1, 0.5, None), index=fire.index, name="PM1")


class SimulatedEra5Backend:
    """模拟 CDS 后端。"""

    def __init__(self, settings: Settings, complete_after: int = 1) -> None:
        self.settings = settings
        self.complete_after = complete_after
        self._checks: dict[str, int] = {}
        self._counter = 0

    def submit(self, task: Task) -> tuple[str, str]:
        self._counter += 1
        request_id = f"sim-{self._counter:04d}-{task.task_id}"
        self._checks[task.task_id] = 0
        return request_id, "accepted"

    def status(self, task: Task) -> str:
        seen = self._checks.get(task.task_id, 0) + 1
        self._checks[task.task_id] = seen
        # 让部分任务稍慢完成，体现排队/运行中的差异
        threshold = self.complete_after + (1 if hash(task.task_id) % 2 else 0)
        if seen >= threshold:
            return "successful"
        if seen == 1:
            return "queued"
        return "running"

    def download(self, task: Task, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f"{target.name}.part.{os.getpid()}")
        _write_simulated_era5(task, temp, self.settings)
        safe_publish(temp, target)


def _write_simulated_era5(task: Task, path: Path, settings: Settings) -> None:
    import xarray as xr

    rng = _rng(task)
    times = pd.date_range(task.start_time, task.end_time, freq="h")
    area = task.area  # north, west, south, east
    north, west, south, east = area
    grid_step = 0.5
    lat = np.arange(south, north + 0.001, grid_step)
    lon = np.arange(west, east + 0.001, grid_step)
    hour = times.hour.to_numpy(dtype=float)
    day = times.dayofyear.to_numpy(dtype=float)

    data_vars: dict[str, Any] = {}
    coords: dict[str, Any] = {"valid_time": ("valid_time", times.values), "latitude": lat, "longitude": lon}

    # 日变化因子（0-1）
    diurnal = (np.sin((hour - 6) / 24 * 2 * np.pi) + 1) / 2

    if task.kind == "era5_single":
        base_shape = (len(times), len(lat), len(lon))
        t2m = 285.0 + 8.0 * np.sin((day - 80) / 365 * 2 * np.pi)[:, None, None] \
            + 6.0 * diurnal[:, None, None] - 0.6 * np.arange(len(lat))[None, :, None]
        t2m = t2m + rng.normal(0, 0.3, size=base_shape)
        # 6 月上旬出现一次降水事件
        rain_days = {5, 6, 9, 12}
        tp = np.zeros(base_shape)
        for i, ts in enumerate(times):
            if ts.day in rain_days and 8 <= ts.hour <= 14:
                tp[i] = 0.0015 * (1 + rng.random())
        blh = (300 + 900 * diurnal)[:, None, None] + rng.normal(0, 25, size=base_shape)
        u10 = 2.5 + 1.5 * np.sin(day / 30)[:, None, None] + rng.normal(0, 0.4, size=base_shape)
        v10 = 0.8 + rng.normal(0, 0.4, size=base_shape)
        sp = 60000.0 - 300.0 * np.arange(len(lat))[None, :, None] + rng.normal(0, 15, size=base_shape)
        generators = {
            "t2m": (t2m, {"units": "K", "long_name": "2 metre temperature"}),
            "tp": (np.clip(tp, 0, None), {"units": "m", "long_name": "Total precipitation"}),
            "blh": (np.clip(blh, 30, None), {"units": "m", "long_name": "Boundary layer height"}),
            "u10": (u10, {"units": "m s-1", "long_name": "10 metre U wind component"}),
            "v10": (v10, {"units": "m s-1", "long_name": "10 metre V wind component"}),
            "sp": (sp, {"units": "Pa", "long_name": "Surface pressure"}),
        }
        wanted = [v for v in task.variables if v in generators] or list(generators)
        for name in wanted:
            values, attrs = generators[name]
            data_vars[name] = (("valid_time", "latitude", "longitude"), values.astype("float32"), attrs)
    else:
        levels = task.levels or [850]
        base_shape = (len(times), len(levels), len(lat), len(lon))
        level_factor = np.array(levels, dtype=float)[:, None, None] / 850.0
        generators = {
            "u": (4.0 + 3.0 * np.sin(day / 20)[:, None, None, None] * level_factor[None]
                  + rng.normal(0, 0.5, size=base_shape), {"units": "m s-1", "long_name": "U component of wind"}),
            "v": (1.0 + 1.5 * np.cos(day / 25)[:, None, None, None] * level_factor[None]
                  + rng.normal(0, 0.5, size=base_shape), {"units": "m s-1", "long_name": "V component of wind"}),
            "z": ((1200.0 * np.array(levels, dtype=float))[None, :, None, None]
                  + rng.normal(0, 30, size=base_shape), {"units": "m2 s-2", "long_name": "Geopotential"}),
            "w": (rng.normal(0, 0.05, size=base_shape), {"units": "Pa s-1", "long_name": "Vertical velocity"}),
        }
        coords["pressure_level"] = ("pressure_level", np.array(levels, dtype="int32"))
        dims = ("valid_time", "pressure_level", "latitude", "longitude")
        wanted = [v for v in task.variables if v in generators] or list(generators)
        for name in wanted:
            values, attrs = generators[name]
            data_vars[name] = (dims, values.astype("float32"), attrs)

    ds = xr.Dataset(data_vars=data_vars, coords=coords)
    ds.attrs["source"] = "SIMULATED (no real API used)"
    encoding = {name: {"zlib": True, "complevel": 1} for name in ds.data_vars}
    ds.to_netcdf(path, engine="netcdf4", encoding=encoding)


class SimulatedFirmsBackend:
    """模拟 FIRMS 后端：即时完成（无排队）。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def submit(self, task: Task) -> tuple[str, str]:
        return f"sim-firms-{task.task_id}", "successful"

    def status(self, task: Task) -> str:
        return "successful"

    def download(self, task: Task, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        product = task.product or "VIIRS_SNPP_SP"
        product_cfg = self.settings.datasets.get("firms", {}).get("products", {}).get(product, {})
        columns = list(product_cfg.get("fields", self.settings.datasets.get("firms", {}).get("default_fields", [])))
        frame = _simulate_fire(task, columns)
        temp = target.with_name(f"{target.name}.part.{os.getpid()}")
        frame.to_csv(temp, index=False)
        safe_publish(temp, target)

        manifest = {
            "source": product,
            "year": task.year,
            "month": task.month,
            "row_count": int(len(frame)),
            "simulated": True,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
        manifest_path = target.with_suffix(".manifest.json")
        if manifest_path.exists():
            manifest_path.unlink()
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _simulate_fire(task: Task, columns: list[str]) -> pd.DataFrame:
    rng = _rng(task, salt=7)
    days = pd.date_range(task.start_time[:10], task.end_time[:10], freq="D")
    fire_forcing = _daily_fire_forcing(task.start_time[:10], task.end_time[:10])
    rows: list[dict[str, Any]] = []
    for ts in days:
        # 火点数由日强度决定，并叠加日内随机
        intensity = float(fire_forcing.loc[ts])
        count = int(rng.poisson(max(intensity, 0.05) * 45))
        if count == 0:
            continue
        lat = rng.uniform(SOURCE_BOX["lat"][0], SOURCE_BOX["lat"][1], count)
        lon = rng.uniform(SOURCE_BOX["lon"][0], SOURCE_BOX["lon"][1], count)
        frp = np.clip(rng.gamma(2.0, 4.0 * intensity + 0.8, count), 0.3, None)
        confidence = rng.choice(["l", "n", "h"], count, p=[0.2, 0.6, 0.2])
        daynight = rng.choice(["D", "N"], count, p=[0.75, 0.25])
        minute = rng.integers(0, 60, count)
        hour = np.where(daynight == "D", rng.integers(5, 12, count), rng.integers(18, 24, count))
        for i in range(count):
            row = {
                "latitude": round(float(lat[i]), 4),
                "longitude": round(float(lon[i]), 4),
                "acq_date": ts.strftime("%Y-%m-%d"),
                "acq_time": f"{int(hour[i]):02d}{int(minute[i]):02d}",
                "frp": round(float(frp[i]), 2),
                "confidence": str(confidence[i]),
                "daynight": str(daynight[i]),
                "satellite": "N20",
            }
            rows.append(row)
    frame = pd.DataFrame(rows)
    ordered = [c for c in columns if c in frame.columns]
    return frame[ordered] if ordered else frame


def write_simulated_station(
    path: Path,
    start: str,
    end: str,
    *,
    lag_days: int = 2,
) -> Path:
    """生成阿里站小时 PM1 观测（供演示/测试）。"""
    daily = _pm1_forcing(start, end, lag_days=lag_days)
    index = pd.date_range(f"{start} 00:00", f"{end} 23:00", freq="h")
    diurnal = 1.0 + 0.18 * np.sin((index.hour.to_numpy() - 5) / 24 * 2 * np.pi)
    base = np.array([daily.get(pd.Timestamp(ts).normalize(), np.nan) for ts in index])
    noise = np.random.default_rng(2022).normal(0, 0.35, size=len(index))
    pm1 = np.clip(base * diurnal + noise, 0.2, None)
    frame = pd.DataFrame({"datetime": index, "PM1": pm1.round(3)})
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path


def make_simulated_backends(settings: Settings, complete_after: int = 1) -> dict[str, Any]:
    return {
        "era5": SimulatedEra5Backend(settings, complete_after=complete_after),
        "firms": SimulatedFirmsBackend(settings),
    }
