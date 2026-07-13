"""Command-line renderer for persistent multi-session conversations."""

from __future__ import annotations

import shlex
import sys
from collections.abc import Callable
from typing import Protocol, TextIO

from prompt_toolkit import PromptSession

from claw.agent import AgentService
from claw.config import load_llm_config
from claw.context import ContextBuilder
from claw.errors import ClawError
from claw.llm import LLMClient
from claw.session import Session
from claw.store.memory import MemoryRecord, MemoryStore
from claw.store.sessions import SessionStore, SessionSummary


EXIT_COMMAND = "/exit"
_prompt_session: PromptSession[str] | None = None


class ConversationService(Protocol):
    @property
    def session(self) -> Session: ...

    def send_message(self, user_input: str) -> str: ...

    def create_session(self, title: str = "新会话") -> Session: ...

    def list_sessions(self) -> list[SessionSummary]: ...

    def switch_session(self, session_id: str) -> Session: ...

    def rename_session(self, session_id: str, title: str) -> Session: ...

    def delete_session(self, session_id: str) -> Session: ...

    def add_memory(self, content: str) -> MemoryRecord: ...

    def list_memories(self) -> list[MemoryRecord]: ...

    def delete_memory(self, memory_id: str) -> None: ...


def run_repl(
    service: ConversationService,
    *,
    input_fn: Callable[[str], str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Read user input continuously and render replies from the core service."""
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr
    read_input = input_fn or _read_terminal_input

    print("claw started. Type /exit to quit.", file=output)
    while True:
        try:
            user_input = read_input("User> ").strip()
        except EOFError:
            print("bye.", file=output)
            return 0
        except KeyboardInterrupt:
            print("\n已中断。", file=error_output)
            return 130

        if user_input == EXIT_COMMAND:
            print("bye.", file=output)
            return 0
        if not user_input:
            continue

        if user_input == "/session" or user_input.startswith("/session "):
            try:
                _handle_session_command(service, user_input, output)
            except ClawError as exc:
                print(f"错误: {exc}", file=error_output)
            continue

        if user_input == "/memory" or user_input.startswith("/memory "):
            try:
                _handle_memory_command(service, user_input, output)
            except ClawError as exc:
                print(f"错误: {exc}", file=error_output)
            continue

        try:
            reply = service.send_message(user_input)
        except KeyboardInterrupt:
            print("\n已中断。", file=error_output)
            return 130
        except ClawError as exc:
            print(f"错误: {exc}", file=error_output)
            continue

        print(f"Assistant> {reply}", file=output)


def _read_terminal_input(prompt: str) -> str:
    """Read one Unicode-aware line from the interactive terminal."""
    global _prompt_session
    if _prompt_session is None:
        _prompt_session = PromptSession()
    return _prompt_session.prompt(prompt)


def _handle_session_command(
    service: ConversationService,
    command: str,
    output: TextIO,
) -> None:
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        print(f"Session 命令格式错误: {exc}", file=output)
        return

    if len(parts) < 2:
        _print_session_usage(output)
        return

    action = parts[1]
    if action == "new" and len(parts) == 2:
        session = service.create_session()
        print(f"Created session: {session.session_id}  {session.title}", file=output)
        return

    if action == "list" and len(parts) == 2:
        sessions = service.list_sessions()
        print("Sessions:", file=output)
        for item in sessions:
            marker = "*" if item.session_id == service.session.session_id else " "
            updated = item.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")
            print(
                f"{marker} {item.session_id}  {item.title}  "
                f"messages={item.message_count}  updated={updated}",
                file=output,
            )
        return

    if action == "switch" and len(parts) == 3:
        session = service.switch_session(parts[2])
        print(f"Switched to session: {session.session_id}", file=output)
        _print_history(session, output)
        return

    if action == "rename" and len(parts) >= 4:
        title = " ".join(parts[3:])
        session = service.rename_session(parts[2], title)
        print(f"Renamed session: {session.session_id}  {session.title}", file=output)
        return

    if action == "delete" and len(parts) == 3:
        deleted_id = parts[2]
        deleting_current = service.session.session_id == deleted_id
        current = service.delete_session(deleted_id)
        print(f"Deleted session: {deleted_id}", file=output)
        if deleting_current:
            print(f"Current session: {current.session_id}  {current.title}", file=output)
        return

    _print_session_usage(output)


def _print_history(session: Session, output: TextIO) -> None:
    print("History:", file=output)
    if not session.messages:
        print("(empty)", file=output)
        return
    for message in session.messages:
        label = "User" if message["role"] == "user" else "Assistant"
        print(f"{label}> {message['content']}", file=output)


def _print_session_usage(output: TextIO) -> None:
    print(
        "用法: /session new | list | switch <sessionId> | "
        "rename <sessionId> <title> | delete <sessionId>",
        file=output,
    )


def _handle_memory_command(
    service: ConversationService,
    command: str,
    output: TextIO,
) -> None:
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        print(f"Memory 命令格式错误: {exc}", file=output)
        return

    if len(parts) < 2:
        _print_memory_usage(output)
        return

    action = parts[1]
    if action == "add" and len(parts) >= 3:
        memory = service.add_memory(" ".join(parts[2:]))
        print(f"Added memory: {memory.memory_id}", file=output)
        return

    if action == "list" and len(parts) == 2:
        memories = service.list_memories()
        print("Memories:", file=output)
        if not memories:
            print("(empty)", file=output)
        for memory in memories:
            print(f"{memory.memory_id}  {memory.content}", file=output)
        return

    if action == "delete" and len(parts) == 3:
        service.delete_memory(parts[2])
        print(f"Deleted memory: {parts[2]}", file=output)
        return

    _print_memory_usage(output)


def _print_memory_usage(output: TextIO) -> None:
    print(
        "用法: /memory add <content> | list | delete <memoryId>",
        file=output,
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("用法: python -m claw.cli", file=sys.stderr)
        return 2

    try:
        config = load_llm_config()
        client = LLMClient(config)
        service = AgentService(
            client,
            store=SessionStore(),
            context_builder=ContextBuilder.from_files(),
            memory_store=MemoryStore(),
        )
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130
    except ClawError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    return run_repl(service)


if __name__ == "__main__":
    raise SystemExit(main())
