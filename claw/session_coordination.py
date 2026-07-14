"""Shared long-lived leases for session turns and lifecycle mutations."""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import AsyncIterator

from filelock import FileLock, Timeout

from claw.errors import SessionConflictError, SessionError


SESSION_ID_PATTERN = re.compile(r"session_[0-9a-f]{12}")


@dataclass
class _LeaseEntry:
    lock: asyncio.Lock
    users: int = 0


class SessionCoordinator:
    """Serialize turns and destructive metadata changes across all entry points."""

    def __init__(self, root: str | Path, *, cross_process_timeout: float = 10) -> None:
        if cross_process_timeout <= 0:
            raise ValueError("cross_process_timeout 必须大于 0。")
        self._root = Path(root)
        self._cross_process_timeout = cross_process_timeout
        self._entries: dict[str, _LeaseEntry] = {}

    @asynccontextmanager
    async def turn(self, session_id: str) -> AsyncIterator[None]:
        async with self._lease(session_id, wait=True):
            yield

    @asynccontextmanager
    async def mutation(self, session_id: str) -> AsyncIterator[None]:
        async with self._lease(session_id, wait=False):
            yield

    @asynccontextmanager
    async def _lease(self, session_id: str, *, wait: bool) -> AsyncIterator[None]:
        self._validate(session_id)
        entry = self._entries.setdefault(session_id, _LeaseEntry(asyncio.Lock()))
        entry.users += 1
        local_acquired = False
        process_lock: FileLock | None = None
        try:
            if not wait and entry.lock.locked():
                raise SessionConflictError(f"Session 正在运行，暂时不能修改: {session_id}。")
            await entry.lock.acquire()
            local_acquired = True
            process_lock = await self._acquire_process_lock(session_id, wait=wait)
            yield
        finally:
            if process_lock is not None:
                process_lock.release()
            if local_acquired:
                entry.lock.release()
            entry.users -= 1
            if entry.users == 0 and not entry.lock.locked():
                self._entries.pop(session_id, None)

    async def _acquire_process_lock(self, session_id: str, *, wait: bool) -> FileLock:
        lock_dir = self._root / ".turn-locks"
        try:
            lock_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SessionError(f"创建 session turn 锁目录失败: {exc}") from exc
        lock = FileLock(lock_dir / f"{session_id}.lock")
        deadline = monotonic() + self._cross_process_timeout
        while True:
            try:
                lock.acquire(timeout=0)
                return lock
            except Timeout as exc:
                if not wait or monotonic() >= deadline:
                    raise SessionConflictError(
                        f"Session 正在其他 runtime 中运行: {session_id}。"
                    ) from exc
                await asyncio.sleep(0.05)

    @staticmethod
    def _validate(session_id: str) -> None:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise SessionError(f"无效的 sessionId: {session_id!r}。")
