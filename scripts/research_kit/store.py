"""任务记录存储（data_requests.json）。

职责：
  * 原子读写任务记录（断点续传的唯一依据）
  * 每次状态变化写日志
  * 提供查询：进行中数量、可提交任务、可下载任务、待重试任务
绝不覆盖原始数据；本文件只是"申请/下载状态"的记录。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .states import TaskState, is_active, is_terminal


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Task:
    """一个数据申请/下载任务。"""

    task_id: str
    kind: str                       # era5_single | era5_pressure | firms
    label: str
    dataset: str = ""               # CDS 数据集名（ERA5）
    product: str = ""               # FIRMS 产品名
    year: int | None = None
    month: int | None = None
    start_time: str = ""            # ISO8601（UTC）
    end_time: str = ""
    variables: list[str] = field(default_factory=list)
    levels: list[int] = field(default_factory=list)
    area: list[float] = field(default_factory=list)   # north, west, south, east
    status: str = TaskState.PENDING.value
    request_id: str | None = None
    active_request_id: str | None = None
    provider_status: str | None = None
    submitted_at: str | None = None
    last_checked: str | None = None
    downloaded: bool = False
    local_path: str | None = None
    error_message: str | None = None
    attempts: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    # ---- 状态 ----
    @property
    def state(self) -> TaskState:
        return TaskState(self.status)

    def set_state(self, target: TaskState, note: str = "") -> None:
        self.history.append(
            {"at": utcnow(), "from": self.status, "to": target.value, "note": note}
        )
        self.status = target.value

    @property
    def effective_request_id(self) -> str | None:
        return self.active_request_id or self.request_id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


class TaskStore:
    """任务记录存储。"""

    def __init__(self, path: Path, log_path: Path | None = None) -> None:
        self.path = Path(path)
        self.log_path = Path(log_path) if log_path else self.path.with_suffix(".log")
        self.data: dict[str, Any] = {"schema": 1, "created_at": utcnow(), "updated_at": utcnow(), "tasks": []}

    # ---- 读写 ----
    def load(self) -> "TaskStore":
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as handle:
                self.data = json.load(handle)
            self.data.setdefault("tasks", [])
        return self

    def save(self) -> None:
        self.data["updated_at"] = utcnow()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, tmp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001 — 落盘失败时清理临时文件并原样抛出，不掩盖真实错误
            tmp.unlink(missing_ok=True)
            raise

    def log(self, message: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{utcnow()} {message}\n")

    # ---- 任务集合 ----
    @property
    def tasks(self) -> list[Task]:
        return [Task.from_dict(item) for item in self.data.get("tasks", [])]

    def _write_tasks(self, tasks: Iterable[Task]) -> None:
        self.data["tasks"] = [task.to_dict() for task in tasks]

    def add(self, task: Task) -> None:
        tasks = self.tasks
        if any(existing.task_id == task.task_id for existing in tasks):
            return
        tasks.append(task)
        self._write_tasks(tasks)

    def add_many(self, new_tasks: Iterable[Task]) -> None:
        tasks = self.tasks
        existing = {task.task_id for task in tasks}
        for task in new_tasks:
            if task.task_id not in existing:
                tasks.append(task)
                existing.add(task.task_id)
        self._write_tasks(tasks)

    def update(self, task: Task) -> None:
        tasks = self.tasks
        for index, existing in enumerate(tasks):
            if existing.task_id == task.task_id:
                tasks[index] = task
                self._write_tasks(tasks)
                return
        tasks.append(task)
        self._write_tasks(tasks)

    def get(self, task_id: str) -> Task | None:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        return None

    # ---- 查询 ----
    def active_count(self) -> int:
        return sum(1 for task in self.tasks if is_active(task.state))

    def active_tasks(self) -> list[Task]:
        return [task for task in self.tasks if is_active(task.state)]

    def pending_tasks(self) -> list[Task]:
        return [task for task in self.tasks if task.state is TaskState.PENDING]

    def submittable_tasks(self) -> list[Task]:
        """可提交的任务：PENDING 或 RETRY。"""
        return [task for task in self.tasks if task.state in {TaskState.PENDING, TaskState.RETRY}]

    def ready_to_download(self) -> list[Task]:
        return [task for task in self.tasks if task.state is TaskState.COMPLETED and not task.downloaded]

    def retryable(self, max_retries: int) -> list[Task]:
        return [
            task
            for task in self.tasks
            if task.state in {TaskState.FAILED, TaskState.RETRY} and task.attempts < max_retries
        ]

    def unfinished(self) -> list[Task]:
        return [task for task in self.tasks if not is_terminal(task.state)]

    def all_downloaded(self) -> bool:
        tasks = self.tasks
        return bool(tasks) and all(task.state is TaskState.DOWNLOADED for task in tasks)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in self.tasks:
            counts[task.status] = counts.get(task.status, 0) + 1
        return counts
