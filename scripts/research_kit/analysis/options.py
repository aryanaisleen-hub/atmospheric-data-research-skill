"""分析/绘图方案推荐。

AI 先分析"当前数据具备什么能力"，再据此推荐图形选项，而不是把所有图都画出来。
"""

from __future__ import annotations

from typing import Any

from ..planning import StudySpec
from ..prepare import PreparedData
from ..settings import Settings


def _satisfies(requires: list[str], capabilities: set[str]) -> bool:
    for requirement in requires:
        if requirement in capabilities:
            continue
        # era5_pressure:850 也可由 era5_pressure 泛化满足
        if ":" in requirement and requirement.split(":")[0] in capabilities:
            continue
        return False
    return True


def _catalog_options(settings: Settings) -> list[dict[str, Any]]:
    return list(settings.catalog.get("options", []))


def available_options(prepared: PreparedData, settings: Settings) -> list[dict[str, Any]]:
    """当前数据能满足的全部方案。"""
    capabilities = prepared.capabilities
    return [option for option in _catalog_options(settings) if _satisfies(option["requires"], capabilities)]


def recommend_options(
    prepared: PreparedData,
    spec: StudySpec,
    settings: Settings,
    top: int = 5,
) -> list[dict[str, Any]]:
    """按与研究问题的匹配度排序，推荐最合适的若干方案。"""
    options = available_options(prepared, settings)
    question = (spec.research_question + " " + spec.title).lower()
    scored: list[tuple[float, dict[str, Any]]] = []
    for option in options:
        score = 0.0
        for keyword in option.get("keywords", []):
            if str(keyword).lower() in question:
                score += 2.0
        # 综合图与滞后分析对"关系/影响"类问题更有价值
        if any(word in question for word in ("关系", "影响", "滞后", "输送", "relate", "impact", "lag", "transport")):
            if option["group"] in ("E", "F", "D"):
                score += 1.0
        # 时间序列作为基础图轻微加权
        if option["group"] == "A":
            score += 0.5
        scored.append((score, option))
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    recommended = [option for score, option in scored[:top]]
    return recommended


def render_menu(options: list[dict[str, Any]], settings: Settings, recommended_ids: set[int] | None = None) -> str:
    """渲染 A–F 菜单供用户选择。"""
    groups = settings.catalog.get("groups", {})
    recommended_ids = recommended_ids or set()
    lines: list[str] = ["当前数据可支持以下分析与绘图方案：", ""]
    for key in ("A", "B", "C", "D", "E", "F"):
        items = [option for option in options if option["group"] == key]
        if not items:
            continue
        meta = groups.get(key, {})
        lines.append(f"### {key}. {meta.get('title', key)}")
        lines.append(f"    {meta.get('desc', '')}")
        for option in items:
            mark = " ★推荐" if option["id"] in recommended_ids else ""
            lines.append(f"  {option['id']:>2}. {option['title']}{mark}")
            lines.append(f"      理由：{option['rationale']}")
        lines.append("")
    lines.append("你可以：选择编号（如 “画 5、11、21”）、多选、要求我推荐，或自行描述想画什么。")
    return "\n".join(lines)


def plan_from_selection(
    selection: list[int],
    options: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把用户选择的编号转换为方案列表。"""
    by_id = {option["id"]: option for option in options}
    return [by_id[item] for item in selection if item in by_id]
