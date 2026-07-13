"""Core conversation service shared by user-facing entry points."""

from __future__ import annotations

from typing import Protocol

from claw.context import ContextBuilder
from claw.llm import Message
from claw.session import Session
from claw.store.memory import MemoryRecord, MemoryStore
from claw.store.sessions import SessionStore, SessionSummary


class ChatClient(Protocol):
    def chat(self, messages: list[Message]) -> str: ...


class AgentService:
    """Run one conversational turn without performing terminal I/O."""

    def __init__(
        self,
        llm: ChatClient,
        session: Session | None = None,
        *,
        store: SessionStore | None = None,
        context_builder: ContextBuilder | None = None,
        memory_store: MemoryStore | None = None,
    ) -> None:
        if session is not None and store is not None:
            raise ValueError("session 和 store 不能同时传入。")
        self._llm = llm
        self._store = store
        self._context_builder = context_builder or ContextBuilder.from_files()
        self._memory_store = memory_store or MemoryStore()
        if store is None:
            self._session = session or Session()
        else:
            sessions = store.list()
            self._session = store.load(sessions[0].session_id) if sessions else store.create()

    @property
    def session(self) -> Session:
        return self._session

    def create_session(self, title: str = "新会话") -> Session:
        store = self._require_store()
        self._session = store.create(title)
        return self._session

    def list_sessions(self) -> list[SessionSummary]:
        return self._require_store().list()

    def switch_session(self, session_id: str) -> Session:
        self._session = self._require_store().load(session_id)
        return self._session

    def rename_session(self, session_id: str, title: str) -> Session:
        renamed = self._require_store().rename(session_id, title)
        if self._session.session_id == session_id:
            self._session = renamed
        return renamed

    def delete_session(self, session_id: str) -> Session:
        store = self._require_store()
        deleting_current = self._session.session_id == session_id
        store.delete(session_id)
        if deleting_current:
            remaining = store.list()
            self._session = store.load(remaining[0].session_id) if remaining else store.create()
        return self._session

    def add_memory(self, content: str) -> MemoryRecord:
        return self._memory_store.add(content)

    def list_memories(self) -> list[MemoryRecord]:
        return self._memory_store.list()

    def delete_memory(self, memory_id: str) -> None:
        self._memory_store.delete(memory_id)

    def send_message(self, user_input: str) -> str:
        if not user_input.strip():
            raise ValueError("user_input 不能为空。")

        previous_messages = self._session.messages
        previous_updated_at = self._session.updated_at
        self._session.append("user", user_input)
        try:
            reply = self._llm.chat(
                self._context_builder.build(self._session, self._memory_store.list())
            )
        except (Exception, KeyboardInterrupt):
            self._session.restore(previous_messages, previous_updated_at)
            raise

        self._session.append("assistant", reply)
        if self._store is not None:
            try:
                self._store.save(self._session)
            except (Exception, KeyboardInterrupt):
                self._session.restore(previous_messages, previous_updated_at)
                raise
        return reply

    def _require_store(self) -> SessionStore:
        if self._store is None:
            raise RuntimeError("AgentService 未配置 SessionStore。")
        return self._store
