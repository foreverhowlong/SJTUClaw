"""Composition root shared by CLI, Gateway, and future entry points."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from claw.agent import AgentService
from claw.compaction import Compactor, load_compaction_prompt
from claw.config import load_llm_config
from claw.context import ContextBuilder
from claw.llm import LLMClient
from claw.logging_config import configure_logging
from claw.paths import RuntimePaths
from claw.scheduler import Scheduler
from claw.store.attachments import AttachmentStore
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore
from claw.store.tasks import TaskStore


@dataclass(frozen=True)
class ClawRuntime:
    paths: RuntimePaths
    session_store: SessionStore
    memory_store: MemoryStore
    attachment_store: AttachmentStore
    task_store: TaskStore
    agent: AgentService
    scheduler: Scheduler


def build_runtime(paths: RuntimePaths | None = None) -> ClawRuntime:
    """Build the one runtime graph used by every external renderer."""
    resolved_paths = paths or RuntimePaths.from_environment()
    configure_logging(resolved_paths.logs_dir)
    config = load_llm_config(resolved_paths.env_file)
    session_store = SessionStore(resolved_paths.sessions_dir)
    memory_store = MemoryStore(resolved_paths.memory_dir)
    attachment_store = AttachmentStore(session_store)
    task_store = TaskStore(resolved_paths.tasks_dir)
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
        attachment_store=attachment_store,
    )
    scheduler = Scheduler(task_store, session_store, agent)
    return ClawRuntime(
        paths=resolved_paths,
        session_store=session_store,
        memory_store=memory_store,
        attachment_store=attachment_store,
        task_store=task_store,
        agent=agent,
        scheduler=scheduler,
    )


@asynccontextmanager
async def serve_runtime(runtime: ClawRuntime) -> AsyncIterator[ClawRuntime]:
    """Explicitly activate long-running services for a runtime host."""
    await runtime.scheduler.start()
    try:
        yield runtime
    finally:
        await runtime.scheduler.stop()
