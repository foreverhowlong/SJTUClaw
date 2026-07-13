import pytest

from claw.agent import AgentService
from claw.context import ContextBuilder
from claw.errors import LLMError
from claw.llm import Message
from claw.store.memory import MemoryStore


STABLE_CONTEXT = "[System Prompt]\nsystem instruction\n\n[Soul]\nstable style"


def make_agent(llm: "FakeLLM", tmp_path, **kwargs) -> AgentService:
    return AgentService(
        llm,
        context_builder=ContextBuilder("system instruction", "stable style"),
        memory_store=MemoryStore(tmp_path / "memory"),
        **kwargs,
    )


class FakeLLM:
    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = iter(responses)
        self.calls: list[list[Message]] = []

    def chat(self, messages: list[Message]) -> str:
        self.calls.append(messages)
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response


def test_agent_sends_complete_history_on_each_turn(tmp_path) -> None:
    llm = FakeLLM(["你好，小明。", "你叫小明。"])
    agent = make_agent(llm, tmp_path)

    assert agent.send_message("你好，我叫小明。") == "你好，小明。"
    assert agent.send_message("我叫什么？") == "你叫小明。"

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
    assert agent.session.messages == [
        {"role": "user", "content": "你好，我叫小明。"},
        {"role": "assistant", "content": "你好，小明。"},
        {"role": "user", "content": "我叫什么？"},
        {"role": "assistant", "content": "你叫小明。"},
    ]


def test_agent_rolls_back_user_message_when_llm_fails(tmp_path) -> None:
    llm = FakeLLM(["first reply", LLMError("temporary failure")])
    agent = make_agent(llm, tmp_path)
    agent.send_message("first")

    with pytest.raises(LLMError, match="temporary failure"):
        agent.send_message("failed turn")

    assert agent.session.messages == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first reply"},
    ]


def test_agent_rejects_blank_input_without_calling_llm(tmp_path) -> None:
    llm = FakeLLM([])
    agent = make_agent(llm, tmp_path)

    with pytest.raises(ValueError, match="不能为空"):
        agent.send_message("   ")

    assert llm.calls == []


def test_agent_restores_and_isolates_persistent_sessions(tmp_path) -> None:
    from claw.store.sessions import SessionStore

    store = SessionStore(tmp_path / "sessions")
    first_llm = FakeLLM(["first reply", "second reply"])
    agent = make_agent(first_llm, tmp_path, store=store)
    first_id = agent.session.session_id
    agent.send_message("first question")

    second = agent.create_session()
    agent.send_message("second question")
    assert first_llm.calls[-1] == [
        {"role": "system", "content": STABLE_CONTEXT},
        {"role": "user", "content": "second question"},
    ]

    restored = make_agent(FakeLLM([]), tmp_path, store=SessionStore(tmp_path / "sessions"))
    assert restored.session.session_id == second.session_id
    assert restored.switch_session(first_id).messages == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first reply"},
    ]


def test_failed_llm_turn_is_not_persisted(tmp_path) -> None:
    from claw.store.sessions import SessionStore

    store = SessionStore(tmp_path / "sessions")
    agent = make_agent(FakeLLM([LLMError("failed")]), tmp_path, store=store)
    session_id = agent.session.session_id

    with pytest.raises(LLMError, match="failed"):
        agent.send_message("do not save")

    assert store.load(session_id).messages == []


def test_failed_session_save_restores_in_memory_state(tmp_path, monkeypatch) -> None:
    from claw.errors import SessionError
    from claw.store.sessions import SessionStore

    store = SessionStore(tmp_path / "sessions")
    agent = make_agent(FakeLLM(["reply"]), tmp_path, store=store)
    session_id = agent.session.session_id

    def fail_save(_session) -> None:
        raise SessionError("disk full")

    monkeypatch.setattr(store, "save", fail_save)
    with pytest.raises(SessionError, match="disk full"):
        agent.send_message("do not retain")

    assert agent.session.messages == []
    assert SessionStore(tmp_path / "sessions").load(session_id).messages == []


def test_memory_is_visible_across_sessions_and_service_restarts(tmp_path) -> None:
    from claw.store.sessions import SessionStore

    session_store = SessionStore(tmp_path / "sessions")
    memory_store = MemoryStore(tmp_path / "memory")
    first_llm = FakeLLM(["first reply", "second reply"])
    agent = AgentService(
        first_llm,
        store=session_store,
        context_builder=ContextBuilder("rules", "style"),
        memory_store=memory_store,
    )
    memory = agent.add_memory("用户正在实现 claw 项目。")

    agent.send_message("first")
    agent.create_session()
    agent.send_message("second")

    for call in first_llm.calls:
        assert f"[{memory.memory_id}]\n用户正在实现 claw 项目。" in call[0]["content"]

    restored_llm = FakeLLM(["restored reply"])
    restored = AgentService(
        restored_llm,
        store=SessionStore(tmp_path / "sessions"),
        context_builder=ContextBuilder("rules", "style"),
        memory_store=MemoryStore(tmp_path / "memory"),
    )
    restored.send_message("after restart")
    assert "用户正在实现 claw 项目。" in restored_llm.calls[0][0]["content"]
