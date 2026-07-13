"""Command-line renderer for persistent multi-session conversations."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Protocol, TextIO

from prompt_toolkit import PromptSession

from claw.agent import AgentService, TurnResult
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
from claw.compaction import CompactionResult, Compactor, load_compaction_prompt
from claw.config import load_llm_config
from claw.context import ContextBuilder
from claw.errors import ClawError, CommandParseError
from claw.llm import LLMClient
from claw.paths import RuntimePaths
from claw.session import Session
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore


_prompt_session: PromptSession[str] | None = None


class AgentRuntime(Protocol):
    def run_turn(self, session_id: str, user_input: str) -> TurnResult: ...

    def compact_session(
        self,
        session_id: str,
        *,
        force: bool = True,
    ) -> CompactionResult: ...


def run_repl(
    agent: AgentRuntime,
    session_store: SessionStore,
    memory_store: MemoryStore,
    *,
    initial_session_id: str | None = None,
    input_fn: Callable[[str], str] | None = None,
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
            raw_input = read_input("User> ")
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
                result = agent.run_turn(current_session_id, parsed.content)
                if result.compaction is not None:
                    _print_compaction(result.compaction, output, error_output)
                print(f"Assistant> {result.reply}", file=output)
            elif isinstance(parsed, CompactCommand):
                result = agent.compact_session(current_session_id, force=True)
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


def _read_terminal_input(prompt: str) -> str:
    """Read one Unicode-aware line from the interactive terminal."""
    global _prompt_session
    if _prompt_session is None:
        _prompt_session = PromptSession()
    return _prompt_session.prompt(prompt)


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
        label = "User" if message["role"] == "user" else "Assistant"
        print(f"{label}> {message['content']}", file=output)


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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("用法: python -m claw.cli", file=sys.stderr)
        return 2

    try:
        paths = RuntimePaths.from_environment()
        config = load_llm_config(paths.env_file)
        session_store = SessionStore(paths.sessions_dir)
        memory_store = MemoryStore(paths.memory_dir)
        llm = LLMClient(config)
        compactor = Compactor(
            llm,
            session_store,
            load_compaction_prompt(),
        )
        agent = AgentService(
            llm,
            session_store,
            ContextBuilder.from_files(
                paths.system_prompt_file,
                paths.soul_file,
            ),
            memory_store,
            compactor,
        )
        return run_repl(agent, session_store, memory_store)
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130
    except ClawError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
