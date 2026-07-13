"""Tool definitions, argument validation, and dispatch."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from claw.errors import ToolError


ToolHandler = Callable[[dict[str, Any]], Any]
SafetyLevel = Literal["read_only", "advanced"]
DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
SUPPORTED_ARGUMENT_TYPES = frozenset({"string", "integer", "number", "boolean"})
TOP_LEVEL_SCHEMA_KEYS = frozenset(
    {"type", "properties", "required", "additionalProperties", "description"}
)
PROPERTY_SCHEMA_KEYS = frozenset({"type", "description", "enum"})


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    ok: bool
    value: Any = None
    error: str = ""

    def model_content(self) -> str:
        payload = (
            {"ok": True, "result": self.value}
            if self.ok
            else {"ok": False, "error": self.error}
        )
        return json.dumps(payload, ensure_ascii=False)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    safety_level: SafetyLevel = "read_only"
    requires_approval: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ToolError("tool name 不能为空。")
        if not self.description.strip():
            raise ToolError(f"tool {self.name} description 不能为空。")
        if self.safety_level == "read_only" and self.requires_approval:
            raise ToolError(f"read_only tool 不能要求 approval: {self.name}。")
        if self.safety_level == "advanced" and not self.requires_approval:
            raise ToolError(f"advanced tool 必须要求 approval: {self.name}。")
        if self.safety_level not in {"read_only", "advanced"}:
            raise ToolError(f"tool safety level 无效: {self.name}。")
        _validate_input_schema(self.name, self.input_schema)

    def api_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


class ToolRegistry:
    """Keep model-visible definitions separate from runtime handlers."""

    def __init__(self, timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS) -> None:
        if timeout_seconds <= 0:
            raise ValueError("tool timeout_seconds 必须大于 0。")
        self._tools: dict[str, ToolDefinition] = {}
        self._timeout_seconds = timeout_seconds

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ToolError(f"tool 已注册: {tool.name}。")
        self._tools[tool.name] = tool

    def definitions(self) -> list[dict[str, Any]]:
        return [self._tools[name].api_definition() for name in sorted(self._tools)]

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    async def execute(self, call: ToolCall, *, approved: bool = False) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(
                call.call_id,
                call.name,
                False,
                error=f"未知 tool: {call.name}。",
            )
        try:
            arguments = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as exc:
            return ToolResult(
                call.call_id,
                call.name,
                False,
                error=f"tool arguments 不是有效 JSON: {exc.msg}。",
            )
        error = _validate_object(arguments, tool.input_schema)
        if error:
            return ToolResult(call.call_id, call.name, False, error=error)
        if tool.requires_approval and not approved:
            return ToolResult(
                call.call_id,
                call.name,
                False,
                error="该工具需要审批，当前调用未获批准。",
            )
        if tool.safety_level == "advanced":
            return ToolResult(
                call.call_id,
                call.name,
                False,
                error=(
                    "advanced tool 尚未启用：运行时缺少持久化执行日志和幂等保护。"
                ),
            )
        try:
            value = await asyncio.wait_for(
                _invoke_handler(tool.handler, arguments),
                timeout=self._timeout_seconds,
            )
            json.dumps(value, ensure_ascii=False)
        except TimeoutError:
            # Cancelling to_thread stops our wait, not the underlying worker thread.
            logger.warning(
                "tool execution timed out; sync worker may still be running: %s",
                tool.name,
            )
            seconds = f"{self._timeout_seconds:g}"
            return ToolResult(
                call.call_id,
                call.name,
                False,
                error=f"tool 执行超时（{seconds} 秒）。",
            )
        except Exception as exc:  # Handlers are an isolation boundary.
            logger.exception("tool handler failed: %s", tool.name)
            return ToolResult(
                call.call_id,
                call.name,
                False,
                error=f"tool 执行失败: {exc}",
            )
        return ToolResult(call.call_id, call.name, True, value=value)


async def _invoke_handler(handler: ToolHandler, arguments: dict[str, Any]) -> Any:
    if inspect.iscoroutinefunction(handler):
        return await handler(arguments)
    value = await asyncio.to_thread(handler, arguments)
    if inspect.isawaitable(value):
        return await value
    return value


def _validate_object(value: Any, schema: Mapping[str, Any]) -> str:
    if not isinstance(value, dict):
        return "tool arguments 必须是 JSON object。"
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        return "tool input schema 无效。"
    for name in required:
        if name not in value:
            return f"tool arguments 缺少必填字段: {name}。"
    if schema.get("additionalProperties") is False:
        extra = sorted(set(value) - set(properties))
        if extra:
            return f"tool arguments 包含未知字段: {', '.join(extra)}。"
    for name, item in value.items():
        property_schema = properties.get(name)
        if property_schema is None:
            continue
        expected = property_schema.get("type")
        if not _matches_type(item, expected):
            return f"tool argument {name} 必须是 {expected}。"
        enum = property_schema.get("enum")
        if enum is not None and item not in enum:
            return f"tool argument {name} 必须是 enum 中的一个值。"
    return ""


def _validate_input_schema(name: str, schema: Any) -> None:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ToolError(f"tool {name} input schema 必须描述 object。")
    unsupported = sorted(set(schema) - TOP_LEVEL_SCHEMA_KEYS)
    if unsupported:
        raise ToolError(
            f"tool {name} input schema 包含不支持的关键字: {', '.join(unsupported)}。"
        )

    properties = schema.get("properties", {})
    required = schema.get("required", [])
    additional = schema.get("additionalProperties", True)
    description = schema.get("description")
    if not isinstance(properties, dict):
        raise ToolError(f"tool {name} input schema properties 必须是 object。")
    if (
        not isinstance(required, list)
        or any(not isinstance(item, str) for item in required)
        or len(set(required)) != len(required)
    ):
        raise ToolError(f"tool {name} input schema required 必须是无重复字符串列表。")
    missing_properties = sorted(set(required) - set(properties))
    if missing_properties:
        raise ToolError(
            f"tool {name} required 字段没有对应 properties: "
            f"{', '.join(missing_properties)}。"
        )
    if not isinstance(additional, bool):
        raise ToolError(
            f"tool {name} additionalProperties 当前只支持 boolean。"
        )
    if description is not None and not isinstance(description, str):
        raise ToolError(f"tool {name} input schema description 必须是 string。")

    for property_name, property_schema in properties.items():
        if not isinstance(property_name, str) or not property_name:
            raise ToolError(f"tool {name} property name 必须是非空字符串。")
        if not isinstance(property_schema, dict):
            raise ToolError(
                f"tool {name} property {property_name} schema 必须是 object。"
            )
        unsupported = sorted(set(property_schema) - PROPERTY_SCHEMA_KEYS)
        if unsupported:
            raise ToolError(
                f"tool {name} property {property_name} 包含不支持的关键字: "
                f"{', '.join(unsupported)}。"
            )
        expected = property_schema.get("type")
        if expected not in SUPPORTED_ARGUMENT_TYPES:
            supported = ", ".join(sorted(SUPPORTED_ARGUMENT_TYPES))
            raise ToolError(
                f"tool {name} property {property_name} type 不支持: "
                f"{expected!r}；当前支持 {supported}。"
            )
        description = property_schema.get("description")
        if description is not None and not isinstance(description, str):
            raise ToolError(
                f"tool {name} property {property_name} description 必须是 string。"
            )
        enum = property_schema.get("enum")
        if enum is not None:
            if not isinstance(enum, list) or not enum:
                raise ToolError(
                    f"tool {name} property {property_name} enum 必须是非空列表。"
                )
            if any(not _matches_type(item, expected) for item in enum):
                raise ToolError(
                    f"tool {name} property {property_name} enum 值与 type 不匹配。"
                )


def _matches_type(value: Any, expected: Any) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return False
