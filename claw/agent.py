"""Core conversation service shared by user-facing entry points."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from claw.compaction import CompactionResult, Compactor
from claw.context import ContextBuilder
from claw.llm import Message
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore


class ChatClient(Protocol):
    def chat(self, messages: list[Message]) -> str: ...


@dataclass(frozen=True)
class TurnResult:
    reply: str
    compaction: CompactionResult | None = None


class AgentService:
    """Run one conversational turn without performing terminal I/O."""

    def __init__(
        self,
        llm: ChatClient,
        store: SessionStore,
        context_builder: ContextBuilder,
        memory_store: MemoryStore,
        compactor: Compactor | None = None,
    ) -> None:
        self._llm = llm
        self._store = store
        self._context_builder = context_builder
        self._memory_store = memory_store
        self._compactor = compactor

    def run_turn(self, session_id: str, user_input: str) -> TurnResult:
        """Compute and commit one completed turn for an explicit session."""
        if not user_input.strip():
            raise ValueError("user_input 不能为空。")

        compaction_result: CompactionResult | None = None
        if self._compactor is not None:
            attempted = self._compactor.compact(session_id)
            if attempted.status != "skipped":
                compaction_result = attempted

        snapshot = self._store.load(session_id)
        user_message: Message = {"role": "user", "content": user_input}
        reply = self._llm.chat(
            self._context_builder.build(
                [*snapshot.messages, user_message],
                self._memory_store.list(),
                snapshot.summary,
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
        return TurnResult(reply=reply, compaction=compaction_result)

    def compact_session(
        self,
        session_id: str,
        *,
        force: bool = True,
    ) -> CompactionResult:
        """Compact one explicit session without treating the command as chat."""
        if self._compactor is None:
            snapshot = self._store.load(session_id)
            return CompactionResult(
                session_id=session_id,
                status="failed",
                old_message_count=0,
                recent_message_count=snapshot.message_count,
                summary=snapshot.summary,
                detail="runtime 未配置 compactor，旧消息未删除。",
            )
        return self._compactor.compact(session_id, force=force)
