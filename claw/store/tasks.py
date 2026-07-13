"""Atomic file-backed storage and state transitions for scheduled tasks."""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from filelock import FileLock, Timeout

from claw.errors import TaskConflictError, TaskError, TaskNotFoundError
from claw.store.sessions import LOCK_TIMEOUT_SECONDS
from claw.tasks import (
    IntervalSchedule,
    OnceSchedule,
    ScheduledTask,
    TaskExecution,
    TaskSchedule,
    as_utc,
    first_run_at,
    next_run_after,
)


TASK_ID_PATTERN = re.compile(r"task_[0-9a-f]{12}")
EXECUTION_ID_PATTERN = re.compile(r"execution_[0-9a-f]{12}")
TASK_STATUSES = {"pending", "running", "completed", "failed", "cancelled"}
EXECUTION_STATUSES = {"running", "succeeded", "failed"}


class TaskStore:
    """Persist one complete task aggregate per JSON file."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def create(
        self,
        session_id: str,
        content: str,
        schedule: TaskSchedule,
        *,
        now: datetime,
    ) -> ScheduledTask:
        created_at = as_utc(now, "now")
        next_run_at = first_run_at(schedule)
        if next_run_at <= created_at:
            raise TaskError("首次触发时间必须晚于当前时间。")
        while True:
            task_id = f"task_{uuid4().hex[:12]}"
            path = self._path(task_id)
            if not path.exists():
                break
        task = ScheduledTask(
            task_id=task_id,
            session_id=session_id,
            content=content,
            schedule=schedule,
            next_run_at=next_run_at,
            status="pending",
            created_at=created_at,
            updated_at=created_at,
        )
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with self._locked(task_id):
                if path.exists():
                    raise TaskConflictError(f"Task ID 冲突: {task_id}。")
                self._write(path, task)
        except TaskError:
            raise
        except OSError as exc:
            raise TaskError(f"创建 task {task_id} 失败: {exc}") from exc
        return task

    def get(self, task_id: str) -> ScheduledTask:
        with self._locked(task_id):
            return self._read_unlocked(task_id)

    def list(self) -> list[ScheduledTask]:
        if not self.root.exists():
            return []
        try:
            paths = sorted(
                path
                for path in self.root.iterdir()
                if path.is_file()
                and not path.is_symlink()
                and TASK_ID_PATTERN.fullmatch(path.stem)
            )
        except OSError as exc:
            raise TaskError(f"无法列出 task 目录 {self.root}: {exc}") from exc
        tasks = [self.get(path.stem) for path in paths]
        return sorted(tasks, key=lambda item: item.updated_at, reverse=True)

    def list_due(self, now: datetime) -> list[ScheduledTask]:
        current = as_utc(now, "now")
        return sorted(
            (
                task
                for task in self.list()
                if task.status in {"pending", "failed"}
                and task.next_run_at is not None
                and task.next_run_at <= current
            ),
            key=lambda item: item.next_run_at or current,
        )

    def claim(self, task_id: str, *, now: datetime) -> ScheduledTask | None:
        current = as_utc(now, "now")
        with self._locked(task_id):
            task = self._read_unlocked(task_id)
            if (
                task.status not in {"pending", "failed"}
                or task.next_run_at is None
                or task.next_run_at > current
            ):
                return None
            execution = TaskExecution(
                execution_id=f"execution_{uuid4().hex[:12]}",
                scheduled_for=task.next_run_at,
                started_at=current,
            )
            claimed = replace(
                task,
                status="running",
                updated_at=current,
                revision=task.revision + 1,
                history=(*task.history, execution),
            )
            self._write(self._path(task_id), claimed)
            return claimed

    def finish(
        self,
        task_id: str,
        execution_id: str,
        *,
        succeeded: bool,
        finished_at: datetime,
        assistant_reply: str = "",
        error_code: str = "",
        error_message: str = "",
    ) -> ScheduledTask:
        current = as_utc(finished_at, "finishedAt")
        with self._locked(task_id):
            task = self._read_unlocked(task_id)
            index = next(
                (
                    position
                    for position, item in enumerate(task.history)
                    if item.execution_id == execution_id
                ),
                None,
            )
            if index is None:
                raise TaskConflictError(
                    f"Task {task_id} 不包含 execution {execution_id}。"
                )
            finished = task.history[index].finish(
                succeeded=succeeded,
                finished_at=current,
                assistant_reply=assistant_reply,
                error_code=error_code,
                error_message=error_message,
            )
            history = list(task.history)
            history[index] = finished

            if task.status == "cancelled":
                status = "cancelled"
                next_run_at = None
            elif isinstance(task.schedule, OnceSchedule):
                status = "completed" if succeeded else "failed"
                next_run_at = None
            else:
                status = "pending" if succeeded else "failed"
                next_run_at = next_run_after(task.schedule, current)

            updated = replace(
                task,
                status=status,
                next_run_at=next_run_at,
                updated_at=current,
                revision=task.revision + 1,
                history=tuple(history),
            )
            self._write(self._path(task_id), updated)
            return updated

    def cancel(self, task_id: str, *, now: datetime) -> ScheduledTask:
        current = as_utc(now, "now")
        with self._locked(task_id):
            task = self._read_unlocked(task_id)
            if task.status == "cancelled":
                raise TaskConflictError(f"Task 已取消: {task_id}。")
            if task.next_run_at is None and task.status != "running":
                raise TaskConflictError(f"Task 没有可取消的未来触发: {task_id}。")
            cancelled = replace(
                task,
                status="cancelled",
                next_run_at=None,
                updated_at=current,
                revision=task.revision + 1,
            )
            self._write(self._path(task_id), cancelled)
            return cancelled

    def recover_interrupted(self, *, now: datetime) -> list[ScheduledTask]:
        current = as_utc(now, "now")
        recovered: list[ScheduledTask] = []
        for candidate in self.list():
            running = next(
                (item for item in reversed(candidate.history) if item.status == "running"),
                None,
            )
            if running is None:
                if candidate.status == "running":
                    raise TaskError(
                        f"Task 数据损坏: {candidate.task_id} 为 running 但没有 execution。"
                    )
                continue
            if candidate.status not in {"running", "cancelled"}:
                raise TaskError(
                    f"Task 数据损坏: {candidate.task_id} 包含未结束 execution。"
                )
            recovered.append(
                self.finish(
                    candidate.task_id,
                    running.execution_id,
                    succeeded=False,
                    finished_at=current,
                    error_code="scheduler_interrupted",
                    error_message="Scheduler 上次运行在任务完成前中断。",
                )
            )
        return recovered

    def _path(self, task_id: str) -> Path:
        if not TASK_ID_PATTERN.fullmatch(task_id):
            raise TaskNotFoundError(f"无效的 taskId: {task_id!r}。")
        return self.root / f"{task_id}.json"

    def _read_unlocked(self, task_id: str) -> ScheduledTask:
        path = self._path(task_id)
        if path.is_symlink() or not path.is_file():
            raise TaskNotFoundError(f"Task 不存在: {task_id}。")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return _decode_task(value, expected_id=task_id)
        except TaskError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise TaskError(f"读取 task {task_id} 失败: {exc}") from exc

    @contextmanager
    def _locked(self, task_id: str) -> Iterator[None]:
        if not TASK_ID_PATTERN.fullmatch(task_id):
            raise TaskNotFoundError(f"无效的 taskId: {task_id!r}。")
        lock_dir = self.root / ".locks"
        try:
            lock_dir.mkdir(parents=True, exist_ok=True)
            lock = FileLock(
                lock_dir / f"{task_id}.lock",
                timeout=LOCK_TIMEOUT_SECONDS,
            )
            with lock:
                yield
        except Timeout as exc:
            raise TaskConflictError(f"Task 正忙: {task_id}。") from exc
        except OSError as exc:
            raise TaskError(f"Task 锁失败 {task_id}: {exc}") from exc

    @staticmethod
    def _write(path: Path, task: ScheduledTask) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(task.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise TaskError(f"保存 task {task.task_id} 失败: {exc}") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _decode_task(value: Any, *, expected_id: str) -> ScheduledTask:
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise TaskError("Task 数据损坏: schemaVersion 必须为 1。")
    try:
        task_id = str(value["taskId"])
        schedule = _decode_schedule(value["schedule"])
        next_value = value["nextRunAt"]
        next_run_at = datetime.fromisoformat(next_value) if next_value else None
        status = str(value["status"])
        history_value = value["history"]
        if task_id != expected_id or not TASK_ID_PATTERN.fullmatch(task_id):
            raise TaskError("Task 数据损坏: taskId 与文件名不一致。")
        if status not in TASK_STATUSES:
            raise TaskError(f"Task 数据损坏: 无效 status {status!r}。")
        if not isinstance(history_value, list):
            raise TaskError("Task 数据损坏: history 必须为 array。")
        history = tuple(_decode_execution(item) for item in history_value)
        return ScheduledTask(
            task_id=task_id,
            session_id=str(value["sessionId"]),
            content=str(value["content"]),
            schedule=schedule,
            next_run_at=next_run_at,
            status=status,  # type: ignore[arg-type]
            created_at=datetime.fromisoformat(str(value["createdAt"])),
            updated_at=datetime.fromisoformat(str(value["updatedAt"])),
            revision=int(value["revision"]),
            history=history,
        )
    except TaskError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise TaskError(f"Task 数据损坏: {exc}") from exc


def _decode_schedule(value: Any) -> TaskSchedule:
    if not isinstance(value, dict):
        raise TaskError("Task 数据损坏: schedule 必须为 object。")
    if value.get("type") == "once":
        return OnceSchedule(datetime.fromisoformat(str(value["runAt"])))
    if value.get("type") == "interval":
        return IntervalSchedule(
            datetime.fromisoformat(str(value["startAt"])),
            int(value["intervalSeconds"]),
        )
    raise TaskError(f"Task 数据损坏: 无效 schedule type {value.get('type')!r}。")


def _decode_execution(value: Any) -> TaskExecution:
    if not isinstance(value, dict):
        raise TaskError("Task 数据损坏: execution 必须为 object。")
    try:
        execution_id = str(value["executionId"])
        status = str(value["status"])
        if not EXECUTION_ID_PATTERN.fullmatch(execution_id):
            raise TaskError("Task 数据损坏: 无效 executionId。")
        if status not in EXECUTION_STATUSES:
            raise TaskError("Task 数据损坏: 无效 execution status。")
        finished_value = value.get("finishedAt")
        return TaskExecution(
            execution_id=execution_id,
            scheduled_for=datetime.fromisoformat(str(value["scheduledFor"])),
            started_at=datetime.fromisoformat(str(value["startedAt"])),
            status=status,  # type: ignore[arg-type]
            finished_at=(
                datetime.fromisoformat(str(finished_value))
                if finished_value is not None
                else None
            ),
            assistant_reply=str(value.get("assistantReply", "")),
            error_code=str(value.get("errorCode", "")),
            error_message=str(value.get("errorMessage", "")),
        )
    except TaskError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise TaskError(f"Task execution 数据损坏: {exc}") from exc
