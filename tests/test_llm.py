from types import SimpleNamespace
from typing import Any

import pytest

from claw.config import LLMConfig
from claw.errors import LLMError
from claw.llm import LLMClient, _extract_assistant_content


class FakeCompletions:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeClient:
    def __init__(self, response: Any) -> None:
        self.completions = FakeCompletions(response)
        self.chat = SimpleNamespace(completions=self.completions)


def make_response(content: Any) -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
            )
        ]
    )


def test_chat_uses_openai_compatible_messages() -> None:
    fake_client = FakeClient(make_response("hello"))
    config = LLMConfig(
        api_key="key",
        base_url="https://example.com/v1",
        model="test-model",
    )

    reply = LLMClient(config, client=fake_client).chat([{"role": "user", "content": "hi"}])

    assert reply == "hello"
    assert fake_client.completions.calls == [
        {
            "model": "test-model",
            "messages": [{"role": "user", "content": "hi"}],
        }
    ]


def test_extract_assistant_content() -> None:
    content = _extract_assistant_content(make_response("hello"))

    assert content == "hello"


def test_extract_assistant_content_rejects_bad_shape() -> None:
    with pytest.raises(LLMError, match="choices"):
        _extract_assistant_content(SimpleNamespace(choices=[]))


def test_extract_assistant_content_rejects_non_string_content() -> None:
    with pytest.raises(LLMError, match="不是字符串"):
        _extract_assistant_content(make_response(None))
