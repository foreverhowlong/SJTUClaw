import pytest

from claw.context import ContextBuilder
from claw.errors import ConfigError
from claw.session import Session
from claw.store.memory import MemoryRecord


def test_context_builder_adds_system_prompt_before_session_history() -> None:
    session = Session(title="private metadata")
    session.append("user", "hello")

    messages = ContextBuilder("system instruction", "stable style").build(session)

    assert messages == [
        {
            "role": "system",
            "content": "[System Prompt]\nsystem instruction\n\n[Soul]\nstable style",
        },
        {"role": "user", "content": "hello"},
    ]
    assert session.session_id not in str(messages)
    assert session.title not in str(messages)


def test_context_builder_rejects_blank_system_prompt() -> None:
    with pytest.raises(ValueError, match="system_prompt"):
        ContextBuilder("  ", "soul")


def test_context_builder_adds_memories_before_session_history() -> None:
    session = Session()
    session.append("user", "current request")

    messages = ContextBuilder("rules", "style").build(
        session,
        [MemoryRecord("mem_0123456789ab", "用户偏好中文回答。")],
    )

    assert messages == [
        {
            "role": "system",
            "content": (
                "[System Prompt]\nrules\n\n[Soul]\nstyle\n\n"
                "[Memory]\n[mem_0123456789ab]\n用户偏好中文回答。"
            ),
        },
        {"role": "user", "content": "current request"},
    ]


def test_context_builder_loads_system_prompt_and_soul_from_files(tmp_path) -> None:
    system_path = tmp_path / "system.md"
    soul_path = tmp_path / "soul.md"
    system_path.write_text("rules\n", encoding="utf-8")
    soul_path.write_text("style\n", encoding="utf-8")

    messages = ContextBuilder.from_files(system_path, soul_path).build(Session())

    assert messages[0]["content"] == "[System Prompt]\nrules\n\n[Soul]\nstyle"


def test_context_builder_reports_missing_or_blank_prompt_files(tmp_path) -> None:
    soul_path = tmp_path / "soul.md"
    soul_path.write_text("style", encoding="utf-8")

    with pytest.raises(ConfigError, match="缺少 system prompt"):
        ContextBuilder.from_files(tmp_path / "missing.md", soul_path)

    system_path = tmp_path / "system.md"
    system_path.write_text("  \n", encoding="utf-8")
    with pytest.raises(ConfigError, match="不能为空"):
        ContextBuilder.from_files(system_path, soul_path)
