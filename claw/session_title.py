"""Generate a compact title from the first successful user turn."""

from __future__ import annotations

import re
from importlib import resources
from typing import Protocol

from claw.errors import ConfigError, LLMError
from claw.messages import Message


DEFAULT_SESSION_TITLE_PROMPT_RESOURCE = "prompts/session_title.md"
MAX_SESSION_TITLE_CHARS = 30
_TITLE_PREFIX = re.compile(r"^(?:会话标题|标题|title)\s*[:：]\s*", re.IGNORECASE)


class ChatClient(Protocol):
    async def chat(self, messages: list[Message]) -> str: ...


class SessionTitleGenerator:
    def __init__(self, llm: ChatClient, prompt: str) -> None:
        normalized = prompt.strip()
        if not normalized:
            raise ValueError("session title prompt 不能为空。")
        self._llm = llm
        self._prompt = normalized

    async def generate(self, user_input: str) -> str:
        response = await self._llm.chat(
            [
                {"role": "system", "content": self._prompt},
                {"role": "user", "content": user_input.strip()},
            ]
        )
        title = normalize_session_title(response)
        if not title:
            raise LLMError("LLM 返回了空 session title。")
        return title


def normalize_session_title(value: str) -> str:
    line = next((item.strip() for item in value.splitlines() if item.strip()), "")
    line = _TITLE_PREFIX.sub("", line).strip()
    for opening, closing in (("\"", "\""), ("'", "'"), ("“", "”"), ("‘", "’")):
        if len(line) >= 2 and line.startswith(opening) and line.endswith(closing):
            line = line[len(opening) : -len(closing)].strip()
            break
    line = re.sub(r"\s+", " ", line).strip()
    return line[:MAX_SESSION_TITLE_CHARS].strip()


def load_session_title_prompt() -> str:
    try:
        content = (
            resources.files("claw")
            .joinpath(DEFAULT_SESSION_TITLE_PROMPT_RESOURCE)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, UnicodeError) as exc:
        raise ConfigError(
            "读取默认 session title prompt 失败 "
            f"{DEFAULT_SESSION_TITLE_PROMPT_RESOURCE}: {exc}"
        ) from exc
    normalized = content.strip()
    if not normalized:
        raise ConfigError(
            f"默认 session title prompt 不能为空: {DEFAULT_SESSION_TITLE_PROMPT_RESOURCE}。"
        )
    return normalized
