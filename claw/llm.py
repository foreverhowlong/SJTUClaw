"""OpenAI-compatible LLM provider."""

from __future__ import annotations

from typing import Any

from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    OpenAIError,
)

from claw.config import LLMConfig
from claw.errors import LLMError

Message = dict[str, str]


class LLMClient:
    def __init__(self, config: LLMConfig, client: Any | None = None) -> None:
        self._config = config
        self._client = client or OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
        )

    def chat(self, messages: list[Message]) -> str:
        if not messages:
            raise LLMError("messages 不能为空。")

        try:
            response = self._client.chat.completions.create(
                model=self._config.model,
                messages=messages,
            )
        except APIStatusError as exc:
            raise LLMError(f"LLM HTTP 请求失败: {exc.status_code}。{_sdk_error_message(exc)}") from exc
        except APITimeoutError as exc:
            raise LLMError(f"LLM 请求超时: {exc}") from exc
        except APIConnectionError as exc:
            raise LLMError(f"LLM 网络请求失败: {exc}") from exc
        except APIResponseValidationError as exc:
            raise LLMError(f"LLM 响应格式异常: {exc}") from exc
        except OpenAIError as exc:
            raise LLMError(f"LLM SDK 调用失败: {exc}") from exc

        return _extract_assistant_content(response)


def _extract_assistant_content(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise LLMError("LLM 响应格式异常: 找不到 choices[0].message.content。") from exc

    if not isinstance(content, str):
        raise LLMError("LLM 响应格式异常: assistant content 不是字符串。")
    if not content.strip():
        raise LLMError("LLM 响应为空。")

    return content


def _sdk_error_message(exc: APIStatusError) -> str:
    response = getattr(exc, "response", None)
    text = getattr(response, "text", "")
    if isinstance(text, str) and text:
        return text[:500]
    return str(exc)
