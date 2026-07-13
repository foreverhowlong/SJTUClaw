"""Asynchronous OpenAI-compatible LLM provider with streamed tool calls."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    OpenAIError,
)

from claw.config import LLMConfig
from claw.errors import LLMError
from claw.tools.registry import ToolCall


Message = dict[str, Any]


@dataclass(frozen=True)
class LLMCompletion:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class LLMStreamEvent:
    type: Literal["text_delta", "completed"]
    text: str = ""
    completion: LLMCompletion | None = None


class LLMClient:
    def __init__(self, config: LLMConfig, client: Any | None = None) -> None:
        self._config = config
        self._client = client or AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
        )

    async def stream_chat(
        self,
        messages: list[Message],
        tools: Sequence[dict[str, Any]] = (),
    ) -> AsyncIterator[LLMStreamEvent]:
        if not messages:
            raise LLMError("messages 不能为空。")

        request: dict[str, Any] = {
            "model": self._config.model,
            "messages": _provider_messages(messages),
            "stream": True,
        }
        if tools:
            request["tools"] = list(tools)

        content_parts: list[str] = []
        call_parts: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        try:
            stream = await self._client.chat.completions.create(**request)
            async for chunk in stream:
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                choice = choices[0]
                reason = getattr(choice, "finish_reason", None)
                if reason is not None:
                    if not isinstance(reason, str):
                        raise LLMError("LLM stream finish_reason 格式异常。")
                    finish_reason = reason
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                content = getattr(delta, "content", None)
                if isinstance(content, str) and content:
                    content_parts.append(content)
                    yield LLMStreamEvent("text_delta", text=content)
                for fragment in getattr(delta, "tool_calls", None) or ():
                    index = getattr(fragment, "index", None)
                    if not isinstance(index, int) or index < 0:
                        raise LLMError("LLM tool call 缺少有效 index。")
                    current = call_parts.setdefault(
                        index,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    call_id = getattr(fragment, "id", None)
                    if isinstance(call_id, str):
                        current["id"] += call_id
                    function = getattr(fragment, "function", None)
                    if function is not None:
                        name = getattr(function, "name", None)
                        arguments = getattr(function, "arguments", None)
                        if isinstance(name, str):
                            current["name"] += name
                        if isinstance(arguments, str):
                            current["arguments"] += arguments
        except LLMError:
            raise
        except APIStatusError as exc:
            raise LLMError(
                f"LLM HTTP 请求失败: {exc.status_code}。{_sdk_error_message(exc)}"
            ) from exc
        except APITimeoutError as exc:
            raise LLMError(f"LLM 请求超时: {exc}") from exc
        except APIConnectionError as exc:
            raise LLMError(f"LLM 网络请求失败: {exc}") from exc
        except APIResponseValidationError as exc:
            raise LLMError(f"LLM 响应格式异常: {exc}") from exc
        except OpenAIError as exc:
            raise LLMError(f"LLM SDK 调用失败: {exc}") from exc
        except (AttributeError, IndexError, TypeError) as exc:
            raise LLMError(f"LLM 流式响应格式异常: {exc}") from exc

        _validate_finish_reason(finish_reason, has_tool_calls=bool(call_parts))
        tool_calls = tuple(
            _complete_tool_call(index, parts)
            for index, parts in sorted(call_parts.items())
        )
        content = "".join(content_parts)
        if not tool_calls and not content.strip():
            raise LLMError("LLM 响应为空。")
        yield LLMStreamEvent(
            "completed",
            completion=LLMCompletion(content=content, tool_calls=tool_calls),
        )

    async def chat(self, messages: list[Message]) -> str:
        """Return one text completion for internal calls such as compaction."""
        completion: LLMCompletion | None = None
        async for event in self.stream_chat(messages):
            if event.type == "completed":
                completion = event.completion
        if completion is None or completion.tool_calls:
            raise LLMError("LLM 响应格式异常: 需要文本回答。")
        if not completion.content.strip():
            raise LLMError("LLM 响应为空。")
        return completion.content


def _complete_tool_call(index: int, parts: dict[str, str]) -> ToolCall:
    if not parts["id"]:
        raise LLMError(f"LLM tool call {index} 缺少 id。")
    if not parts["name"]:
        raise LLMError(f"LLM tool call {index} 缺少 function name。")
    return ToolCall(parts["id"], parts["name"], parts["arguments"] or "{}")


def _provider_messages(messages: Sequence[Message]) -> list[Message]:
    """Project internal messages onto the Chat Completions request schema."""
    projected: list[Message] = []
    for message in messages:
        copied = deepcopy(message)
        if copied.get("role") == "tool":
            copied.pop("name", None)
        projected.append(copied)
    return projected


def _validate_finish_reason(
    finish_reason: str | None,
    *,
    has_tool_calls: bool,
) -> None:
    if finish_reason is None:
        raise LLMError("LLM stream 未返回 finish_reason。")
    if finish_reason == "length":
        raise LLMError("LLM 响应因长度限制而未完成。")
    if finish_reason == "content_filter":
        raise LLMError("LLM 响应被内容过滤器截断。")
    if finish_reason == "tool_calls":
        if not has_tool_calls:
            raise LLMError("LLM 声明 tool_calls，但未返回 tool call。")
        return
    if finish_reason == "stop":
        if has_tool_calls:
            raise LLMError("LLM 返回 tool call，但 finish_reason 不是 tool_calls。")
        return
    raise LLMError(f"LLM stream finish_reason 不受支持: {finish_reason}。")


def _sdk_error_message(exc: APIStatusError) -> str:
    response = getattr(exc, "response", None)
    text = getattr(response, "text", "")
    if isinstance(text, str) and text:
        return text[:500]
    return str(exc)
