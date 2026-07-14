"""Decoder for append-only session message logs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from claw.errors import SessionError
from claw.messages import Message
from claw.skills.models import SkillUsage
from claw.store.session_messages import (
    copy_message,
    is_legacy_message,
    validate_history,
    validate_turn,
)


@dataclass(frozen=True)
class MessageLog:
    messages: tuple[Message, ...]
    summary: str
    revision: int
    last_committed_at: datetime | None
    skill_usages: tuple[SkillUsage, ...]


def read_message_log(path: Path) -> MessageLog:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise SessionError(f"Session 数据损坏: 缺少 {path}。") from exc
    except (OSError, UnicodeError) as exc:
        raise SessionError(f"读取 session 消息失败 {path}: {exc}") from exc

    messages: list[Message] = []
    summary = ""
    revision = 0
    last_committed_at: datetime | None = None
    skill_usages: list[SkillUsage] = []
    for line_number, line in enumerate(lines, start=1):
        record = _decode_record(line, path, line_number)
        if is_legacy_message(record):
            messages.append(copy_message(record))
            revision += 1
            continue

        _validate_record_header(record, path, line_number, revision + 1)
        if record["type"] == "turn":
            committed_at, new_messages, usage = _read_turn_record(
                record, path, line_number
            )
            messages.extend(new_messages)
            if usage is not None:
                skill_usages.append(usage)
        else:
            committed_at, summary, messages = _read_compaction_record(
                record,
                current_messages=messages,
                path=path,
                line_number=line_number,
            )
        revision += 1
        last_committed_at = committed_at

    return MessageLog(
        tuple(messages), summary, revision, last_committed_at, tuple(skill_usages)
    )


def parse_datetime(value: Any, path: Path, field: str) -> datetime:
    if not isinstance(value, str):
        raise SessionError(f"Session 数据损坏: {path} 中的 {field} 无效。")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SessionError(f"Session 数据损坏: {path} 中的 {field} 无效。") from exc
    if parsed.tzinfo is None:
        raise SessionError(f"Session 数据损坏: {path} 中的 {field} 缺少时区。")
    return parsed


def _decode_record(line: str, path: Path, line_number: int) -> Any:
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise SessionError(
            f"Session 数据损坏: {path} 第 {line_number} 行不是有效 JSON。"
        ) from exc


def _validate_record_header(
    record: Any,
    path: Path,
    line_number: int,
    expected_revision: int,
) -> None:
    if not isinstance(record, dict) or record.get("type") not in {
        "turn",
        "compaction",
    }:
        raise SessionError(
            f"Session 数据损坏: {path} 第 {line_number} 行记录格式无效。"
        )
    if record.get("revision") != expected_revision:
        raise SessionError(
            f"Session 数据损坏: {path} 第 {line_number} 行 revision 无效。"
        )


def _read_turn_record(
    record: dict[str, Any],
    path: Path,
    line_number: int,
) -> tuple[datetime, list[Message], SkillUsage | None]:
    messages = record.get("messages")
    if not isinstance(messages, list):
        raise SessionError(
            f"Session 数据损坏: {path} 第 {line_number} 行 messages 无效。"
        )
    try:
        validated = validate_turn(messages)
    except SessionError as exc:
        raise SessionError(
            f"Session 数据损坏: {path} 第 {line_number} 行: {exc}"
        ) from exc
    committed_at = parse_datetime(
        record.get("committedAt"),
        path,
        f"第 {line_number} 行 committedAt",
    )
    usage = _read_skill_usage(record, validated, path, line_number)
    return committed_at, validated, usage


def _read_skill_usage(
    record: dict[str, Any],
    messages: list[Message],
    path: Path,
    line_number: int,
) -> SkillUsage | None:
    value = record.get("skillUsage")
    if value is None:
        return None
    try:
        if not isinstance(value, dict):
            raise TypeError("skillUsage is not an object")
        source = value["source"]
        outcome = value["outcome"]
        if source not in {"explicit", "auto"}:
            raise ValueError("source invalid")
        if outcome not in {"completed", "failed", "interrupted"}:
            raise ValueError("outcome invalid")
        usage = SkillUsage(
            usage_id=str(value["usageId"]),
            turn_id=str(value["turnId"]),
            skill_name=str(value["skillName"]),
            session_id=str(value["sessionId"]),
            task=str(value["task"]),
            source=source,
            reason=str(value["reason"]),
            used_at=datetime.fromisoformat(str(value["usedAt"])),
            outcome=outcome,
            final_output=str(value["finalOutput"]),
        )
        if (
            not usage.usage_id.startswith("usage_")
            or usage.turn_id != record.get("turnId")
            or not usage.skill_name
            or not usage.session_id
            or not usage.reason
            or usage.task != messages[0]["content"]
            or usage.final_output != messages[-1]["content"]
            or usage.used_at.tzinfo is None
        ):
            raise ValueError("skillUsage fields inconsistent")
        return usage
    except (KeyError, TypeError, ValueError) as exc:
        raise SessionError(
            f"Session 数据损坏: {path} 第 {line_number} 行 skillUsage 无效: {exc}"
        ) from exc


def _read_compaction_record(
    record: dict[str, Any],
    *,
    current_messages: list[Message],
    path: Path,
    line_number: int,
) -> tuple[datetime, str, list[Message]]:
    summary = record.get("summary")
    recent_messages = record.get("recentMessages")
    old_message_count = record.get("oldMessageCount")
    recent_message_count = record.get("recentMessageCount")
    if not isinstance(summary, str) or not summary.strip():
        raise SessionError(
            f"Session 数据损坏: {path} 第 {line_number} 行 summary 无效。"
        )
    if not isinstance(recent_messages, list):
        raise SessionError(
            f"Session 数据损坏: {path} 第 {line_number} 行 recentMessages 无效。"
        )
    try:
        retained = validate_history(recent_messages)
    except SessionError as exc:
        raise SessionError(
            f"Session 数据损坏: {path} 第 {line_number} 行: {exc}"
        ) from exc
    expected_old_count = len(current_messages) - len(retained)
    if (
        not isinstance(old_message_count, int)
        or old_message_count <= 0
        or old_message_count != expected_old_count
        or recent_message_count != len(retained)
        or current_messages[-len(retained) :] != retained
    ):
        raise SessionError(
            f"Session 数据损坏: {path} 第 {line_number} 行 compaction 边界无效。"
        )
    compacted_at = parse_datetime(
        record.get("compactedAt"),
        path,
        f"第 {line_number} 行 compactedAt",
    )
    return compacted_at, summary.strip(), retained
