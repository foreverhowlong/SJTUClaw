"""Tool adapters for the session-scoped ShellManager."""

from __future__ import annotations

from claw.errors import WorkspaceError
from claw.shell import ShellManager
from claw.tools.registry import ToolDefinition, ToolRegistry
from claw.workspace import Workspace


def register_shell_tools(
    registry: ToolRegistry,
    workspace: Workspace | None,
    session_id: str,
    shells: ShellManager,
) -> None:
    async def new_shell(args):
        active = _require(workspace)
        cwd = active.resolve(
            args.get("cwd", "."),
            must_exist=True,
            kind="directory",
        )
        return await shells.new_shell(session_id, active, cwd)

    async def run_command(args):
        active = _require(workspace)
        return await shells.run_command(session_id, active, args["command"])

    registry.register(
        ToolDefinition(
            "new_shell",
            "Start a persistent shell in the workspace or one of its subdirectories.",
            {
                "type": "object",
                "properties": {"cwd": {"type": "string"}},
                "additionalProperties": False,
            },
            new_shell,
            safety_level="advanced",
            requires_approval=True,
        )
    )
    registry.register(
        ToolDefinition(
            "run_command",
            "Run one command in the current persistent session shell.",
            {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
            run_command,
            safety_level="advanced",
            requires_approval=True,
        )
    )


def _require(workspace: Workspace | None) -> Workspace:
    if workspace is None:
        raise WorkspaceError("当前 session 尚未设置 workspace。")
    return workspace
