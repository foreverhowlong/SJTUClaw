import asyncio
import json
import threading
from io import BytesIO

import pytest

from claw.approval import ApprovalDecision
from claw.agent import AgentService
from claw.compaction import CompactionResult
from claw.context import ContextBuilder
from claw.errors import LLMError, SessionError, ToolError
from claw.llm import LLMCompletion, LLMStreamEvent, Message
from claw.store.attachments import AttachmentStore
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore
from claw.tools import ToolCall, ToolDefinition, ToolRegistry


STABLE_CONTEXT = "[System Prompt]\nsystem instruction\n\n[Soul]\nstable style"


class FakeLLM:
    def __init__(self, responses) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[list[Message], list[dict]]] = []

    async def stream_chat(self, messages, tools=()):
        self.calls.append((messages, list(tools)))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        for event in response:
            yield event


def final(text: str):
    return [
        LLMStreamEvent("text_delta", text=text),
        LLMStreamEvent("completed", completion=LLMCompletion(text)),
    ]


def calls(*items: ToolCall, content: str = ""):
    return [
        LLMStreamEvent(
            "completed",
            completion=LLMCompletion(content, tuple(items)),
        )
    ]


async def collect(agent, session_id, user_input):
    return [event async for event in agent.run_turn(session_id, user_input)]


def make_runtime(tmp_path, responses, registry=None):
    store = SessionStore(tmp_path / "sessions")
    memories = MemoryStore(tmp_path / "memory")
    llm = FakeLLM(responses)
    agent = AgentService(
        llm,
        store,
        ContextBuilder("system instruction", "stable style"),
        memories,
        tool_registry=registry or ToolRegistry(),
    )
    return agent, llm, store, memories


def test_agent_runs_streamed_turns_for_explicit_session(tmp_path) -> None:
    agent, llm, store, _ = make_runtime(tmp_path, [final("你好，小明。"), final("你叫小明。")])
    session_id = store.create().session_id

    first = asyncio.run(collect(agent, session_id, "你好，我叫小明。"))
    second = asyncio.run(collect(agent, session_id, "我叫什么？"))

    assert [event.type for event in first] == [
        "turn_start",
        "llm_delta",
        "llm_message",
        "turn_end",
    ]
    assert second[-2].payload["content"] == "你叫小明。"
    assert llm.calls[1][0] == [
        {"role": "system", "content": STABLE_CONTEXT},
        {"role": "user", "content": "你好，我叫小明。"},
        {"role": "assistant", "content": "你好，小明。"},
        {"role": "user", "content": "我叫什么？"},
    ]


def test_agent_executes_tool_and_persists_complete_protocol_turn(tmp_path) -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "echo",
            "Echo text.",
            {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            lambda args: args["text"],
        )
    )
    call = ToolCall("call_1", "echo", '{"text":"real observation"}')
    agent, llm, store, _ = make_runtime(
        tmp_path,
        [calls(call), final("Based on real observation.")],
        registry,
    )
    session_id = store.create().session_id

    events = asyncio.run(collect(agent, session_id, "inspect"))

    assert [event.type for event in events] == [
        "turn_start",
        "tool_call",
        "tool_result",
        "llm_delta",
        "llm_message",
        "turn_end",
    ]
    assert llm.calls[1][0][-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "name": "echo",
        "content": '{"ok": true, "result": "real observation"}',
    }
    persisted = store.load(session_id).messages
    assert [message["role"] for message in persisted] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert persisted[-1]["content"] == "Based on real observation."


def test_agent_reads_attachment_through_session_scoped_tool(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    memories = MemoryStore(tmp_path / "memory")
    attachments = AttachmentStore(store)
    session = store.create()
    saved = attachments.save(
        session.session_id,
        "brief.md",
        "text/markdown",
        BytesIO(b"# Real brief"),
    )
    call = ToolCall(
        "attachment_call",
        "read_attachment",
        json.dumps({"attachment_id": saved.attachment_id}),
    )
    llm = FakeLLM([calls(call), final("The uploaded brief is real.")])
    agent = AgentService(
        llm,
        store,
        ContextBuilder("rules", "style"),
        memories,
        tool_registry=ToolRegistry(),
        attachment_store=attachments,
    )

    events = asyncio.run(collect(agent, session.session_id, "read the upload"))

    definition_names = [item["function"]["name"] for item in llm.calls[0][1]]
    assert definition_names == ["read_attachment"]
    result = next(event for event in events if event.type == "tool_result")
    assert result.payload["ok"] is True
    assert result.payload["result"]["content"] == "# Real brief"
    assert "# Real brief" in llm.calls[1][0][-1]["content"]
    assert store.load(session.session_id).messages[2]["name"] == "read_attachment"


def test_attachment_tool_cannot_read_another_session(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    memories = MemoryStore(tmp_path / "memory")
    attachments = AttachmentStore(store)
    first = store.create()
    second = store.create()
    foreign = attachments.save(
        second.session_id,
        "secret.txt",
        "text/plain",
        BytesIO(b"secret"),
    )
    call = ToolCall(
        "foreign_call",
        "read_attachment",
        json.dumps({"attachment_id": foreign.attachment_id}),
    )
    llm = FakeLLM([calls(call), final("I cannot read it.")])
    agent = AgentService(
        llm,
        store,
        ContextBuilder("rules", "style"),
        memories,
        tool_registry=ToolRegistry(),
        attachment_store=attachments,
    )

    events = asyncio.run(collect(agent, first.session_id, "read foreign"))

    result = next(event for event in events if event.type == "tool_result")
    assert result.payload["ok"] is False
    assert "当前 session 不存在附件" in result.payload["error"]


def test_tool_validation_failure_is_observation_not_turn_failure(tmp_path) -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "need_path",
            "Need a path.",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            lambda args: args["path"],
        )
    )
    agent, llm, store, _ = make_runtime(
        tmp_path,
        [calls(ToolCall("bad", "need_path", "{}")), final("The path was missing.")],
        registry,
    )
    session_id = store.create().session_id

    events = asyncio.run(collect(agent, session_id, "inspect"))

    result = next(event for event in events if event.type == "tool_result")
    assert result.payload["ok"] is False
    assert "缺少必填字段" in result.payload["error"]
    assert "缺少必填字段" in llm.calls[1][0][-1]["content"]
    assert store.load(session_id).messages[-1]["content"] == "The path was missing."


def test_more_than_five_tool_calls_execute_none_and_return_errors(tmp_path) -> None:
    executed = []
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "record",
            "Record a value.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            lambda _args: executed.append(True),
        )
    )
    batch = tuple(ToolCall(f"call_{i}", "record", "{}") for i in range(6))
    agent, _, store, _ = make_runtime(
        tmp_path,
        [calls(*batch), final("Retried without the oversized batch.")],
        registry,
    )
    session_id = store.create().session_id

    events = asyncio.run(collect(agent, session_id, "too many"))

    assert executed == []
    results = [event for event in events if event.type == "tool_result"]
    assert len(results) == 6
    assert all("最多请求 5" in event.payload["error"] for event in results)


def test_failed_llm_stream_emits_error_without_persisting_partial_turn(tmp_path) -> None:
    agent, _, store, _ = make_runtime(tmp_path, [LLMError("stream failed")])
    session_id = store.create().session_id

    events = asyncio.run(collect(agent, session_id, "do not save"))

    assert [event.type for event in events] == ["turn_start", "error", "turn_end"]
    assert events[1].payload == {
        "code": "llm_error",
        "message": "LLM 调用失败，请稍后重试。",
    }
    assert store.load(session_id).messages == []


def test_agent_emits_compaction_events_before_normal_stream(tmp_path) -> None:
    class StubCompactor:
        def __init__(self) -> None:
            self.request_chars = 0
            self.should_calls = 0
            self.compact_calls = 0

        def should_compact(self, request_chars):
            self.should_calls += 1
            self.request_chars = request_chars
            return True

        async def compact(self, session_id, **_kwargs):
            self.compact_calls += 1
            return CompactionResult(session_id, "skipped", 0, 0, detail="one turn")

    store = SessionStore(tmp_path / "sessions")
    memories = MemoryStore(tmp_path / "memory")
    session_id = store.create().session_id
    llm = FakeLLM([final("reply")])
    compactor = StubCompactor()
    agent = AgentService(
        llm,
        store,
        ContextBuilder("rules", "style"),
        memories,
        compactor=compactor,
        tool_registry=ToolRegistry(),
    )

    events = asyncio.run(collect(agent, session_id, "trigger"))

    assert [event.type for event in events[:4]] == [
        "turn_start",
        "compaction_started",
        "compaction_done",
        "warning",
    ]
    assert compactor.request_chars > len("trigger")
    assert compactor.should_calls == 2
    assert compactor.compact_calls == 1


def test_tool_iterations_never_trigger_mid_turn_compaction(tmp_path) -> None:
    class NeverCompactor:
        def __init__(self) -> None:
            self.should_calls = 0

        def should_compact(self, _request_chars):
            self.should_calls += 1
            return False

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "echo",
            "Echo.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            lambda _args: "x",
        )
    )
    store = SessionStore(tmp_path / "sessions")
    memories = MemoryStore(tmp_path / "memory")
    session_id = store.create().session_id
    compactor = NeverCompactor()
    agent = AgentService(
        FakeLLM(
            [
                calls(ToolCall("1", "echo", "{}")),
                calls(ToolCall("2", "echo", "{}")),
                final("done"),
            ]
        ),
        store,
        ContextBuilder("rules", "style"),
        memories,
        compactor=compactor,
        tool_registry=registry,
    )

    asyncio.run(collect(agent, session_id, "run"))

    assert compactor.should_calls == 1


def test_tool_call_event_is_emitted_before_sync_handler_finishes(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()

    def blocking(_args):
        started.set()
        release.wait()
        return "done"

    registry = ToolRegistry(timeout_seconds=1)
    registry.register(
        ToolDefinition(
            "blocking",
            "Block until released.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            blocking,
        )
    )
    agent, _, store, _ = make_runtime(
        tmp_path,
        [calls(ToolCall("1", "blocking", "{}")), final("finished")],
        registry,
    )
    session_id = store.create().session_id

    async def scenario():
        events = agent.run_turn(session_id, "run")
        assert (await anext(events)).type == "turn_start"
        call_event = await anext(events)
        assert call_event.type == "tool_call"
        pending = asyncio.create_task(anext(events))
        await asyncio.sleep(0.01)
        assert started.is_set() and not pending.done()
        release.set()
        result_event = await pending
        remaining = [event async for event in events]
        return result_event, remaining

    result_event, remaining = asyncio.run(scenario())
    assert result_event.type == "tool_result"
    assert remaining[-1].type == "turn_end"


def test_advanced_tool_uses_deny_all_approval_policy(tmp_path) -> None:
    executed = []
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "write_file",
            "Write.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            lambda _args: executed.append(True),
            safety_level="advanced",
            requires_approval=True,
        )
    )
    agent, llm, store, _ = make_runtime(
        tmp_path,
        [calls(ToolCall("1", "write_file", "{}")), final("denied")],
        registry,
    )
    session_id = store.create().session_id

    events = asyncio.run(collect(agent, session_id, "write"))

    assert executed == []
    assert [event.type for event in events[:5]] == [
        "turn_start",
        "tool_call",
        "approval_required",
        "approval_resolved",
        "tool_result",
    ]
    assert events[3].payload["approved"] is False
    assert "未配置" in llm.calls[1][0][-1]["content"]


def test_approved_policy_cannot_bypass_missing_execution_journal(tmp_path) -> None:
    class ApproveAll:
        async def authorize(self, session_id, tool, call):
            del session_id, tool, call
            return ApprovalDecision(True, "approved for test")

    executed = []
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "advanced",
            "Advanced.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            lambda _args: executed.append(True),
            safety_level="advanced",
            requires_approval=True,
        )
    )
    store = SessionStore(tmp_path / "sessions")
    memories = MemoryStore(tmp_path / "memory")
    session_id = store.create().session_id
    agent = AgentService(
        FakeLLM([calls(ToolCall("1", "advanced", "{}")), final("done")]),
        store,
        ContextBuilder("rules", "style"),
        memories,
        tool_registry=registry,
        approval_policy=ApproveAll(),
    )

    events = asyncio.run(collect(agent, session_id, "run"))

    result = next(event for event in events if event.type == "tool_result")
    assert result.payload["ok"] is False
    assert "执行日志" in result.payload["error"]
    assert executed == []


def test_large_tool_result_is_projected_but_persisted_in_full(tmp_path) -> None:
    value = "x" * 20_000
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "large",
            "Return large text.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            lambda _args: value,
        )
    )
    agent, llm, store, _ = make_runtime(
        tmp_path,
        [calls(ToolCall("1", "large", "{}")), final("done")],
        registry,
    )
    session_id = store.create().session_id

    events = asyncio.run(collect(agent, session_id, "large"))

    result_event = next(event for event in events if event.type == "tool_result")
    assert result_event.payload["truncated"] is True
    projected = json.loads(llm.calls[1][0][-1]["content"])
    assert projected["runtimeTruncated"] is True
    persisted = store.load(session_id).messages[2]["content"]
    assert value in persisted


@pytest.mark.parametrize("failure", [ValueError("bad"), RuntimeError("boom")])
def test_unexpected_runtime_errors_are_sanitized_and_end_turn(
    tmp_path,
    failure,
    caplog,
) -> None:
    agent, _, store, _ = make_runtime(tmp_path, [failure])
    session_id = store.create().session_id

    events = asyncio.run(collect(agent, session_id, "fail"))

    assert [event.type for event in events] == ["turn_start", "error", "turn_end"]
    assert events[1].payload == {
        "code": "internal_error",
        "message": "Agent 运行时发生内部错误。",
    }
    assert events[-1].payload["status"] == "failed"
    assert str(failure) in caplog.text


def test_cancelled_error_propagates_without_error_event(tmp_path) -> None:
    agent, _, store, _ = make_runtime(tmp_path, [asyncio.CancelledError()])
    session_id = store.create().session_id

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(collect(agent, session_id, "cancel"))


@pytest.mark.parametrize(
    "failure,code,message",
    [
        (SessionError("private session path"), "session_error", "会话状态处理失败。"),
        (ToolError("private tool detail"), "tool_error", "工具运行时发生错误。"),
    ],
)
def test_known_runtime_errors_use_stable_public_codes(
    tmp_path,
    monkeypatch,
    failure,
    code,
    message,
) -> None:
    agent, _, store, _ = make_runtime(tmp_path, [])
    session_id = store.create().session_id
    if isinstance(failure, SessionError):
        monkeypatch.setattr(store, "load", lambda _session_id: (_ for _ in ()).throw(failure))
    else:
        monkeypatch.setattr(
            agent._tools,
            "clone",
            lambda: (_ for _ in ()).throw(failure),
        )

    events = asyncio.run(collect(agent, session_id, "fail"))

    assert events[1].payload == {"code": code, "message": message}
    assert "private" not in events[1].payload["message"]
    assert events[-1].type == "turn_end"


def test_sessions_are_isolated_and_memory_is_shared(tmp_path) -> None:
    agent, llm, store, memories = make_runtime(tmp_path, [final("one"), final("two")])
    first_id = store.create().session_id
    second_id = store.create().session_id
    memory = memories.add("用户正在实现 claw 项目。")

    asyncio.run(collect(agent, first_id, "first"))
    asyncio.run(collect(agent, second_id, "second"))

    assert store.load(first_id).messages[0]["content"] == "first"
    assert store.load(second_id).messages[0]["content"] == "second"
    for messages, _ in llm.calls:
        assert f"[{memory.memory_id}]\n用户正在实现 claw 项目。" in messages[0]["content"]
