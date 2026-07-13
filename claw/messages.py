"""Provider-neutral message shapes used across the agent domain."""

from __future__ import annotations

from typing import Literal, NotRequired, TypeAlias, TypedDict


class FunctionCallPayload(TypedDict):
    name: str
    arguments: str


class ToolCallPayload(TypedDict):
    id: str
    type: Literal["function"]
    function: FunctionCallPayload


class TextMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class AssistantToolMessage(TypedDict):
    role: Literal["assistant"]
    content: str | None
    tool_calls: list[ToolCallPayload]


class ToolResultMessage(TypedDict):
    role: Literal["tool"]
    tool_call_id: str
    content: str
    name: NotRequired[str]


Message: TypeAlias = TextMessage | AssistantToolMessage | ToolResultMessage
