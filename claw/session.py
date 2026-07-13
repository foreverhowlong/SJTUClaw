"""In-memory conversation state for a single session."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from claw.llm import Message


ConversationRole = Literal["user", "assistant"]


@dataclass
class Session:
    """Store the completed message history for one in-process conversation."""

    _messages: list[Message] = field(default_factory=list, repr=False)

    @property
    def messages(self) -> list[Message]:
        """Return a copy so callers cannot mutate session state accidentally."""
        return [message.copy() for message in self._messages]

    def append(self, role: ConversationRole, content: str) -> None:
        self._messages.append({"role": role, "content": content})

    def discard_last_user_message(self) -> None:
        """Roll back the pending user message after a failed LLM request."""
        if not self._messages or self._messages[-1]["role"] != "user":
            raise RuntimeError("没有可回滚的 user 消息。")
        self._messages.pop()
