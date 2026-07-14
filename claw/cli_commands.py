"""Pure parsing for local slash commands."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import TypeAlias

from claw.errors import CommandParseError


@dataclass(frozen=True)
class ChatInput:
    content: str


@dataclass(frozen=True)
class ExitCommand:
    pass


@dataclass(frozen=True)
class HelpCommand:
    pass


@dataclass(frozen=True)
class CompactCommand:
    pass


@dataclass(frozen=True)
class SessionNew:
    pass


@dataclass(frozen=True)
class SessionList:
    pass


@dataclass(frozen=True)
class SessionSwitch:
    session_id: str


@dataclass(frozen=True)
class SessionRename:
    session_id: str
    title: str


@dataclass(frozen=True)
class SessionDelete:
    session_id: str


@dataclass(frozen=True)
class MemoryAdd:
    content: str


@dataclass(frozen=True)
class MemoryList:
    pass


@dataclass(frozen=True)
class MemoryDelete:
    memory_id: str


@dataclass(frozen=True)
class WorkspaceSet:
    path: str


@dataclass(frozen=True)
class WorkspaceShow:
    pass


@dataclass(frozen=True)
class WorkspaceClear:
    pass


@dataclass(frozen=True)
class SkillList:
    pass


@dataclass(frozen=True)
class SkillShow:
    name: str


@dataclass(frozen=True)
class SkillUsageCommand:
    pass


@dataclass(frozen=True)
class SkillRun:
    name: str
    task: str


CliInput: TypeAlias = (
    ChatInput
    | ExitCommand
    | HelpCommand
    | CompactCommand
    | SessionNew
    | SessionList
    | SessionSwitch
    | SessionRename
    | SessionDelete
    | MemoryAdd
    | MemoryList
    | MemoryDelete
    | WorkspaceSet
    | WorkspaceShow
    | WorkspaceClear
    | SkillList
    | SkillShow
    | SkillUsageCommand
    | SkillRun
)


HELP_TEXT = """Commands:
  /exit
  /help
  /compact
  /session new
  /session list
  /session switch <sessionId>
  /session rename <sessionId> <title>
  /session delete <sessionId>
  /memory add <content>
  /memory list
  /memory delete <memoryId>
  /workspace set <path>
  /workspace show
  /workspace clear
  /skill list
  /skill show <skill-name>
  /skill usage
  /skill <skill-name> <task>
Prefix a message with // to send a literal leading slash."""


def parse_cli_input(raw: str) -> CliInput | None:
    """Parse one REPL line without performing I/O or runtime operations."""
    text = raw.strip()
    if not text:
        return None
    if text.startswith("//"):
        return ChatInput(text[1:])
    if not text.startswith("/"):
        return ChatInput(text)

    try:
        parts = shlex.split(text)
    except ValueError as exc:
        raise CommandParseError(f"命令格式错误: {exc}") from exc

    match parts:
        case ["/exit"]:
            return ExitCommand()
        case ["/help"]:
            return HelpCommand()
        case ["/compact"]:
            return CompactCommand()
        case ["/session", "new"]:
            return SessionNew()
        case ["/session", "list"]:
            return SessionList()
        case ["/session", "switch", session_id]:
            return SessionSwitch(session_id)
        case ["/session", "rename", session_id, *title] if title:
            return SessionRename(session_id, " ".join(title))
        case ["/session", "delete", session_id]:
            return SessionDelete(session_id)
        case ["/memory", "add", *content] if content:
            return MemoryAdd(" ".join(content))
        case ["/memory", "list"]:
            return MemoryList()
        case ["/memory", "delete", memory_id]:
            return MemoryDelete(memory_id)
        case ["/workspace", "set", path]:
            return WorkspaceSet(path)
        case ["/workspace", "show"]:
            return WorkspaceShow()
        case ["/workspace", "clear"]:
            return WorkspaceClear()
        case ["/skill", "list"]:
            return SkillList()
        case ["/skill", "show", name]:
            return SkillShow(name)
        case ["/skill", "usage"]:
            return SkillUsageCommand()
        case ["/skill", name, *task] if task:
            return SkillRun(name, " ".join(task))
        case _:
            raise CommandParseError(
                f"未知或格式错误的命令: {text}。输入 /help 查看用法。"
            )
