import asyncio

import pytest

from claw.compaction import (
    CompactionPolicy,
    Compactor,
    load_compaction_prompt,
    serialized_request_chars,
)
from claw.errors import LLMError, SessionError
from claw.llm import Message
from claw.store.sessions import SessionStore


class FakeLLM:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = iter(responses)
        self.calls: list[list[Message]] = []

    async def chat(self, messages: list[Message]) -> str:
        self.calls.append(messages)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def commit_turn(store: SessionStore, session_id: str, index: int) -> None:
    snapshot = store.load(session_id)
    store.commit_turn(
        session_id,
        expected_revision=snapshot.revision,
        messages=[
            {"role": "user", "content": f"question {index}"},
            {"role": "assistant", "content": f"answer {index}"},
        ],
    )


def populated_store(tmp_path, turns: int = 3):
    store = SessionStore(tmp_path / "sessions")
    session = store.create()
    for index in range(turns):
        commit_turn(store, session.session_id, index)
    return store, session.session_id


def compact(compactor, session_id, **kwargs):
    return asyncio.run(compactor.compact(session_id, **kwargs))


def test_policy_requires_positive_recent_budget_and_a_larger_threshold() -> None:
    with pytest.raises(ValueError, match="大于 0"):
        CompactionPolicy(max_context_chars=10, recent_context_chars=0)
    with pytest.raises(ValueError, match="大于"):
        CompactionPolicy(max_context_chars=10, recent_context_chars=10)


def test_compactor_skips_below_threshold_without_calling_llm(tmp_path) -> None:
    store, session_id = populated_store(tmp_path, turns=2)
    llm = FakeLLM([])
    compactor = Compactor(
        llm,
        store,
        "compact prompt",
        CompactionPolicy(max_context_chars=10_000, recent_context_chars=1_000),
    )

    result = compact(compactor, session_id)

    assert result.status == "skipped"
    assert llm.calls == []
    assert store.load(session_id).message_count == 4


def test_compactor_merges_existing_summary_and_only_sends_old_messages(tmp_path) -> None:
    store, session_id = populated_store(tmp_path)
    llm = FakeLLM(["first summary", "merged summary"])
    compactor = Compactor(
        llm,
        store,
        "dedicated compact prompt",
        CompactionPolicy(max_context_chars=200, recent_context_chars=120),
    )

    first = compact(compactor, session_id)
    commit_turn(store, session_id, 3)
    commit_turn(store, session_id, 4)
    second = compact(compactor, session_id)

    assert first.status == "compacted"
    assert first.old_message_count == 4
    assert first.recent_message_count == 2
    assert second.status == "compacted"
    assert store.load(session_id).summary == "merged summary"
    assert store.load(session_id).messages == [
        {"role": "user", "content": "question 4"},
        {"role": "assistant", "content": "answer 4"},
    ]
    assert llm.calls[0][0] == {
        "role": "system",
        "content": "dedicated compact prompt",
    }
    assert "question 0" in llm.calls[0][1]["content"]
    assert "question 2" not in llm.calls[0][1]["content"]
    assert "first summary" in llm.calls[1][1]["content"]
    assert "[System Prompt]" not in str(llm.calls)
    assert "[Soul]" not in str(llm.calls)
    assert "[Memory]" not in str(llm.calls)


@pytest.mark.parametrize(
    "response, expected_detail",
    [
        (LLMError("offline"), "生成 summary 失败"),
        ("   ", "空 summary"),
    ],
)
def test_invalid_summary_never_changes_session_history(
    tmp_path,
    response,
    expected_detail,
) -> None:
    store, session_id = populated_store(tmp_path)
    before = store.load(session_id)
    compactor = Compactor(
        FakeLLM([response]),
        store,
        "compact prompt",
        CompactionPolicy(max_context_chars=200, recent_context_chars=120),
    )

    result = compact(compactor, session_id)
    restored = store.load(session_id)

    assert result.status == "failed"
    assert expected_detail in result.detail
    assert restored.summary == before.summary
    assert restored.messages == before.messages
    assert restored.revision == before.revision


def test_persistence_failure_returns_warning_and_keeps_old_history(
    tmp_path,
    monkeypatch,
) -> None:
    store, session_id = populated_store(tmp_path)
    before = store.load(session_id)
    compactor = Compactor(
        FakeLLM(["summary"]),
        store,
        "compact prompt",
        CompactionPolicy(max_context_chars=200, recent_context_chars=120),
    )

    def fail_commit(*_args, **_kwargs):
        raise SessionError("disk full")

    monkeypatch.setattr(store, "commit_compaction", fail_commit)
    result = compact(compactor, session_id)

    assert result.status == "failed"
    assert "保存 compaction 失败" in result.detail
    assert store.load(session_id).messages == before.messages


def test_force_compacts_below_auto_threshold_but_preserves_recent_turn(tmp_path) -> None:
    store, session_id = populated_store(tmp_path, turns=2)
    compactor = Compactor(
        FakeLLM(["manual summary"]),
        store,
        "compact prompt",
        CompactionPolicy(max_context_chars=10_000, recent_context_chars=1_000),
    )

    result = compact(compactor, session_id, force=True)

    assert result.status == "compacted"
    assert store.load(session_id).message_count == 2
    assert store.load(session_id).summary == "manual summary"


def test_packaged_compaction_prompt_describes_required_summary_content() -> None:
    prompt = load_compaction_prompt()

    assert "current task" in prompt
    assert "existing summary" in prompt


def test_serialized_request_character_count_includes_tool_definitions() -> None:
    messages = [{"role": "user", "content": "你好"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "current_time",
                "description": "Return time.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    assert serialized_request_chars(messages, tools) > serialized_request_chars(messages)


def test_compaction_keeps_complete_tool_protocol_turn(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()
    first = store.commit_turn(
        session.session_id,
        expected_revision=0,
        messages=[
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old answer"},
        ],
    )
    tool_turn = [
        {"role": "user", "content": "read"},
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
            "content": '{"ok":false,"error":"missing"}',
        },
        {"role": "assistant", "content": "The file was missing."},
    ]
    store.commit_turn(
        session.session_id,
        expected_revision=first.revision,
        messages=tool_turn,
    )
    compactor = Compactor(
        FakeLLM(["tool summary"]),
        store,
        "compact prompt",
        CompactionPolicy(max_context_chars=10_000, recent_context_chars=100),
    )

    result = compact(compactor, session.session_id, force=True)

    assert result.compacted
    assert store.load(session.session_id).messages == tool_turn


def test_compactor_summarizes_projected_not_full_tool_results(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()
    large_value = "x" * 20_000
    tool_turn = [
        {"role": "user", "content": "read"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "large", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "large",
            "content": large_value,
        },
        {"role": "assistant", "content": "done"},
    ]
    first = store.commit_turn(
        session.session_id,
        expected_revision=0,
        messages=tool_turn,
    )
    store.commit_turn(
        session.session_id,
        expected_revision=first.revision,
        messages=[
            {"role": "user", "content": "recent"},
            {"role": "assistant", "content": "recent answer"},
        ],
    )
    llm = FakeLLM(["summary"])
    compactor = Compactor(
        llm,
        store,
        "compact prompt",
        CompactionPolicy(max_context_chars=10_000, recent_context_chars=100),
    )

    compact(compactor, session.session_id, force=True)

    rendered = llm.calls[0][1]["content"]
    assert "runtimeTruncated" in rendered
    assert large_value not in rendered
