import asyncio
from io import BytesIO

import pytest

from claw.approval import ApprovalCoordinator
from claw.errors import ShellError, WorkspaceError
from claw.shell import ShellManager
from claw.store.approvals import ApprovalStore
from claw.store.attachments import AttachmentStore
from claw.store.downloads import DownloadStore
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore
from claw.tools.factory import SessionToolProvider
from claw.tools.registry import ToolCall
from claw.workspace import Workspace, WorkspaceService


def run(registry, call, *, approved=False):
    return asyncio.run(registry.execute(call, approved=approved))


def test_workspace_binding_persists_and_rejects_escape(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    session = sessions.create()
    project = tmp_path / "project"
    project.mkdir()

    updated = WorkspaceService(sessions).set(session.session_id, str(project))

    assert updated.workspace == str(project.resolve())
    assert sessions.load(session.session_id).workspace == str(project.resolve())
    workspace = Workspace.from_path(project)
    assert workspace.resolve("new.txt") == project / "new.txt"
    with pytest.raises(WorkspaceError, match="相对路径"):
        workspace.resolve(str(tmp_path / "outside.txt"))
    with pytest.raises(WorkspaceError, match="边界"):
        workspace.resolve("../outside.txt")


def test_workspace_resolver_rejects_symlink_escape(tmp_path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceError, match="边界"):
        Workspace.from_path(project).resolve("link/secret.txt")


def test_session_tools_require_approval_and_keep_attachment_scope(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    attachments = AttachmentStore(sessions)
    downloads = DownloadStore(tmp_path / "downloads")
    shells = ShellManager(timeout_seconds=2)
    first = sessions.create()
    second = sessions.create()
    project = tmp_path / "project"
    project.mkdir()
    first = WorkspaceService(sessions).set(first.session_id, str(project))
    foreign = attachments.save(
        second.session_id,
        "foreign.txt",
        "text/plain",
        BytesIO(b"secret"),
    )
    registry = SessionToolProvider(
        attachments,
        downloads,
        shells,
        MemoryStore(tmp_path / "memory"),
    ).for_session(first)
    assert registry.get("new_shell") is not None
    assert registry.get("restart_shell") is None

    denied = run(
        registry,
        ToolCall("1", "create_file", '{"path":"note.txt","content":"hello"}'),
    )
    assert not denied.ok and not (project / "note.txt").exists()

    created = run(
        registry,
        ToolCall("2", "create_file", '{"path":"note.txt","content":"hello"}'),
        approved=True,
    )
    assert created.ok and (project / "note.txt").read_text() == "hello"

    foreign_copy = run(
        registry,
        ToolCall(
            "3",
            "copy_attachment_to_workspace",
            f'{{"attachment_id":"{foreign.attachment_id}","path":"foreign.txt"}}',
        ),
        approved=True,
    )
    assert not foreign_copy.ok and "当前 session" in foreign_copy.error

    download = run(
        registry,
        ToolCall("4", "create_download", '{"path":"note.txt"}'),
    )
    assert download.ok
    record = downloads.get(download.value["downloadId"])
    assert record.blob_path.read_text() == "hello"


def test_new_shell_keeps_state_and_run_command_requires_explicit_restart(tmp_path) -> None:
    async def scenario():
        project = tmp_path / "project"
        child = project / "child"
        child.mkdir(parents=True)
        workspace = Workspace.from_path(project)
        manager = ShellManager(timeout_seconds=2)
        with pytest.raises(ShellError, match="new_shell"):
            await manager.run_command("session", workspace, "pwd")
        started = await manager.new_shell("session", workspace, project.resolve())
        first = await manager.run_command("session", workspace, "export CLAW_TEST=kept; cd child")
        second = await manager.run_command("session", workspace, 'printf "%s" "$CLAW_TEST"')
        restarted = await manager.new_shell("session", workspace, child.resolve())
        escaped = await manager.run_command("session", workspace, "cd ../..")
        with pytest.raises(ShellError, match="new_shell"):
            await manager.run_command("session", workspace, "pwd")
        await manager.close_all()
        return started, first, second, restarted, escaped

    started, first, second, restarted, escaped = asyncio.run(scenario())
    assert started["tool"] == "new_shell"
    assert first["success"] is True and first["cwd"].endswith("/child")
    assert first["shellStarted"] is False
    assert second["stdout"] == "kept"
    assert second["shellStarted"] is False
    assert restarted["tool"] == "new_shell"
    assert restarted["cwd"].endswith("/child")
    assert escaped["success"] is False and "离开 workspace" in escaped["error"]


def test_approval_coordinator_persists_and_wakes_waiter(tmp_path) -> None:
    store = ApprovalStore(tmp_path / "approvals")
    coordinator = ApprovalCoordinator(store, timeout_seconds=1)

    async def scenario():
        from claw.tools.registry import ToolDefinition, ToolRegistry

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                "write",
                "write",
                {"type": "object", "properties": {}, "additionalProperties": False},
                lambda _args: None,
                safety_level="advanced",
                requires_approval=True,
            )
        )
        prepared, error = registry.prepare(ToolCall("call", "write", "{}"))
        assert error is None and prepared is not None
        request = coordinator.create("session_000000000000", prepared, "/workspace")
        pending = asyncio.create_task(coordinator.wait(request.approval_id))
        await asyncio.sleep(0)
        coordinator.resolve(request.approval_id, approved=False, reason="not now")
        return request, await pending

    request, decision = asyncio.run(scenario())
    assert not decision.approved and decision.reason == "not now"
    assert store.get(request.approval_id).status == "denied"
