"""Command-line renderer for persistent multi-session conversations."""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol, TextIO

from prompt_toolkit import PromptSession

from claw.agent import AgentService
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
    parse_cli_input,
)
from claw.compaction import CompactionResult
from claw.errors import ClawError, CommandParseError
from claw.events import AgentEvent
from claw.runtime import build_runtime
from claw.session import Session
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore


_prompt_session: PromptSession[str] | None = None
InputFunction = Callable[[str], str | Awaitable[str]]


class AgentRuntime(Protocol):
    def run_turn(self, session_id: str, user_input: str) -> AsyncIterator[AgentEvent]: ...

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
    for message in session.messages:
        role = message["role"]
        if role == "user":
            print(f"User> {message['content']}", file=output)
        elif role == "tool":
            print(
                f"[tool_result] {message.get('name', '')} {message['content']}",
                file=output,
            )
        elif message.get("tool_calls"):
            for call in message["tool_calls"]:
                function = call["function"]
                print(
                    f"[tool_call] {function['name']} {function['arguments']}",
                    file=output,
                )
        else:
            print(f"Assistant> {message['content']}", file=output)


def _print_memories(store: MemoryStore, output: TextIO) -> None:
    memories = store.list()
    print("Memories:", file=output)
    if not memories:
        print("(empty)", file=output)
    for memory in memories:
        print(f"{memory.memory_id}  {memory.content}", file=output)


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
) -> None:
    streaming = False
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
            print(
                f"[tool_call] {event.payload['name']} "
                f"{event.payload['arguments']}",
                file=output,
            )
        elif event.type == "tool_result":
            if event.payload.get("truncated"):
                detail = {
                    "truncated": True,
                    "originalCharacters": event.payload["originalCharacters"],
                    "preview": event.payload["preview"],
                }
            else:
                detail = (
                    event.payload["result"]
                    if event.payload["ok"]
                    else {"error": event.payload["error"]}
                )
            print(
                f"[tool_result] {event.payload['name']} "
                f"{json.dumps(detail, ensure_ascii=False)}",
                file=output,
            )
        elif event.type == "approval_required":
            print(
                f"[approval_required] {event.payload['name']}",
                file=output,
            )
        elif event.type == "approval_resolved":
            status = "approved" if event.payload["approved"] else "denied"
            print(
                f"[approval_resolved] {event.payload['name']} {status}: "
                f"{event.payload['reason']}",
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
