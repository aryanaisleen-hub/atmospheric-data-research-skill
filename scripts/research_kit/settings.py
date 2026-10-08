"""配置加载与工作区路径解析。

所有运行参数来自 config/*.yaml；密钥一律来自环境变量。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = SKILL_ROOT / "config"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"缺少配置文件：{path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


@dataclass(frozen=True)
class Region:
    """研究区域。统一记录 north/south/east/west（度）。"""

    north: float
    south: float
    west: float
    east: float

    def as_cds_area(self) -> list[float]:
        """CDS 顺序：north, west, south, east。"""
        return [self.north, self.west, self.south, self.east]

    def as_bbox_wsn(self) -> tuple[float, float, float, float]:
        """FIRMS / 绘图顺序：west, south, east, north。"""
        return (self.west, self.south, self.east, self.north)

    def as_dict(self) -> dict[str, float]:
        return {"north": self.north, "south": self.south, "west": self.west, "east": self.east}

    def contains(self, lat: float, lon: float) -> bool:
        return self.south <= lat <= self.north and self.west <= lon <= self.east


@dataclass
class Settings:
    """技能设置。"""

    skill_root: Path
    config_dir: Path
    raw: dict[str, Any]
    datasets: dict[str, Any]
    variables: dict[str, Any]
    catalog: dict[str, Any]
    workspace_root: Path
    raw_dir: Path
    processed_dir: Path
    results_dir: Path
    figures_dir: Path
    tables_dir: Path
    logs_dir: Path
    requests_file: Path
    download: dict[str, Any] = field(default_factory=dict)
    region_defaults: dict[str, Any] = field(default_factory=dict)
    plotting: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)
    assets: dict[str, Any] = field(default_factory=dict)

    def boundaries_path(self) -> Path | None:
        """地图边界矢量路径（随 Skill 附带）。"""
        value = self.assets.get("boundaries")
        if not value:
            return None
        path = Path(value)
        if not path.is_absolute():
            path = self.skill_root / path
        return path if path.exists() else None

    # ---- 便捷访问 ----
    def ensure_dirs(self) -> None:
        for folder in (
            self.raw_dir,
            self.processed_dir,
            self.results_dir,
            self.figures_dir,
            self.tables_dir,
            self.logs_dir,
        ):
            folder.mkdir(parents=True, exist_ok=True)

    def path(self, *parts: str) -> Path:
        return self.workspace_root.joinpath(*parts)

    def variable_meta(self, name: str) -> dict[str, Any]:
        return self.variables.get("variables", {}).get(name, {})

    def firms_map_key_env(self) -> str:
        return str(self.download.get("firms_map_key_env", "FIRMS_MAP_KEY"))

    def firms_map_key(self) -> str | None:
        """从环境变量读取 FIRMS MAP_KEY。绝不写入代码/日志。"""
        return os.environ.get(self.firms_map_key_env()) or None

    def default_pressure_levels(self) -> list[int]:
        return list(self.datasets.get("era5", {}).get("default_pressure_levels", [850, 700, 600, 500]))


def load_settings(config_dir: Path | None = None, workspace_root: str | Path | None = None) -> Settings:
    """加载全部配置。

    workspace_root 可覆盖 settings.yaml 里的 workspace.root（便于测试用沙箱目录）。
    """
    cdir = Path(config_dir) if config_dir else CONFIG_DIR
    raw = _read_yaml(cdir / "settings.yaml")
    datasets = _read_yaml(cdir / "datasets.yaml")
    variables = _read_yaml(cdir / "variables.yaml")
    catalog = _read_yaml(cdir / "analysis_catalog.yaml")

    ws = raw.get("workspace", {})
    root = Path(workspace_root) if workspace_root else Path(ws["root"])

    def sub(key: str, default: str) -> Path:
        value = ws.get(key, default)
        return Path(value) if Path(value).is_absolute() else root / value

    return Settings(
        skill_root=SKILL_ROOT,
        config_dir=cdir,
        raw=raw,
        datasets=datasets,
        variables=variables,
        catalog=catalog,
        workspace_root=root,
        raw_dir=sub("raw_dir", "data"),
        processed_dir=sub("processed_dir", "processed"),
        results_dir=sub("results_dir", "results"),
        figures_dir=sub("figures_dir", "outputs/figures"),
        tables_dir=sub("tables_dir", "outputs/tables"),
        logs_dir=sub("logs_dir", "logs"),
        requests_file=sub("requests_file", "logs/data_requests.json"),
        download=raw.get("download", {}),
        region_defaults=raw.get("region_defaults", {}),
        plotting=raw.get("plotting", {}),
        review=raw.get("review", {}),
        report=raw.get("report", {}),
        assets=raw.get("assets", {}),
    )
