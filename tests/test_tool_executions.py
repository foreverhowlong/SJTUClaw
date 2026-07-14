import asyncio

from claw.store.approvals import ApprovalStore
from claw.store.sessions import SessionStore
from claw.store.tool_executions import ToolExecutionStore
from claw.tool_execution import ToolExecutionCoordinator
from claw.tools import ToolCall, ToolDefinition, ToolRegistry


def _advanced_registry(handler) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "overwrite_file",
            "Overwrite.",
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
    )
    return registry


def test_file_precondition_rejects_changes_after_approval(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "note.txt"
    target.write_text("before", encoding="utf-8")
    approvals = ApprovalStore(tmp_path / "approvals")
    store = ToolExecutionStore(tmp_path / "executions")
    coordinator = ToolExecutionCoordinator(store, approvals)
    registry = _advanced_registry(
        lambda args: target.write_text(args["content"], encoding="utf-8")
    )
    prepared, error = registry.prepare(
        ToolCall(
            "call_1",
            "overwrite_file",
            '{"path":"note.txt","content":"approved"}',
        )
    )
    assert prepared is not None and error is None
    approval = approvals.create(
        "session_0123456789ab",
        prepared.call.call_id,
        prepared.call.name,
        prepared.arguments,
        str(project),
    )
    execution = coordinator.prepare(approval, prepared)
    assert execution is not None
    target.write_text("changed after approval", encoding="utf-8")

    result = asyncio.run(coordinator.execute(execution, registry, prepared))

    assert not result.ok and "审批后" in result.error
    assert target.read_text(encoding="utf-8") == "changed after approval"
    assert store.get(execution.execution_id).status == "failed"


def test_recovery_reconciles_completed_atomic_file_write(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "note.txt"
    target.write_text("before", encoding="utf-8")
    approvals = ApprovalStore(tmp_path / "approvals")
    store = ToolExecutionStore(tmp_path / "executions")
    sessions = SessionStore(tmp_path / "sessions")
    session = sessions.create()
    coordinator = ToolExecutionCoordinator(store, approvals, sessions=sessions)
    registry = _advanced_registry(lambda _args: None)
    prepared, error = registry.prepare(
        ToolCall(
            "call_1",
            "overwrite_file",
            '{"path":"note.txt","content":"after"}',
        )
    )
    assert prepared is not None and error is None
    approval = approvals.create(
        session.session_id,
        prepared.call.call_id,
        prepared.call.name,
        prepared.arguments,
        str(project),
    )
    approvals.resolve(approval.approval_id, approved=True)
    approvals.mark_execution(approval.approval_id, "executing")
    execution = coordinator.prepare(approval, prepared)
    assert execution is not None
    store.transition(
        execution.execution_id,
        "running",
        allowed_from={"prepared"},
    )
    target.write_text("after", encoding="utf-8")

    recovered = coordinator.recover_interrupted()

    assert recovered[0].status == "succeeded"
    assert approvals.get(approval.approval_id).status == "succeeded"
    assert recovered[0].result["value"]["path"] == "note.txt"
    restored = sessions.load(session.session_id)
    assert [message["role"] for message in restored.messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert store.get(execution.execution_id).session_recorded


def test_recovery_marks_non_reconcilable_shell_execution_uncertain(tmp_path) -> None:
    approvals = ApprovalStore(tmp_path / "approvals")
    store = ToolExecutionStore(tmp_path / "executions")
    coordinator = ToolExecutionCoordinator(store, approvals)
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "run_command",
            "Run.",
            {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
            lambda _args: None,
            safety_level="advanced",
            requires_approval=True,
        )
    )
    prepared, error = registry.prepare(
        ToolCall("call_1", "run_command", '{"command":"make"}')
    )
    assert prepared is not None and error is None
    approval = approvals.create(
        "session_0123456789ab",
        prepared.call.call_id,
        prepared.call.name,
        prepared.arguments,
        "/workspace",
    )
    approvals.resolve(approval.approval_id, approved=True)
    approvals.mark_execution(approval.approval_id, "executing")
    execution = coordinator.prepare(approval, prepared)
    assert execution is not None
    store.transition(execution.execution_id, "running", allowed_from={"prepared"})

    recovered = coordinator.recover_interrupted()

    assert recovered[0].status == "uncertain"
    assert approvals.get(approval.approval_id).status == "interrupted"


def test_recovery_cancels_prepared_execution_and_expires_approval(tmp_path) -> None:
    approvals = ApprovalStore(tmp_path / "approvals")
    store = ToolExecutionStore(tmp_path / "executions")
    coordinator = ToolExecutionCoordinator(store, approvals)
    registry = _advanced_registry(lambda _args: None)
    prepared, error = registry.prepare(
        ToolCall(
            "call_1",
            "overwrite_file",
            '{"path":"note.txt","content":"after"}',
        )
    )
    assert prepared is not None and error is None
    approval = approvals.create(
        "session_0123456789ab",
        prepared.call.call_id,
        prepared.call.name,
        prepared.arguments,
        str(tmp_path),
    )
    execution = coordinator.prepare(approval, prepared)
    assert execution is not None

    assert coordinator.recover_interrupted() == []
    approvals.recover_interrupted()

    assert store.get(execution.execution_id).status == "cancelled"
    assert approvals.get(approval.approval_id).status == "expired"
