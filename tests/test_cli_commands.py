import pytest

from claw.cli_commands import (
    ChatInput,
    CompactCommand,
    MemoryAdd,
    SessionList,
    SessionRename,
    WorkspaceClear,
    WorkspaceSet,
    WorkspaceShow,
    parse_cli_input,
)
from claw.errors import CommandParseError


def test_parser_accepts_arbitrary_shell_whitespace_and_quotes() -> None:
    assert isinstance(parse_cli_input("/session\tlist"), SessionList)
    assert parse_cli_input('/session rename session_0123456789ab "Course Project"') == (
        SessionRename("session_0123456789ab", "Course Project")
    )
    assert parse_cli_input('/memory add "prefer Chinese answers"') == MemoryAdd(
        "prefer Chinese answers"
    )


def test_double_slash_escapes_a_literal_leading_slash() -> None:
    assert parse_cli_input("//session list") == ChatInput("/session list")


def test_compact_is_a_local_command() -> None:
    assert isinstance(parse_cli_input("/compact"), CompactCommand)


def test_workspace_commands_are_parsed_locally() -> None:
    assert parse_cli_input('/workspace set "/tmp/course project"') == WorkspaceSet(
        "/tmp/course project"
    )
    assert isinstance(parse_cli_input("/workspace show"), WorkspaceShow)
    assert isinstance(parse_cli_input("/workspace clear"), WorkspaceClear)


def test_unknown_and_malformed_commands_raise_parse_error() -> None:
    with pytest.raises(CommandParseError, match="未知"):
        parse_cli_input("/unknown")
    with pytest.raises(CommandParseError, match="格式错误"):
        parse_cli_input('/memory add "unterminated')
