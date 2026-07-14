"""Coordinated session deletion across runtime-owned aggregates."""

from __future__ import annotations

from claw.errors import SessionConflictError
from claw.scheduler import Scheduler
from claw.session_coordination import SessionCoordinator
from claw.shell import ShellManager
from claw.store.approvals import ApprovalStore
from claw.store.sessions import SessionStore


class SessionLifecycleService:
    def __init__(
        self,
        sessions: SessionStore,
        coordinator: SessionCoordinator,
        *,
        scheduler: Scheduler | None = None,
        approvals: ApprovalStore | None = None,
        shells: ShellManager | None = None,
    ) -> None:
        self._sessions = sessions
        self._coordinator = coordinator
        self._scheduler = scheduler
        self._approvals = approvals
        self._shells = shells

    async def delete(self, session_id: str, *, cascade: bool = False) -> None:
        async with self._coordinator.mutation(session_id):
            self._sessions.load(session_id)
            tasks = (
                [
                    task
                    for task in self._scheduler.list_tasks()
                    if task.session_id == session_id
                    and (task.next_run_at is not None or task.status == "running")
                ]
                if self._scheduler is not None
                else []
            )
            if tasks and not cascade:
                raise SessionConflictError(
                    f"Session 仍有 {len(tasks)} 个活动定时任务，请先取消。"
                )
            if cascade and self._scheduler is not None:
                for task in tasks:
                    if task.status != "cancelled":
                        self._scheduler.cancel_task(task.task_id)

            approvals = (
                [
                    item
                    for item in self._approvals.list(session_id=session_id)
                    if item.status in {"pending", "approved", "executing"}
                ]
                if self._approvals is not None
                else []
            )
            hard_blockers = [
                item for item in approvals if item.status in {"approved", "executing"}
            ]
            if hard_blockers or (approvals and not cascade):
                raise SessionConflictError(
                    f"Session 仍有 {len(approvals)} 个未结束 approval，暂时不能删除。"
                )
            if cascade and self._approvals is not None:
                for item in approvals:
                    self._approvals.resolve(
                        item.approval_id,
                        approved=False,
                        reason="session deleted before approval was resolved",
                    )

            if self._shells is not None:
                await self._shells.close(session_id)
            self._sessions.delete(session_id)
