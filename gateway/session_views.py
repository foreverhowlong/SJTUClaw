"""Transport-neutral serialization of persisted session state."""

from __future__ import annotations

from typing import Any

from claw.presentation.timeline import build_conversation_timeline
from claw.session import Session
from claw.store.sessions import SessionSummary


def session_summary(item: SessionSummary) -> dict[str, Any]:
    return {
        "sessionId": item.session_id,
        "title": item.title,
        "messageCount": item.message_count,
        "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
    }


def session_detail(session: Session) -> dict[str, Any]:
    return {
        "sessionId": session.session_id,
        "title": session.title,
        "messageCount": session.message_count,
        "createdAt": session.created_at.isoformat(),
        "updatedAt": session.updated_at.isoformat(),
        "revision": session.revision,
        "summary": session.summary,
        "workspace": session.workspace,
        "messages": session.messages,
        "timeline": build_conversation_timeline(session.messages),
    }
