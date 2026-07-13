"""Long-running time-based entry point into the shared agent runtime."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone
from typing import Protocol

from claw.events import AgentEvent
from claw.messages import MessageSource
from claw.store.sessions import SessionStore
from claw.store.tasks import TaskStore
from claw.tasks import ScheduledTask, TaskSchedule


logger = logging.getLogger(__name__)


class AgentRunner(Protocol):
    def run_turn(
        self,
        session_id: str,
        user_input: str,
        *,
        source: MessageSource | None = None,
    ) -> AsyncIterator[AgentEvent]: ...


SessionUpdatedListener = Callable[[str], Awaitable[None]]


class Scheduler:
    """Persist schedules and dispatch due instructions through ``AgentService``."""

    def __init__(
        self,
        task_store: TaskStore,
        session_store: SessionStore,
        agent: AgentRunner,
        *,
        poll_interval_seconds: float = 1.0,
        max_concurrent_runs: int = 4,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds 必须大于 0。")
        if max_concurrent_runs <= 0:
            raise ValueError("max_concurrent_runs 必须大于 0。")
        self._store = task_store
        self._sessions = session_store
        self._agent = agent
        self._poll_interval = poll_interval_seconds
        self._max_concurrent_runs = max_concurrent_runs
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._wake = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None
        self._jobs: set[asyncio.Task[None]] = set()
        self._session_updated_listeners: set[SessionUpdatedListener] = set()

    @property
    def running(self) -> bool:
        return self._runner is not None and not self._runner.done()

    def create_task(
        self,
        session_id: str,
        content: str,
        schedule: TaskSchedule,
    ) -> ScheduledTask:
        self._sessions.load(session_id)
        task = self._store.create(
            session_id,
            content,
            schedule,
            now=self._now(),
        )
        self._wake.set()
        return task

    def list_tasks(self) -> list[ScheduledTask]:
        return self._store.list()

    def get_task(self, task_id: str) -> ScheduledTask:
        return self._store.get(task_id)

    def cancel_task(self, task_id: str) -> ScheduledTask:
        task = self._store.cancel(task_id, now=self._now())
        self._wake.set()
        return task

    def subscribe_session_updates(
        self,
        listener: SessionUpdatedListener,
    ) -> Callable[[], None]:
        """Subscribe a runtime host without coupling the scheduler to its transport."""
        self._session_updated_listeners.add(listener)
        return lambda: self._session_updated_listeners.discard(listener)

    async def start(self) -> None:
        if self.running:
            return
        self._runner = asyncio.create_task(
            self.run_forever(),
            name="claw-scheduler",
        )
        await asyncio.sleep(0)

    async def stop(self) -> None:
        runner = self._runner
        if runner is None:
            return
        self._runner = None
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)

    async def run_forever(self) -> None:
        """Recover persisted work, then poll without blocking active executions."""
        self._store.recover_interrupted(now=self._now())
        try:
            while True:
                self._dispatch_due(self._now())
                self._wake.clear()
                try:
                    await asyncio.wait_for(
                        self._wake.wait(),
                        timeout=self._poll_interval,
                    )
                except TimeoutError:
                    pass
        finally:
            jobs = tuple(self._jobs)
            for job in jobs:
                job.cancel()
            if jobs:
                await asyncio.gather(*jobs, return_exceptions=True)

    async def run_due(self, *, now: datetime | None = None) -> list[ScheduledTask]:
        """Execute the currently due set; intended for deterministic tests and demos."""
        current = now or self._now()
        claimed = [
            task
            for candidate in self._store.list_due(current)
            if (task := self._store.claim(candidate.task_id, now=current)) is not None
        ]
        if claimed:
            await asyncio.gather(*(self._execute(task) for task in claimed))
        return [self._store.get(task.task_id) for task in claimed]

    def _dispatch_due(self, now: datetime) -> None:
        capacity = self._max_concurrent_runs - len(self._jobs)
        if capacity <= 0:
            return
        for candidate in self._store.list_due(now)[:capacity]:
            claimed = self._store.claim(candidate.task_id, now=now)
            if claimed is None:
                continue
            job = asyncio.create_task(
                self._execute(claimed),
                name=f"claw-scheduled-{claimed.task_id}",
            )
            self._jobs.add(job)
            job.add_done_callback(self._job_finished)

    def _job_finished(self, job: asyncio.Task[None]) -> None:
        self._jobs.discard(job)
        if not job.cancelled():
            exception = job.exception()
            if exception is not None:
                logger.error(
                    "scheduled job escaped execution boundary",
                    exc_info=(type(exception), exception, exception.__traceback__),
                )
        self._wake.set()

    async def _execute(self, task: ScheduledTask) -> None:
        execution = task.history[-1]
        assistant_reply = ""
        error_code = ""
        error_message = ""
        turn_status = ""
        try:
            async for event in self._agent.run_turn(
                task.session_id,
                task.content,
                source="scheduled_task",
            ):
                if event.type == "llm_message":
                    assistant_reply = str(event.payload.get("content", "")).strip()
                elif event.type == "error":
                    error_code = str(event.payload.get("code", "agent_error"))
                    error_message = str(
                        event.payload.get("message", "Agent 执行失败。")
                    )
                elif event.type == "turn_end":
                    turn_status = str(event.payload.get("status", ""))
            succeeded = turn_status == "completed" and bool(assistant_reply)
            if not succeeded and not error_message:
                error_code = "incomplete_agent_turn"
                error_message = "Agent turn 未产生完整的最终回复。"
            self._store.finish(
                task.task_id,
                execution.execution_id,
                succeeded=succeeded,
                finished_at=self._now(),
                assistant_reply=assistant_reply,
                error_code=error_code,
                error_message=error_message,
            )
            if succeeded:
                await self._notify_session_updated(task.session_id)
        except asyncio.CancelledError:
            self._finish_unexpected(
                task,
                execution.execution_id,
                "scheduler_stopped",
                "Scheduler 停止时任务仍在执行。",
            )
            raise
        except Exception as exc:
            logger.exception("scheduled task failed: task=%s", task.task_id)
            self._finish_unexpected(
                task,
                execution.execution_id,
                "scheduler_error",
                f"Scheduler 执行任务失败: {exc}",
            )

    def _finish_unexpected(
        self,
        task: ScheduledTask,
        execution_id: str,
        code: str,
        message: str,
    ) -> None:
        try:
            self._store.finish(
                task.task_id,
                execution_id,
                succeeded=False,
                finished_at=self._now(),
                error_code=code,
                error_message=message,
            )
        except Exception:
            logger.exception(
                "failed to persist scheduled task failure: task=%s",
                task.task_id,
            )

    async def _notify_session_updated(self, session_id: str) -> None:
        listeners = tuple(self._session_updated_listeners)
        if not listeners:
            return
        results = await asyncio.gather(
            *(listener(session_id) for listener in listeners),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                logger.warning(
                    "scheduled session update listener failed: session=%s",
                    session_id,
                    exc_info=(type(result), result, result.__traceback__),
                )
