"""分析方案推荐与统计。"""

from __future__ import annotations

from .metrics import lag_correlation, linear_fit, pearson, spearman
from .options import available_options, recommend_options, render_menu

__all__ = [
    "available_options",
    "recommend_options",
    "render_menu",
    "pearson",
    "spearman",
    "linear_fit",
    "lag_correlation",
]
