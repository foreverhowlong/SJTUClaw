"""Durable approval and advanced-tool execution records."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from filelock import FileLock

from claw.errors import ApprovalError


ApprovalStatus = Literal[
    "pending",
    "approved",
    "denied",
    "executing",
    "succeeded",
    "failed",
    "expired",
    "interrupted",
]


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    session_id: str
    call_id: str
    tool: str
    arguments: dict[str, Any]
    workspace: str | None
    status: ApprovalStatus
    reason: str
    created_at: datetime
    updated_at: datetime
    result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "approvalId": self.approval_id,
            "sessionId": self.session_id,
            "callId": self.call_id,
            "tool": self.tool,
            "arguments": self.arguments,
            "workspace": self.workspace,
            "status": self.status,
            "reason": self.reason,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
            "result": self.result,
        }


class ApprovalStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def create(
        self,
        session_id: str,
        call_id: str,
        tool: str,
        arguments: dict[str, Any],
        workspace: str | None,
    ) -> ApprovalRequest:
        now = datetime.now(timezone.utc)
        request = ApprovalRequest(
            f"approval_{uuid4().hex}",
            session_id,
            call_id,
            tool,
            arguments,
            workspace,
            "pending",
            "",
            now,
            now,
        )
        with self._lock():
            self._write(request)
        return request

    def get(self, approval_id: str) -> ApprovalRequest:
        path = self._path(approval_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ApprovalError(f"Approval 不存在: {approval_id}。") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ApprovalError(f"读取 approval 失败 {path}: {exc}") from exc
        return _decode(value, path)

    def list(
        self,
        *,
        session_id: str | None = None,
        status: ApprovalStatus | None = None,
    ) -> list[ApprovalRequest]:
        if not self.root.exists():
            return []
        records = []
        for path in self.root.glob("approval_*.json"):
            record = self.get(path.stem)
            if session_id is not None and record.session_id != session_id:
                continue
            if status is not None and record.status != status:
                continue
            records.append(record)
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    def resolve(
        self,
        approval_id: str,
        *,
        approved: bool,
        reason: str = "",
    ) -> ApprovalRequest:
        with self._lock():
            current = self.get(approval_id)
            if current.status != "pending":
                raise ApprovalError(
                    f"Approval {approval_id} 已处理: {current.status}。"
                )
            updated = replace(
                current,
                status="approved" if approved else "denied",
                reason=reason.strip(),
                updated_at=datetime.now(timezone.utc),
            )
            self._write(updated)
            return updated

    def mark_execution(
        self,
        approval_id: str,
        status: Literal["executing", "succeeded", "failed", "interrupted"],
        *,
        result: dict[str, Any] | None = None,
        reason: str = "",
    ) -> ApprovalRequest:
        with self._lock():
            current = self.get(approval_id)
            allowed = {
                "executing": {"approved"},
                "succeeded": {"executing"},
                "failed": {"executing"},
                "interrupted": {"executing"},
            }
            if current.status not in allowed[status]:
                raise ApprovalError(
                    f"Approval {approval_id} 不能从 {current.status} 进入 {status}。"
                )
            updated = replace(
                current,
                status=status,
                result=result,
                reason=reason.strip() or current.reason,
                updated_at=datetime.now(timezone.utc),
            )
            self._write(updated)
            return updated

    def recover_interrupted(self) -> None:
        for status in ("pending", "approved"):
            for record in self.list(status=status):
                self._expire(
                    record.approval_id,
                    reason="runtime restarted before the approval flow completed",
                )
        for record in self.list(status="executing"):
            self.mark_execution(
                record.approval_id,
                "interrupted",
                reason="runtime restarted while tool execution was in progress",
            )

    def _expire(self, approval_id: str, *, reason: str) -> ApprovalRequest:
        with self._lock():
            current = self.get(approval_id)
            if current.status not in {"pending", "approved"}:
                raise ApprovalError(
                    f"Approval {approval_id} 不能从 {current.status} 进入 expired。"
                )
            updated = replace(
                current,
                status="expired",
                reason=reason,
                updated_at=datetime.now(timezone.utc),
            )
            self._write(updated)
            return updated

    def _path(self, approval_id: str) -> Path:
        if not approval_id.startswith("approval_") or any(
            part in approval_id for part in ("/", "\\", "..")
        ):
            raise ApprovalError(f"无效的 approvalId: {approval_id!r}。")
        return self.root / f"{approval_id}.json"

    def _write(self, request: ApprovalRequest) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(request.approval_id)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(request.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise ApprovalError(f"写入 approval 失败 {path}: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def _lock(self) -> FileLock:
        self.root.mkdir(parents=True, exist_ok=True)
        return FileLock(self.root / ".approvals.lock", timeout=10)


def _decode(value: Any, path: Path) -> ApprovalRequest:
    try:
        if not isinstance(value, dict):
            raise TypeError("record is not an object")
        arguments = value["arguments"]
        result = value.get("result")
        if not isinstance(arguments, dict) or (
            result is not None and not isinstance(result, dict)
        ):
            raise TypeError("arguments/result invalid")
        return ApprovalRequest(
            str(value["approvalId"]),
            str(value["sessionId"]),
            str(value["callId"]),
            str(value["tool"]),
            arguments,
            str(value["workspace"]) if value.get("workspace") is not None else None,
            value["status"],
            str(value.get("reason", "")),
            datetime.fromisoformat(str(value["createdAt"])),
            datetime.fromisoformat(str(value["updatedAt"])),
            result,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ApprovalError(f"Approval 数据损坏 {path}: {exc}") from exc
