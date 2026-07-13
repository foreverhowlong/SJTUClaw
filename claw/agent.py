"""Event-streaming agent service shared by every user-facing entry point."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict
from typing import Protocol

from claw.approval import ApprovalPolicy, DenyAllPolicy
from claw.compaction import CompactionResult, Compactor, serialized_request_chars
from claw.context import ContextBuilder, TOOL_RESULT_PREVIEW_CHARS
from claw.errors import LLMError, SessionError, ToolError
from claw.events import AgentEvent
from claw.llm import LLMStreamEvent
from claw.messages import Message, MessageSource, TextMessage
from claw.session import Session
from claw.store.attachments import AttachmentStore
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore
from claw.tools import ToolCall, ToolRegistry, ToolResult, build_read_only_registry
from claw.tools.attachment import (
    READ_ATTACHMENT_TOOL_NAME,
    build_read_attachment_tool,
)


MAX_TOOL_CALLS_PER_BATCH = 5


logger = logging.getLogger(__name__)


class ChatClient(Protocol):
    def stream_chat(
        self,
        messages: list[Message],
        tools: Sequence[dict],
    ) -> AsyncIterator[LLMStreamEvent]: ...


class AgentService:
    """Run one complete agent turn and expose every observable step as an event."""

    def __init__(
        self,
        llm: ChatClient,
        store: SessionStore,
        context_builder: ContextBuilder,
        memory_store: MemoryStore,
        compactor: Compactor | None = None,
        tool_registry: ToolRegistry | None = None,
        approval_policy: ApprovalPolicy | None = None,
        attachment_store: AttachmentStore | None = None,
    ) -> None:
        self._llm = llm
        self._store = store
        self._context_builder = context_builder
        self._memory_store = memory_store
        self._compactor = compactor
        self._tools = tool_registry or build_read_only_registry()
        self._approval_policy = approval_policy or DenyAllPolicy()
        self._attachment_store = attachment_store
        self._session_locks: dict[str, asyncio.Lock] = {}

    async def run_turn(
        self,
        session_id: str,
        user_input: str,
        *,
        source: MessageSource | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run LLM -> tools -> LLM until a final answer is atomically committed."""
        if not user_input.strip():
            raise ValueError("user_input 不能为空。")

        async with self._session_lock(session_id):
            async for event in self._run_turn(session_id, user_input, source):
                yield event

    async def _run_turn(
        self,
        session_id: str,
        user_input: str,
        source: MessageSource | None,
    ) -> AsyncIterator[AgentEvent]:

        yield AgentEvent("turn_start", session_id, {"userInput": user_input})
        try:
            tools = self._tools_for_session(session_id)
            definitions = tools.definitions()
            user_message: TextMessage = {"role": "user", "content": user_input}
            if source is not None:
                user_message["source"] = source
            working: list[Message] = [user_message]
            snapshot = self._store.load(session_id)
            if self._compactor is not None:
                request_chars = self._request_chars(
                    session_id, snapshot, working, definitions
                )
                if self._compactor.should_compact(request_chars):
                    yield AgentEvent(
                        "compaction_started",
                        session_id,
                        {"requestCharacters": request_chars},
                    )
                    result = await self._compactor.compact(
                        session_id,
                        request_chars=request_chars,
                    )
                    yield AgentEvent("compaction_done", session_id, asdict(result))
                    snapshot = self._store.load(session_id)
                    remaining_chars = self._request_chars(
                        session_id,
                        snapshot,
                        working,
                        definitions,
                    )
                    if self._compactor.should_compact(remaining_chars):
                        # Compaction owns committed turns only. Oversized working
                        # context is handled by tool-result projection instead.
                        yield AgentEvent(
                            "warning",
                            session_id,
                            {
                                "code": "context_still_oversized",
                                "message": "上下文压缩未能降到目标预算，但仍将继续本轮。",
                                "requestCharacters": remaining_chars,
                            },
                        )
            async for event in self._run_loop(
                session_id,
                snapshot,
                working,
                definitions,
                tools,
            ):
                yield event
            yield AgentEvent("turn_end", session_id, {"status": "completed"})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("agent turn failed: session=%s", session_id)
            code, message = _public_error(exc)
            yield AgentEvent(
                "error",
                session_id,
                {"code": code, "message": message},
            )
            yield AgentEvent("turn_end", session_id, {"status": "failed"})

    def _build_context(
        self,
        session_id: str,
        snapshot: Session,
        working: list[Message],
    ) -> list[Message]:
        attachments = (
            self._attachment_store.list(session_id)
            if self._attachment_store is not None
            else ()
        )
        return self._context_builder.build(
            [*snapshot.messages, *working],
            self._memory_store.list(),
            snapshot.summary,
            attachments,
        )

    def _request_chars(
        self,
        session_id: str,
        snapshot: Session,
        working: list[Message],
        definitions: list[dict],
    ) -> int:
        messages = self._build_context(session_id, snapshot, working)
        return serialized_request_chars(messages, definitions)

    def _tools_for_session(self, session_id: str) -> ToolRegistry:
        tools = self._tools.clone()
        if self._attachment_store is None:
            return tools
        if tools.get(READ_ATTACHMENT_TOOL_NAME) is not None:
            raise ToolError(f"tool 已注册: {READ_ATTACHMENT_TOOL_NAME}。")
        tools.register(build_read_attachment_tool(self._attachment_store, session_id))
        return tools

    async def _run_loop(
        self,
        session_id: str,
        snapshot: Session,
        working: list[Message],
        definitions: list[dict],
        tools: ToolRegistry,
    ) -> AsyncIterator[AgentEvent]:
        while True:
            messages = self._build_context(session_id, snapshot, working)
            completion = None
            async for llm_event in self._llm.stream_chat(messages, definitions):
                if llm_event.type == "text_delta":
                    yield AgentEvent(
                        "llm_delta",
                        session_id,
                        {"delta": llm_event.text},
                    )
                elif llm_event.type == "completed":
                    completion = llm_event.completion
            if completion is None:
                raise LLMError("LLM stream 未返回 completed 事件。")

            if completion.tool_calls:
                working.append(
                    _assistant_tool_message(completion.content, completion.tool_calls)
                )
                async for event in self._run_tool_batch(
                    session_id,
                    working,
                    completion.tool_calls,
                    tools,
                ):
                    yield event
                continue

            final = completion.content.strip()
            if not final:
                raise LLMError("LLM 最终回答为空。")
            working.append({"role": "assistant", "content": final})
            self._store.commit_turn(
                session_id,
                expected_revision=snapshot.revision,
                messages=working,
            )
            yield AgentEvent("llm_message", session_id, {"content": final})
            return

    async def _run_tool_batch(
        self,
        session_id: str,
        working: list[Message],
        calls: tuple[ToolCall, ...],
        tools: ToolRegistry,
    ) -> AsyncIterator[AgentEvent]:
        oversized = len(calls) > MAX_TOOL_CALLS_PER_BATCH
        for call in calls:
            async for event in self._run_tool_call(
                session_id,
                working,
                call,
                tools,
                oversized=oversized,
            ):
                yield event

    async def _run_tool_call(
        self,
        session_id: str,
        working: list[Message],
        call: ToolCall,
        tools: ToolRegistry,
        *,
        oversized: bool,
    ) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(
            "tool_call",
            session_id,
            {
                "callId": call.call_id,
                "name": call.name,
                "arguments": call.arguments,
            },
        )
        if oversized:
            result = _batch_limit_result(call)
            working.append(_tool_result_message(result))
            yield AgentEvent(
                "tool_result",
                session_id,
                _tool_result_event_payload(result),
            )
            return

        tool = tools.get(call.name)
        if tool is None or not tool.requires_approval:
            result = await tools.execute(call)
        else:
            yield AgentEvent(
                "approval_required",
                session_id,
                {"callId": call.call_id, "name": call.name},
            )
            decision = await self._approval_policy.authorize(session_id, tool, call)
            yield AgentEvent(
                "approval_resolved",
                session_id,
                {
                    "callId": call.call_id,
                    "name": call.name,
                    "approved": decision.approved,
                    "reason": decision.reason,
                },
            )
            if decision.approved:
                result = await tools.execute(call, approved=True)
            else:
                result = ToolResult(
                    call.call_id,
                    call.name,
                    False,
                    error=decision.reason,
                )

        working.append(_tool_result_message(result))
        yield AgentEvent(
            "tool_result",
            session_id,
            _tool_result_event_payload(result),
        )

    async def compact_session(
        self,
        session_id: str,
        *,
        force: bool = True,
    ) -> CompactionResult:
        async with self._session_lock(session_id):
            if self._compactor is None:
                snapshot = self._store.load(session_id)
                return CompactionResult(
                    session_id=session_id,
                    status="unavailable",
                    old_message_count=0,
                    recent_message_count=snapshot.message_count,
                    summary=snapshot.summary,
                    detail="runtime 未配置 compactor，旧消息未删除。",
                )
            return await self._compactor.compact(session_id, force=force)

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        return self._session_locks.setdefault(session_id, asyncio.Lock())


def _assistant_tool_message(
    content: str,
    calls: tuple[ToolCall, ...],
) -> Message:
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.arguments,
                },
            }
            for call in calls
        ],
    }


def _tool_result_message(result: ToolResult) -> Message:
    return {
        "role": "tool",
        "tool_call_id": result.call_id,
        "name": result.name,
        "content": result.model_content(),
    }


def _batch_limit_result(call: ToolCall) -> ToolResult:
    return ToolResult(
        call.call_id,
        call.name,
        False,
        error=(
            f"一次最多请求 {MAX_TOOL_CALLS_PER_BATCH} 个 tool calls，"
            "本批未执行。"
        ),
    )


def _tool_result_event_payload(result: ToolResult) -> dict:
    content = result.model_content()
    payload = {
        "callId": result.call_id,
        "name": result.name,
        "ok": result.ok,
        "result": result.value if result.ok else None,
        "error": result.error,
        "truncated": False,
    }
    if len(content) > TOOL_RESULT_PREVIEW_CHARS:
        payload.update(
            {
                "result": None,
                "error": "",
                "truncated": True,
                "originalCharacters": len(content),
                "preview": content[:TOOL_RESULT_PREVIEW_CHARS],
            }
        )
    return payload


def _public_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, LLMError):
        return "llm_error", "LLM 调用失败，请稍后重试。"
    if isinstance(exc, SessionError):
        return "session_error", "会话状态处理失败。"
    if isinstance(exc, ToolError):
        return "tool_error", "工具运行时发生错误。"
    return "internal_error", "Agent 运行时发生内部错误。"
