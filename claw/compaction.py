"""Safe session-history compaction backed by the shared LLM and SessionStore."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal, Protocol

from claw.context import project_messages
from claw.errors import ConfigError, LLMError, SessionError
from claw.llm import Message
from claw.store.sessions import SessionStore


DEFAULT_COMPACTION_PROMPT_RESOURCE = "prompts/compaction.md"


class ChatClient(Protocol):
    async def chat(self, messages: list[Message]) -> str: ...


@dataclass(frozen=True)
class CompactionPolicy:
    """Use deterministic serialized request characters as the context budget."""

    max_context_chars: int = 80_000
    recent_context_chars: int = 20_000

    def __post_init__(self) -> None:
        if self.recent_context_chars <= 0:
            raise ValueError("recent_context_chars 必须大于 0。")
        if self.max_context_chars <= self.recent_context_chars:
            raise ValueError("max_context_chars 必须大于 recent_context_chars。")


CompactionStatus = Literal["compacted", "skipped", "failed", "unavailable"]


@dataclass(frozen=True)
class CompactionResult:
    session_id: str
    status: CompactionStatus
    old_message_count: int
    recent_message_count: int
    summary: str = ""
    detail: str = ""

    @property
    def compacted(self) -> bool:
        return self.status == "compacted"


class Compactor:
    """Summarize only one session's old conversation messages."""

    def __init__(
        self,
        llm: ChatClient,
        store: SessionStore,
        prompt: str,
        policy: CompactionPolicy | None = None,
    ) -> None:
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("compaction prompt 不能为空。")
        self._llm = llm
        self._store = store
        self._prompt = normalized_prompt
        self._policy = policy or CompactionPolicy()

    def should_compact(self, request_chars: int) -> bool:
        return request_chars > self._policy.max_context_chars

    async def compact(
        self,
        session_id: str,
        *,
        force: bool = False,
        request_chars: int | None = None,
    ) -> CompactionResult:
        snapshot = self._store.load(session_id)
        measured_chars = (
            serialized_request_chars(snapshot.messages)
            if request_chars is None
            else request_chars
        )
        if not force and measured_chars <= self._policy.max_context_chars:
            return CompactionResult(
                session_id=session_id,
                status="skipped",
                old_message_count=0,
                recent_message_count=snapshot.message_count,
                detail=(
                    f"request characters={measured_chars}，"
                    f"尚未超过阈值 {self._policy.max_context_chars}。"
                ),
            )
        turns = _split_turns(snapshot.messages)
        if len(turns) <= 1:
            return CompactionResult(
                session_id=session_id,
                status="skipped",
                old_message_count=0,
                recent_message_count=snapshot.message_count,
                summary=snapshot.summary,
                detail="没有足够的完整旧 turns 可压缩。",
            )

        recent_turns = [turns[-1]]
        for turn in reversed(turns[:-1]):
            candidate = [*turn, *(message for item in recent_turns for message in item)]
            if (
                serialized_request_chars(project_messages(candidate))
                > self._policy.recent_context_chars
            ):
                break
            recent_turns.insert(0, turn)
        old_turns = turns[: len(turns) - len(recent_turns)]
        if force and not old_turns:
            recent_turns = [turns[-1]]
            old_turns = turns[:-1]
        if not old_turns:
            return CompactionResult(
                session_id=session_id,
                status="skipped",
                old_message_count=0,
                recent_message_count=snapshot.message_count,
                summary=snapshot.summary,
                detail="所有完整 turns 都在 recent character budget 内。",
            )
        old = [message for turn in old_turns for message in turn]
        recent = [message for turn in recent_turns for message in turn]
        try:
            summary = (await self._llm.chat(
                self._build_messages(snapshot.summary, old)
            )).strip()
        except LLMError as exc:
            return CompactionResult(
                session_id=session_id,
                status="failed",
                old_message_count=len(old),
                recent_message_count=len(recent),
                summary=snapshot.summary,
                detail=f"生成 summary 失败，旧消息未删除: {exc}",
            )
        if not summary:
            return CompactionResult(
                session_id=session_id,
                status="failed",
                old_message_count=len(old),
                recent_message_count=len(recent),
                summary=snapshot.summary,
                detail="LLM 返回了空 summary，旧消息未删除。",
            )

        try:
            compacted = self._store.commit_compaction(
                session_id,
                expected_revision=snapshot.revision,
                summary=summary,
                recent_messages=recent,
            )
        except SessionError as exc:
            return CompactionResult(
                session_id=session_id,
                status="failed",
                old_message_count=len(old),
                recent_message_count=len(recent),
                summary=snapshot.summary,
                detail=f"保存 compaction 失败，旧消息未删除: {exc}",
            )
        return CompactionResult(
            session_id=session_id,
            status="compacted",
            old_message_count=len(old),
            recent_message_count=compacted.message_count,
            summary=compacted.summary,
            detail="session summary 已更新。",
        )

    def _build_messages(
        self,
        existing_summary: str,
        old_messages: list[Message],
    ) -> list[Message]:
        rendered_messages = "\n\n".join(
            json.dumps(message, ensure_ascii=False, separators=(",", ":"))
            for message in project_messages(old_messages)
        )
        return [
            {"role": "system", "content": self._prompt},
            {
                "role": "user",
                "content": (
                    "[Existing Session Summary]\n"
                    f"{existing_summary or '(empty)'}\n\n"
                    "[Old Session Messages]\n"
                    f"{rendered_messages}"
                ),
            },
        ]


def serialized_request_chars(
    messages: list[Message],
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """Count deterministic JSON characters in the model context payload."""
    request: dict[str, Any] = {"messages": messages}
    if tools:
        request["tools"] = tools
    return len(json.dumps(request, ensure_ascii=False, separators=(",", ":")))


def _split_turns(messages: list[Message]) -> list[list[Message]]:
    turns: list[list[Message]] = []
    for message in messages:
        if message.get("role") == "user":
            turns.append([])
        if not turns:
            raise SessionError("session history 没有从 user turn 开始。")
        turns[-1].append(message)
    return turns


def load_compaction_prompt() -> str:
    try:
        content = (
            resources.files("claw")
            .joinpath(DEFAULT_COMPACTION_PROMPT_RESOURCE)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, UnicodeError) as exc:
        raise ConfigError(
            "读取默认 compaction prompt 失败 "
            f"{DEFAULT_COMPACTION_PROMPT_RESOURCE}: {exc}"
        ) from exc
    normalized = content.strip()
    if not normalized:
        raise ConfigError("默认 compaction prompt 不能为空。")
    return normalized
