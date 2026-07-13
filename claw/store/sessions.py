"""Append-only storage for independent conversation sessions."""

from __future__ import annotations

import json
import os
import re
import shutil
from copy import deepcopy
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from filelock import FileLock, Timeout

from claw.errors import SessionConflictError, SessionError
from claw.llm import Message
from claw.session import Session


SESSION_ID_PATTERN = re.compile(r"session_[0-9a-f]{12}")
LOCK_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class _MessageLog:
    messages: tuple[Message, ...]
    summary: str
    revision: int
    last_committed_at: datetime | None


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
        committed_messages = self._validate_turn(messages)
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
        retained = self._validate_history(recent_messages)

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
        log = self._read_log(messages_path)

        if meta.get("sessionId") != session_id:
            raise SessionError(
                f"Session 数据损坏: {meta_path} 中的 sessionId 与目录名不一致。"
            )
        title = meta.get("title")
        if not isinstance(title, str) or not title.strip():
            raise SessionError(f"Session 数据损坏: {meta_path} 中的 title 无效。")

        created_at = self._parse_datetime(meta.get("createdAt"), meta_path, "createdAt")
        metadata_updated_at = self._parse_datetime(
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

    def _read_log(self, path: Path) -> _MessageLog:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError as exc:
            raise SessionError(f"Session 数据损坏: 缺少 {path}。") from exc
        except (OSError, UnicodeError) as exc:
            raise SessionError(f"读取 session 消息失败 {path}: {exc}") from exc

        messages: list[Message] = []
        summary = ""
        revision = 0
        last_committed_at: datetime | None = None
        for line_number, line in enumerate(lines, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SessionError(
                    f"Session 数据损坏: {path} 第 {line_number} 行不是有效 JSON。"
                ) from exc

            if self._is_message(record):
                messages.append(self._copy_message(record))
                revision += 1
                continue

            if not isinstance(record, dict) or record.get("type") not in {
                "turn",
                "compaction",
            }:
                raise SessionError(
                    f"Session 数据损坏: {path} 第 {line_number} 行记录格式无效。"
                )
            expected_record_revision = revision + 1
            if record.get("revision") != expected_record_revision:
                raise SessionError(
                    f"Session 数据损坏: {path} 第 {line_number} 行 revision 无效。"
                )
            if record["type"] == "turn":
                turn_messages = record.get("messages")
                if not isinstance(turn_messages, list):
                    raise SessionError(
                        f"Session 数据损坏: {path} 第 {line_number} 行 messages 无效。"
                    )
                try:
                    validated = self._validate_turn(turn_messages)
                except SessionError as exc:
                    raise SessionError(
                        f"Session 数据损坏: {path} 第 {line_number} 行: {exc}"
                    ) from exc
                committed_at = self._parse_datetime(
                    record.get("committedAt"),
                    path,
                    f"第 {line_number} 行 committedAt",
                )
                messages.extend(validated)
            else:
                committed_at, summary, messages = self._read_compaction_record(
                    record,
                    current_messages=messages,
                    path=path,
                    line_number=line_number,
                )
            revision = expected_record_revision
            last_committed_at = committed_at

        return _MessageLog(tuple(messages), summary, revision, last_committed_at)

    def _read_compaction_record(
        self,
        record: dict[str, Any],
        *,
        current_messages: list[Message],
        path: Path,
        line_number: int,
    ) -> tuple[datetime, str, list[Message]]:
        summary = record.get("summary")
        recent_messages = record.get("recentMessages")
        old_message_count = record.get("oldMessageCount")
        recent_message_count = record.get("recentMessageCount")
        if not isinstance(summary, str) or not summary.strip():
            raise SessionError(
                f"Session 数据损坏: {path} 第 {line_number} 行 summary 无效。"
            )
        if not isinstance(recent_messages, list):
            raise SessionError(
                f"Session 数据损坏: {path} 第 {line_number} 行 recentMessages 无效。"
            )
        try:
            retained = self._validate_history(recent_messages)
        except SessionError as exc:
            raise SessionError(
                f"Session 数据损坏: {path} 第 {line_number} 行: {exc}"
            ) from exc
        expected_old_count = len(current_messages) - len(retained)
        if (
            not isinstance(old_message_count, int)
            or old_message_count <= 0
            or old_message_count != expected_old_count
            or recent_message_count != len(retained)
            or current_messages[-len(retained) :] != retained
        ):
            raise SessionError(
                f"Session 数据损坏: {path} 第 {line_number} 行 compaction 边界无效。"
            )
        compacted_at = self._parse_datetime(
            record.get("compactedAt"),
            path,
            f"第 {line_number} 行 compactedAt",
        )
        return compacted_at, summary.strip(), retained

    @staticmethod
    def _validate_turn(messages: Sequence[Message]) -> list[Message]:
        copied = [SessionStore._copy_message(message) for message in messages]
        if len(copied) < 2:
            raise SessionError("一个已完成 turn 至少包含 user 和 assistant 消息。")
        if not SessionStore._is_text_message(copied[0], "user"):
            raise SessionError("turn 必须以 user 文本消息开始。")
        if not SessionStore._is_text_message(copied[-1], "assistant"):
            raise SessionError("turn 必须以 assistant 最终文本消息结束。")

        index = 1
        while index < len(copied) - 1:
            assistant = copied[index]
            call_ids = SessionStore._tool_call_ids(assistant)
            if not call_ids:
                raise SessionError("turn 中间消息必须是 assistant tool call。")
            if len(set(call_ids)) != len(call_ids):
                raise SessionError("同一 assistant 消息中的 tool call id 必须唯一。")
            results = copied[index + 1 : index + 1 + len(call_ids)]
            if len(results) != len(call_ids):
                raise SessionError("每个 tool call 都必须有对应 tool result。")
            result_ids: list[str] = []
            for result in results:
                if not SessionStore._is_tool_result(result):
                    raise SessionError("tool result 消息格式无效。")
                result_ids.append(result["tool_call_id"])
            if set(result_ids) != set(call_ids) or len(set(result_ids)) != len(result_ids):
                raise SessionError("tool result 必须与本轮 tool call id 一一对应。")
            index += 1 + len(call_ids)
        return copied

    @staticmethod
    def _validate_history(messages: Sequence[Message]) -> list[Message]:
        copied = [SessionStore._copy_message(message) for message in messages]
        if not copied:
            raise SessionError("recent messages 必须包含至少一个完整 turn。")
        starts = [index for index, message in enumerate(copied) if message.get("role") == "user"]
        if not starts or starts[0] != 0:
            raise SessionError("recent messages 必须从完整 user turn 开始。")
        for position, start in enumerate(starts):
            end = starts[position + 1] if position + 1 < len(starts) else len(copied)
            SessionStore._validate_turn(copied[start:end])
        return copied

    @staticmethod
    def _is_message(value: Any) -> bool:
        """Recognize the legacy one-message-per-line JSONL format."""
        return (
            isinstance(value, dict)
            and value.get("role") in {"user", "assistant"}
            and isinstance(value.get("content"), str)
            and "tool_calls" not in value
        )

    @staticmethod
    def _copy_message(value: Any) -> Message:
        if not isinstance(value, dict) or not isinstance(value.get("role"), str):
            raise SessionError("消息格式无效。")
        try:
            copied = deepcopy(value)
            json.dumps(copied, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise SessionError(f"消息必须可 JSON 序列化: {exc}") from exc
        return copied

    @staticmethod
    def _is_text_message(value: Message, role: str) -> bool:
        return (
            value.get("role") == role
            and isinstance(value.get("content"), str)
            and bool(value["content"].strip())
            and "tool_calls" not in value
        )

    @staticmethod
    def _tool_call_ids(value: Message) -> list[str]:
        if value.get("role") != "assistant":
            return []
        content = value.get("content")
        if content is not None and not isinstance(content, str):
            return []
        calls = value.get("tool_calls")
        if not isinstance(calls, list) or not calls:
            return []
        call_ids: list[str] = []
        for call in calls:
            if not isinstance(call, dict) or call.get("type") != "function":
                return []
            call_id = call.get("id")
            function = call.get("function")
            if (
                not isinstance(call_id, str)
                or not call_id
                or not isinstance(function, dict)
                or not isinstance(function.get("name"), str)
                or not function["name"]
                or not isinstance(function.get("arguments"), str)
            ):
                return []
            call_ids.append(call_id)
        return call_ids

    @staticmethod
    def _is_tool_result(value: Message) -> bool:
        return (
            value.get("role") == "tool"
            and isinstance(value.get("tool_call_id"), str)
            and bool(value["tool_call_id"])
            and isinstance(value.get("name"), str)
            and bool(value["name"])
            and isinstance(value.get("content"), str)
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
    def _parse_datetime(value: Any, path: Path, field: str) -> datetime:
        if not isinstance(value, str):
            raise SessionError(f"Session 数据损坏: {path} 中的 {field} 无效。")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise SessionError(f"Session 数据损坏: {path} 中的 {field} 无效。") from exc
        if parsed.tzinfo is None:
            raise SessionError(f"Session 数据损坏: {path} 中的 {field} 缺少时区。")
        return parsed

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
