"""Terminal rendering for local stores and AgentEvent streams."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TextIO

from claw.approval import ApprovalCoordinator
from claw.compaction import CompactionResult
from claw.events import AgentEvent
from claw.presentation.timeline import (
    ToolActivityItem,
    build_conversation_timeline,
    tool_activity,
)
from claw.session import Session
from claw.skills import SkillRegistry
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore


InputFunction = Callable[[str], str | Awaitable[str]]


def print_sessions(store: SessionStore, current_session_id: str, output: TextIO) -> None:
    print("Sessions:", file=output)
    for item in store.list():
        marker = "*" if item.session_id == current_session_id else " "
        updated = item.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")
        print(
            f"{marker} {item.session_id}  {item.title}  "
            f"messages={item.message_count}  updated={updated}",
            file=output,
        )


def print_history(session: Session, output: TextIO) -> None:
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
            print(format_tool_activity(item), file=output)


def print_memories(store: MemoryStore, output: TextIO) -> None:
    memories = store.list()
    print("Memories:", file=output)
    if not memories:
        print("(empty)", file=output)
    for memory in memories:
        print(f"{memory.memory_id}  {memory.content}", file=output)


def print_skills(registry: SkillRegistry, output: TextIO) -> None:
    skills = registry.list()
    print("Skills:", file=output)
    if not skills:
        print("(empty)", file=output)
    for skill in skills:
        print(f"{skill.name}  [{skill.origin}]  {skill.description}", file=output)


def print_skill_usages(session: Session, output: TextIO) -> None:
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


def print_compaction(
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


async def render_turn(
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
            print(format_tool_activity(tool_activity(call_id, name, arguments)), file=output)
        elif event.type == "tool_result":
            call_id = str(event.payload["callId"])
            name, arguments = tool_calls.get(
                call_id, (str(event.payload["name"]), "{}")
            )
            print(
                format_tool_activity(
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
                call_id, (str(event.payload["name"]), "{}")
            )
            print(
                format_tool_activity(
                    tool_activity(
                        call_id,
                        name,
                        arguments,
                        status="awaiting_approval",
                    )
                ),
                file=output,
            )
            await _resolve_cli_approval(
                event,
                output,
                approval_coordinator,
                input_fn,
            )
        elif event.type == "approval_resolved":
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
            print_compaction(CompactionResult(**event.payload), output, error_output)
        elif event.type == "warning":
            print(f"[warning] {event.payload['message']}", file=error_output)
        elif event.type == "error":
            if streaming:
                print("\n[stream interrupted]", file=output)
                streaming = False
            print(f"错误: {event.payload['message']}", file=error_output)
    if streaming:
        print(file=output)


async def _resolve_cli_approval(
    event: AgentEvent,
    output: TextIO,
    coordinator: ApprovalCoordinator | None,
    input_fn: InputFunction | None,
) -> None:
    approval_id = event.payload.get("approvalId")
    if coordinator is None or input_fn is None or not isinstance(approval_id, str):
        return
    print(f"Approval: {approval_id}", file=output)
    print(f"Arguments: {event.payload.get('arguments', {})}", file=output)
    print(f"Workspace: {event.payload.get('workspace') or '(not set)'}", file=output)
    entered = input_fn("Approve? [y/N] ")
    answer = await entered if inspect.isawaitable(entered) else entered
    approved = answer.strip().lower() in {"y", "yes"}
    reason = ""
    if not approved:
        entered_reason = input_fn("Reason (optional)> ")
        value = await entered_reason if inspect.isawaitable(entered_reason) else entered_reason
        reason = value.strip()
    coordinator.resolve(approval_id, approved=approved, reason=reason)


def format_tool_activity(item: ToolActivityItem) -> str:
    target = f" · {item['target']}" if item["target"] else ""
    if item["status"] == "succeeded":
        status, note = "DONE", item["detail"]
    elif item["status"] == "failed":
        status, note = "FAILED", item["error"]
    elif item["status"] == "awaiting_approval":
        status, note = "APPROVAL REQUIRED", ""
    else:
        status, note = "RUNNING", ""
    suffix = f" · {note}" if note else ""
    return f"Tool> {item['action']}{target} [{status}]{suffix}"
