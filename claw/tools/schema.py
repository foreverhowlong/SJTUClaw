"""The supported JSON-schema subset for tool arguments."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from claw.errors import ToolError


SUPPORTED_ARGUMENT_TYPES = frozenset({"string", "integer", "number", "boolean"})
TOP_LEVEL_SCHEMA_KEYS = frozenset(
    {"type", "properties", "required", "additionalProperties", "description"}
)
PROPERTY_SCHEMA_KEYS = frozenset({"type", "description", "enum"})


def validate_arguments(value: Any, schema: Mapping[str, Any]) -> str:
    """Return a user-facing validation error, or an empty string when valid."""
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


def validate_input_schema(name: str, schema: Any) -> None:
    """Reject schema features that runtime argument validation cannot enforce."""
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
        raise ToolError(f"tool {name} additionalProperties 当前只支持 boolean。")
    if description is not None and not isinstance(description, str):
        raise ToolError(f"tool {name} input schema description 必须是 string。")

    for property_name, property_schema in properties.items():
        _validate_property_schema(name, property_name, property_schema)


def _validate_property_schema(
    tool_name: str,
    property_name: Any,
    schema: Any,
) -> None:
    if not isinstance(property_name, str) or not property_name:
        raise ToolError(f"tool {tool_name} property name 必须是非空字符串。")
    if not isinstance(schema, dict):
        raise ToolError(
            f"tool {tool_name} property {property_name} schema 必须是 object。"
        )
    unsupported = sorted(set(schema) - PROPERTY_SCHEMA_KEYS)
    if unsupported:
        raise ToolError(
            f"tool {tool_name} property {property_name} 包含不支持的关键字: "
            f"{', '.join(unsupported)}。"
        )
    expected = schema.get("type")
    if expected not in SUPPORTED_ARGUMENT_TYPES:
        supported = ", ".join(sorted(SUPPORTED_ARGUMENT_TYPES))
        raise ToolError(
            f"tool {tool_name} property {property_name} type 不支持: "
            f"{expected!r}；当前支持 {supported}。"
        )
    description = schema.get("description")
    if description is not None and not isinstance(description, str):
        raise ToolError(
            f"tool {tool_name} property {property_name} description 必须是 string。"
        )
    enum = schema.get("enum")
    if enum is not None:
        if not isinstance(enum, list) or not enum:
            raise ToolError(
                f"tool {tool_name} property {property_name} enum 必须是非空列表。"
            )
        if any(not _matches_type(item, expected) for item in enum):
            raise ToolError(
                f"tool {tool_name} property {property_name} enum 值与 type 不匹配。"
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
