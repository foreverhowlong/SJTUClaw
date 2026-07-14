"""Structured events shared by every agent entry-point renderer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


AgentEventType = Literal[
    "turn_start",
    "llm_delta",
    "llm_message",
    "tool_call",
    "tool_result",
    "approval_required",
    "approval_resolved",
    "skill_selected",
    "compaction_started",
    "compaction_done",
    "warning",
    "error",
    "turn_end",
]


@dataclass(frozen=True)
class AgentEvent:
    """One JSON-serializable observation emitted by the shared runtime."""

    type: AgentEventType
    session_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "sessionId": self.session_id,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
        }
