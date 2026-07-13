"""Append-only storage for independent conversation sessions."""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from filelock import FileLock, Timeout

from claw.errors import SessionConflictError, SessionError
from claw.messages import Message
from claw.session import Session
from claw.store.session_log import parse_datetime, read_message_log
from claw.store.session_messages import validate_history, validate_turn


SESSION_ID_PATTERN = re.compile(r"session_[0-9a-f]{12}")
LOCK_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime


class SessionStore:
    """Persist metadata plus append-only, revisioned turn records."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def create(self, title: str = "新会话") -> Session:
        normalized_title = title.strip() or "新会话"
        while True:
            session = Session(title=normalized_title)
            session_dir = self._session_dir(session.session_id)
            if not session_dir.exists():
                break

        temporary = self.root / f".{session.session_id}.{uuid4().hex}.tmp"
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            temporary.mkdir()
            self._atomic_write(temporary / "messages.jsonl", "")
            self._atomic_write(
                temporary / "meta.json",
                self._serialize_meta(session),
            )
            os.replace(temporary, session_dir)
        except (OSError, TypeError, ValueError) as exc:
            raise SessionError(f"创建 session 失败: {exc}") from exc
        finally:
            try:
                shutil.rmtree(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        return session

    def list(self) -> list[SessionSummary]:
        if not self.root.exists():
            return []
        try:
            directories = sorted(
                path
                for path in self.root.iterdir()
                if path.is_dir()
                and not path.is_symlink()
                and SESSION_ID_PATTERN.fullmatch(path.name)
            )
        except OSError as exc:
            raise SessionError(f"无法列出 session 目录 {self.root}: {exc}") from exc

        summaries = [self._summary(self.load(path.name)) for path in directories]
        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    def load(self, session_id: str) -> Session:
        with self._locked(session_id):
            return self._load_unlocked(session_id)

    def commit_turn(
        self,
        session_id: str,
        *,
        expected_revision: int,
        messages: Sequence[Message],
    ) -> Session:
        committed_messages = validate_turn(messages)
        with self._locked(session_id):
            snapshot = self._load_unlocked(session_id)
            if snapshot.revision != expected_revision:
                raise SessionConflictError(
                    f"Session {session_id} 已更新: expected revision "
                    f"{expected_revision}, current revision {snapshot.revision}。"
                )

            committed_at = datetime.now(timezone.utc)
            record = {
                "type": "turn",
                "revision": snapshot.revision + 1,
                "turnId": f"turn_{uuid4().hex[:12]}",
                "committedAt": committed_at.isoformat(),
                "messages": committed_messages,
            }
            self._append_record(
                self._session_dir(session_id) / "messages.jsonl",
                record,
            )
            return Session(
                session_id=snapshot.session_id,
                title=snapshot.title,
                created_at=snapshot.created_at,
                updated_at=committed_at,
                revision=snapshot.revision + 1,
                summary=snapshot.summary,
                _messages=tuple([*snapshot.messages, *committed_messages]),
            )

    def commit_compaction(
        self,
        session_id: str,
        *,
        expected_revision: int,
        summary: str,
        recent_messages: Sequence[Message],
    ) -> Session:
        """Atomically append a new logical boundary for active conversation state."""
        normalized_summary = summary.strip()
        if not normalized_summary:
            raise SessionError("session summary 不能为空。")
        retained = validate_history(recent_messages)

        with self._locked(session_id):
            snapshot = self._load_unlocked(session_id)
            if snapshot.revision != expected_revision:
                raise SessionConflictError(
                    f"Session {session_id} 已更新: expected revision "
                    f"{expected_revision}, current revision {snapshot.revision}。"
                )
            if len(retained) >= snapshot.message_count:
                raise SessionError("compaction 必须压缩至少一条旧消息。")
            if snapshot.messages[-len(retained) :] != retained:
                raise SessionError("recent messages 必须是当前 session 历史的后缀。")

            compacted_at = datetime.now(timezone.utc)
            old_message_count = snapshot.message_count - len(retained)
            record = {
                "type": "compaction",
                "revision": snapshot.revision + 1,
                "compactedAt": compacted_at.isoformat(),
                "summary": normalized_summary,
                "oldMessageCount": old_message_count,
                "recentMessageCount": len(retained),
                "recentMessages": retained,
            }
            self._append_record(
                self._session_dir(session_id) / "messages.jsonl",
                record,
            )
            return Session(
                session_id=snapshot.session_id,
                title=snapshot.title,
                created_at=snapshot.created_at,
                updated_at=compacted_at,
                revision=snapshot.revision + 1,
                summary=normalized_summary,
                _messages=tuple(retained),
            )

    def rename(self, session_id: str, title: str) -> Session:
        normalized = title.strip()
        if not normalized:
            raise SessionError("session title 不能为空。")

        with self._locked(session_id):
            snapshot = self._load_unlocked(session_id)
            renamed = Session(
                session_id=snapshot.session_id,
                title=normalized,
                created_at=snapshot.created_at,
                updated_at=datetime.now(timezone.utc),
                revision=snapshot.revision,
                summary=snapshot.summary,
                _messages=tuple(snapshot.messages),
            )
            try:
                self._atomic_write(
                    self._session_dir(session_id) / "meta.json",
                    self._serialize_meta(renamed),
                )
            except (OSError, TypeError, ValueError) as exc:
                raise SessionError(f"重命名 session {session_id} 失败: {exc}") from exc
            return renamed

    def delete(self, session_id: str) -> None:
        with self._locked(session_id):
            session_dir = self._existing_session_dir(session_id)
            try:
                shutil.rmtree(session_dir)
            except OSError as exc:
                raise SessionError(f"删除 session {session_id} 失败: {exc}") from exc

    def _load_unlocked(self, session_id: str) -> Session:
        session_dir = self._existing_session_dir(session_id)
        meta_path = session_dir / "meta.json"
        messages_path = session_dir / "messages.jsonl"
        meta = self._read_meta(meta_path)
        log = read_message_log(messages_path)

        if meta.get("sessionId") != session_id:
            raise SessionError(
                f"Session 数据损坏: {meta_path} 中的 sessionId 与目录名不一致。"
            )
        title = meta.get("title")
        if not isinstance(title, str) or not title.strip():
            raise SessionError(f"Session 数据损坏: {meta_path} 中的 title 无效。")

        created_at = parse_datetime(meta.get("createdAt"), meta_path, "createdAt")
        metadata_updated_at = parse_datetime(
            meta.get("updatedAt"),
            meta_path,
            "updatedAt",
        )
        updated_at = max(
            metadata_updated_at,
            log.last_committed_at or metadata_updated_at,
        )
        return Session(
            session_id=session_id,
            title=title,
            created_at=created_at,
            updated_at=updated_at,
            revision=log.revision,
            summary=log.summary,
            _messages=log.messages,
        )

    def _append_record(self, path: Path, record: dict[str, Any]) -> None:
        try:
            encoded = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise SessionError(f"序列化 session turn 失败: {exc}") from exc

        try:
            with path.open("a+b") as handle:
                handle.seek(0, os.SEEK_END)
                original_size = handle.tell()
                try:
                    written = handle.write(encoded)
                    if written != len(encoded):
                        raise OSError(f"short write: {written}/{len(encoded)} bytes")
                    handle.flush()
                    os.fsync(handle.fileno())
                except OSError as exc:
                    try:
                        handle.seek(original_size)
                        handle.truncate()
                        handle.flush()
                        os.fsync(handle.fileno())
                    except OSError as rollback_exc:
                        raise SessionError(
                            f"提交 session turn 失败且回滚失败: {exc}; {rollback_exc}"
                        ) from rollback_exc
                    raise SessionError(f"提交 session turn 失败: {exc}") from exc
        except FileNotFoundError as exc:
            raise SessionError(f"Session 数据损坏: 缺少 {path}。") from exc
        except OSError as exc:
            raise SessionError(f"打开 session 消息文件失败 {path}: {exc}") from exc

    @contextmanager
    def _locked(self, session_id: str) -> Iterator[None]:
        self._validate_session_id(session_id)
        lock_dir = self.root / ".locks"
        try:
            lock_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SessionError(f"创建 session 锁目录失败 {lock_dir}: {exc}") from exc
        lock = FileLock(lock_dir / f"{session_id}.lock", timeout=LOCK_TIMEOUT_SECONDS)
        try:
            with lock:
                yield
        except Timeout as exc:
            raise SessionError(f"等待 session {session_id} 锁超时。") from exc
        except OSError as exc:
            raise SessionError(f"获取 session {session_id} 锁失败: {exc}") from exc

    def _summary(self, session: Session) -> SessionSummary:
        return SessionSummary(
            session_id=session.session_id,
            title=session.title,
            message_count=session.message_count,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    def _session_dir(self, session_id: str) -> Path:
        self._validate_session_id(session_id)
        return self.root / session_id

    def _existing_session_dir(self, session_id: str) -> Path:
        session_dir = self._session_dir(session_id)
        if session_dir.is_symlink() or not session_dir.is_dir():
            raise SessionError(f"Session 不存在: {session_id}。")
        return session_dir

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise SessionError(f"无效的 sessionId: {session_id!r}。")

    def _read_meta(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SessionError(f"Session 数据损坏: 缺少 {path}。") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SessionError(f"读取 session 元数据失败 {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise SessionError(f"Session 数据损坏: {path} 必须包含 JSON object。")
        return value

    @staticmethod
    def _serialize_meta(session: Session) -> str:
        return json.dumps(
            {
                "sessionId": session.session_id,
                "title": session.title,
                "createdAt": session.created_at.isoformat(),
                "updatedAt": session.updated_at.isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n"

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
