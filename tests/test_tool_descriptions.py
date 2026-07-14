from claw.context import ContextBuilder
from claw.shell import ShellManager
from claw.store.attachments import AttachmentStore
from claw.store.downloads import DownloadStore
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore
from claw.tools.factory import SessionToolProvider


def test_session_tool_catalog_exposes_planning_constraints(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    session = sessions.create()
    provider = SessionToolProvider(
        AttachmentStore(sessions),
        DownloadStore(tmp_path / "downloads", ttl_seconds=60),
        ShellManager(),
        MemoryStore(tmp_path / "memory"),
    )

    definitions = {
        item["function"]["name"]: item["function"]
        for item in provider.for_session(session).definitions()
    }

    assert "64 KiB" in definitions["read_file"]["description"]
    assert (
        "does not create a user-visible download"
        in definitions["read_file"]["description"]
    )
    assert "must not already exist" in definitions["create_file"]["description"]
    assert "entire contents" in definitions["overwrite_file"]["description"]
    assert "occurs more than once" in definitions["edit_file"]["description"]
    assert (
        "preserving its bytes"
        in definitions["copy_attachment_to_workspace"]["description"]
    )
    assert "1-minute" in definitions["create_download"]["description"]
    assert "cross-session" in definitions["save_memory"]["description"]
    assert "requires user approval" in definitions["delete_memory"]["description"]
    assert "do not update this download" in definitions["create_download"]["description"]
    assert "defaults to the workspace root" in definitions["new_shell"]["description"]
    assert "persist across calls" in definitions["run_command"]["description"]
    assert "64 KiB" in definitions["read_attachment"]["description"]
    assert (
        "does not create a user-visible download"
        in definitions["read_attachment"]["description"]
    )

    for name in (
        "list_dir",
        "read_file",
        "create_file",
        "overwrite_file",
        "edit_file",
        "copy_attachment_to_workspace",
        "create_download",
    ):
        path_schema = definitions[name]["parameters"]["properties"]["path"]
        assert "Workspace-relative" in path_schema["description"]


def test_default_system_prompt_explains_tool_results_are_not_user_delivery() -> None:
    system_message = ContextBuilder.from_files().build([])[0]["content"]

    assert "Tool results are internal observations" in system_message
    assert "include the relevant content or a clear summary" in system_message
    assert "use create_download when available" in system_message
    assert "merely because a read tool succeeded" in system_message
