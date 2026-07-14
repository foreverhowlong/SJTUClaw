"""Command-line renderer for persistent multi-session conversations."""

from __future__ import annotations

import asyncio
import inspect
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol, TextIO

from prompt_toolkit import PromptSession

from claw.agent import AgentService
from claw.approval import ApprovalCoordinator
from claw.cli_commands import (
    HELP_TEXT,
    ChatInput,
    CompactCommand,
    ExitCommand,
    HelpCommand,
    MemoryAdd,
    MemoryDelete,
    MemoryList,
    SessionDelete,
    SessionList,
    SessionNew,
    SessionRename,
    SessionSwitch,
    SkillList,
    SkillRun,
    SkillShow,
    SkillUsageCommand,
    WorkspaceClear,
    WorkspaceSet,
    WorkspaceShow,
    parse_cli_input,
)
from claw.compaction import CompactionResult
from claw.cli_renderer import (
    InputFunction,
    print_compaction,
    print_history,
    print_memories,
    print_sessions,
    print_skill_usages,
    print_skills,
    render_turn,
)
from claw.errors import ClawError, CommandParseError
from claw.events import AgentEvent
from claw.runtime import build_runtime
from claw.session_coordination import SessionCoordinator
from claw.session_lifecycle import SessionLifecycleService
from claw.skills import SkillRegistry, SkillRequest
from claw.shell import ShellManager
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore
from claw.workspace import WorkspaceService


_prompt_session: PromptSession[str] | None = None


class AgentRuntime(Protocol):
    def run_turn(
        self,
        session_id: str,
        user_input: str,
        *,
        skill_request: SkillRequest | None = None,
    ) -> AsyncIterator[AgentEvent]: ...

    async def compact_session(
        self,
        session_id: str,
        *,
        force: bool = True,
    ) -> CompactionResult: ...


async def run_repl(
    agent: AgentRuntime,
    session_store: SessionStore,
    memory_store: MemoryStore,
    workspace_service: WorkspaceService | None = None,
    approval_coordinator: ApprovalCoordinator | None = None,
    shell_manager: ShellManager | None = None,
    skill_registry: SkillRegistry | None = None,
    session_coordinator: SessionCoordinator | None = None,
    session_lifecycle: SessionLifecycleService | None = None,
    *,
    initial_session_id: str | None = None,
    input_fn: InputFunction | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Render local commands and agent turns while owning CLI session state."""
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr
    read_input = input_fn or _read_terminal_input
    current_session_id = _initial_session_id(session_store, initial_session_id)

    print("claw started. Type /exit to quit.", file=output)
    while True:
        try:
            entered = read_input("User> ")
            raw_input = await entered if inspect.isawaitable(entered) else entered
        except EOFError:
            print("bye.", file=output)
            return 0
        except KeyboardInterrupt:
            print("\n已中断。", file=error_output)
            return 130

        try:
            parsed = parse_cli_input(raw_input)
        except CommandParseError as exc:
            print(f"错误: {exc}", file=error_output)
            continue
        if parsed is None:
            continue

        if isinstance(parsed, ExitCommand):
            print("bye.", file=output)
            return 0
        if isinstance(parsed, HelpCommand):
            print(HELP_TEXT, file=output)
            continue

        try:
            if isinstance(parsed, ChatInput):
                await render_turn(
                    agent.run_turn(current_session_id, parsed.content),
                    output,
                    error_output,
                    approval_coordinator=approval_coordinator,
                    input_fn=read_input,
                )
            elif isinstance(parsed, CompactCommand):
                result = await agent.compact_session(current_session_id, force=True)
                print_compaction(result, output, error_output)
            elif isinstance(parsed, SessionNew):
                session = session_store.create()
                current_session_id = session.session_id
                print(f"Created session: {session.session_id}  {session.title}", file=output)
            elif isinstance(parsed, SessionList):
                print_sessions(session_store, current_session_id, output)
            elif isinstance(parsed, SessionSwitch):
                session = session_store.load(parsed.session_id)
                current_session_id = session.session_id
                print(f"Switched to session: {session.session_id}", file=output)
                print_history(session, output)
            elif isinstance(parsed, SessionRename):
                async with _session_mutation(session_coordinator, parsed.session_id):
                    session = session_store.rename(parsed.session_id, parsed.title)
                print(f"Renamed session: {session.session_id}  {session.title}", file=output)
            elif isinstance(parsed, SessionDelete):
                deleting_current = parsed.session_id == current_session_id
                if session_lifecycle is not None:
                    await session_lifecycle.delete(parsed.session_id)
                else:
                    async with _session_mutation(session_coordinator, parsed.session_id):
                        if shell_manager is not None:
                            await shell_manager.close(parsed.session_id)
                        session_store.delete(parsed.session_id)
                print(f"Deleted session: {parsed.session_id}", file=output)
                if deleting_current:
                    current_session_id = _initial_session_id(session_store, None)
                    current = session_store.load(current_session_id)
                    print(f"Current session: {current.session_id}  {current.title}", file=output)
            elif isinstance(parsed, MemoryAdd):
                memory = memory_store.add(parsed.content)
                print(f"Added memory: {memory.memory_id}", file=output)
            elif isinstance(parsed, MemoryList):
                print_memories(memory_store, output)
            elif isinstance(parsed, MemoryDelete):
                memory_store.delete(parsed.memory_id)
                print(f"Deleted memory: {parsed.memory_id}", file=output)
            elif isinstance(parsed, WorkspaceSet):
                if workspace_service is None:
                    raise ClawError("当前 CLI 未配置 workspace service。")
                async with _session_mutation(session_coordinator, current_session_id):
                    session = workspace_service.set(current_session_id, parsed.path)
                print(f"Workspace: {session.workspace}", file=output)
            elif isinstance(parsed, WorkspaceShow):
                session = session_store.load(current_session_id)
                print(f"Workspace: {session.workspace or '(not set)'}", file=output)
            elif isinstance(parsed, WorkspaceClear):
                if workspace_service is None:
                    raise ClawError("当前 CLI 未配置 workspace service。")
                async with _session_mutation(session_coordinator, current_session_id):
                    workspace_service.clear(current_session_id)
                print("Workspace cleared.", file=output)
            elif isinstance(parsed, SkillList):
                if skill_registry is None:
                    raise ClawError("当前 CLI 未配置 skill registry。")
                print_skills(skill_registry, output)
            elif isinstance(parsed, SkillShow):
                if skill_registry is None:
                    raise ClawError("当前 CLI 未配置 skill registry。")
                package = skill_registry.get(parsed.name)
                print(f"Skill: {package.summary.name}", file=output)
                print(f"Description: {package.summary.description}", file=output)
                print(f"Origin: {package.summary.origin}", file=output)
            elif isinstance(parsed, SkillUsageCommand):
                print_skill_usages(session_store.load(current_session_id), output)
            elif isinstance(parsed, SkillRun):
                await render_turn(
                    agent.run_turn(
                        current_session_id,
                        parsed.task,
                        skill_request=SkillRequest.explicit(parsed.name),
                    ),
                    output,
                    error_output,
                    approval_coordinator=approval_coordinator,
                    input_fn=read_input,
                )
        except KeyboardInterrupt:
            print("\n已中断。", file=error_output)
            return 130
        except ClawError as exc:
            print(f"错误: {exc}", file=error_output)


def _initial_session_id(store: SessionStore, requested: str | None) -> str:
    if requested is not None:
        return store.load(requested).session_id
    sessions = store.list()
    return sessions[0].session_id if sessions else store.create().session_id


@asynccontextmanager
async def _session_mutation(
    coordinator: SessionCoordinator | None,
    session_id: str,
):
    if coordinator is None:
        yield
        return
    async with coordinator.mutation(session_id):
        yield


async def _read_terminal_input(prompt: str) -> str:
    """Read one Unicode-aware line from the interactive terminal."""
    global _prompt_session
    if _prompt_session is None:
        _prompt_session = PromptSession()
    return await _prompt_session.prompt_async(prompt)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("用法: python -m claw.cli", file=sys.stderr)
        return 2

    try:
        runtime = build_runtime()
        return asyncio.run(
            run_repl(
                runtime.agent,
                runtime.session_store,
                runtime.memory_store,
                runtime.workspace_service,
                runtime.approval_coordinator,
                runtime.shell_manager,
                runtime.skill_registry,
                runtime.session_coordinator,
                runtime.session_lifecycle,
            )
        )
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130
    except ClawError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
