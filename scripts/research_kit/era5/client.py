"""ERA5 / CDS 客户端实现。"""

from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path
from typing import Any

from ..settings import Settings
from ..store import Task
from ..validation import month_days, safe_publish, validate_era5_file

KIND_DIR = {"era5_single": "ERA5_single", "era5_pressure": "ERA5_pressure"}


def variable_aliases(settings: Settings, kind: str, variables: list[str]) -> dict[str, set[str]]:
    """从数据目录取出变量的 NetCDF 别名集合，用于质检。"""
    catalog_key = "single_variables" if kind == "era5_single" else "pressure_variables"
    catalog = settings.datasets.get("era5", {}).get(catalog_key, {})
    aliases: dict[str, set[str]] = {}
    for name in variables:
        cfg = catalog.get(name, {})
        names = set(cfg.get("aliases", [])) or {name}
        aliases[name] = names
    return aliases


def build_request(task: Task, settings: Settings) -> dict[str, Any]:
    """把 Task 转成 CDS 请求字典。"""
    defaults = settings.datasets.get("era5", {}).get("request_defaults", {})
    request: dict[str, Any] = {
        "product_type": list(defaults.get("product_type", ["reanalysis"])),
        "variable": list(task.variables),
        "year": [str(task.year)],
        "month": [f"{task.month:02d}"],
        "day": month_days(int(task.year), int(task.month)),
        "time": list(defaults.get("time", [f"{h:02d}:00" for h in range(24)])),
        "area": list(task.area),  # north, west, south, east
        "data_format": defaults.get("data_format", "netcdf"),
        "download_format": defaults.get("download_format", "unarchived"),
    }
    if task.levels:
        request["pressure_level"] = [str(level) for level in task.levels]
    return request


def monthly_target(task: Task, settings: Settings) -> Path:
    """月度文件落盘路径。"""
    base = settings.raw_dir / KIND_DIR[task.kind] / "_parts" / str(task.year)
    if task.kind == "era5_single":
        return base / f"single_{task.year}_{int(task.month):02d}.nc"
    variable = task.variables[0] if task.variables else "var"
    return base / f"pressure_{task.year}_{int(task.month):02d}_{variable}.nc"


def _month_bounds(task: Task) -> tuple[str, str]:
    """由任务的 start/end 推出该月的完整小时范围。"""
    return task.start_time, task.end_time


def validate_task_file(task: Task, path: Path, settings: Settings) -> tuple[bool, str]:
    """校验某个 ERA5 任务对应的文件。"""
    aliases = variable_aliases(settings, task.kind, task.variables)
    region = None
    if len(task.area) == 4:
        north, west, south, east = task.area
        region = (north, south, west, east)
    return validate_era5_file(
        path,
        start=task.start_time,
        end=task.end_time,
        variable_aliases=aliases,
        region=region,
        pressure_levels=task.levels or None,
    )


def materialize(source: Path, target: Path, task: Task, settings: Settings) -> None:
    """把 CDS 返回（可能是 ZIP，也可能直接是 NetCDF）整理为单个 NetCDF 并落盘。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    if not zipfile.is_zipfile(source):
        ok, reason = validate_task_file(task, source, settings)
        if not ok:
            raise RuntimeError(f"下载文件未通过检查，已保留：{source}\n原因：{reason}")
        safe_publish(source, target)
        return

    # 保留原始 ZIP 供排查
    source_zip = target.with_name(f"{target.name}.source.zip")
    if source != source_zip:
        if not source_zip.exists():
            safe_publish(source, source_zip)

    extract_dir = target.with_name(f"{target.stem}.source")
    extract_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(source_zip) as archive:
        members = [
            info for info in archive.infolist()
            if not info.is_dir() and info.filename.lower().endswith(".nc")
        ]
        if not members:
            raise RuntimeError(f"CDS ZIP 中没有 NetCDF 文件，已保留：{source_zip}")
        for info in members:
            if Path(info.filename).name != info.filename:
                raise RuntimeError(f"CDS ZIP 含不安全路径，拒绝解包：{info.filename}")
            out = extract_dir / info.filename
            extracted.append(out)
            if out.exists() and out.stat().st_size == info.file_size and info.file_size > 0:
                continue
            extracting = out.with_name(f"{out.name}.extracting.{os.getpid()}")
            with archive.open(info) as src, extracting.open("xb") as dst:
                shutil.copyfileobj(src, dst)
            safe_publish(extracting, out)

    building = target.with_name(f"{target.name}.building.{os.getpid()}")
    _merge_netcdf(extracted, building)
    ok, reason = validate_task_file(task, building, settings)
    if not ok:
        raise RuntimeError(f"ZIP 合并后文件未通过检查，已保留：{building}\n原因：{reason}")
    safe_publish(building, target)


def _merge_netcdf(sources: list[Path], target: Path) -> None:
    import xarray as xr

    datasets = [xr.open_dataset(path, chunks={}) for path in sources]
    try:
        combined = xr.merge(datasets, compat="no_conflicts", join="exact", combine_attrs="drop_conflicts")
        encoding = {
            name: {"zlib": True, "complevel": 4}
            for name in combined.data_vars
            if combined[name].dtype.kind in "fiu"
        }
        time_name = "valid_time" if "valid_time" in combined.coords else "time"
        combined.to_netcdf(target, engine="netcdf4", encoding=encoding, unlimited_dims=[time_name])
    finally:
        for opened in datasets:
            opened.close()


def merge_annual(tasks: list[Task], settings: Settings) -> list[Path]:
    """把同一年、同一 kind 的月度文件合并为年度文件。"""
    import xarray as xr

    outputs: list[Path] = []
    buckets: dict[tuple[str, int], list[Task]] = {}
    for task in tasks:
        if task.kind not in KIND_DIR:
            continue
        buckets.setdefault((task.kind, int(task.year)), []).append(task)

    for (kind, year), items in sorted(buckets.items()):
        months = sorted({int(task.month) for task in items})
        parts = [monthly_target(task, settings) for task in items]
        if not all(part.exists() for part in parts):
            continue
        labels = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        span = f"{labels[months[0] - 1]}-{labels[months[-1] - 1]}"
        annual = settings.raw_dir / KIND_DIR[kind] / f"ERA5_{kind.split('_')[1]}_{year}_{span}_hourly.nc"
        if annual.exists():
            outputs.append(annual)
            continue
        datasets = [xr.open_dataset(path, chunks={}) for path in parts]
        try:
            time_name = "valid_time" if "valid_time" in datasets[0].coords else "time"
            combined = xr.concat(datasets, dim=time_name).sortby(time_name)
            encoding = {
                name: {"zlib": True, "complevel": 4}
                for name in combined.data_vars
                if combined[name].dtype.kind in "fiu"
            }
            building = annual.with_name(f"{annual.name}.building.{os.getpid()}")
            combined.to_netcdf(building, engine="netcdf4", encoding=encoding, unlimited_dims=[time_name])
        finally:
            for opened in datasets:
                opened.close()
        safe_publish(building, annual)
        outputs.append(annual)
    return outputs


class RealEra5Backend:
    """真实 CDS 后端（仅在 --confirm 且已配置 .cdsapirc 时使用）。"""

    def __init__(self, settings: Settings) -> None:
        import cdsapi

        self.settings = settings
        self._client = cdsapi.Client(
            retry_max=int(settings.download.get("cds_retry_max", 2)),
            sleep_max=int(settings.download.get("cds_sleep_max", 2)),
            timeout=int(settings.download.get("cds_timeout", 30)),
        ).client

    def submit(self, task: Task) -> tuple[str, str]:
        remote = self._client.submit(task.dataset, build_request(task, self.settings))
        return remote.request_id, remote.status

    def status(self, task: Task) -> str:
        request_id = task.effective_request_id
        if not request_id:
            return "unknown"
        try:
            return self._client._retrieve_api.get_job(request_id)._json_dict.get("status", "unknown")
        except Exception:  # noqa: BLE001
            return "lookup_error"

    def download(self, task: Task, target: Path) -> None:
        request_id = task.effective_request_id
        if not request_id:
            raise RuntimeError("任务没有 request_id，无法下载")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f"{target.name}.part.{os.getpid()}")
        if temp.exists():
            raise RuntimeError(f"存在未完成的临时文件，拒绝覆盖：{temp}")
        self._client.download_results(request_id, str(temp))
        if not temp.exists() or temp.stat().st_size == 0:
            raise RuntimeError(f"下载文件缺失或为空：{temp}")
        materialize(temp, target, task, self.settings)
