"""Validation for the persisted conversation-message protocol."""

from __future__ import annotations

import json
from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from claw.errors import SessionError
from claw.messages import Message


def validate_turn(messages: Sequence[Message]) -> list[Message]:
    """Copy and validate one complete user-to-assistant turn."""
    copied = [copy_message(message) for message in messages]
    if len(copied) < 2:
        raise SessionError("一个已完成 turn 至少包含 user 和 assistant 消息。")
    if not _is_text_message(copied[0], "user"):
        raise SessionError("turn 必须以 user 文本消息开始。")
    if not _is_text_message(copied[-1], "assistant"):
        raise SessionError("turn 必须以 assistant 最终文本消息结束。")

    index = 1
    while index < len(copied) - 1:
        assistant = copied[index]
        call_ids = _tool_call_ids(assistant)
        if not call_ids:
            raise SessionError("turn 中间消息必须是 assistant tool call。")
        if len(set(call_ids)) != len(call_ids):
            raise SessionError("同一 assistant 消息中的 tool call id 必须唯一。")
        results = copied[index + 1 : index + 1 + len(call_ids)]
        if len(results) != len(call_ids):
            raise SessionError("每个 tool call 都必须有对应 tool result。")
        result_ids: list[str] = []
        for result in results:
            if not _is_tool_result(result):
                raise SessionError("tool result 消息格式无效。")
            result_ids.append(result["tool_call_id"])
        if set(result_ids) != set(call_ids) or len(set(result_ids)) != len(result_ids):
            raise SessionError("tool result 必须与本轮 tool call id 一一对应。")
        index += 1 + len(call_ids)
    return copied


def validate_history(messages: Sequence[Message]) -> list[Message]:
    """Copy and validate a sequence of complete turns."""
    copied = [copy_message(message) for message in messages]
    if not copied:
        raise SessionError("recent messages 必须包含至少一个完整 turn。")
    starts = [
        index for index, message in enumerate(copied) if message.get("role") == "user"
    ]
    if not starts or starts[0] != 0:
        raise SessionError("recent messages 必须从完整 user turn 开始。")
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(copied)
        validate_turn(copied[start:end])
    return copied


def is_legacy_message(value: Any) -> bool:
    """Recognize the legacy one-message-per-line JSONL format."""
    return (
        isinstance(value, dict)
        and value.get("role") in {"user", "assistant"}
        and isinstance(value.get("content"), str)
        and "tool_calls" not in value
    )


def copy_message(value: Any) -> Message:
    if not isinstance(value, dict) or not isinstance(value.get("role"), str):
        raise SessionError("消息格式无效。")
    try:
        copied = deepcopy(value)
        json.dumps(copied, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise SessionError(f"消息必须可 JSON 序列化: {exc}") from exc
    return copied


def _is_text_message(value: Message, role: str) -> bool:
    return (
        value.get("role") == role
        and isinstance(value.get("content"), str)
        and bool(value["content"].strip())
        and "tool_calls" not in value
    )


def _tool_call_ids(value: Message) -> list[str]:
    if value.get("role") != "assistant":
        return []
    content = value.get("content")
    if content is not None and not isinstance(content, str):
        return []
    calls = value.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        return []
    call_ids: list[str] = []
    for call in calls:
        if not isinstance(call, dict) or call.get("type") != "function":
            return []
        call_id = call.get("id")
        function = call.get("function")
        if (
            not isinstance(call_id, str)
            or not call_id
            or not isinstance(function, dict)
            or not isinstance(function.get("name"), str)
            or not function["name"]
            or not isinstance(function.get("arguments"), str)
        ):
            return []
        call_ids.append(call_id)
    return call_ids


def _is_tool_result(value: Message) -> bool:
    return (
        value.get("role") == "tool"
        and isinstance(value.get("tool_call_id"), str)
        and bool(value["tool_call_id"])
        and isinstance(value.get("name"), str)
        and bool(value["name"])
        and isinstance(value.get("content"), str)
    )
