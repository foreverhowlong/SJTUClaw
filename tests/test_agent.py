import pytest

from claw.agent import AgentService
from claw.errors import LLMError
from claw.llm import Message


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


def test_agent_sends_complete_history_on_each_turn() -> None:
    llm = FakeLLM(["你好，小明。", "你叫小明。"])
    agent = AgentService(llm)

    assert agent.send_message("你好，我叫小明。") == "你好，小明。"
    assert agent.send_message("我叫什么？") == "你叫小明。"

    assert llm.calls == [
        [{"role": "user", "content": "你好，我叫小明。"}],
        [
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


def test_agent_rolls_back_user_message_when_llm_fails() -> None:
    llm = FakeLLM(["first reply", LLMError("temporary failure")])
    agent = AgentService(llm)
    agent.send_message("first")

    with pytest.raises(LLMError, match="temporary failure"):
        agent.send_message("failed turn")

    assert agent.session.messages == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first reply"},
    ]


def test_agent_rejects_blank_input_without_calling_llm() -> None:
    llm = FakeLLM([])
    agent = AgentService(llm)

    with pytest.raises(ValueError, match="不能为空"):
        agent.send_message("   ")

    assert llm.calls == []
