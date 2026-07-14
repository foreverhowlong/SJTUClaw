import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from claw.approval import ApprovalCoordinator
from claw.errors import SessionConflictError
from claw.scheduler import Scheduler
from claw.session_coordination import SessionCoordinator
from claw.session_lifecycle import SessionLifecycleService
from claw.store.approvals import ApprovalStore
from claw.store.sessions import SessionStore
from claw.store.tasks import TaskStore
from claw.tasks import OnceSchedule


class NoopAgent:
    async def run_turn(self, session_id, user_input, *, source=None):
        del session_id, user_input, source
        if False:
            yield


def test_mutation_rejects_an_active_turn_and_entries_are_released(tmp_path) -> None:
    coordinator = SessionCoordinator(tmp_path / "sessions")
    session_id = "session_0123456789ab"

    async def scenario() -> None:
        async with coordinator.turn(session_id):
            with pytest.raises(SessionConflictError, match="正在运行"):
                async with coordinator.mutation(session_id):
                    pass
        async with coordinator.mutation(session_id):
            pass

    asyncio.run(scenario())


def test_process_lease_is_shared_by_independent_coordinators(tmp_path) -> None:
    first = SessionCoordinator(tmp_path / "sessions")
    second = SessionCoordinator(tmp_path / "sessions")
    session_id = "session_0123456789ab"

    async def scenario() -> None:
        async with first.turn(session_id):
            with pytest.raises(SessionConflictError, match="其他 runtime"):
                async with second.mutation(session_id):
                    pass

    asyncio.run(scenario())


def test_session_lifecycle_blocks_tasks_and_cascade_closes_references(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    session = sessions.create()
    tasks = TaskStore(tmp_path / "tasks")
    scheduler = Scheduler(tasks, sessions, NoopAgent())
    scheduled = scheduler.create_task(
        session.session_id,
        "later",
        OnceSchedule(datetime.now(timezone.utc) + timedelta(hours=1)),
    )
    coordinator = SessionCoordinator(sessions.root)
    lifecycle = SessionLifecycleService(
        sessions,
        coordinator,
        scheduler=scheduler,
    )

    async def scenario() -> None:
        with pytest.raises(SessionConflictError, match="活动定时任务"):
            await lifecycle.delete(session.session_id)
        await lifecycle.delete(session.session_id, cascade=True)

    asyncio.run(scenario())

    assert tasks.get(scheduled.task_id).status == "cancelled"
    assert sessions.list() == []


def test_session_lifecycle_denies_pending_approval_before_cascade_delete(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    session = sessions.create()
    approvals = ApprovalStore(tmp_path / "approvals")
    request = approvals.create(
        session.session_id,
        "call_1",
        "create_file",
        {"path": "note.txt"},
        "/workspace",
    )
    lifecycle = SessionLifecycleService(
        sessions,
        SessionCoordinator(sessions.root),
        approvals=approvals,
    )

    async def scenario() -> None:
        with pytest.raises(SessionConflictError, match="approval"):
            await lifecycle.delete(session.session_id)
        await lifecycle.delete(session.session_id, cascade=True)

    asyncio.run(scenario())

    assert approvals.get(request.approval_id).status == "denied"
