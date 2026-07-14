import asyncio

import pytest

from claw.errors import LLMError
from claw.session_title import (
    MAX_SESSION_TITLE_CHARS,
    SessionTitleGenerator,
    load_session_title_prompt,
    normalize_session_title,
)


class FakeChat:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def chat(self, messages):
        self.calls.append(messages)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_generator_uses_only_dedicated_prompt_and_first_user_message() -> None:
    llm = FakeChat("标题：Session 持久化分析")
    generator = SessionTitleGenerator(llm, "Generate one title.")

    title = asyncio.run(generator.generate("分析 session 持久化设计"))

    assert title == "Session 持久化分析"
    assert llm.calls == [[
        {"role": "system", "content": "Generate one title."},
        {"role": "user", "content": "分析 session 持久化设计"},
    ]]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"课程报告写作"\n额外解释', "课程报告写作"),
        ("Title: Memory   tool design", "Memory tool design"),
        ("“首轮标题生成”", "首轮标题生成"),
        ("\n\n", ""),
    ],
)
def test_title_normalization(raw, expected) -> None:
    assert normalize_session_title(raw) == expected


def test_title_normalization_enforces_hard_character_limit() -> None:
    assert normalize_session_title("x" * 80) == "x" * MAX_SESSION_TITLE_CHARS


def test_generator_rejects_empty_normalized_title() -> None:
    generator = SessionTitleGenerator(FakeChat("Title:"), "prompt")

    with pytest.raises(LLMError, match="空 session title"):
        asyncio.run(generator.generate("hello"))


def test_packaged_session_title_prompt_loads() -> None:
    assert "Return only the title" in load_session_title_prompt()
