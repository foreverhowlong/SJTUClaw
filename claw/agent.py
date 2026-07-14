"""Event-streaming agent service shared by every user-facing entry point."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict
from typing import Protocol

from claw.approval import ApprovalPolicy, DenyAllPolicy
from claw.compaction import CompactionResult, Compactor, serialized_request_chars
from claw.context import ContextBuilder
from claw.errors import LLMError, ToolError
from claw.events import AgentEvent
from claw.llm import LLMStreamEvent
from claw.messages import Message, MessageSource, TextMessage
from claw.session import DEFAULT_SESSION_TITLE, Session
from claw.session_coordination import SessionCoordinator
from claw.session_title import SessionTitleGenerator
from claw.skills import SkillRegistry, SkillRequest
from claw.skills.turn import SkillTurn
from claw.store.attachments import AttachmentStore
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore
from claw.tools import ToolRegistry, build_read_only_registry
from claw.tools.factory import SessionToolProvider
from claw.tool_execution import ToolExecutionCoordinator
from claw.tool_loop import ToolLoopRunner
from claw.turn_context import TurnContextSnapshot
from claw.turn_limits import TurnBudget, TurnLimits
from claw.turn_protocol import (
    assistant_tool_message,
    commit_interrupted_tool_turn,
    public_error,
    tool_call_ids,
    tool_result_event_payload,
    tool_result_message,
    turn_budget_result,
)
from claw.tools.attachment import (
    READ_ATTACHMENT_TOOL_NAME,
    build_read_attachment_tool,
)


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
        turn_limits: TurnLimits | None = None,
        session_coordinator: SessionCoordinator | None = None,
        tool_execution_coordinator: ToolExecutionCoordinator | None = None,
        title_generator: SessionTitleGenerator | None = None,
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
        self._turn_limits = turn_limits or TurnLimits()
        self._session_coordinator = session_coordinator or SessionCoordinator(store.root)
        self._tool_execution_coordinator = tool_execution_coordinator
        self._title_generator = title_generator
        self._tool_loop = ToolLoopRunner(
            self._approval_policy,
            tool_execution_coordinator,
        )

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

        async with self._session_coordinator.turn(session_id):
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
        title_task: asyncio.Task[str] | None = None
        try:
            user_message: TextMessage = {"role": "user", "content": user_input}
            if source is not None:
                user_message["source"] = source
            working = [user_message]
            snapshot = self._store.load(session_id)
            title_task = self._start_title_generation(snapshot, user_input, source)
            turn_context = self._capture_turn_context(session_id)
            if turn_context.skills is not None:
                skill_turn = SkillTurn(
                    turn_context.skills,
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
                    snapshot, working, definitions, turn_context, skill_turn
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
                        snapshot,
                        working,
                        definitions,
                        turn_context,
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
                turn_context,
                skill_turn,
            ):
                yield event
            await self._apply_generated_title(session_id, title_task)
            title_task = None
            yield AgentEvent("turn_end", session_id, {"status": "completed"})
        except asyncio.CancelledError:
            await self._discard_title_generation(title_task)
            title_task = None
            committed = commit_interrupted_tool_turn(
                self._store,
                session_id,
                snapshot,
                working,
                "工具流程已中断；已完成的 tool result 保留在会话中。",
                skill_turn=skill_turn,
                outcome="interrupted",
            )
            self._mark_executions_recorded(session_id, working, committed)
            raise
        except Exception as exc:
            await self._discard_title_generation(title_task)
            title_task = None
            logger.exception("agent turn failed: session=%s", session_id)
            code, message = public_error(exc)
            committed = commit_interrupted_tool_turn(
                self._store,
                session_id,
                snapshot,
                working,
                f"{message} 已完成的 tool result 已保留。",
                skill_turn=skill_turn,
                outcome="failed",
            )
            self._mark_executions_recorded(session_id, working, committed)
            yield AgentEvent(
                "error",
                session_id,
                {"code": code, "message": message},
            )
            yield AgentEvent("turn_end", session_id, {"status": "failed"})

    def _start_title_generation(
        self,
        snapshot: Session,
        user_input: str,
        source: MessageSource | None,
    ) -> asyncio.Task[str] | None:
        if (
            self._title_generator is None
            or snapshot.message_count != 0
            or snapshot.title != DEFAULT_SESSION_TITLE
            or source == "scheduled_task"
        ):
            return None
        return asyncio.create_task(self._title_generator.generate(user_input))

    async def _apply_generated_title(
        self,
        session_id: str,
        task: asyncio.Task[str] | None,
    ) -> None:
        if task is None:
            return
        try:
            title = await task
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "session title generation failed: session=%s",
                session_id,
                exc_info=True,
            )
            return
        try:
            self._store.rename(session_id, title)
        except Exception:
            logger.warning(
                "session title persistence failed: session=%s",
                session_id,
                exc_info=True,
            )

    @staticmethod
    async def _discard_title_generation(task: asyncio.Task[str] | None) -> None:
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def _build_context(
        self,
        snapshot: Session,
        working: list[Message],
        turn_context: TurnContextSnapshot,
        skill_turn: SkillTurn | None = None,
    ) -> list[Message]:
        arguments = (
            [*snapshot.messages, *working],
            turn_context.memories,
            snapshot.summary,
            turn_context.attachments,
        )
        kwargs = {"skills": skill_turn.context() if skill_turn is not None else None}
        if self._tool_provider is None:
            return self._context_builder.build(*arguments, **kwargs)
        return self._context_builder.build(
            *arguments, workspace=snapshot.workspace, **kwargs
        )

    def _request_chars(
        self,
        snapshot: Session,
        working: list[Message],
        definitions: list[dict],
        turn_context: TurnContextSnapshot,
        skill_turn: SkillTurn | None = None,
    ) -> int:
        messages = self._build_context(snapshot, working, turn_context, skill_turn)
        return serialized_request_chars(messages, definitions)

    def _capture_turn_context(self, session_id: str) -> TurnContextSnapshot:
        return TurnContextSnapshot(
            memories=tuple(self._memory_store.list()),
            attachments=(
                tuple(self._attachment_store.list(session_id))
                if self._attachment_store is not None
                else ()
            ),
            skills=(
                self._skill_registry.snapshot()
                if self._skill_registry is not None
                else None
            ),
        )

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
        turn_context: TurnContextSnapshot,
        skill_turn: SkillTurn | None,
    ) -> AsyncIterator[AgentEvent]:
        budget = TurnBudget(self._turn_limits)
        force_final = False
        budget_warning_emitted = False
        while True:
            if not force_final and not budget.record_llm_round():
                force_final = True
            if force_final and not budget_warning_emitted:
                budget_warning_emitted = True
                yield AgentEvent(
                    "warning",
                    session_id,
                    {
                        "code": "turn_budget_exhausted",
                        "message": "本轮达到模型或工具调用预算，将停止调用工具并生成最终回复。",
                        "llmRounds": budget.llm_rounds,
                        "toolCalls": budget.tool_calls,
                    },
                )
            messages = self._build_context(snapshot, working, turn_context, skill_turn)
            completion = None
            active_definitions = [] if force_final else definitions
            async for llm_event in self._llm.stream_chat(messages, active_definitions):
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
                if force_final:
                    raise LLMError("LLM 在禁用 tools 的最终回复阶段仍返回 tool calls。")
                working.append(
                    assistant_tool_message(completion.content, completion.tool_calls)
                )
                accepted = budget.accept_tool_batch(len(completion.tool_calls))
                if accepted:
                    async for event in self._tool_loop.run_batch(
                        session_id,
                        working,
                        completion.tool_calls,
                        tools,
                        workspace=snapshot.workspace,
                        skill_turn=skill_turn,
                    ):
                        yield event
                else:
                    for call in completion.tool_calls:
                        yield AgentEvent(
                            "tool_call",
                            session_id,
                            {
                                "callId": call.call_id,
                                "name": call.name,
                                "arguments": call.arguments,
                            },
                        )
                        result = turn_budget_result(call)
                        working.append(tool_result_message(result))
                        yield AgentEvent(
                            "tool_result",
                            session_id,
                            tool_result_event_payload(result),
                        )
                    force_final = True
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
            self._mark_executions_recorded(session_id, working, True)
            yield AgentEvent("llm_message", session_id, {"content": final})
            return

    def _mark_executions_recorded(
        self,
        session_id: str,
        working: list[Message],
        committed: bool,
    ) -> None:
        if committed and self._tool_execution_coordinator is not None:
            self._tool_execution_coordinator.mark_turn_committed(
                session_id,
                tool_call_ids(working),
            )

    async def compact_session(
        self,
        session_id: str,
        *,
        force: bool = True,
    ) -> CompactionResult:
        async with self._session_coordinator.turn(session_id):
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
