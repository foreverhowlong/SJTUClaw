"""Safe session-history compaction backed by the shared LLM and SessionStore."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Literal, Protocol

from claw.errors import ConfigError, LLMError, SessionError
from claw.llm import Message
from claw.store.sessions import SessionStore


DEFAULT_COMPACTION_PROMPT_RESOURCE = "prompts/compaction.md"


class ChatClient(Protocol):
    def chat(self, messages: list[Message]) -> str: ...


@dataclass(frozen=True)
class CompactionPolicy:
    """Use active message count as a transparent approximation for token usage."""

    max_messages: int = 32
    recent_messages: int = 8

    def __post_init__(self) -> None:
        if self.recent_messages < 2 or self.recent_messages % 2 != 0:
            raise ValueError("recent_messages 必须是至少为 2 的偶数。")
        if self.max_messages <= self.recent_messages:
            raise ValueError("max_messages 必须大于 recent_messages。")


CompactionStatus = Literal["compacted", "skipped", "failed"]


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

    def compact(self, session_id: str, *, force: bool = False) -> CompactionResult:
        snapshot = self._store.load(session_id)
        if not force and snapshot.message_count <= self._policy.max_messages:
            return CompactionResult(
                session_id=session_id,
                status="skipped",
                old_message_count=0,
                recent_message_count=snapshot.message_count,
                detail="active messages 尚未超过自动压缩阈值。",
            )
        if snapshot.message_count <= self._policy.recent_messages:
            return CompactionResult(
                session_id=session_id,
                status="skipped",
                old_message_count=0,
                recent_message_count=snapshot.message_count,
                summary=snapshot.summary,
                detail="没有足够的旧消息可压缩。",
            )

        messages = snapshot.messages
        recent = messages[-self._policy.recent_messages :]
        old = messages[: -self._policy.recent_messages]
        try:
            summary = self._llm.chat(
                self._build_messages(snapshot.summary, old)
            ).strip()
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
            f"{message['role'].upper()}:\n{message['content']}"
            for message in old_messages
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
