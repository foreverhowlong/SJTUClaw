"""Deterministic budgets for one agent turn."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TurnLimits:
    """Bound provider/tool churn while leaving approval timeout independent."""

    max_llm_rounds: int = 12
    max_total_tool_calls: int = 30

    def __post_init__(self) -> None:
        if self.max_llm_rounds <= 0:
            raise ValueError("max_llm_rounds 必须大于 0。")
        if self.max_total_tool_calls <= 0:
            raise ValueError("max_total_tool_calls 必须大于 0。")


@dataclass
class TurnBudget:
    limits: TurnLimits
    llm_rounds: int = 0
    tool_calls: int = 0

    def record_llm_round(self) -> bool:
        """Return false when normal tool-enabled rounds are exhausted."""
        if self.llm_rounds >= self.limits.max_llm_rounds:
            return False
        self.llm_rounds += 1
        return True

    def accept_tool_batch(self, count: int) -> bool:
        if count < 0:
            raise ValueError("tool call count 不能为负数。")
        accepted = self.tool_calls + count <= self.limits.max_total_tool_calls
        self.tool_calls += count
        return accepted
