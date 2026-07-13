from datetime import datetime, timedelta, timezone

import pytest

from claw.errors import TaskConflictError, TaskError
from claw.store.tasks import TaskStore
from claw.tasks import IntervalSchedule, OnceSchedule, next_run_after


NOW = datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc)


def test_once_task_persists_and_cancel_is_a_visible_terminal_state(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks")
    task = store.create(
        "session_0123456789ab",
        "prepare report",
        OnceSchedule(NOW + timedelta(hours=1)),
        now=NOW,
    )

    loaded = TaskStore(tmp_path / "tasks").get(task.task_id)
    cancelled = store.cancel(task.task_id, now=NOW + timedelta(minutes=1))

    assert loaded == task
    assert cancelled.status == "cancelled"
    assert cancelled.next_run_at is None
    with pytest.raises(TaskConflictError):
        store.cancel(task.task_id, now=NOW + timedelta(minutes=2))


def test_interval_task_keeps_all_results_and_skips_elapsed_boundaries(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks")
    schedule = IntervalSchedule(NOW + timedelta(minutes=1), 60)
    task = store.create(
        "session_0123456789ab",
        "check source",
        schedule,
        now=NOW,
    )
    first = store.claim(task.task_id, now=NOW + timedelta(minutes=1))
    assert first is not None
    first_done = store.finish(
        task.task_id,
        first.history[-1].execution_id,
        succeeded=True,
        finished_at=NOW + timedelta(minutes=3, seconds=10),
        assistant_reply="done",
    )
    second = store.claim(task.task_id, now=NOW + timedelta(minutes=4))
    assert second is not None
    failed = store.finish(
        task.task_id,
        second.history[-1].execution_id,
        succeeded=False,
        finished_at=NOW + timedelta(minutes=4, seconds=5),
        error_code="llm_error",
        error_message="unavailable",
    )

    assert first_done.next_run_at == NOW + timedelta(minutes=4)
    assert failed.status == "failed"
    assert failed.next_run_at == NOW + timedelta(minutes=5)
    assert [item.status for item in failed.history] == ["succeeded", "failed"]
    assert failed.history[0].assistant_reply == "done"
    assert failed.history[1].error_message == "unavailable"


def test_recovery_marks_interrupted_execution_failed(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks")
    task = store.create(
        "session_0123456789ab",
        "once",
        OnceSchedule(NOW + timedelta(seconds=1)),
        now=NOW,
    )
    claimed = store.claim(task.task_id, now=NOW + timedelta(seconds=1))
    assert claimed is not None

    recovered = TaskStore(tmp_path / "tasks").recover_interrupted(
        now=NOW + timedelta(seconds=2)
    )

    assert recovered[0].status == "failed"
    assert recovered[0].next_run_at is None
    assert recovered[0].history[-1].error_code == "scheduler_interrupted"


def test_recovery_closes_running_history_without_reviving_cancelled_task(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks")
    task = store.create(
        "session_0123456789ab",
        "repeat",
        IntervalSchedule(NOW + timedelta(seconds=1), 60),
        now=NOW,
    )
    claimed = store.claim(task.task_id, now=NOW + timedelta(seconds=1))
    assert claimed is not None
    store.cancel(task.task_id, now=NOW + timedelta(seconds=2))

    recovered = TaskStore(tmp_path / "tasks").recover_interrupted(
        now=NOW + timedelta(seconds=3)
    )

    assert recovered[0].status == "cancelled"
    assert recovered[0].next_run_at is None
    assert recovered[0].history[-1].status == "failed"


def test_task_time_rules_require_timezone_and_are_strictly_future() -> None:
    with pytest.raises(TaskError, match="时区"):
        OnceSchedule(datetime(2026, 7, 14, 9, 0))
    schedule = IntervalSchedule(NOW, 60)
    assert next_run_after(schedule, NOW) == NOW + timedelta(minutes=1)
    assert next_run_after(schedule, NOW + timedelta(seconds=61)) == NOW + timedelta(minutes=2)
