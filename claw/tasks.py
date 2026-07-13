"""Domain types and recurrence rules for persisted scheduled tasks."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from math import floor
from typing import Literal, TypeAlias

from claw.errors import TaskError


TaskStatus: TypeAlias = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
]
ExecutionStatus: TypeAlias = Literal["running", "succeeded", "failed"]


@dataclass(frozen=True)
class OnceSchedule:
    run_at: datetime
    type: Literal["once"] = "once"

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_at", as_utc(self.run_at, "runAt"))

    def to_dict(self) -> dict[str, object]:
        return {"type": self.type, "runAt": self.run_at.isoformat()}


@dataclass(frozen=True)
class IntervalSchedule:
    start_at: datetime
    interval_seconds: int
    type: Literal["interval"] = "interval"

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_at", as_utc(self.start_at, "startAt"))
        if isinstance(self.interval_seconds, bool) or self.interval_seconds <= 0:
            raise TaskError("intervalSeconds 必须是大于 0 的整数。")

    def to_dict(self) -> dict[str, object]:
        return {
            "type": self.type,
            "startAt": self.start_at.isoformat(),
            "intervalSeconds": self.interval_seconds,
        }


TaskSchedule: TypeAlias = OnceSchedule | IntervalSchedule


@dataclass(frozen=True)
class TaskExecution:
    execution_id: str
    scheduled_for: datetime
    started_at: datetime
    status: ExecutionStatus = "running"
    finished_at: datetime | None = None
    assistant_reply: str = ""
    error_code: str = ""
    error_message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "scheduled_for",
            as_utc(self.scheduled_for, "scheduledFor"),
        )
        object.__setattr__(self, "started_at", as_utc(self.started_at, "startedAt"))
        if self.finished_at is not None:
            object.__setattr__(
                self,
                "finished_at",
                as_utc(self.finished_at, "finishedAt"),
            )
        if self.status == "running" and self.finished_at is not None:
            raise TaskError("running execution 不能包含 finishedAt。")
        if self.status != "running" and self.finished_at is None:
            raise TaskError("已结束 execution 必须包含 finishedAt。")

    def finish(
        self,
        *,
        succeeded: bool,
        finished_at: datetime,
        assistant_reply: str = "",
        error_code: str = "",
        error_message: str = "",
    ) -> TaskExecution:
        if self.status != "running":
            raise TaskError(f"Execution 已结束: {self.execution_id}。")
        return replace(
            self,
            status="succeeded" if succeeded else "failed",
            finished_at=finished_at,
            assistant_reply=assistant_reply.strip(),
            error_code=error_code.strip(),
            error_message=error_message.strip(),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "executionId": self.execution_id,
            "scheduledFor": self.scheduled_for.isoformat(),
            "startedAt": self.started_at.isoformat(),
            "finishedAt": (
                self.finished_at.isoformat() if self.finished_at is not None else None
            ),
            "status": self.status,
            "assistantReply": self.assistant_reply,
            "errorCode": self.error_code,
            "errorMessage": self.error_message,
        }


@dataclass(frozen=True)
class ScheduledTask:
    task_id: str
    session_id: str
    content: str
    schedule: TaskSchedule
    next_run_at: datetime | None
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    revision: int = 0
    history: tuple[TaskExecution, ...] = ()

    def __post_init__(self) -> None:
        normalized = self.content.strip()
        if not normalized:
            raise TaskError("任务内容不能为空。")
        object.__setattr__(self, "content", normalized)
        object.__setattr__(self, "created_at", as_utc(self.created_at, "createdAt"))
        object.__setattr__(self, "updated_at", as_utc(self.updated_at, "updatedAt"))
        if self.next_run_at is not None:
            object.__setattr__(
                self,
                "next_run_at",
                as_utc(self.next_run_at, "nextRunAt"),
            )
        if self.revision < 0:
            raise TaskError("task revision 不能小于 0。")

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "taskId": self.task_id,
            "sessionId": self.session_id,
            "content": self.content,
            "schedule": self.schedule.to_dict(),
            "nextRunAt": (
                self.next_run_at.isoformat() if self.next_run_at is not None else None
            ),
            "status": self.status,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
            "revision": self.revision,
            "history": [item.to_dict() for item in self.history],
        }


def first_run_at(schedule: TaskSchedule) -> datetime:
    if isinstance(schedule, OnceSchedule):
        return schedule.run_at
    return schedule.start_at


def next_run_after(schedule: TaskSchedule, moment: datetime) -> datetime | None:
    """Return the first interval boundary strictly later than ``moment``."""
    if isinstance(schedule, OnceSchedule):
        return None
    current = as_utc(moment, "moment")
    if current < schedule.start_at:
        return schedule.start_at
    elapsed = (current - schedule.start_at).total_seconds()
    steps = floor(elapsed / schedule.interval_seconds) + 1
    return schedule.start_at + timedelta(seconds=steps * schedule.interval_seconds)


def as_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TaskError(f"{field} 必须包含时区。")
    return value.astimezone(timezone.utc)
