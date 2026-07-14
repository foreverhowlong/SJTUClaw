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
from claw.errors import (
    ApprovalError,
    DownloadError,
    LLMError,
    SessionError,
    ShellError,
    SkillError,
    ToolError,
    WorkspaceError,
)
from claw.events import AgentEvent
from claw.llm import LLMStreamEvent
from claw.messages import Message, MessageSource, TextMessage
from claw.session import Session
from claw.skills import SkillRegistry, SkillRequest
from claw.skills.turn import SkillTurn
from claw.store.attachments import AttachmentStore
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore
from claw.tools import ToolCall, ToolRegistry, ToolResult, build_read_only_registry
from claw.tools.factory import SessionToolProvider
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
        tool_provider: SessionToolProvider | None = None,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        self._llm = llm
        self._store = store
        self._context_builder = context_builder
        self._memory_store = memory_store
        self._compactor = compactor
        self._tools = tool_registry or build_read_only_registry()
        self._approval_policy = approval_policy or DenyAllPolicy()
        self._attachment_store = attachment_store
        self._tool_provider = tool_provider
        self._skill_registry = skill_registry
        self._session_locks: dict[str, asyncio.Lock] = {}

    async def run_turn(
        self,
        session_id: str,
        user_input: str,
        *,
        source: MessageSource | None = None,
        skill_request: SkillRequest | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run LLM -> tools -> LLM until a final answer is atomically committed."""
        if not user_input.strip():
            raise ValueError("user_input 不能为空。")

        async with self._session_lock(session_id):
            async for event in self._run_turn(
                session_id, user_input, source, skill_request
            ):
                yield event

    async def _run_turn(
        self,
        session_id: str,
        user_input: str,
        source: MessageSource | None,
        skill_request: SkillRequest | None,
    ) -> AsyncIterator[AgentEvent]:

        yield AgentEvent("turn_start", session_id, {"userInput": user_input})
        snapshot: Session | None = None
        working: list[Message] = []
        skill_turn: SkillTurn | None = None
        try:
            user_message: TextMessage = {"role": "user", "content": user_input}
            if source is not None:
                user_message["source"] = source
            working = [user_message]
            snapshot = self._store.load(session_id)
            if self._skill_registry is not None:
                skill_turn = SkillTurn(
                    self._skill_registry.snapshot(),
                    user_input,
                    allow_auto=source != "scheduled_task" and skill_request is None,
                )
                if skill_request is not None:
                    skill_turn.apply_explicit(skill_request)
                    selected = skill_turn.consume_selection_event()
                    assert selected is not None
                    yield AgentEvent("skill_selected", session_id, selected)
            tools = self._tools_for_session(snapshot)
            if skill_turn is not None:
                skill_tool = skill_turn.tool()
                if skill_tool is not None:
                    tools.register(skill_tool)
            definitions = tools.definitions()
            if self._compactor is not None:
                request_chars = self._request_chars(
                    session_id, snapshot, working, definitions, skill_turn
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
                        skill_turn,
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
                skill_turn,
            ):
                yield event
            yield AgentEvent("turn_end", session_id, {"status": "completed"})
        except asyncio.CancelledError:
            _commit_interrupted_tool_turn(
                self._store,
                session_id,
                snapshot,
                working,
                "工具流程已中断；已完成的 tool result 保留在会话中。",
                skill_turn=skill_turn,
                outcome="interrupted",
            )
            raise
        except Exception as exc:
            logger.exception("agent turn failed: session=%s", session_id)
            code, message = _public_error(exc)
            _commit_interrupted_tool_turn(
                self._store,
                session_id,
                snapshot,
                working,
                f"{message} 已完成的 tool result 已保留。",
                skill_turn=skill_turn,
                outcome="failed",
            )
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
        skill_turn: SkillTurn | None = None,
    ) -> list[Message]:
        attachments = (
            self._attachment_store.list(session_id)
            if self._attachment_store is not None
            else ()
        )
        arguments = (
            [*snapshot.messages, *working],
            self._memory_store.list(),
            snapshot.summary,
            attachments,
        )
        kwargs = {"skills": skill_turn.context() if skill_turn is not None else None}
        if self._tool_provider is None:
            return self._context_builder.build(*arguments, **kwargs)
        return self._context_builder.build(
            *arguments, workspace=snapshot.workspace, **kwargs
        )

    def _request_chars(
        self,
        session_id: str,
        snapshot: Session,
        working: list[Message],
        definitions: list[dict],
        skill_turn: SkillTurn | None = None,
    ) -> int:
        messages = self._build_context(session_id, snapshot, working, skill_turn)
        return serialized_request_chars(messages, definitions)

    def _tools_for_session(self, session: Session) -> ToolRegistry:
        if self._tool_provider is not None:
            return self._tool_provider.for_session(session)
        tools = self._tools.clone()
        if self._attachment_store is None:
            return tools
        if tools.get(READ_ATTACHMENT_TOOL_NAME) is not None:
            raise ToolError(f"tool 已注册: {READ_ATTACHMENT_TOOL_NAME}。")
        tools.register(
            build_read_attachment_tool(self._attachment_store, session.session_id)
        )
        return tools

    async def _run_loop(
        self,
        session_id: str,
        snapshot: Session,
        working: list[Message],
        definitions: list[dict],
        tools: ToolRegistry,
        skill_turn: SkillTurn | None,
    ) -> AsyncIterator[AgentEvent]:
        while True:
            messages = self._build_context(session_id, snapshot, working, skill_turn)
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
                    workspace=snapshot.workspace,
                    skill_turn=skill_turn,
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
                skill_usage=(
                    skill_turn.usage(session_id, "completed", final)
                    if skill_turn is not None
                    else None
                ),
            )
            yield AgentEvent("llm_message", session_id, {"content": final})
            return

    async def _run_tool_batch(
        self,
        session_id: str,
        working: list[Message],
        calls: tuple[ToolCall, ...],
        tools: ToolRegistry,
        *,
        workspace: str | None,
        skill_turn: SkillTurn | None,
    ) -> AsyncIterator[AgentEvent]:
        oversized = len(calls) > MAX_TOOL_CALLS_PER_BATCH
        for call in calls:
            async for event in self._run_tool_call(
                session_id,
                working,
                call,
                tools,
                oversized=oversized,
                workspace=workspace,
                skill_turn=skill_turn,
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
        workspace: str | None,
        skill_turn: SkillTurn | None,
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

        prepared, preparation_error = tools.prepare(call)
        if preparation_error is not None:
            result = preparation_error
        elif prepared is not None and not prepared.tool.requires_approval:
            result = await tools.execute_prepared(prepared)
        else:
            assert prepared is not None
            request = self._approval_policy.create(
                session_id,
                prepared,
                workspace,
            )
            yield AgentEvent(
                "approval_required",
                session_id,
                {
                    "approvalId": request.approval_id,
                    "callId": call.call_id,
                    "name": call.name,
                    "arguments": prepared.arguments,
                    "workspace": workspace,
                },
            )
            decision = await self._approval_policy.wait(request.approval_id)
            yield AgentEvent(
                "approval_resolved",
                session_id,
                {
                    "callId": call.call_id,
                    "name": call.name,
                    "approvalId": request.approval_id,
                    "approved": decision.approved,
                    "reason": decision.reason,
                },
            )
            if decision.approved:
                self._approval_policy.record_execution_started(request.approval_id)
                result = await tools.execute_prepared(prepared, approved=True)
                self._approval_policy.record_execution_result(
                    request.approval_id,
                    result,
                )
            else:
                result = ToolResult(
                    call.call_id,
                    call.name,
                    False,
                    error=(
                        f"用户拒绝执行（approvalId={request.approval_id}）: "
                        f"{decision.reason or '未提供原因'}"
                    ),
                )

        working.append(_tool_result_message(result))
        yield AgentEvent(
            "tool_result",
            session_id,
            _tool_result_event_payload(result),
        )
        if skill_turn is not None:
            selected = skill_turn.consume_selection_event()
            if selected is not None:
                yield AgentEvent("skill_selected", session_id, selected)

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
        "result": result.value,
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
    if isinstance(
        exc,
        (ToolError, WorkspaceError, ApprovalError, DownloadError, ShellError),
    ):
        return "tool_error", "工具运行时发生错误。"
    if isinstance(exc, SkillError):
        return "skill_error", str(exc)
    return "internal_error", "Agent 运行时发生内部错误。"


def _commit_interrupted_tool_turn(
    store: SessionStore,
    session_id: str,
    snapshot: Session | None,
    working: list[Message],
    message: str,
    *,
    skill_turn: SkillTurn | None = None,
    outcome: str = "failed",
) -> None:
    """Best-effort close a protocol-complete tool turn after later failure."""
    if snapshot is None or (
        not any(item.get("role") == "tool" for item in working)
        and (skill_turn is None or skill_turn.selection is None)
    ):
        return
    messages = list(working)
    if not (
        messages
        and messages[-1].get("role") == "assistant"
        and "tool_calls" not in messages[-1]
    ):
        messages.append({"role": "assistant", "content": message})
    try:
        store.commit_turn(
            session_id,
            expected_revision=snapshot.revision,
            messages=messages,
            skill_usage=(
                skill_turn.usage(session_id, outcome, messages[-1]["content"])
                if skill_turn is not None
                else None
            ),
        )
    except Exception:
        logger.exception(
            "failed to preserve interrupted tool turn: session=%s",
            session_id,
        )
