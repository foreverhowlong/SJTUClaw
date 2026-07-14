"""HTTP transport for scheduled-task management."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from claw.runtime import ClawRuntime
from claw.tasks import IntervalSchedule, OnceSchedule, TaskSchedule


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class OnceScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["once"]
    run_at: datetime = Field(alias="runAt")

    def to_domain(self) -> OnceSchedule:
        return OnceSchedule(self.run_at)


class IntervalScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["interval"]
    start_at: datetime = Field(alias="startAt")
    interval_seconds: int = Field(alias="intervalSeconds", gt=0)

    def to_domain(self) -> IntervalSchedule:
        return IntervalSchedule(self.start_at, self.interval_seconds)


ScheduleRequest = Annotated[
    OnceScheduleRequest | IntervalScheduleRequest,
    Field(discriminator="type"),
]


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    content: str
    schedule: ScheduleRequest

    def domain_schedule(self) -> TaskSchedule:
        return self.schedule.to_domain()


@router.post("", status_code=201)
async def create_task(payload: CreateTaskRequest, request: Request) -> dict[str, object]:
    runtime = _runtime(request)
    async with runtime.session_coordinator.mutation(payload.session_id):
        task = runtime.scheduler.create_task(
            payload.session_id,
            payload.content,
            payload.domain_schedule(),
        )
    return task.to_dict()


@router.get("")
async def list_tasks(request: Request) -> dict[str, list[dict[str, object]]]:
    tasks = _runtime(request).scheduler.list_tasks()
    return {"tasks": [task.to_dict() for task in tasks]}


@router.get("/{task_id}")
async def get_task(task_id: str, request: Request) -> dict[str, object]:
    return _runtime(request).scheduler.get_task(task_id).to_dict()


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request) -> dict[str, object]:
    return _runtime(request).scheduler.cancel_task(task_id).to_dict()


def _runtime(request: Request) -> ClawRuntime:
    return request.app.state.runtime
