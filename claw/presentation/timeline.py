"""Project persisted protocol messages into a shared conversation timeline."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal, NotRequired, TypedDict, cast

from claw.messages import Message


ToolStatus = Literal["running", "succeeded", "failed", "awaiting_approval"]


class TextTimelineItem(TypedDict):
    type: Literal["user_message", "assistant_message", "working_note"]
    content: str
    source: NotRequired[Literal["scheduled_task"]]


class ToolActivityItem(TypedDict):
    type: Literal["tool_activity"]
    callId: str
    toolName: str
    action: str
    target: str
    status: ToolStatus
    detail: str
    error: str
    download: NotRequired[dict[str, Any]]
    approval: NotRequired[dict[str, Any]]


TimelineItem = TextTimelineItem | ToolActivityItem


_TOOL_ACTIONS = {
    "current_time": "获取当前时间",
    "list_dir": "查看目录",
    "read_file": "读取文件",
    "read_attachment": "读取附件",
    "create_file": "创建文件",
    "overwrite_file": "覆盖文件",
    "edit_file": "编辑文件",
    "copy_attachment_to_workspace": "拷贝附件",
    "restart_shell": "重启 Shell",
    "run_command": "运行命令",
    "create_download": "准备下载",
}

_TARGET_ARGUMENTS = {
    "list_dir": "path",
    "read_file": "path",
    "read_attachment": "attachment_id",
    "create_file": "path",
    "overwrite_file": "path",
    "edit_file": "path",
    "copy_attachment_to_workspace": "path",
    "restart_shell": "cwd",
    "run_command": "command",
    "create_download": "path",
}


def build_conversation_timeline(messages: Sequence[Message]) -> list[TimelineItem]:
    """Pair tool calls and results without leaking provider protocol into a UI."""
    timeline: list[TimelineItem] = []
    tools_by_call_id: dict[str, int] = {}

    for message in messages:
        role = message["role"]
        if role == "user":
            item: TextTimelineItem = {
                "type": "user_message",
                "content": message["content"],
            }
            if message.get("source") == "scheduled_task":
                item["source"] = "scheduled_task"
            timeline.append(item)
            continue
        if role == "assistant" and "tool_calls" not in message:
            timeline.append(
                {"type": "assistant_message", "content": message["content"]}
            )
            continue
        if role == "assistant":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                timeline.append({"type": "working_note", "content": content})
            for call in message["tool_calls"]:
                function = call["function"]
                item = tool_activity(
                    call["id"],
                    function["name"],
                    function["arguments"],
                )
                tools_by_call_id[call["id"]] = len(timeline)
                timeline.append(item)
            continue
        if role != "tool":
            continue

        call_id = message["tool_call_id"]
        index = tools_by_call_id.get(call_id)
        if index is None:
            continue
        pending = cast(ToolActivityItem, timeline[index])
        ok, result, error = _parse_tool_result(message["content"])
        timeline[index] = tool_activity(
            call_id,
            pending["toolName"],
            _arguments_for_target(pending["toolName"], pending["target"]),
            status="succeeded" if ok else "failed",
            result=result,
            error=error,
        )

    return timeline


def tool_activity(
    call_id: str,
    name: str,
    arguments: str | dict[str, Any],
    *,
    status: ToolStatus = "running",
    result: Any = None,
    error: str = "",
) -> ToolActivityItem:
    """Build a compact, safe description shared by Web history and CLI output."""
    parsed_arguments = _parse_arguments(arguments)
    action = _TOOL_ACTIONS.get(name, f"运行工具 {name}")
    target_key = _TARGET_ARGUMENTS.get(name)
    target_value = parsed_arguments.get(target_key) if target_key else None
    target = target_value if isinstance(target_value, str) else ""
    if name == "list_dir" and not target:
        target = "."

    if name == "read_attachment" and isinstance(result, dict):
        filename = result.get("filename")
        if isinstance(filename, str) and filename.strip():
            target = filename

    item: ToolActivityItem = {
        "type": "tool_activity",
        "callId": call_id,
        "toolName": name,
        "action": action,
        "target": target,
        "status": status,
        "detail": _result_detail(name, result) if status == "succeeded" else "",
        "error": error if status == "failed" else "",
    }
    if name == "create_download" and status == "succeeded" and isinstance(result, dict):
        download_id = result.get("downloadId")
        download_url = result.get("downloadUrl")
        filename = result.get("filename")
        expires_at = result.get("expiresAt")
        if all(isinstance(value, str) for value in (download_id, download_url, filename, expires_at)):
            item["download"] = {
                "downloadId": download_id,
                "downloadUrl": download_url,
                "filename": filename,
                "expiresAt": expires_at,
            }
    return item


def _parse_arguments(value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _arguments_for_target(name: str, target: str) -> dict[str, str]:
    key = _TARGET_ARGUMENTS.get(name)
    return {key: target} if key and target else {}


def _parse_tool_result(content: str) -> tuple[bool, Any, str]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return False, None, "工具结果格式无效。"
    if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
        return False, None, "工具结果格式无效。"
    if payload["ok"]:
        return True, payload.get("result"), ""
    error = payload.get("error")
    return (
        False,
        payload.get("result"),
        error if isinstance(error, str) else "工具执行失败。",
    )


def _result_detail(name: str, result: Any) -> str:
    if name == "list_dir" and isinstance(result, list):
        return f"{len(result)} 项"
    if name in {"read_file", "read_attachment"} and isinstance(result, dict):
        count = result.get("charactersRead")
        truncated = result.get("truncated") is True
        if isinstance(count, int) and not isinstance(count, bool):
            suffix = " · 已截断" if truncated else ""
            return f"{count:,} 字符{suffix}"
    if name == "current_time" and isinstance(result, str):
        return result
    if name in {"create_file", "overwrite_file", "edit_file", "copy_attachment_to_workspace"} and isinstance(result, dict):
        message = result.get("message")
        return message if isinstance(message, str) else ""
    if name == "restart_shell" and isinstance(result, dict):
        cwd = result.get("cwd")
        return str(cwd) if cwd else ""
    if name == "run_command" and isinstance(result, dict):
        code = result.get("exitCode")
        return f"exit {code}" if code is not None else ""
    if name == "create_download" and isinstance(result, dict):
        filename = result.get("filename")
        return str(filename) if filename else ""
    return ""
