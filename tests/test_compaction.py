import pytest

from claw.compaction import (
    CompactionPolicy,
    Compactor,
    load_compaction_prompt,
)
from claw.errors import LLMError, SessionError
from claw.llm import Message
from claw.store.sessions import SessionStore


class FakeLLM:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = iter(responses)
        self.calls: list[list[Message]] = []

    def chat(self, messages: list[Message]) -> str:
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


def test_policy_requires_complete_recent_turns_and_a_larger_threshold() -> None:
    with pytest.raises(ValueError, match="偶数"):
        CompactionPolicy(max_messages=10, recent_messages=3)
    with pytest.raises(ValueError, match="大于"):
        CompactionPolicy(max_messages=2, recent_messages=2)


def test_compactor_skips_below_threshold_without_calling_llm(tmp_path) -> None:
    store, session_id = populated_store(tmp_path, turns=2)
    llm = FakeLLM([])
    compactor = Compactor(
        llm,
        store,
        "compact prompt",
        CompactionPolicy(max_messages=4, recent_messages=2),
    )

    result = compactor.compact(session_id)

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
        CompactionPolicy(max_messages=4, recent_messages=2),
    )

    first = compactor.compact(session_id)
    commit_turn(store, session_id, 3)
    commit_turn(store, session_id, 4)
    second = compactor.compact(session_id)

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
        CompactionPolicy(max_messages=4, recent_messages=2),
    )

    result = compactor.compact(session_id)
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
        CompactionPolicy(max_messages=4, recent_messages=2),
    )

    def fail_commit(*_args, **_kwargs):
        raise SessionError("disk full")

    monkeypatch.setattr(store, "commit_compaction", fail_commit)
    result = compactor.compact(session_id)

    assert result.status == "failed"
    assert "保存 compaction 失败" in result.detail
    assert store.load(session_id).messages == before.messages


def test_force_compacts_below_auto_threshold_but_preserves_recent_turn(tmp_path) -> None:
    store, session_id = populated_store(tmp_path, turns=2)
    compactor = Compactor(
        FakeLLM(["manual summary"]),
        store,
        "compact prompt",
        CompactionPolicy(max_messages=10, recent_messages=2),
    )

    result = compactor.compact(session_id, force=True)

    assert result.status == "compacted"
    assert store.load(session_id).message_count == 2
    assert store.load(session_id).summary == "manual summary"


def test_packaged_compaction_prompt_describes_required_summary_content() -> None:
    prompt = load_compaction_prompt()

    assert "current task" in prompt
    assert "existing summary" in prompt
