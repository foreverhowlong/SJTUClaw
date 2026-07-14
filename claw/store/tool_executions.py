"""Durable records for approved side-effect execution and recovery."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from filelock import FileLock

from claw.errors import ToolError


ExecutionStatus = Literal[
    "prepared",
    "running",
    "succeeded",
    "failed",
    "uncertain",
    "cancelled",
]
EXECUTION_ID_PATTERN = re.compile(r"execution_[0-9a-f]{32}")


@dataclass(frozen=True)
class ToolExecutionRecord:
    execution_id: str
    approval_id: str
    session_id: str
    call_id: str
    tool: str
    arguments: dict[str, Any]
    workspace: str | None
    idempotency_key: str
    status: ExecutionStatus
    preconditions: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    result: dict[str, Any] | None = None
    detail: str = ""
    session_recorded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "executionId": self.execution_id,
            "approvalId": self.approval_id,
            "sessionId": self.session_id,
            "callId": self.call_id,
            "tool": self.tool,
            "arguments": self.arguments,
            "workspace": self.workspace,
            "idempotencyKey": self.idempotency_key,
            "status": self.status,
            "preconditions": self.preconditions,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
            "result": self.result,
            "detail": self.detail,
            "sessionRecorded": self.session_recorded,
        }


class ToolExecutionStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def create(
        self,
        *,
        approval_id: str,
        session_id: str,
        call_id: str,
        tool: str,
        arguments: dict[str, Any],
        workspace: str | None,
        idempotency_key: str,
        preconditions: dict[str, Any],
    ) -> ToolExecutionRecord:
        now = datetime.now(timezone.utc)
        record = ToolExecutionRecord(
            execution_id=f"execution_{uuid4().hex}",
            approval_id=approval_id,
            session_id=session_id,
            call_id=call_id,
            tool=tool,
            arguments=arguments,
            workspace=workspace,
            idempotency_key=idempotency_key,
            status="prepared",
            preconditions=preconditions,
            created_at=now,
            updated_at=now,
        )
        with self._lock():
            self._write(record)
        return record

    def get(self, execution_id: str) -> ToolExecutionRecord:
        path = self._path(execution_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ToolError(f"Tool execution 不存在: {execution_id}。") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ToolError(f"读取 tool execution 失败: {exc}") from exc
        return _decode(value, path)

    def list(self, *, status: ExecutionStatus | None = None) -> list[ToolExecutionRecord]:
        if not self.root.exists():
            return []
        records = [self.get(path.stem) for path in self.root.glob("execution_*.json")]
        if status is not None:
            records = [record for record in records if record.status == status]
        return sorted(records, key=lambda item: item.created_at)

    def transition(
        self,
        execution_id: str,
        status: ExecutionStatus,
        *,
        allowed_from: set[ExecutionStatus],
        result: dict[str, Any] | None = None,
        detail: str = "",
    ) -> ToolExecutionRecord:
        with self._lock():
            current = self.get(execution_id)
            if current.status not in allowed_from:
                raise ToolError(
                    f"Tool execution {execution_id} 不能从 {current.status} 进入 {status}。"
                )
            updated = replace(
                current,
                status=status,
                result=result,
                detail=detail.strip(),
                updated_at=datetime.now(timezone.utc),
            )
            self._write(updated)
            return updated

    def mark_session_recorded(self, execution_id: str) -> ToolExecutionRecord:
        with self._lock():
            current = self.get(execution_id)
            if current.status not in {"succeeded", "failed", "uncertain"}:
                raise ToolError(
                    f"未结束 execution 不能标记 sessionRecorded: {execution_id}。"
                )
            if current.session_recorded:
                return current
            updated = replace(
                current,
                session_recorded=True,
                updated_at=datetime.now(timezone.utc),
            )
            self._write(updated)
            return updated

    def _path(self, execution_id: str) -> Path:
        if not EXECUTION_ID_PATTERN.fullmatch(execution_id):
            raise ToolError(f"无效的 executionId: {execution_id!r}。")
        return self.root / f"{execution_id}.json"

    def _write(self, record: ToolExecutionRecord) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(record.execution_id)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(record.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise ToolError(f"保存 tool execution 失败: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def _lock(self) -> FileLock:
        self.root.mkdir(parents=True, exist_ok=True)
        return FileLock(self.root / ".executions.lock", timeout=10)


def _decode(value: Any, path: Path) -> ToolExecutionRecord:
    try:
        if not isinstance(value, dict):
            raise TypeError("record is not an object")
        status = value["status"]
        if status not in {
            "prepared",
            "running",
            "succeeded",
            "failed",
            "uncertain",
            "cancelled",
        }:
            raise ValueError("status invalid")
        record = ToolExecutionRecord(
            execution_id=str(value["executionId"]),
            approval_id=str(value["approvalId"]),
            session_id=str(value["sessionId"]),
            call_id=str(value["callId"]),
            tool=str(value["tool"]),
            arguments=dict(value["arguments"]),
            workspace=(
                str(value["workspace"]) if value.get("workspace") is not None else None
            ),
            idempotency_key=str(value["idempotencyKey"]),
            status=status,
            preconditions=dict(value["preconditions"]),
            created_at=datetime.fromisoformat(str(value["createdAt"])),
            updated_at=datetime.fromisoformat(str(value["updatedAt"])),
            result=dict(value["result"]) if value.get("result") is not None else None,
            detail=str(value.get("detail", "")),
            session_recorded=bool(value.get("sessionRecorded", False)),
        )
        if record.execution_id != path.stem or not EXECUTION_ID_PATTERN.fullmatch(
            record.execution_id
        ):
            raise ValueError("executionId invalid")
        if record.created_at.tzinfo is None or record.updated_at.tzinfo is None:
            raise ValueError("timestamps require timezone")
        return record
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError(f"Tool execution 数据损坏 {path}: {exc}") from exc
