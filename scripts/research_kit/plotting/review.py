"""自动读图审查。

绘制完成后必须"实际查看"图片，而不是仅根据代码判断。
本模块打开 PNG，检查：是否生成、是否空白、内容是否被裁切、是否多色（图例/色标存在）、
尺寸是否合理，并支持最多 N 次自动修正重绘。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..settings import Settings
from .figures import FigureResult, RenderContext, render_option


@dataclass
class Review:
    ok: bool
    issues: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)


def review_figure(png_path: str | Path, settings: Settings) -> Review:
    """打开并检查一张 PNG。"""
    path = Path(png_path)
    issues: list[str] = []
    metrics: dict[str, float] = {}

    if not path.exists() or path.stat().st_size == 0:
        return Review(ok=False, issues=["图片未成功生成或为空文件"], metrics=metrics)

    try:
        from PIL import Image

        with Image.open(path) as image:
            image = image.convert("L")
            arr = np.asarray(image, dtype=np.float32) / 255.0
    except Exception as exc:  # noqa: BLE001
        return Review(ok=False, issues=[f"无法打开图片：{exc}"], metrics=metrics)

    height, width = arr.shape
    metrics["width"] = float(width)
    metrics["height"] = float(height)

    # 空白检测：近白像素占比
    blank_ratio = float((arr > 0.98).mean())
    metrics["blank_ratio"] = blank_ratio
    threshold = float(settings.review.get("blank_pixel_threshold", 0.995))
    if blank_ratio > threshold:
        issues.append(f"疑似空白图（近白像素占比 {blank_ratio:.3f}）")

    # 墨迹占比（非背景内容）
    ink_ratio = float((arr < 0.85).mean())
    metrics["ink_ratio"] = ink_ratio
    min_ink = float(settings.review.get("ink_ratio_min", 0.005))
    if ink_ratio < min_ink:
        issues.append(f"图面内容过少（墨迹占比 {ink_ratio:.4f}）")

    # 尺寸
    if width < 400 or height < 300:
        issues.append(f"图片尺寸偏小（{width}×{height}）")

    # 裁切检测：四条边框应为背景色
    border = 2
    edges = {
        "上": arr[:border, :], "下": arr[-border:, :], "左": arr[:, :border], "右": arr[:, -border:],
    }
    for name, strip in edges.items():
        if float((strip < 0.6).mean()) > 0.02:
            issues.append(f"{name}边缘存在内容，可能被裁切")

    # 内容多样性（图例/色标通常带来多种灰阶）
    metrics["unique_levels"] = float(len(np.unique((arr * 255).astype(np.uint8))))
    if metrics["unique_levels"] < 8:
        issues.append("灰度层次过少，图例/色标可能缺失")

    return Review(ok=not issues, issues=issues, metrics=metrics)


def review_and_fix(
    ctx: RenderContext,
    option: dict,
    result: FigureResult,
    settings: Settings,
) -> FigureResult:
    """审查结果图；若发现明显问题则自动修正重绘，最多 max_auto_fix 次。"""
    max_fix = int(settings.review.get("max_auto_fix", 3))
    attempt = 0
    current = result
    while attempt <= max_fix:
        review = review_figure(current.png, settings)
        current.review_notes = review.issues
        current.stats.setdefault("review", {}).update(review.metrics)  # type: ignore[arg-type]
        if review.ok:
            current.status = "ok"
            return current
        if attempt == max_fix:
            current.status = "needs_attention"
            current.review_notes = review.issues + [f"已自动修正 {max_fix} 次仍有问题，请人工检查。"]
            return current
        # 自动修正：以"稳健样式"重绘（加大字号与留白），并记录
        attempt += 1
        current = render_option(ctx, option, attempt=attempt)
        current.review_notes = [f"第 {attempt} 次自动修正重绘（稳健样式）"] + review.issues
    return current
