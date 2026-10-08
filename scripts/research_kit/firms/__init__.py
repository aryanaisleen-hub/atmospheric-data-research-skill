"""FIRMS 火点数据后端（重构自现有项目 download_firms.py）。"""

from __future__ import annotations

from .client import (
    FIRMS_BASE_URL,
    MissingMapKeyError,
    RealFirmsBackend,
    build_area_url,
    check_map_key,
    chunks_for_range,
    firms_target,
)

__all__ = [
    "FIRMS_BASE_URL",
    "MissingMapKeyError",
    "RealFirmsBackend",
    "build_area_url",
    "check_map_key",
    "chunks_for_range",
    "firms_target",
]
