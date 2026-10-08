"""并发调度。

核心规则（严格实现）：
  * 首次启动：最多同时提交 `initial_submit`（默认 3）个数据申请。
  * 之后：每当有任务完成并下载、释放名额，就补充提交新任务。
  * 任意时刻：`running/submitted/queued` 任务数 <= `max_concurrent`（默认 5）。

本模块只做"该提交哪些"的纯逻辑，实际提交/下载由 era5/firms 客户端执行。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .settings import Settings
from .states import TaskState
from .store import Task, TaskStore


@dataclass
class QueuePlan:
    """一次调度决策。"""

    active: int
    capacity: int
    to_submit: list[Task]
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "capacity": self.capacity,
            "to_submit": [task.task_id for task in self.to_submit],
            "reason": self.reason,
        }


def _limits(settings: Settings) -> tuple[int, int]:
    initial = int(settings.download.get("initial_submit", 3))
    maximum = int(settings.download.get("max_concurrent", 5))
    if initial < 1:
        initial = 1
    if maximum < initial:
        maximum = initial
    return initial, maximum


def is_initial_phase(store: TaskStore) -> bool:
    """是否仍处于"首次提交"阶段。"""
    return not bool(store.data.get("initial_batch_done"))


def mark_initial_batch(store: TaskStore) -> None:
    store.data["initial_batch_done"] = True


def plan_submissions(store: TaskStore, settings: Settings) -> QueuePlan:
    """计算本次应提交的任务。

    首次阶段：上限 = min(initial_submit, max_concurrent)。
    之后阶段：上限 = max_concurrent。
    """
    initial, maximum = _limits(settings)
    active = store.active_count()
    limit = initial if is_initial_phase(store) else maximum
    capacity = max(0, limit - active)
    pending = store.submittable_tasks()
    to_submit = pending[:capacity]

    if not pending:
        reason = "没有待提交任务"
    elif capacity <= 0:
        reason = f"并发已满（{active}/{limit}），等待名额释放"
    elif is_initial_phase(store):
        reason = f"首次提交：{active} 个进行中，本次提交 {len(to_submit)} 个（上限 {limit}）"
    else:
        reason = f"补充提交：{active} 个进行中，本次提交 {len(to_submit)} 个（上限 {limit}）"
    return QueuePlan(active=active, capacity=capacity, to_submit=to_submit, reason=reason)


def free_slots_after_download(store: TaskStore, settings: Settings) -> int:
    """下载完成后可用于补充提交的名额数。"""
    _, maximum = _limits(settings)
    return max(0, maximum - store.active_count())


def can_submit_more(store: TaskStore, settings: Settings) -> bool:
    _, maximum = _limits(settings)
    return store.active_count() < maximum and bool(store.submittable_tasks())


def queue_status(store: TaskStore, settings: Settings) -> dict[str, Any]:
    """供定时检查与报告使用的队列概览。"""
    initial, maximum = _limits(settings)
    return {
        "phase": "initial" if is_initial_phase(store) else "steady",
        "active": store.active_count(),
        "limit": initial if is_initial_phase(store) else maximum,
        "max_concurrent": maximum,
        "initial_submit": initial,
        "pending": len(store.submittable_tasks()),
        "ready_to_download": len(store.ready_to_download()),
        "states": store.summary(),
        "all_downloaded": store.all_downloaded(),
    }
