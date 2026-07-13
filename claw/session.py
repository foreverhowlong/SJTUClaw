"""Immutable conversation snapshot for one persisted session."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from claw.llm import Message


@dataclass(frozen=True)
class Session:
    """Describe one committed version of a conversation."""

    session_id: str = field(default_factory=lambda: f"session_{uuid4().hex[:12]}")
    title: str = "新会话"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    revision: int = 0
    _messages: tuple[Message, ...] = field(default_factory=tuple, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_messages",
            tuple(message.copy() for message in self._messages),
        )

    @property
    def messages(self) -> list[Message]:
        """Return a copy so callers cannot mutate session state accidentally."""
        return [message.copy() for message in self._messages]

    @property
    def message_count(self) -> int:
        return len(self._messages)
