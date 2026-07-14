"""Tool definitions, registration, and runtime dispatch."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from claw.errors import ToolError
from claw.tools.schema import validate_arguments, validate_input_schema


ToolHandler = Callable[[dict[str, Any]], Any]
SafetyLevel = Literal["read_only", "advanced", "download", "context_extension"]
DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
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
    uncertain: bool = False

    def model_content(self) -> str:
        if self.ok:
            payload = {"ok": True, "result": self.value}
        else:
            payload = {"ok": False, "error": self.error}
            if self.value is not None:
                payload["result"] = self.value
            if self.uncertain:
                payload["uncertain"] = True
        return json.dumps(payload, ensure_ascii=False)


@dataclass(frozen=True)
class PreparedToolCall:
    call: ToolCall
    tool: "ToolDefinition"
    arguments: dict[str, Any]


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
        if self.safety_level == "download" and self.requires_approval:
            raise ToolError(f"download tool 不进入显式 approval: {self.name}。")
        if self.safety_level == "context_extension" and not self.requires_approval:
            raise ToolError(f"context_extension tool 必须要求 approval: {self.name}。")
        if self.safety_level not in {
            "read_only",
            "advanced",
            "download",
            "context_extension",
        }:
            raise ToolError(f"tool safety level 无效: {self.name}。")
        validate_input_schema(self.name, self.input_schema)

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

    def clone(self) -> ToolRegistry:
        """Copy definitions and handlers for safe per-turn extension."""
        copied = ToolRegistry(timeout_seconds=self._timeout_seconds)
        for tool in self._tools.values():
            copied.register(tool)
        return copied

    async def execute(self, call: ToolCall, *, approved: bool = False) -> ToolResult:
        prepared, error = self.prepare(call)
        if error is not None:
            return error
        assert prepared is not None
        return await self.execute_prepared(prepared, approved=approved)

    def prepare(
        self,
        call: ToolCall,
    ) -> tuple[PreparedToolCall | None, ToolResult | None]:
        """Resolve and validate a call before asking a user to approve it."""
        tool = self._tools.get(call.name)
        if tool is None:
            return None, ToolResult(
                call.call_id,
                call.name,
                False,
                error=f"未知 tool: {call.name}。",
            )
        try:
            arguments = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as exc:
            return None, ToolResult(
                call.call_id,
                call.name,
                False,
                error=f"tool arguments 不是有效 JSON: {exc.msg}。",
            )
        error = validate_arguments(arguments, tool.input_schema)
        if error:
            return None, ToolResult(call.call_id, call.name, False, error=error)
        return PreparedToolCall(call, tool, arguments), None

    async def execute_prepared(
        self,
        prepared: PreparedToolCall,
        *,
        approved: bool = False,
    ) -> ToolResult:
        call = prepared.call
        tool = prepared.tool
        arguments = prepared.arguments
        if tool.requires_approval and not approved:
            return ToolResult(
                call.call_id,
                call.name,
                False,
                error="该工具需要审批，当前调用未获批准。",
            )
        try:
            value = await asyncio.wait_for(
                _invoke_handler(tool.handler, arguments),
                timeout=self._timeout_seconds,
            )
            json.dumps(value, ensure_ascii=False)
            if isinstance(value, dict) and value.get("success") is False:
                error = value.get("error")
                return ToolResult(
                    call.call_id,
                    call.name,
                    False,
                    value=value,
                    error=(
                        error
                        if isinstance(error, str) and error
                        else "tool reported an unsuccessful operation"
                    ),
                )
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
                uncertain=tool.requires_approval and not inspect.iscoroutinefunction(
                    tool.handler
                ),
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
