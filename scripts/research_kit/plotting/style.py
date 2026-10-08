"""绘图样式与保存工具（matplotlib，论文级）。

不使用 seaborn。默认 300 dpi，输出 PNG，并尽可能输出 PDF/SVG。
cartopy 为可选依赖：检测到则用于地图投影，否则降级为经纬度坐标轴。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

from ..settings import Settings  # noqa: E402


def has_cartopy() -> bool:
    return importlib.util.find_spec("cartopy") is not None


def setup_style(settings: Settings, *, robust: bool = False) -> None:
    """设置全局绘图样式。

    ``robust=True`` 用于自动读图失败后的重绘：加大字号、加宽留白（修复边缘裁切）、
    加粗线宽与网格，使重绘结果与上一版**确实不同**，从而具备修复能力。
    """
    # 用字体族列表启用 matplotlib 的逐字形回退：西文用 Arial，中文自动回退到中文字体。
    fonts = list(settings.plotting.get("font_family", ["Arial", "DejaVu Sans"]))
    base = {
        "figure.dpi": 120,
        "savefig.dpi": int(settings.plotting.get("dpi", 300)),
        "savefig.bbox": "tight",
        "font.family": fonts,
        "font.sans-serif": fonts,
        "axes.unicode_minus": False,
        "axes.titlesize": 12,
        "axes.labelsize": 10.5,
        "xtick.labelsize": 9.5,
        "ytick.labelsize": 9.5,
        "legend.fontsize": 9,
        "legend.frameon": False,
        "axes.grid": True,
        "grid.alpha": 0.28,
        "grid.linewidth": 0.6,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
    }
    if robust:
        scale = 1.15
        base.update(
            {
                "savefig.pad_inches": 0.35,  # 修复"边缘内容被裁切"
                "axes.titlesize": round(12 * scale, 1),
                "axes.labelsize": round(10.5 * scale, 1),
                "xtick.labelsize": round(9.5 * scale, 1),
                "ytick.labelsize": round(9.5 * scale, 1),
                "legend.fontsize": round(9 * scale, 1),
                "grid.linewidth": 0.8,
                "axes.linewidth": 1.0,
                "lines.linewidth": 1.8,
            }
        )
    else:
        base["savefig.pad_inches"] = 0.1
    plt.rcParams.update(base)


def new_figure(width: float = 7.2, height: float = 4.4):
    """新建单面板图，返回 (fig, ax)。"""
    fig, ax = plt.subplots(figsize=(width, height))
    return fig, ax


def save_figure(fig, stem: Path, settings: Settings) -> dict[str, str]:
    """保存为多种格式，返回 {格式: 路径}。"""
    stem.parent.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    for fmt in settings.plotting.get("formats", ["png"]):
        path = stem.with_suffix(f".{fmt}")
        fig.savefig(path, format=fmt)
        outputs[fmt] = str(path)
    plt.close(fig)
    return outputs


def annotate_units(ax, label: str, unit: str) -> None:
    ax.set_ylabel(f"{label} ({unit})" if unit else label)
