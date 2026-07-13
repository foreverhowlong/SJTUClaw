"""Conversation state for one independently persisted session."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from claw.llm import Message


ConversationRole = Literal["user", "assistant"]


@dataclass
class Session:
    """Store metadata and completed message history for one conversation."""

    session_id: str = field(default_factory=lambda: f"session_{uuid4().hex[:12]}")
    title: str = "新会话"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _messages: list[Message] = field(default_factory=list, repr=False)

    @property
    def messages(self) -> list[Message]:
        """Return a copy so callers cannot mutate session state accidentally."""
        return [message.copy() for message in self._messages]

    def append(self, role: ConversationRole, content: str) -> None:
        self._messages.append({"role": role, "content": content})
        self.updated_at = datetime.now(timezone.utc)

    def discard_last_user_message(self, *, updated_at: datetime | None = None) -> None:
        """Roll back the pending user message after a failed LLM request."""
        if not self._messages or self._messages[-1]["role"] != "user":
            raise RuntimeError("没有可回滚的 user 消息。")
        self._messages.pop()
        self.updated_at = updated_at or datetime.now(timezone.utc)

    def rename(self, title: str) -> None:
        normalized = title.strip()
        if not normalized:
            raise ValueError("session title 不能为空。")
        self.title = normalized
        self.updated_at = datetime.now(timezone.utc)

    def restore(self, messages: list[Message], updated_at: datetime) -> None:
        """Restore a previously captured state after a failed operation."""
        self._messages = [message.copy() for message in messages]
        self.updated_at = updated_at

    @property
    def message_count(self) -> int:
        return len(self._messages)
