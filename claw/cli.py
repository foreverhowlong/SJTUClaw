"""Command-line renderer for persistent multi-session conversations."""

from __future__ import annotations

import asyncio
import inspect
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
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
from claw.errors import ClawError, CommandParseError
from claw.events import AgentEvent
from claw.presentation.timeline import (
    ToolActivityItem,
    build_conversation_timeline,
    tool_activity,
)
from claw.runtime import build_runtime
from claw.session import Session
from claw.skills import SkillRegistry, SkillRequest
from claw.shell import ShellManager
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore
from claw.workspace import WorkspaceService


_prompt_session: PromptSession[str] | None = None
InputFunction = Callable[[str], str | Awaitable[str]]


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
                await _render_turn(
                    agent.run_turn(current_session_id, parsed.content),
                    output,
                    error_output,
                    approval_coordinator=approval_coordinator,
                    input_fn=read_input,
                )
            elif isinstance(parsed, CompactCommand):
                result = await agent.compact_session(current_session_id, force=True)
                _print_compaction(result, output, error_output)
            elif isinstance(parsed, SessionNew):
                session = session_store.create()
                current_session_id = session.session_id
                print(f"Created session: {session.session_id}  {session.title}", file=output)
            elif isinstance(parsed, SessionList):
                _print_sessions(session_store, current_session_id, output)
            elif isinstance(parsed, SessionSwitch):
                session = session_store.load(parsed.session_id)
                current_session_id = session.session_id
                print(f"Switched to session: {session.session_id}", file=output)
                _print_history(session, output)
            elif isinstance(parsed, SessionRename):
                session = session_store.rename(parsed.session_id, parsed.title)
                print(f"Renamed session: {session.session_id}  {session.title}", file=output)
            elif isinstance(parsed, SessionDelete):
                deleting_current = parsed.session_id == current_session_id
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
                _print_memories(memory_store, output)
            elif isinstance(parsed, MemoryDelete):
                memory_store.delete(parsed.memory_id)
                print(f"Deleted memory: {parsed.memory_id}", file=output)
            elif isinstance(parsed, WorkspaceSet):
                if workspace_service is None:
                    raise ClawError("当前 CLI 未配置 workspace service。")
                session = workspace_service.set(current_session_id, parsed.path)
                print(f"Workspace: {session.workspace}", file=output)
            elif isinstance(parsed, WorkspaceShow):
                session = session_store.load(current_session_id)
                print(f"Workspace: {session.workspace or '(not set)'}", file=output)
            elif isinstance(parsed, WorkspaceClear):
                if workspace_service is None:
                    raise ClawError("当前 CLI 未配置 workspace service。")
                workspace_service.clear(current_session_id)
                print("Workspace cleared.", file=output)
            elif isinstance(parsed, SkillList):
                if skill_registry is None:
                    raise ClawError("当前 CLI 未配置 skill registry。")
                _print_skills(skill_registry, output)
            elif isinstance(parsed, SkillShow):
                if skill_registry is None:
                    raise ClawError("当前 CLI 未配置 skill registry。")
                package = skill_registry.get(parsed.name)
                print(f"Skill: {package.summary.name}", file=output)
                print(f"Description: {package.summary.description}", file=output)
                print(f"Origin: {package.summary.origin}", file=output)
            elif isinstance(parsed, SkillUsageCommand):
                _print_skill_usages(session_store.load(current_session_id), output)
            elif isinstance(parsed, SkillRun):
                await _render_turn(
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


async def _read_terminal_input(prompt: str) -> str:
    """Read one Unicode-aware line from the interactive terminal."""
    global _prompt_session
    if _prompt_session is None:
        _prompt_session = PromptSession()
    return await _prompt_session.prompt_async(prompt)


def _print_sessions(store: SessionStore, current_session_id: str, output: TextIO) -> None:
    print("Sessions:", file=output)
    for item in store.list():
        marker = "*" if item.session_id == current_session_id else " "
        updated = item.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")
        print(
            f"{marker} {item.session_id}  {item.title}  "
            f"messages={item.message_count}  updated={updated}",
            file=output,
        )


def _print_history(session: Session, output: TextIO) -> None:
    if session.summary:
        print("Summary:", file=output)
        print(session.summary, file=output)
    print("History:", file=output)
    if not session.messages:
        print("(empty)", file=output)
        return
    for item in build_conversation_timeline(session.messages):
        if item["type"] == "user_message":
            print(f"User> {item['content']}", file=output)
        elif item["type"] == "working_note":
            print(f"Assistant [working]> {item['content']}", file=output)
        elif item["type"] == "assistant_message":
            print(f"Assistant> {item['content']}", file=output)
        else:
            print(_format_tool_activity(item), file=output)


def _print_memories(store: MemoryStore, output: TextIO) -> None:
    memories = store.list()
    print("Memories:", file=output)
    if not memories:
        print("(empty)", file=output)
    for memory in memories:
        print(f"{memory.memory_id}  {memory.content}", file=output)


def _print_skills(registry: SkillRegistry, output: TextIO) -> None:
    skills = registry.list()
    print("Skills:", file=output)
    if not skills:
        print("(empty)", file=output)
    for skill in skills:
        print(f"{skill.name}  [{skill.origin}]  {skill.description}", file=output)


def _print_skill_usages(session: Session, output: TextIO) -> None:
    print("Skill usage:", file=output)
    if not session.skill_usages:
        print("(empty)", file=output)
    for usage in session.skill_usages:
        used = usage.used_at.astimezone().strftime("%Y-%m-%d %H:%M")
        print(
            f"{usage.skill_name}  source={usage.source}  outcome={usage.outcome}  "
            f"used={used}",
            file=output,
        )
        print(f"  reason: {usage.reason}", file=output)
        print(f"  task: {usage.task}", file=output)
        print(f"  output: {usage.final_output}", file=output)


def _print_compaction(
    result: CompactionResult,
    output: TextIO,
    error_output: TextIO,
) -> None:
    if result.status == "failed":
        print(f"[system] compaction failed: {result.detail}", file=error_output)
        return
    if result.status == "unavailable":
        print(f"[system] compaction unavailable: {result.detail}", file=error_output)
        return
    if result.status == "skipped":
        print(f"[system] compaction skipped: {result.detail}", file=output)
        return
    print(
        f"[system] compact session {result.session_id}: "
        f"old_messages={result.old_message_count}, "
        f"recent_messages={result.recent_message_count}",
        file=output,
    )
    print("[system] summary:", file=output)
    print(result.summary, file=output)


async def _render_turn(
    events: AsyncIterator[AgentEvent],
    output: TextIO,
    error_output: TextIO,
    *,
    approval_coordinator: ApprovalCoordinator | None = None,
    input_fn: InputFunction | None = None,
) -> None:
    streaming = False
    tool_calls: dict[str, tuple[str, str]] = {}
    async for event in events:
        if event.type == "llm_delta":
            if not streaming:
                print("Assistant> ", end="", file=output, flush=True)
                streaming = True
            print(event.payload["delta"], end="", file=output, flush=True)
        elif event.type == "tool_call":
            if streaming:
                print(file=output)
                streaming = False
            call_id = str(event.payload["callId"])
            name = str(event.payload["name"])
            arguments = str(event.payload["arguments"])
            tool_calls[call_id] = (name, arguments)
            print(
                _format_tool_activity(tool_activity(call_id, name, arguments)),
                file=output,
            )
        elif event.type == "tool_result":
            call_id = str(event.payload["callId"])
            name, arguments = tool_calls.get(
                call_id,
                (str(event.payload["name"]), "{}"),
            )
            print(
                _format_tool_activity(
                    tool_activity(
                        call_id,
                        name,
                        arguments,
                        status="succeeded" if event.payload["ok"] else "failed",
                        result=event.payload.get("result"),
                        error=str(event.payload.get("error", "")),
                    )
                ),
                file=output,
            )
        elif event.type == "approval_required":
            call_id = str(event.payload["callId"])
            name, arguments = tool_calls.get(
                call_id,
                (str(event.payload["name"]), "{}"),
            )
            print(
                _format_tool_activity(
                    tool_activity(
                        call_id,
                        name,
                        arguments,
                        status="awaiting_approval",
                    )
                ),
                file=output,
            )
            approval_id = event.payload.get("approvalId")
            if (
                approval_coordinator is not None
                and input_fn is not None
                and isinstance(approval_id, str)
            ):
                print(f"Approval: {approval_id}", file=output)
                print(f"Arguments: {event.payload.get('arguments', {})}", file=output)
                print(f"Workspace: {event.payload.get('workspace') or '(not set)'}", file=output)
                entered = input_fn("Approve? [y/N] ")
                answer = await entered if inspect.isawaitable(entered) else entered
                approved = answer.strip().lower() in {"y", "yes"}
                reason = ""
                if not approved:
                    entered_reason = input_fn("Reason (optional)> ")
                    reason_value = (
                        await entered_reason
                        if inspect.isawaitable(entered_reason)
                        else entered_reason
                    )
                    reason = reason_value.strip()
                approval_coordinator.resolve(
                    approval_id,
                    approved=approved,
                    reason=reason,
                )
        elif event.type == "approval_resolved":
            # The following tool_result is the durable, user-relevant outcome.
            continue
        elif event.type == "skill_selected":
            print(
                f"Skill> {event.payload['name']} "
                f"[{event.payload['source']}] · {event.payload['reason']}",
                file=output,
            )
        elif event.type == "llm_message":
            if streaming:
                print(file=output)
                streaming = False
            else:
                print(f"Assistant> {event.payload['content']}", file=output)
        elif event.type == "compaction_done":
            _print_compaction(
                CompactionResult(**event.payload),
                output,
                error_output,
            )
        elif event.type == "warning":
            print(f"[warning] {event.payload['message']}", file=error_output)
        elif event.type == "error":
            if streaming:
                print("\n[stream interrupted]", file=output)
                streaming = False
            print(f"错误: {event.payload['message']}", file=error_output)

    if streaming:
        print(file=output)


def _format_tool_activity(item: ToolActivityItem) -> str:
    target = f" · {item['target']}" if item["target"] else ""
    if item["status"] == "succeeded":
        status = "DONE"
        note = item["detail"]
    elif item["status"] == "failed":
        status = "FAILED"
        note = item["error"]
    elif item["status"] == "awaiting_approval":
        status = "APPROVAL REQUIRED"
        note = ""
    else:
        status = "RUNNING"
        note = ""
    suffix = f" · {note}" if note else ""
    return f"Tool> {item['action']}{target} [{status}]{suffix}"


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
