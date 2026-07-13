"""Approval policy seam for tools that must not execute implicitly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from claw.tools.registry import ToolCall, ToolDefinition


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    reason: str


class ApprovalPolicy(Protocol):
    async def authorize(
        self,
        session_id: str,
        tool: ToolDefinition,
        call: ToolCall,
    ) -> ApprovalDecision: ...


class DenyAllPolicy:
    """Fail closed until a real ApprovalStore-backed policy is configured."""

    async def authorize(
        self,
        session_id: str,
        tool: ToolDefinition,
        call: ToolCall,
    ) -> ApprovalDecision:
        del session_id, tool, call
        return ApprovalDecision(
            approved=False,
            reason="该工具需要审批，本环境未配置。",
        )
