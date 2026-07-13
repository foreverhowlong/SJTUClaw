"""Core conversation service shared by user-facing entry points."""

from __future__ import annotations

from typing import Protocol

from claw.llm import Message
from claw.session import Session


class ChatClient(Protocol):
    def chat(self, messages: list[Message]) -> str: ...


class AgentService:
    """Run one conversational turn without performing terminal I/O."""

    def __init__(self, llm: ChatClient, session: Session | None = None) -> None:
        self._llm = llm
        self._session = session or Session()

    @property
    def session(self) -> Session:
        return self._session

    def send_message(self, user_input: str) -> str:
        if not user_input.strip():
            raise ValueError("user_input 不能为空。")

        self._session.append("user", user_input)
        try:
            reply = self._llm.chat(self._session.messages)
        except (Exception, KeyboardInterrupt):
            self._session.discard_last_user_message()
            raise

        self._session.append("assistant", reply)
        return reply
