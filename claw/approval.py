"""Approval coordination between the agent loop and external renderers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from claw.store.approvals import ApprovalRequest, ApprovalStore
from claw.tools.registry import PreparedToolCall, ToolResult


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    reason: str
    approval_id: str = ""


class ApprovalPolicy(Protocol):
    def create(
        self,
        session_id: str,
        prepared: PreparedToolCall,
        workspace: str | None,
    ) -> ApprovalRequest: ...

    async def wait(self, approval_id: str) -> ApprovalDecision: ...

    def record_execution_started(self, approval_id: str) -> None: ...

    def record_execution_result(self, approval_id: str, result: ToolResult) -> None: ...


class DenyAllPolicy:
    def create(
        self,
        session_id: str,
        prepared: PreparedToolCall,
        workspace: str | None,
    ) -> ApprovalRequest:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        return ApprovalRequest(
            f"approval_unconfigured_{prepared.call.call_id}",
            session_id,
            prepared.call.call_id,
            prepared.call.name,
            prepared.arguments,
            workspace,
            "pending",
            "",
            now,
            now,
        )

    async def wait(self, approval_id: str) -> ApprovalDecision:
        return ApprovalDecision(False, "该工具需要审批，本环境未配置。", approval_id)

    def record_execution_started(self, approval_id: str) -> None:
        del approval_id

    def record_execution_result(self, approval_id: str, result: ToolResult) -> None:
        del approval_id, result


class ApprovalCoordinator:
    """Persist decisions and wake the suspended agent coroutine."""

    def __init__(self, store: ApprovalStore, *, timeout_seconds: float = 900) -> None:
        if timeout_seconds <= 0:
            raise ValueError("approval timeout_seconds 必须大于 0。")
        self.store = store
        self.timeout_seconds = timeout_seconds
        self._waiters: dict[str, asyncio.Future[ApprovalDecision]] = {}

    def create(
        self,
        session_id: str,
        prepared: PreparedToolCall,
        workspace: str | None,
    ) -> ApprovalRequest:
        return self.store.create(
            session_id,
            prepared.call.call_id,
            prepared.call.name,
            prepared.arguments,
            workspace,
        )

    async def wait(self, approval_id: str) -> ApprovalDecision:
        current = self.store.get(approval_id)
        decided = _decision(current)
        if decided is not None:
            return decided
        loop = asyncio.get_running_loop()
        future = self._waiters.setdefault(approval_id, loop.create_future())
        try:
            return await asyncio.wait_for(
                asyncio.shield(future),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            current = self.store.get(approval_id)
            decided = _decision(current)
            if decided is not None:
                return decided
            request = self.store.resolve(
                approval_id,
                approved=False,
                reason="等待用户审批超时，工具未执行。",
            )
            decision = _decision(request)
            assert decision is not None
            return decision
        finally:
            self._waiters.pop(approval_id, None)

    def resolve(
        self,
        approval_id: str,
        *,
        approved: bool,
        reason: str = "",
    ) -> ApprovalRequest:
        request = self.store.resolve(
            approval_id,
            approved=approved,
            reason=reason,
        )
        future = self._waiters.get(approval_id)
        decision = _decision(request)
        if future is not None and not future.done() and decision is not None:
            future.set_result(decision)
        return request

    def record_execution_started(self, approval_id: str) -> None:
        self.store.mark_execution(approval_id, "executing")

    def record_execution_result(self, approval_id: str, result: ToolResult) -> None:
        self.store.mark_execution(
            approval_id,
            "succeeded" if result.ok else "failed",
            result={
                "ok": result.ok,
                "tool": result.name,
                "value": result.value,
                "error": result.error,
            },
        )


def _decision(request: ApprovalRequest) -> ApprovalDecision | None:
    if request.status == "approved":
        return ApprovalDecision(True, request.reason, request.approval_id)
    if request.status in {"denied", "expired"}:
        return ApprovalDecision(False, request.reason, request.approval_id)
    return None
