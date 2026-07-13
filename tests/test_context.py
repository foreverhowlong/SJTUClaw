import pytest

from claw.context import ContextBuilder
from claw.errors import ConfigError
from claw.store.memory import MemoryRecord


def test_context_builder_adds_stable_context_before_message_history() -> None:
    messages = ContextBuilder("system instruction", "stable style").build(
        [{"role": "user", "content": "hello"}]
    )

    assert messages == [
        {
            "role": "system",
            "content": "[System Prompt]\nsystem instruction\n\n[Soul]\nstable style",
        },
        {"role": "user", "content": "hello"},
    ]


def test_context_builder_copies_input_messages() -> None:
    source = [{"role": "user", "content": "original"}]
    built = ContextBuilder("rules", "style").build(source)
    built[1]["content"] = "changed"

    assert source == [{"role": "user", "content": "original"}]


def test_context_builder_adds_memories_before_history() -> None:
    messages = ContextBuilder("rules", "style").build(
        [{"role": "user", "content": "current request"}],
        [MemoryRecord("mem_0123456789ab", "用户偏好中文回答。")],
    )

    assert messages[0]["content"] == (
        "[System Prompt]\nrules\n\n[Soul]\nstyle\n\n"
        "[Memory]\n[mem_0123456789ab]\n用户偏好中文回答。"
    )


def test_context_builder_loads_packaged_defaults() -> None:
    messages = ContextBuilder.from_files().build([])

    assert "You are Claw" in messages[0]["content"]
    assert "calm, practical" in messages[0]["content"]


def test_context_builder_loads_overrides_from_files(tmp_path) -> None:
    system_path = tmp_path / "system.md"
    soul_path = tmp_path / "soul.md"
    system_path.write_text("rules\n", encoding="utf-8")
    soul_path.write_text("style\n", encoding="utf-8")

    messages = ContextBuilder.from_files(system_path, soul_path).build([])

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
