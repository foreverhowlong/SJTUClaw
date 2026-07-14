"""Provider protocol messages and failure finalization for agent turns."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from claw.context import TOOL_RESULT_PREVIEW_CHARS
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
from claw.messages import Message
from claw.skills.turn import SkillTurn
from claw.store.sessions import SessionStore
from claw.tools import ToolCall, ToolResult


logger = logging.getLogger(__name__)


def assistant_tool_message(content: str, calls: tuple[ToolCall, ...]) -> Message:
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


def tool_result_message(result: ToolResult) -> Message:
    return {
        "role": "tool",
        "tool_call_id": result.call_id,
        "name": result.name,
        "content": result.model_content(),
    }


def batch_limit_result(call: ToolCall, maximum: int) -> ToolResult:
    return ToolResult(
        call.call_id,
        call.name,
        False,
        error=f"一次最多请求 {maximum} 个 tool calls，本批未执行。",
    )


def turn_budget_result(call: ToolCall) -> ToolResult:
    return ToolResult(
        call.call_id,
        call.name,
        False,
        error="本轮 tool call 总预算已耗尽，本次调用未执行。",
    )


def tool_result_event_payload(result: ToolResult) -> dict:
    content = result.model_content()
    payload = {
        "callId": result.call_id,
        "name": result.name,
        "ok": result.ok,
        "result": result.value,
        "error": result.error,
        "truncated": False,
        "uncertain": result.uncertain,
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


def tool_call_ids(messages: Sequence[Message]) -> set[str]:
    return {
        str(call["id"])
        for message in messages
        if message.get("role") == "assistant"
        for call in message.get("tool_calls", [])
        if isinstance(call, dict) and isinstance(call.get("id"), str)
    }


def public_error(exc: Exception) -> tuple[str, str]:
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


def commit_interrupted_tool_turn(
    store: SessionStore,
    session_id: str,
    snapshot,
    working: list[Message],
    message: str,
    *,
    skill_turn: SkillTurn | None = None,
    outcome: str = "failed",
) -> bool:
    """Best-effort close a protocol-complete tool turn after later failure."""
    if snapshot is None or (
        not any(item.get("role") == "tool" for item in working)
        and (skill_turn is None or skill_turn.selection is None)
    ):
        return False
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
        return True
    except Exception:
        logger.exception(
            "failed to preserve interrupted tool turn: session=%s",
            session_id,
        )
        return False
