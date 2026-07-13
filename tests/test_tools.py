import asyncio
import threading
import time
from datetime import datetime

import pytest

from claw.errors import ToolError
from claw.tools.builtin import MAX_READ_CHARS, build_read_only_registry
from claw.tools.registry import ToolCall, ToolDefinition, ToolRegistry


def execute(registry, call, **kwargs):
    return asyncio.run(registry.execute(call, **kwargs))


def definition(name="echo", handler=lambda args: args):
    return ToolDefinition(
        name,
        "Echo arguments.",
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        handler,
    )


def test_registry_exports_sorted_read_only_definitions_and_rejects_duplicates() -> None:
    registry = ToolRegistry()
    z_tool = definition("z_tool")
    registry.register(z_tool)
    registry.register(definition("a_tool"))

    assert [item["function"]["name"] for item in registry.definitions()] == [
        "a_tool",
        "z_tool",
    ]
    assert z_tool.safety_level == "read_only"
    with pytest.raises(ToolError, match="已注册"):
        registry.register(definition("a_tool"))


def test_registry_clone_can_be_extended_without_mutating_source() -> None:
    registry = ToolRegistry()
    original = definition("original")
    registry.register(original)

    copied = registry.clone()
    copied.register(definition("scoped"))

    assert registry.get("scoped") is None
    assert copied.get("original") is original


@pytest.mark.parametrize(
    "call,error",
    [
        (ToolCall("1", "missing", "{}"), "未知 tool"),
        (ToolCall("1", "echo", "not-json"), "不是有效 JSON"),
        (ToolCall("1", "echo", "[]"), "JSON object"),
        (ToolCall("1", "echo", "{}"), "缺少必填字段"),
        (ToolCall("1", "echo", '{"text":1}'), "必须是 string"),
        (ToolCall("1", "echo", '{"text":"x","extra":1}'), "未知字段"),
    ],
)
def test_registry_returns_argument_and_lookup_errors_as_observations(call, error) -> None:
    registry = ToolRegistry()
    registry.register(definition())

    result = execute(registry, call)

    assert result.ok is False
    assert error in result.error
    assert '"ok": false' in result.model_content()


def test_registry_validates_number_and_enum_arguments() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "scale",
            "Scale a value.",
            {
                "type": "object",
                "properties": {
                    "factor": {"type": "number"},
                    "mode": {"type": "string", "enum": ["up", "down"]},
                },
                "required": ["factor", "mode"],
                "additionalProperties": False,
            },
            lambda args: args,
        )
    )

    wrong_type = execute(
        registry,
        ToolCall("1", "scale", '{"factor":"2","mode":"up"}'),
    )
    wrong_enum = execute(
        registry,
        ToolCall("2", "scale", '{"factor":2,"mode":"sideways"}'),
    )
    valid = execute(
        registry,
        ToolCall("3", "scale", '{"factor":2.5,"mode":"down"}'),
    )

    assert not wrong_type.ok and "必须是 number" in wrong_type.error
    assert not wrong_enum.ok and "enum" in wrong_enum.error
    assert valid.ok and valid.value == {"factor": 2.5, "mode": "down"}


def test_registry_isolates_handler_and_non_json_result_failures() -> None:
    registry = ToolRegistry()
    registry.register(definition("boom", lambda _args: 1 / 0))
    registry.register(definition("opaque", lambda _args: object()))

    assert "division by zero" in execute(
        registry,
        ToolCall("1", "boom", '{"text":"x"}')
    ).error
    assert "JSON serializable" in execute(
        registry,
        ToolCall("2", "opaque", '{"text":"x"}')
    ).error


def test_builtin_tools_read_real_environment_without_mutating_it(tmp_path) -> None:
    (tmp_path / "folder").mkdir()
    (tmp_path / "README.md").write_text("hello claw", encoding="utf-8")
    registry = build_read_only_registry(tmp_path)

    listing = execute(registry, ToolCall("1", "list_dir", "{}"))
    read = execute(registry, ToolCall("2", "read_file", '{"path":"README.md"}'))
    now = execute(registry, ToolCall("3", "current_time", "{}"))

    assert listing.ok and listing.value == [
        {"name": "folder", "type": "directory"},
        {"name": "README.md", "type": "file"},
    ]
    assert read.ok and read.value["content"] == "hello claw"
    assert read.value["truncated"] is False
    assert datetime.fromisoformat(now.value).tzinfo is not None


def test_read_file_caps_context_and_reports_file_errors(tmp_path) -> None:
    (tmp_path / "large.txt").write_text("x" * (MAX_READ_CHARS + 20), encoding="utf-8")
    registry = build_read_only_registry(tmp_path)

    large = execute(registry, ToolCall("1", "read_file", '{"path":"large.txt"}'))
    missing = execute(registry, ToolCall("2", "read_file", '{"path":"missing"}'))
    directory = execute(registry, ToolCall("3", "read_file", '{"path":"."}'))

    assert large.ok and len(large.value["content"]) == MAX_READ_CHARS
    assert large.value["truncated"] is True
    assert missing.ok is False and "文件不存在" in missing.error
    assert directory.ok is False and "不是文件" in directory.error


def test_registry_supports_async_handlers_and_does_not_block_on_sync_handlers() -> None:
    release = threading.Event()
    registry = ToolRegistry(timeout_seconds=1)
    registry.register(definition("sync_wait", lambda _args: release.wait()))

    async def async_handler(args):
        await asyncio.sleep(0)
        return args["text"]

    registry.register(definition("async_echo", async_handler))

    async def scenario():
        pending = asyncio.create_task(
            registry.execute(ToolCall("1", "sync_wait", '{"text":"x"}'))
        )
        await asyncio.sleep(0.01)
        assert not pending.done()
        release.set()
        sync_result = await pending
        async_result = await registry.execute(
            ToolCall("2", "async_echo", '{"text":"hello"}')
        )
        return sync_result, async_result

    sync_result, async_result = asyncio.run(scenario())
    assert sync_result.ok
    assert async_result.ok and async_result.value == "hello"


def test_registry_returns_timeout_observation() -> None:
    registry = ToolRegistry(timeout_seconds=0.01)
    registry.register(definition("slow", lambda _args: time.sleep(0.05)))

    result = execute(registry, ToolCall("1", "slow", '{"text":"x"}'))

    assert result.ok is False
    assert result.error == "tool 执行超时（0.01 秒）。"


def test_advanced_tools_remain_disabled_even_after_approval() -> None:
    executed = []
    advanced = ToolDefinition(
        "write_file",
        "Write a file.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda _args: executed.append(True),
        safety_level="advanced",
        requires_approval=True,
    )
    registry = ToolRegistry()
    registry.register(advanced)

    denied = execute(registry, ToolCall("1", "write_file", "{}"))
    approved = execute(
        registry,
        ToolCall("2", "write_file", "{}"),
        approved=True,
    )

    assert denied.ok is False and "未获批准" in denied.error
    assert approved.ok is False and "执行日志" in approved.error
    assert executed == []


def test_tool_definition_rejects_invalid_safety_combinations() -> None:
    with pytest.raises(ToolError, match="read_only"):
        ToolDefinition(
            "read",
            "Read.",
            {"type": "object", "properties": {}},
            lambda _args: None,
            requires_approval=True,
        )
    with pytest.raises(ToolError, match="advanced"):
        ToolDefinition(
            "write",
            "Write.",
            {"type": "object", "properties": {}},
            lambda _args: None,
            safety_level="advanced",
        )


@pytest.mark.parametrize("unsupported_type", ["array", "object", "null"])
def test_tool_definition_rejects_unsupported_schema_types(unsupported_type) -> None:
    with pytest.raises(ToolError, match="type 不支持"):
        ToolDefinition(
            "unsupported",
            "Unsupported schema.",
            {
                "type": "object",
                "properties": {"value": {"type": unsupported_type}},
            },
            lambda args: args,
        )


def test_tool_definition_rejects_unsupported_schema_keywords() -> None:
    with pytest.raises(ToolError, match="不支持的关键字"):
        ToolDefinition(
            "unsupported",
            "Unsupported schema.",
            {
                "type": "object",
                "properties": {
                    "value": {"type": "string", "minLength": 1},
                },
            },
            lambda args: args,
        )
