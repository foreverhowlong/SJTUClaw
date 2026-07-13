import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from claw.config import LLMConfig
from claw.errors import LLMError
from claw.llm import LLMClient


class FakeStream:
    def __init__(self, chunks) -> None:
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeCompletions:
    def __init__(self, chunks: Any) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.chunks, Exception):
            raise self.chunks
        return FakeStream(self.chunks)


class FakeClient:
    def __init__(self, chunks: Any) -> None:
        self.completions = FakeCompletions(chunks)
        self.chat = SimpleNamespace(completions=self.completions)


def chunk(*, content=None, tool_calls=None, finish_reason=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason=finish_reason,
            )
        ]
    )


def tool_fragment(index, *, call_id=None, name=None, arguments=None):
    function = None
    if name is not None or arguments is not None:
        function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=call_id, function=function)


def client_for(chunks):
    fake = FakeClient(chunks)
    config = LLMConfig(
        api_key="key",
        base_url="https://example.com/v1",
        model="test-model",
    )
    return LLMClient(config, client=fake), fake


async def collect_stream(client, messages, tools=()):
    return [event async for event in client.stream_chat(messages, tools)]


def test_stream_chat_yields_text_deltas_and_completed_content() -> None:
    client, fake = client_for(
        [chunk(content="hel"), chunk(content="lo", finish_reason="stop")]
    )

    events = asyncio.run(collect_stream(client, [{"role": "user", "content": "hi"}]))

    assert [event.text for event in events[:-1]] == ["hel", "lo"]
    assert events[-1].completion.content == "hello"
    assert fake.completions.calls == [
        {
            "model": "test-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
    ]


def test_stream_chat_aggregates_interleaved_tool_call_fragments() -> None:
    fragments = [
        chunk(
            tool_calls=[
                tool_fragment(0, call_id="call_1", name="read_file", arguments='{"pa'),
                tool_fragment(1, call_id="call_2", name="current_time", arguments="{}"),
            ]
        ),
        chunk(
            tool_calls=[tool_fragment(0, arguments='th":"README.md"}')],
            finish_reason="tool_calls",
        ),
    ]
    client, fake = client_for(fragments)
    tools = [{"type": "function", "function": {"name": "read_file"}}]

    events = asyncio.run(
        collect_stream(client, [{"role": "user", "content": "inspect"}], tools)
    )

    calls = events[-1].completion.tool_calls
    assert [(call.call_id, call.name, call.arguments) for call in calls] == [
        ("call_1", "read_file", '{"path":"README.md"}'),
        ("call_2", "current_time", "{}"),
    ]
    assert fake.completions.calls[0]["tools"] == tools


def test_chat_collects_internal_non_tool_text_response() -> None:
    client, _ = client_for([chunk(content="summary", finish_reason="stop")])

    assert asyncio.run(client.chat([{"role": "user", "content": "compact"}])) == "summary"


def test_stream_chat_rejects_empty_or_malformed_tool_response() -> None:
    empty, _ = client_for([chunk(finish_reason="stop")])
    with pytest.raises(LLMError, match="为空"):
        asyncio.run(collect_stream(empty, [{"role": "user", "content": "hi"}]))

    malformed, _ = client_for(
        [
            chunk(
                tool_calls=[tool_fragment(0, name="read_file", arguments="{}")],
                finish_reason="tool_calls",
            )
        ]
    )
    with pytest.raises(LLMError, match="缺少 id"):
        asyncio.run(collect_stream(malformed, [{"role": "user", "content": "hi"}]))


@pytest.mark.parametrize("finish_reason", [None, "length", "content_filter"])
def test_stream_chat_rejects_missing_or_incomplete_finish_reason(finish_reason) -> None:
    client, _ = client_for(
        [chunk(content="partial", finish_reason=finish_reason)]
    )

    with pytest.raises(LLMError, match="finish_reason|未完成|内容过滤器"):
        asyncio.run(collect_stream(client, [{"role": "user", "content": "hi"}]))


def test_stream_chat_rejects_finish_reason_protocol_mismatches() -> None:
    missing_calls, _ = client_for([chunk(finish_reason="tool_calls")])
    with pytest.raises(LLMError, match="未返回 tool call"):
        asyncio.run(
            collect_stream(missing_calls, [{"role": "user", "content": "inspect"}])
        )

    unexpected_calls, _ = client_for(
        [
            chunk(
                tool_calls=[
                    tool_fragment(0, call_id="call_1", name="read_file", arguments="{}")
                ],
                finish_reason="stop",
            )
        ]
    )
    with pytest.raises(LLMError, match="不是 tool_calls"):
        asyncio.run(
            collect_stream(unexpected_calls, [{"role": "user", "content": "inspect"}])
        )


def test_stream_chat_projects_internal_tool_metadata_at_provider_boundary() -> None:
    messages = [
        {"role": "user", "content": "inspect"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "read_file",
            "content": '{"ok":true}',
        },
    ]
    client, fake = client_for([chunk(content="done", finish_reason="stop")])

    asyncio.run(collect_stream(client, messages))

    assert "name" not in fake.completions.calls[0]["messages"][-1]
    assert messages[-1]["name"] == "read_file"
