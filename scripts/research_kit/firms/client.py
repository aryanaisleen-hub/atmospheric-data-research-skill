"""FIRMS Area API 客户端。

FIRMS 是"直接下载"型数据源（无排队申请），但仍作为任务纳入统一状态机：
    PENDING -> SUBMITTED -> COMPLETED -> DOWNLOADING -> DOWNLOADED

MAP_KEY 一律从环境变量读取，绝不写死、绝不写日志、绝不进报告。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from ..settings import Settings
from ..store import Task
from ..validation import safe_publish, validate_firms_csv

FIRMS_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"


class MissingMapKeyError(RuntimeError):
    """缺少 FIRMS MAP_KEY。"""


def check_map_key(settings: Settings) -> tuple[bool, str]:
    """检查 FIRMS MAP_KEY 是否已配置。返回 (是否就绪, 说明)。"""
    env_name = settings.firms_map_key_env()
    key = settings.firms_map_key()
    if key:
        # 绝不输出完整 KEY，只给出长度与前 2 位。
        return True, f"已检测到环境变量 {env_name}（长度 {len(key)}，前缀 {key[:2]}***）。"
    return False, (
        f"未检测到环境变量 {env_name}。\n"
        "FIRMS MAP_KEY 需要你自行在 FIRMS 官方网站免费申请，我无法代替你申请。\n"
        "申请地址：https://firms.modaps.eosdis.nasa.gov/api/map_key/\n"
        "配置方法（PowerShell，仅对当前窗口有效）：\n"
        f'  $env:{env_name} = "你收到的 MAP_KEY"\n'
        "不要把 KEY 写入代码、README、Git 或聊天记录。"
    )


def chunks_for_range(start: str, end: str, chunk_days: int) -> list[tuple[date, date]]:
    """把时间范围切成不超过 chunk_days 天的分块（FIRMS 单次最多 5 天）。"""
    cursor = date.fromisoformat(start[:10])
    final = date.fromisoformat(end[:10])
    chunks: list[tuple[date, date]] = []
    while cursor <= final:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), final)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def build_area_url(task: Task, settings: Settings, map_key: str) -> str:
    """构造 FIRMS Area CSV 请求 URL（含 KEY，禁止落盘/打印）。"""
    west, south, east, north = (task.area[1], task.area[2], task.area[3], task.area[0])
    start = date.fromisoformat(task.start_time[:10])
    end = date.fromisoformat(task.end_time[:10])
    day_range = (end - start).days + 1
    product = task.product or settings.datasets.get("firms", {}).get("default_product", "VIIRS_SNPP_SP")
    return f"{FIRMS_BASE_URL}/{map_key}/{product}/{west},{south},{east},{north}/{day_range}/{start.isoformat()}"


def firms_target(task: Task, settings: Settings) -> Path:
    product = task.product or "FIRMS"
    return settings.raw_dir / "FIRMS" / product / f"FIRMS_{product}_{task.year}_{int(task.month):02d}.csv"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"acq_time": "string", "confidence": "string", "daynight": "string"})


def _session():
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    retry = Retry(total=5, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = "atmospheric-data-research/1.0"
    return session


def _fetch_chunk(session: Any, url: str, columns: list[str], label: str) -> pd.DataFrame:
    try:
        response = session.get(url, timeout=(30, 300))
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        # 绝不回显 URL（含 KEY）
        raise RuntimeError(f"FIRMS 请求失败（{label}；凭据已隐藏）: {type(exc).__name__}") from None
    try:
        raw = pd.read_csv(io.StringIO(response.text), dtype={"acq_time": "string", "confidence": "string"})
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"FIRMS 返回内容不是有效 CSV（{label}；凭据已隐藏）: {exc}") from None
    missing = [column for column in columns if column not in raw.columns]
    if missing:
        raise RuntimeError(f"FIRMS 返回缺少列 {missing}（{label}；可能是 API 错误信息）")
    result = raw[columns].copy()
    if "acq_time" in result.columns:
        result["acq_time"] = result["acq_time"].astype("string").str.zfill(4)
    return result


class RealFirmsBackend:
    """真实 FIRMS 后端：直接下载并合并为月度 CSV + manifest。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        ready, message = check_map_key(settings)
        if not ready:
            raise MissingMapKeyError(message)
        self.map_key = settings.firms_map_key()
        self.chunk_days = int(settings.download.get("firms_chunk_days", 5))
        self.session = _session()

    def download(self, task: Task, target: Path) -> None:
        product = task.product or self.settings.datasets.get("firms", {}).get("default_product")
        product_cfg = self.settings.datasets.get("firms", {}).get("products", {}).get(product, {})
        columns = list(product_cfg.get("fields", self.settings.datasets.get("firms", {}).get("default_fields", [])))
        target.parent.mkdir(parents=True, exist_ok=True)
        part_dir = target.parent / "_parts" / str(task.year)
        part_dir.mkdir(parents=True, exist_ok=True)

        part_paths: list[Path] = []
        completed: list[str] = []
        for start, end in chunks_for_range(task.start_time, task.end_time, self.chunk_days):
            part = part_dir / f"{product}_{start.isoformat()}_{end.isoformat()}.csv"
            part_paths.append(part)
            if part.exists():
                completed.append(f"{start.isoformat()}_{end.isoformat()}")
                continue
            # 按分块重算日期范围（单块最多 firms_chunk_days 天）
            day_range = (end - start).days + 1
            west, south, east, north = (task.area[1], task.area[2], task.area[3], task.area[0])
            url = f"{FIRMS_BASE_URL}/{self.map_key}/{product}/{west},{south},{east},{north}/{day_range}/{start.isoformat()}"
            frame = _fetch_chunk(self.session, url, columns, f"{start}~{end}")
            temp = part.with_name(f"{part.name}.part.{os.getpid()}")
            frame.to_csv(temp, index=False)
            safe_publish(temp, part)
            completed.append(f"{start.isoformat()}_{end.isoformat()}")

        combined = pd.concat([_read_csv(path) for path in part_paths], ignore_index=True)
        combined = combined.drop_duplicates().sort_values(
            [c for c in ("acq_date", "acq_time", "latitude", "longitude") if c in combined.columns]
        )
        building = target.with_name(f"{target.name}.building.{os.getpid()}")
        combined.to_csv(building, index=False)
        ok, reason = validate_firms_csv(
            building, columns=columns, start=task.start_time[:10], end=task.end_time[:10],
            region=(task.area[1], task.area[2], task.area[3], task.area[0]),
        )
        if not ok:
            raise RuntimeError(f"合并后 CSV 未通过检查，已保留：{building}\n原因：{reason}")
        safe_publish(building, target)

        manifest = {
            "source": product,
            "year": task.year,
            "month": task.month,
            "request_area_north_west_south_east": list(task.area),
            "date_range": [task.start_time[:10], task.end_time[:10]],
            "completed_chunks": completed,
            "row_count": int(len(combined)),
            "sha256": _sha256(target),
            "created_at": datetime.utcnow().isoformat(),
        }
        manifest_path = target.with_suffix(".manifest.json")
        temp_manifest = manifest_path.with_name(f"{manifest_path.name}.building.{os.getpid()}")
        temp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if manifest_path.exists():
            manifest_path.unlink()
        safe_publish(temp_manifest, manifest_path)
