"""Workspace-scoped update, attachment-copy, and download tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from claw.errors import WorkspaceError
from claw.store.attachments import AttachmentStore
from claw.store.downloads import DownloadStore
from claw.tools.registry import ToolDefinition, ToolRegistry
from claw.workspace import Workspace


def register_workspace_tools(
    registry: ToolRegistry,
    workspace: Workspace | None,
    session_id: str,
    attachments: AttachmentStore,
    downloads: DownloadStore,
) -> None:
    definitions = [
        _update_definition(
            "create_file",
            "Create a new UTF-8 file in the workspace.",
            lambda args: _create(_require(workspace), args),
        ),
        _update_definition(
            "overwrite_file",
            "Overwrite an existing UTF-8 workspace file.",
            lambda args: _overwrite(_require(workspace), args),
        ),
        ToolDefinition(
            "edit_file",
            "Replace one exact text occurrence in an existing workspace file.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
            lambda args: _edit(_require(workspace), args),
            safety_level="advanced",
            requires_approval=True,
        ),
        ToolDefinition(
            "copy_attachment_to_workspace",
            "Copy an attachment owned by this session into the workspace.",
            {
                "type": "object",
                "properties": {
                    "attachment_id": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["attachment_id", "path"],
                "additionalProperties": False,
            },
            lambda args: _copy_attachment(
                _require(workspace), attachments, session_id, args
            ),
            safety_level="advanced",
            requires_approval=True,
        ),
        ToolDefinition(
            "create_download",
            "Create a temporary Gateway download for an existing workspace file.",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            lambda args: _download(_require(workspace), downloads, session_id, args),
            safety_level="download",
        ),
    ]
    for definition in definitions:
        registry.register(definition)


def _update_definition(name: str, description: str, handler) -> ToolDefinition:
    return ToolDefinition(
        name,
        description,
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        handler,
        safety_level="advanced",
        requires_approval=True,
    )


def _require(workspace: Workspace | None) -> Workspace:
    if workspace is None:
        raise WorkspaceError("当前 session 尚未设置 workspace。")
    return workspace


def _create(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    path = workspace.resolve(args["path"])
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(args["content"])
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise WorkspaceError(f"文件已存在: {args['path']}。") from exc
    return _update_result("create_file", workspace, path, "file created")


def _overwrite(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    path = workspace.resolve(args["path"], must_exist=True, kind="file")
    _atomic_write(path, args["content"])
    return _update_result("overwrite_file", workspace, path, "file overwritten")


def _edit(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    old = args["old_text"]
    if not old:
        raise WorkspaceError("edit_file old_text 不能为空。")
    path = workspace.resolve(args["path"], must_exist=True, kind="file")
    content = path.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise WorkspaceError(f"old_text 必须唯一匹配，当前匹配 {count} 次。")
    _atomic_write(path, content.replace(old, args["new_text"], 1))
    return _update_result("edit_file", workspace, path, "replaced 1 occurrence")


def _copy_attachment(
    workspace: Workspace,
    attachments: AttachmentStore,
    session_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    record, content = attachments.read_bytes(session_id, args["attachment_id"])
    path = workspace.resolve(args["path"])
    if path.exists():
        raise WorkspaceError(f"目标文件已存在: {args['path']}。")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "success": True,
        "tool": "copy_attachment_to_workspace",
        "path": workspace.relative(path),
        "attachmentId": record.attachment_id,
        "message": "attachment copied",
    }


def _download(
    workspace: Workspace,
    downloads: DownloadStore,
    session_id: str,
    args: dict[str, Any],
) -> dict[str, object]:
    path = workspace.resolve(args["path"], must_exist=True, kind="file")
    return downloads.create(session_id, path).to_dict()


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _update_result(
    tool: str,
    workspace: Workspace,
    path: Path,
    message: str,
) -> dict[str, object]:
    return {
        "success": True,
        "tool": tool,
        "path": workspace.relative(path),
        "message": message,
    }
