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
    async def restart_shell(args):
        active = _require(workspace)
        cwd = active.resolve(
            args.get("cwd", "."),
            must_exist=True,
            kind="directory",
        )
        return await shells.restart_shell(session_id, active, cwd)

    async def run_command(args):
        active = _require(workspace)
        return await shells.run_command(session_id, active, args["command"])

    registry.register(
        ToolDefinition(
            "restart_shell",
            "Restart the session shell, discarding its current cwd and environment "
            "state. Optional cwd must be an existing workspace directory and defaults "
            "to the workspace root. Use only for an explicit reset or starting-"
            "directory change.",
            {
                "type": "object",
                "properties": {
                    "cwd": {
                        "type": "string",
                        "description": (
                            "Workspace-relative existing directory; defaults to the "
                            "workspace root."
                        ),
                    }
                },
                "additionalProperties": False,
            },
            restart_shell,
            safety_level="advanced",
            requires_approval=True,
        )
    )
    registry.register(
        ToolDefinition(
            "run_command",
            "Run one command in the persistent session shell. Shell cwd and "
            "environment changes persist across calls. A shell is started at the "
            "workspace root when needed. Commands have a runtime timeout; timeout "
            "terminates the shell, and large stdout or stderr may be truncated.",
            {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to run in the persistent shell.",
                    }
                },
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
