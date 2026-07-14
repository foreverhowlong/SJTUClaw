"""Composition root shared by CLI, Gateway, and future entry points."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from claw.agent import AgentService
from claw.approval import ApprovalCoordinator
from claw.compaction import Compactor, load_compaction_prompt
from claw.config import load_llm_config
from claw.context import ContextBuilder
from claw.llm import LLMClient
from claw.logging_config import configure_logging
from claw.paths import RuntimePaths
from claw.scheduler import Scheduler
from claw.session_coordination import SessionCoordinator
from claw.session_lifecycle import SessionLifecycleService
from claw.shell import ShellManager
from claw.skills import SkillRegistry
from claw.store.approvals import ApprovalStore
from claw.store.attachments import AttachmentStore
from claw.store.memory import MemoryStore
from claw.store.downloads import DownloadStore
from claw.store.sessions import SessionStore
from claw.store.tasks import TaskStore
from claw.store.tool_executions import ToolExecutionStore
from claw.tool_execution import ToolExecutionCoordinator
from claw.tools.factory import SessionToolProvider
from claw.workspace import WorkspaceService


@dataclass(frozen=True)
class ClawRuntime:
    paths: RuntimePaths
    session_store: SessionStore
    memory_store: MemoryStore
    attachment_store: AttachmentStore
    task_store: TaskStore
    approval_store: ApprovalStore
    approval_coordinator: ApprovalCoordinator
    download_store: DownloadStore
    workspace_service: WorkspaceService
    shell_manager: ShellManager
    skill_registry: SkillRegistry
    session_coordinator: SessionCoordinator
    session_lifecycle: SessionLifecycleService
    execution_store: ToolExecutionStore
    tool_execution_coordinator: ToolExecutionCoordinator
    agent: AgentService
    scheduler: Scheduler


def build_runtime(paths: RuntimePaths | None = None) -> ClawRuntime:
    """Build the one runtime graph used by every external renderer."""
    resolved_paths = paths or RuntimePaths.from_environment()
    configure_logging(resolved_paths.logs_dir)
    config = load_llm_config(resolved_paths.env_file)
    session_store = SessionStore(resolved_paths.sessions_dir)
    session_coordinator = SessionCoordinator(resolved_paths.sessions_dir)
    memory_store = MemoryStore(resolved_paths.memory_dir)
    attachment_store = AttachmentStore(session_store)
    task_store = TaskStore(resolved_paths.tasks_dir)
    approval_store = ApprovalStore(resolved_paths.approvals_dir)
    approval_coordinator = ApprovalCoordinator(approval_store)
    execution_store = ToolExecutionStore(resolved_paths.executions_dir)
    tool_execution_coordinator = ToolExecutionCoordinator(
        execution_store,
        approval_store,
        attachment_store,
        session_store,
    )
    download_store = DownloadStore(resolved_paths.downloads_dir)
    workspace_service = WorkspaceService(session_store)
    shell_manager = ShellManager(timeout_seconds=25)
    skill_registry = SkillRegistry(resolved_paths.skills_dir)
    tool_provider = SessionToolProvider(
        attachment_store,
        download_store,
        shell_manager,
    )
    llm = LLMClient(config)
    compactor = Compactor(llm, session_store, load_compaction_prompt())
    agent = AgentService(
        llm,
        session_store,
        ContextBuilder.from_files(
            resolved_paths.system_prompt_file,
            resolved_paths.soul_file,
        ),
        memory_store,
        compactor,
        approval_policy=approval_coordinator,
        attachment_store=attachment_store,
        tool_provider=tool_provider,
        skill_registry=skill_registry,
        session_coordinator=session_coordinator,
        tool_execution_coordinator=tool_execution_coordinator,
    )
    scheduler = Scheduler(task_store, session_store, agent)
    session_lifecycle = SessionLifecycleService(
        session_store,
        session_coordinator,
        scheduler=scheduler,
        approvals=approval_store,
        shells=shell_manager,
    )
    return ClawRuntime(
        paths=resolved_paths,
        session_store=session_store,
        memory_store=memory_store,
        attachment_store=attachment_store,
        task_store=task_store,
        approval_store=approval_store,
        approval_coordinator=approval_coordinator,
        download_store=download_store,
        workspace_service=workspace_service,
        shell_manager=shell_manager,
        skill_registry=skill_registry,
        session_coordinator=session_coordinator,
        session_lifecycle=session_lifecycle,
        execution_store=execution_store,
        tool_execution_coordinator=tool_execution_coordinator,
        agent=agent,
        scheduler=scheduler,
    )


@asynccontextmanager
async def serve_runtime(runtime: ClawRuntime) -> AsyncIterator[ClawRuntime]:
    """Explicitly activate long-running services for a runtime host."""
    if hasattr(runtime, "tool_execution_coordinator"):
        runtime.tool_execution_coordinator.recover_interrupted()
    if hasattr(runtime, "approval_store"):
        runtime.approval_store.recover_interrupted()
    if hasattr(runtime, "download_store"):
        runtime.download_store.cleanup_expired()
    await runtime.scheduler.start()
    try:
        yield runtime
    finally:
        await runtime.scheduler.stop()
        if hasattr(runtime, "shell_manager"):
            await runtime.shell_manager.close_all()
