"""Command-line interface for an in-memory multi-turn conversation."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Protocol, TextIO

from claw.agent import AgentService
from claw.config import load_llm_config
from claw.errors import ClawError
from claw.llm import LLMClient


EXIT_COMMAND = "/exit"


class ConversationService(Protocol):
    def send_message(self, user_input: str) -> str: ...


def run_repl(
    service: ConversationService,
    *,
    input_fn: Callable[[str], str] = input,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Read user input continuously and render replies from the core service."""
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr

    print("claw started. Type /exit to quit.", file=output)
    while True:
        try:
            user_input = input_fn("User> ").strip()
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

        try:
            reply = service.send_message(user_input)
        except KeyboardInterrupt:
            print("\n已中断。", file=error_output)
            return 130
        except ClawError as exc:
            print(f"错误: {exc}", file=error_output)
            continue

        print(f"Assistant> {reply}", file=output)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("用法: python -m claw.cli", file=sys.stderr)
        return 2

    try:
        config = load_llm_config()
        client = LLMClient(config)
        service = AgentService(client)
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130
    except ClawError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    return run_repl(service)


if __name__ == "__main__":
    raise SystemExit(main())
