"""Approval-aware execution of one model-requested tool batch."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from claw.approval import ApprovalPolicy
from claw.errors import ToolError
from claw.events import AgentEvent
from claw.messages import Message
from claw.skills.turn import SkillTurn
from claw.tool_execution import ToolExecutionCoordinator
from claw.tools import ToolCall, ToolRegistry, ToolResult
from claw.turn_protocol import (
    batch_limit_result,
    tool_result_event_payload,
    tool_result_message,
)


MAX_TOOL_CALLS_PER_BATCH = 5
logger = logging.getLogger(__name__)


class ToolLoopRunner:
    def __init__(
        self,
        approval_policy: ApprovalPolicy,
        execution_coordinator: ToolExecutionCoordinator | None = None,
    ) -> None:
        self._approval_policy = approval_policy
        self._execution_coordinator = execution_coordinator

    async def run_batch(
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
            async for event in self._run_call(
                session_id,
                working,
                call,
                tools,
                oversized=oversized,
                workspace=workspace,
                skill_turn=skill_turn,
            ):
                yield event

    async def _run_call(
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
            {"callId": call.call_id, "name": call.name, "arguments": call.arguments},
        )
        if oversized:
            result = batch_limit_result(call, MAX_TOOL_CALLS_PER_BATCH)
            working.append(tool_result_message(result))
            yield AgentEvent("tool_result", session_id, tool_result_event_payload(result))
            return

        prepared, preparation_error = tools.prepare(call)
        if preparation_error is not None:
            result = preparation_error
        elif prepared is not None and not prepared.tool.requires_approval:
            result = await tools.execute_prepared(prepared)
        else:
            assert prepared is not None
            request = self._approval_policy.create(session_id, prepared, workspace)
            try:
                execution = (
                    self._execution_coordinator.prepare(request, prepared)
                    if self._execution_coordinator is not None
                    else None
                )
            except Exception:
                self._close_orphan_approval(request.approval_id)
                raise
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
                result = (
                    await self._execution_coordinator.execute(execution, tools, prepared)
                    if self._execution_coordinator is not None
                    else await tools.execute_prepared(prepared, approved=True)
                )
                self._approval_policy.record_execution_result(request.approval_id, result)
            else:
                if self._execution_coordinator is not None:
                    self._execution_coordinator.cancel(
                        execution, decision.reason or "用户拒绝执行。"
                    )
                result = ToolResult(
                    call.call_id,
                    call.name,
                    False,
                    error=(
                        f"用户拒绝执行（approvalId={request.approval_id}）: "
                        f"{decision.reason or '未提供原因'}"
                    ),
                )

        working.append(tool_result_message(result))
        yield AgentEvent("tool_result", session_id, tool_result_event_payload(result))
        if result.uncertain:
            raise ToolError("工具执行结果不确定，本轮已停止以避免继续推理。")
        if skill_turn is not None:
            selected = skill_turn.consume_selection_event()
            if selected is not None:
                yield AgentEvent("skill_selected", session_id, selected)

    def _close_orphan_approval(self, approval_id: str) -> None:
        resolver = getattr(self._approval_policy, "resolve", None)
        if not callable(resolver):
            return
        try:
            resolver(
                approval_id,
                approved=False,
                reason="execution journal preparation failed",
            )
        except Exception:
            logger.exception(
                "failed to close approval after journal error: %s", approval_id
            )
