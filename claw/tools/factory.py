"""Build immutable tool catalogs for one session turn."""

from __future__ import annotations

from claw.session import Session
from claw.shell import ShellManager
from claw.store.attachments import AttachmentStore
from claw.store.downloads import DownloadStore
from claw.store.memory import MemoryStore
from claw.tools.attachment import READ_ATTACHMENT_TOOL_NAME, build_read_attachment_tool
from claw.tools.builtin import build_workspace_read_only_registry
from claw.tools.memory import register_memory_tools
from claw.tools.registry import ToolRegistry
from claw.tools.shell import register_shell_tools
from claw.tools.workspace import register_workspace_tools
from claw.workspace import workspace_from_session


class SessionToolProvider:
    def __init__(
        self,
        attachments: AttachmentStore,
        downloads: DownloadStore,
        shells: ShellManager,
        memories: MemoryStore,
    ) -> None:
        self._attachments = attachments
        self._downloads = downloads
        self._shells = shells
        self._memories = memories

    def for_session(self, session: Session) -> ToolRegistry:
        workspace = workspace_from_session(session)
        registry = build_workspace_read_only_registry(workspace)
        if registry.get(READ_ATTACHMENT_TOOL_NAME) is None:
            registry.register(
                build_read_attachment_tool(self._attachments, session.session_id)
            )
        register_workspace_tools(
            registry,
            workspace,
            session.session_id,
            self._attachments,
            self._downloads,
        )
        register_shell_tools(registry, workspace, session.session_id, self._shells)
        register_memory_tools(registry, self._memories)
        return registry
