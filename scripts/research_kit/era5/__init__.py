"""ERA5 数据后端（重构自现有项目 era5_workflow.py / download_era5_*.py）。

职责：
  * 请求生成（request generation）
  * 请求校验（request validation）
  * 请求提交（submission）
  * 状态查询（status）
  * 下载（download，含 CDS ZIP 解包与 NetCDF 合并）
  * 重试（retry，由编排层调用）
"""

from __future__ import annotations

from .client import (
    RealEra5Backend,
    build_request,
    merge_annual,
    monthly_target,
    variable_aliases,
)

__all__ = [
    "RealEra5Backend",
    "build_request",
    "merge_annual",
    "monthly_target",
    "variable_aliases",
]
