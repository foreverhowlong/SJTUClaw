"""File-backed storage for independent conversation sessions."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from claw.errors import SessionError
from claw.llm import Message
from claw.session import Session


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime


class SessionStore:
    """Persist each session in its own directory under ``data/sessions``."""

    def __init__(self, root: str | Path = "data/sessions") -> None:
        self.root = Path(root)

    def create(self, title: str = "新会话") -> Session:
        session = Session(title=title.strip() or "新会话")
        while self._session_dir(session.session_id).exists():
            session = Session(title=title.strip() or "新会话")
        self.save(session)
        return session

    def list(self) -> list[SessionSummary]:
        if not self.root.exists():
            return []
        try:
            directories = sorted(path for path in self.root.iterdir() if path.is_dir())
        except OSError as exc:
            raise SessionError(f"无法列出 session 目录 {self.root}: {exc}") from exc

        summaries = [self._summary(self.load(path.name)) for path in directories]
        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    def load(self, session_id: str) -> Session:
        session_dir = self._existing_session_dir(session_id)
        meta_path = session_dir / "meta.json"
        messages_path = session_dir / "messages.jsonl"
        meta = self._read_meta(meta_path)
        messages = self._read_messages(messages_path)

        if meta.get("sessionId") != session_id:
            raise SessionError(
                f"Session 数据损坏: {meta_path} 中的 sessionId 与目录名不一致。"
            )
        title = meta.get("title")
        if not isinstance(title, str) or not title.strip():
            raise SessionError(f"Session 数据损坏: {meta_path} 中的 title 无效。")

        created_at = self._parse_datetime(meta.get("createdAt"), meta_path, "createdAt")
        updated_at = self._parse_datetime(meta.get("updatedAt"), meta_path, "updatedAt")
        return Session(
            session_id=session_id,
            title=title,
            created_at=created_at,
            updated_at=updated_at,
            _messages=messages,
        )

    def save(self, session: Session) -> None:
        session_dir = self._session_dir(session.session_id)
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
            meta = {
                "sessionId": session.session_id,
                "title": session.title,
                "createdAt": session.created_at.isoformat(),
                "updatedAt": session.updated_at.isoformat(),
            }
            messages_text = "".join(
                json.dumps(message, ensure_ascii=False) + "\n" for message in session.messages
            )
            self._atomic_write(session_dir / "messages.jsonl", messages_text)
            self._atomic_write(
                session_dir / "meta.json",
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            )
        except (OSError, TypeError, ValueError) as exc:
            raise SessionError(f"保存 session {session.session_id} 失败: {exc}") from exc

    def rename(self, session_id: str, title: str) -> Session:
        session = self.load(session_id)
        try:
            session.rename(title)
        except ValueError as exc:
            raise SessionError(str(exc)) from exc
        self.save(session)
        return session

    def delete(self, session_id: str) -> None:
        session_dir = self._existing_session_dir(session_id)
        try:
            shutil.rmtree(session_dir)
        except OSError as exc:
            raise SessionError(f"删除 session {session_id} 失败: {exc}") from exc

    def _summary(self, session: Session) -> SessionSummary:
        return SessionSummary(
            session_id=session.session_id,
            title=session.title,
            message_count=session.message_count,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    def _session_dir(self, session_id: str) -> Path:
        if not session_id or session_id in {".", ".."} or Path(session_id).name != session_id:
            raise SessionError(f"无效的 sessionId: {session_id!r}。")
        return self.root / session_id

    def _existing_session_dir(self, session_id: str) -> Path:
        session_dir = self._session_dir(session_id)
        if not session_dir.is_dir():
            raise SessionError(f"Session 不存在: {session_id}。")
        return session_dir

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

    def _read_messages(self, path: Path) -> list[Message]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError as exc:
            raise SessionError(f"Session 数据损坏: 缺少 {path}。") from exc
        except (OSError, UnicodeError) as exc:
            raise SessionError(f"读取 session 消息失败 {path}: {exc}") from exc

        messages: list[Message] = []
        for line_number, line in enumerate(lines, start=1):
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SessionError(
                    f"Session 数据损坏: {path} 第 {line_number} 行不是有效 JSON。"
                ) from exc
            if (
                not isinstance(message, dict)
                or message.get("role") not in {"user", "assistant"}
                or not isinstance(message.get("content"), str)
            ):
                raise SessionError(
                    f"Session 数据损坏: {path} 第 {line_number} 行消息格式无效。"
                )
            messages.append({"role": message["role"], "content": message["content"]})
        return messages

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
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_name(f".{path.name}.tmp")
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
