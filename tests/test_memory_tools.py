import asyncio

from claw.store.memory import MemoryStore
from claw.tools import ToolCall, ToolRegistry
from claw.tools.memory import register_memory_tools


def execute(registry: ToolRegistry, call: ToolCall, *, approved: bool = False):
    return asyncio.run(registry.execute(call, approved=approved))


def build_registry(tmp_path):
    memories = MemoryStore(tmp_path / "memory")
    registry = ToolRegistry()
    register_memory_tools(registry, memories)
    return registry, memories


def test_save_memory_writes_without_approval_and_returns_record(tmp_path) -> None:
    registry, memories = build_registry(tmp_path)

    result = execute(
        registry,
        ToolCall("call_save", "save_memory", '{"content":"用户偏好中文回答。"}'),
    )

    assert result.ok
    assert result.value["content"] == "用户偏好中文回答。"
    assert result.value["memoryId"].startswith("mem_")
    assert memories.list()[0].content == "用户偏好中文回答。"
    assert registry.get("save_memory").safety_level == "memory_write"
    assert registry.get("save_memory").requires_approval is False


def test_save_memory_rejects_blank_content_as_a_tool_failure(tmp_path) -> None:
    registry, memories = build_registry(tmp_path)

    result = execute(
        registry,
        ToolCall("call_save", "save_memory", '{"content":"   "}'),
    )

    assert not result.ok
    assert "memory 内容不能为空" in result.error
    assert memories.list() == []


def test_delete_memory_requires_approval_and_uses_exact_id(tmp_path) -> None:
    registry, memories = build_registry(tmp_path)
    saved = memories.add("需要删除的记忆")
    call = ToolCall(
        "call_delete",
        "delete_memory",
        f'{{"memory_id":"{saved.memory_id}"}}',
    )

    denied = execute(registry, call)
    assert not denied.ok and "需要审批" in denied.error
    assert memories.list() == [saved]

    approved = execute(registry, call, approved=True)
    assert approved.ok
    assert approved.value == {"memoryId": saved.memory_id, "deleted": True}
    assert memories.list() == []


def test_delete_memory_reports_unknown_id_after_approval(tmp_path) -> None:
    registry, _ = build_registry(tmp_path)

    result = execute(
        registry,
        ToolCall(
            "call_delete",
            "delete_memory",
            '{"memory_id":"mem_0123456789ab"}',
        ),
        approved=True,
    )

    assert not result.ok
    assert "Memory 不存在" in result.error
