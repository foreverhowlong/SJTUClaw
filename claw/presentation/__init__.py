"""Shared, interface-neutral projections for human-facing Claw surfaces."""

from claw.presentation.timeline import (
    TimelineItem,
    ToolActivityItem,
    build_conversation_timeline,
    tool_activity,
)

__all__ = [
    "TimelineItem",
    "ToolActivityItem",
    "build_conversation_timeline",
    "tool_activity",
]
