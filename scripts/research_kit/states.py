"""任务状态机。

状态（禁止模糊状态）：
    PENDING      已生成、尚未提交
    SUBMITTED    已提交给数据提供方
    QUEUED       提供方排队中
    RUNNING      提供方处理中
    COMPLETED    提供方完成、尚未下载
    DOWNLOADING  正在下载
    DOWNLOADED   已下载并校验通过（终态）
    FAILED       失败
    RETRY        标记为待重试

并发名额：SUBMITTED / QUEUED / RUNNING 计入"进行中"名额。
可重试：FAILED / RETRY（且未超过 max_retries）。
"""

from __future__ import annotations

from enum import Enum


class TaskState(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOADED = "DOWNLOADED"
    FAILED = "FAILED"
    RETRY = "RETRY"


# 允许的状态迁移
TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.PENDING: {TaskState.SUBMITTED, TaskState.FAILED, TaskState.RETRY},
    TaskState.SUBMITTED: {TaskState.QUEUED, TaskState.RUNNING, TaskState.COMPLETED, TaskState.FAILED},
    TaskState.QUEUED: {TaskState.RUNNING, TaskState.COMPLETED, TaskState.FAILED},
    TaskState.RUNNING: {TaskState.COMPLETED, TaskState.FAILED},
    TaskState.COMPLETED: {TaskState.DOWNLOADING, TaskState.FAILED},
    TaskState.DOWNLOADING: {TaskState.DOWNLOADED, TaskState.FAILED},
    TaskState.DOWNLOADED: set(),                      # 终态
    TaskState.FAILED: {TaskState.RETRY, TaskState.FAILED},
    TaskState.RETRY: {TaskState.SUBMITTED, TaskState.FAILED},
}

# 计入并发名额的状态
ACTIVE_STATES = {TaskState.SUBMITTED, TaskState.QUEUED, TaskState.RUNNING}
# 提供方已完成、可下载
READY_STATES = {TaskState.COMPLETED}
# 终态
TERMINAL_STATES = {TaskState.DOWNLOADED}
# 可重试
RETRYABLE_STATES = {TaskState.FAILED, TaskState.RETRY}

# 提供方返回的状态 -> 本地状态
PROVIDER_STATUS_MAP = {
    "accepted": TaskState.SUBMITTED,
    "queued": TaskState.QUEUED,
    "running": TaskState.RUNNING,
    "successful": TaskState.COMPLETED,
    "completed": TaskState.COMPLETED,
    "failed": TaskState.FAILED,
    "rejected": TaskState.FAILED,
    "dismissed": TaskState.FAILED,
    "unknown": None,          # 无法判定，保持原状态
    "lookup_error": None,     # 瞬时查询失败，保持原状态（避免误判为失败而重复提交）
}


class StateTransitionError(RuntimeError):
    """非法状态迁移。"""


def can_transition(current: TaskState, target: TaskState) -> bool:
    return target in TRANSITIONS.get(current, set())


def assert_transition(current: TaskState, target: TaskState) -> None:
    if not can_transition(current, target):
        raise StateTransitionError(f"非法状态迁移：{current.value} -> {target.value}")


def map_provider_status(status: str) -> TaskState | None:
    """把提供方状态映射为本地状态；无法判定时返回 None。"""
    return PROVIDER_STATUS_MAP.get(str(status).lower().strip())


def is_active(state: TaskState) -> bool:
    return state in ACTIVE_STATES


def is_terminal(state: TaskState) -> bool:
    return state in TERMINAL_STATES


def is_retryable(state: TaskState) -> bool:
    return state in RETRYABLE_STATES
