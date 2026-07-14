"""Prepare, execute, and recover approved side effects."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from claw.store.approvals import ApprovalRequest, ApprovalStore
from claw.store.attachments import AttachmentStore
from claw.store.tool_executions import ToolExecutionRecord, ToolExecutionStore
from claw.store.sessions import SessionStore
from claw.tools.registry import PreparedToolCall, ToolRegistry, ToolResult
from claw.workspace import Workspace


FILE_TOOLS = {
    "create_file",
    "overwrite_file",
    "edit_file",
    "copy_attachment_to_workspace",
}
logger = logging.getLogger(__name__)


class ToolExecutionCoordinator:
    def __init__(
        self,
        store: ToolExecutionStore,
        approvals: ApprovalStore,
        attachments: AttachmentStore | None = None,
        sessions: SessionStore | None = None,
    ) -> None:
        self.store = store
        self._approvals = approvals
        self._attachments = attachments
        self._sessions = sessions

    def prepare(
        self,
        request: ApprovalRequest,
        prepared: PreparedToolCall,
    ) -> ToolExecutionRecord | None:
        if prepared.tool.safety_level != "advanced":
            return None
        preconditions = self._capture_preconditions(
            request.session_id,
            request.workspace,
            prepared,
        )
        identity = json.dumps(
            {
                "sessionId": request.session_id,
                "callId": prepared.call.call_id,
                "tool": prepared.call.name,
                "arguments": prepared.arguments,
                "workspace": request.workspace,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return self.store.create(
            approval_id=request.approval_id,
            session_id=request.session_id,
            call_id=prepared.call.call_id,
            tool=prepared.call.name,
            arguments=prepared.arguments,
            workspace=request.workspace,
            idempotency_key=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            preconditions=preconditions,
        )

    def cancel(self, execution: ToolExecutionRecord | None, reason: str) -> None:
        if execution is None:
            return
        self.store.transition(
            execution.execution_id,
            "cancelled",
            allowed_from={"prepared"},
            detail=reason,
        )

    async def execute(
        self,
        execution: ToolExecutionRecord | None,
        tools: ToolRegistry,
        prepared: PreparedToolCall,
    ) -> ToolResult:
        if execution is None:
            return await tools.execute_prepared(prepared, approved=True)
        self.store.transition(
            execution.execution_id,
            "running",
            allowed_from={"prepared"},
        )
        conflict = self._validate_preconditions(execution)
        if conflict:
            result = ToolResult(
                prepared.call.call_id,
                prepared.call.name,
                False,
                error=conflict,
            )
        else:
            result = await tools.execute_prepared(prepared, approved=True)
        status = (
            "uncertain" if result.uncertain else ("succeeded" if result.ok else "failed")
        )
        self.store.transition(
            execution.execution_id,
            status,
            allowed_from={"running"},
            result=_result_dict(result),
            detail=result.error,
        )
        return result

    def recover_interrupted(self) -> list[ToolExecutionRecord]:
        recovered: list[ToolExecutionRecord] = []
        for execution in self.store.list(status="prepared"):
            self.store.transition(
                execution.execution_id,
                "cancelled",
                allowed_from={"prepared"},
                detail="runtime 重启前尚未开始执行，已安全取消。",
            )
        for execution in self.store.list(status="running"):
            status, result, detail = self._reconcile(execution)
            updated = self.store.transition(
                execution.execution_id,
                status,
                allowed_from={"running"},
                result=result,
                detail=detail,
            )
            recovered.append(updated)
        for execution in self.store.list():
            self._sync_approval(execution)
        self._recover_session_records()
        return recovered

    def _sync_approval(self, execution: ToolExecutionRecord) -> None:
        if execution.status not in {"succeeded", "failed", "uncertain"}:
            return
        try:
            approval = self._approvals.get(execution.approval_id)
            if approval.status != "executing":
                return
            self._approvals.mark_execution(
                execution.approval_id,
                "succeeded" if execution.status == "succeeded" else (
                    "failed" if execution.status == "failed" else "interrupted"
                ),
                result=execution.result,
                reason=execution.detail,
            )
        except Exception:
            logger.exception(
                "failed to synchronize approval from execution: %s",
                execution.execution_id,
            )

    def mark_turn_committed(self, session_id: str, call_ids: set[str]) -> None:
        if not call_ids:
            return
        for execution in self.store.list():
            if (
                execution.session_id == session_id
                and execution.call_id in call_ids
                and execution.status in {"succeeded", "failed", "uncertain"}
                and not execution.session_recorded
            ):
                self.store.mark_session_recorded(execution.execution_id)

    def _recover_session_records(self) -> None:
        if self._sessions is None:
            return
        for execution in self.store.list():
            if (
                execution.status not in {"succeeded", "failed", "uncertain"}
                or execution.session_recorded
            ):
                continue
            try:
                session = self._sessions.load(execution.session_id)
            except Exception:
                continue
            if _history_contains_call(session.messages, execution.call_id):
                self.store.mark_session_recorded(execution.execution_id)
                continue
            result = execution.result or {
                "ok": False,
                "tool": execution.tool,
                "value": None,
                "error": execution.detail or "工具执行结果不确定。",
                "uncertain": execution.status == "uncertain",
            }
            content = json.dumps(
                {
                    "ok": bool(result.get("ok")),
                    **(
                        {"result": result.get("value")}
                        if result.get("value") is not None
                        else {}
                    ),
                    **(
                        {"error": result.get("error")}
                        if result.get("error")
                        else {}
                    ),
                    **(
                        {"uncertain": True}
                        if execution.status == "uncertain"
                        else {}
                    ),
                },
                ensure_ascii=False,
            )
            try:
                self._sessions.commit_turn(
                    execution.session_id,
                    expected_revision=session.revision,
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                "[Runtime recovery] 恢复未写入会话的已审批工具执行 "
                                f"{execution.execution_id}。"
                            ),
                        },
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": execution.call_id,
                                    "type": "function",
                                    "function": {
                                        "name": execution.tool,
                                        "arguments": json.dumps(
                                            execution.arguments,
                                            ensure_ascii=False,
                                            sort_keys=True,
                                        ),
                                    },
                                }
                            ],
                        },
                        {
                            "role": "tool",
                            "tool_call_id": execution.call_id,
                            "name": execution.tool,
                            "content": content,
                        },
                        {
                            "role": "assistant",
                            "content": (
                                "Runtime 已恢复该工具执行记录；"
                                + (
                                    "结果仍不确定，请人工检查环境。"
                                    if execution.status == "uncertain"
                                    else "结果已写入会话审计历史。"
                                )
                            ),
                        },
                    ],
                )
                self.store.mark_session_recorded(execution.execution_id)
            except Exception:
                logger.exception(
                    "failed to restore execution into session: %s",
                    execution.execution_id,
                )

    def _capture_preconditions(
        self,
        session_id: str,
        workspace_path: str | None,
        prepared: PreparedToolCall,
    ) -> dict[str, Any]:
        if prepared.call.name not in FILE_TOOLS or workspace_path is None:
            return {"kind": "non_reconcilable"}
        try:
            workspace = Workspace.from_path(workspace_path)
            path = workspace.resolve(prepared.arguments["path"])
            before_exists = path.is_file()
            before_hash = _hash_file(path) if before_exists else None
            expected_hash = _expected_hash(
                session_id,
                prepared.call.name,
                prepared.arguments,
                path,
                self._attachments,
            )
            return {
                "kind": "file",
                "path": str(path),
                "relativePath": workspace.relative(path),
                "beforeExists": before_exists,
                "beforeSha256": before_hash,
                "expectedSha256": expected_hash,
            }
        except Exception as exc:
            return {"kind": "unavailable", "detail": str(exc)}

    def _validate_preconditions(self, execution: ToolExecutionRecord) -> str:
        preconditions = execution.preconditions
        if preconditions.get("kind") != "file":
            return ""
        path = Path(str(preconditions["path"]))
        before_exists = bool(preconditions["beforeExists"])
        if before_exists:
            if not path.is_file() or _hash_file(path) != preconditions.get("beforeSha256"):
                return "审批后目标文件发生变化，本次操作未执行。"
        elif path.exists():
            return "审批后目标路径已出现，本次操作未执行。"
        return ""

    def _reconcile(
        self,
        execution: ToolExecutionRecord,
    ) -> tuple[str, dict[str, Any] | None, str]:
        preconditions = execution.preconditions
        if preconditions.get("kind") != "file":
            return "uncertain", None, "runtime 重启，无法确认该副作用是否完成。"
        path = Path(str(preconditions["path"]))
        expected = preconditions.get("expectedSha256")
        current = _hash_file(path) if path.is_file() else None
        if expected is not None and current == expected:
            result = {
                "ok": True,
                "tool": execution.tool,
                "value": {
                    "success": True,
                    "tool": execution.tool,
                    "path": preconditions.get("relativePath", ""),
                    "message": "recovered after runtime restart",
                },
                "error": "",
                "uncertain": False,
            }
            return "succeeded", result, "文件结果已通过 SHA-256 确认。"
        before = preconditions.get("beforeSha256")
        before_exists = bool(preconditions.get("beforeExists"))
        if (before_exists and current == before) or (not before_exists and current is None):
            return "failed", None, "未观察到目标文件变化，操作未完成。"
        return "uncertain", None, "目标文件状态与执行前后快照均不匹配。"


def _expected_hash(
    session_id: str,
    tool: str,
    arguments: dict[str, Any],
    path: Path,
    attachments: AttachmentStore | None,
) -> str | None:
    if tool in {"create_file", "overwrite_file"}:
        return hashlib.sha256(arguments["content"].encode("utf-8")).hexdigest()
    if tool == "edit_file" and path.is_file():
        content = path.read_text(encoding="utf-8")
        old = arguments["old_text"]
        if old and content.count(old) == 1:
            updated = content.replace(old, arguments["new_text"], 1)
            return hashlib.sha256(updated.encode("utf-8")).hexdigest()
    if tool == "copy_attachment_to_workspace" and attachments is not None:
        _, content = attachments.read_bytes(session_id, arguments["attachment_id"])
        return hashlib.sha256(content).hexdigest()
    return None


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _result_dict(result: ToolResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "tool": result.name,
        "value": result.value,
        "error": result.error,
        "uncertain": result.uncertain,
    }


def _history_contains_call(messages, call_id: str) -> bool:
    return any(
        message.get("role") == "assistant"
        and any(
            call.get("id") == call_id
            for call in message.get("tool_calls", [])
            if isinstance(call, dict)
        )
        for message in messages
    )
