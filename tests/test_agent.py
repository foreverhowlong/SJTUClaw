import pytest

from claw.agent import AgentService
from claw.context import ContextBuilder
from claw.errors import LLMError, SessionError
from claw.llm import Message
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore


STABLE_CONTEXT = "[System Prompt]\nsystem instruction\n\n[Soul]\nstable style"


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


def make_runtime(tmp_path, responses):
    store = SessionStore(tmp_path / "sessions")
    memory_store = MemoryStore(tmp_path / "memory")
    llm = FakeLLM(responses)
    agent = AgentService(
        llm,
        store,
        ContextBuilder("system instruction", "stable style"),
        memory_store,
    )
    return agent, llm, store, memory_store


def test_agent_runs_turn_for_explicit_session_and_sends_complete_history(tmp_path) -> None:
    agent, llm, store, _ = make_runtime(tmp_path, ["你好，小明。", "你叫小明。"])
    session_id = store.create().session_id

    assert agent.run_turn(session_id, "你好，我叫小明。") == "你好，小明。"
    assert agent.run_turn(session_id, "我叫什么？") == "你叫小明。"

    assert llm.calls == [
        [
            {"role": "system", "content": STABLE_CONTEXT},
            {"role": "user", "content": "你好，我叫小明。"},
        ],
        [
            {"role": "system", "content": STABLE_CONTEXT},
            {"role": "user", "content": "你好，我叫小明。"},
            {"role": "assistant", "content": "你好，小明。"},
            {"role": "user", "content": "我叫什么？"},
        ],
    ]
    assert store.load(session_id).messages[-1] == {
        "role": "assistant",
        "content": "你叫小明。",
    }


def test_agent_has_no_current_session_or_crud_facade(tmp_path) -> None:
    agent, _, _, _ = make_runtime(tmp_path, [])

    for name in (
        "session",
        "create_session",
        "switch_session",
        "list_sessions",
        "add_memory",
        "list_memories",
    ):
        assert not hasattr(agent, name)


def test_failed_llm_turn_does_not_change_persistent_state(tmp_path) -> None:
    agent, _, store, _ = make_runtime(tmp_path, [LLMError("temporary failure")])
    session_id = store.create().session_id

    with pytest.raises(LLMError, match="temporary failure"):
        agent.run_turn(session_id, "do not save")

    assert store.load(session_id).messages == []


def test_failed_commit_requires_no_in_memory_rollback(tmp_path, monkeypatch) -> None:
    agent, _, store, _ = make_runtime(tmp_path, ["reply"])
    session_id = store.create().session_id

    def fail_commit(*_args, **_kwargs) -> None:
        raise SessionError("disk full")

    monkeypatch.setattr(store, "commit_turn", fail_commit)
    with pytest.raises(SessionError, match="disk full"):
        agent.run_turn(session_id, "do not retain")

    assert SessionStore(tmp_path / "sessions").load(session_id).messages == []


def test_agent_rejects_blank_input_without_calling_llm(tmp_path) -> None:
    agent, llm, store, _ = make_runtime(tmp_path, [])
    session_id = store.create().session_id

    with pytest.raises(ValueError, match="不能为空"):
        agent.run_turn(session_id, "   ")

    assert llm.calls == []


def test_sessions_are_isolated_and_memory_is_shared(tmp_path) -> None:
    agent, llm, store, memories = make_runtime(tmp_path, ["first reply", "second reply"])
    first_id = store.create().session_id
    second_id = store.create().session_id
    memory = memories.add("用户正在实现 claw 项目。")

    agent.run_turn(first_id, "first")
    agent.run_turn(second_id, "second")

    assert store.load(first_id).messages[0]["content"] == "first"
    assert store.load(second_id).messages[0]["content"] == "second"
    for call in llm.calls:
        assert f"[{memory.memory_id}]\n用户正在实现 claw 项目。" in call[0]["content"]
