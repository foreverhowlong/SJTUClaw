"""Core conversation service shared by user-facing entry points."""

from __future__ import annotations

from typing import Protocol

from claw.context import ContextBuilder
from claw.llm import Message
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore


class ChatClient(Protocol):
    def chat(self, messages: list[Message]) -> str: ...


class AgentService:
    """Run one conversational turn without performing terminal I/O."""

    def __init__(
        self,
        llm: ChatClient,
        store: SessionStore,
        context_builder: ContextBuilder,
        memory_store: MemoryStore,
    ) -> None:
        self._llm = llm
        self._store = store
        self._context_builder = context_builder
        self._memory_store = memory_store

    def run_turn(self, session_id: str, user_input: str) -> str:
        """Compute and commit one completed turn for an explicit session."""
        if not user_input.strip():
            raise ValueError("user_input 不能为空。")

        snapshot = self._store.load(session_id)
        user_message: Message = {"role": "user", "content": user_input}
        reply = self._llm.chat(
            self._context_builder.build(
                [*snapshot.messages, user_message],
                self._memory_store.list(),
            )
        )
        self._store.commit_turn(
            session_id,
            expected_revision=snapshot.revision,
            messages=[
                user_message,
                {"role": "assistant", "content": reply},
            ],
        )
        return reply
