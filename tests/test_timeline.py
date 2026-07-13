from __future__ import annotations

import json

from claw.messages import Message
from claw.presentation.timeline import build_conversation_timeline, tool_activity


def test_timeline_pairs_working_notes_calls_and_results_in_order() -> None:
    messages: list[Message] = [
        {"role": "user", "content": "检查项目"},
        {
            "role": "assistant",
            "content": "我先读取文件。",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"README.md"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "read_file",
            "content": json.dumps(
                {
                    "ok": True,
                    "result": {
                        "path": "README.md",
                        "content": "hello",
                        "charactersRead": 5,
                        "truncated": False,
                    },
                }
            ),
        },
        {"role": "assistant", "content": "完成。"},
    ]

    assert build_conversation_timeline(messages) == [
        {"type": "user_message", "content": "检查项目"},
        {"type": "working_note", "content": "我先读取文件。"},
        {
            "type": "tool_activity",
            "callId": "call_1",
            "toolName": "read_file",
            "action": "读取文件",
            "target": "README.md",
            "status": "succeeded",
            "detail": "5 字符",
            "error": "",
        },
        {"type": "assistant_message", "content": "完成。"},
    ]


def test_timeline_handles_multiple_calls_failure_and_attachment_filename() -> None:
    messages: list[Message] = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": "{}"},
                },
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {
                        "name": "read_attachment",
                        "arguments": '{"attachment_id":"attachment_0123456789ab"}',
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "list_dir",
            "content": '{"ok":false,"error":"目录不存在。"}',
        },
        {
            "role": "tool",
            "tool_call_id": "call_2",
            "name": "read_attachment",
            "content": json.dumps(
                {
                    "ok": True,
                    "result": {
                        "attachmentId": "attachment_0123456789ab",
                        "filename": "task6.MD",
                        "charactersRead": 65_536,
                        "truncated": True,
                    },
                }
            ),
        },
    ]

    timeline = build_conversation_timeline(messages)

    assert [item["callId"] for item in timeline if item["type"] == "tool_activity"] == [
        "call_1",
        "call_2",
    ]
    assert timeline[0]["status"] == "failed"
    assert timeline[0]["error"] == "目录不存在。"
    assert timeline[1]["target"] == "task6.MD"
    assert timeline[1]["detail"] == "65,536 字符 · 已截断"


def test_timeline_keeps_unknown_or_unfinished_tools_generic_and_safe() -> None:
    item = tool_activity("call_1", "custom_tool", "not-json")

    assert item["action"] == "运行工具 custom_tool"
    assert item["target"] == ""
    assert item["status"] == "running"
