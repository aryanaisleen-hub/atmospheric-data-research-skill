"""极简 Shapefile 读取与国界绘制（纯 Python，无 geopandas/cartopy 依赖）。

只解析 ESRI Shapefile 的多边形/折线记录，足以绘制 Natural Earth 110m 国界。
这样在没有 cartopy 的环境下，地图仍具有可读的地理背景。
"""

from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path

import numpy as np


def _read_shapefile(path: Path) -> list[tuple[np.ndarray, np.ndarray]]:
    """读取 .shp，返回若干 (lon, lat) 顶点数组。"""
    rings: list[tuple[np.ndarray, np.ndarray]] = []
    data = path.read_bytes()
    if len(data) < 100:
        return rings
    offset = 100
    while offset + 8 <= len(data):
        _, content_length = struct.unpack(">ii", data[offset:offset + 8])
        offset += 8
        content_end = offset + content_length * 2
        if content_end > len(data):
            break
        shape_type = struct.unpack("<i", data[offset:offset + 4])[0]
        if shape_type in (3, 5):  # PolyLine / Polygon
            cursor = offset + 4
            cursor += 32  # 跳过 bbox（4 个 double）
            num_parts, num_points = struct.unpack("<ii", data[cursor:cursor + 8])
            cursor += 8
            parts = struct.unpack(f"<{num_parts}i", data[cursor:cursor + num_parts * 4])
            cursor += num_parts * 4
            points = np.frombuffer(
                data, dtype="<f8", count=num_points * 2, offset=cursor
            ).reshape(num_points, 2)
            boundaries = list(parts) + [num_points]
            for index in range(num_parts):
                segment = points[boundaries[index]:boundaries[index + 1]]
                if segment.shape[0] >= 2:
                    rings.append((segment[:, 0], segment[:, 1]))
        offset = content_end
    return rings


@lru_cache(maxsize=4)
def load_boundaries(path_str: str) -> tuple[tuple[np.ndarray, np.ndarray], ...]:
    path = Path(path_str)
    if not path.exists():
        return tuple()
    return tuple(_read_shapefile(path))


def draw_boundaries(ax, settings, region, *, color: str = "#4F5660", linewidth: float = 0.8) -> bool:
    """在经纬度坐标轴上绘制国界。返回是否绘制成功。"""
    path = settings.boundaries_path()
    if path is None:
        return False
    margin = 10.0
    west, south = region.west - margin, region.south - margin
    east, north = region.east + margin, region.north + margin
    drawn = False
    for lon, lat in load_boundaries(str(path)):
        if lon.max() < west or lon.min() > east or lat.max() < south or lat.min() > north:
            continue
        ax.plot(lon, lat, color=color, linewidth=linewidth, zorder=1)
        drawn = True
    return drawn
