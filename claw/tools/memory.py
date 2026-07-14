"""Model-facing tools for the existing global MemoryStore."""

from __future__ import annotations

from typing import Any

from claw.store.memory import MemoryStore
from claw.tools.registry import ToolDefinition, ToolRegistry


SAVE_MEMORY_TOOL_NAME = "save_memory"
DELETE_MEMORY_TOOL_NAME = "delete_memory"


def register_memory_tools(registry: ToolRegistry, memories: MemoryStore) -> None:
    registry.register(
        ToolDefinition(
            name=SAVE_MEMORY_TOOL_NAME,
            description=(
                "Save one durable, cross-session user fact or preference to global "
                "memory. Use only for information the user stated or confirmed that "
                "will remain useful in future sessions. Do not save temporary task "
                "state, ordinary conversation, model inferences, secrets, large copied "
                "content, or information already present in memory."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": (
                            "A concise, self-contained long-term fact or preference."
                        ),
                    }
                },
                "required": ["content"],
                "additionalProperties": False,
            },
            handler=lambda args: _save_memory(memories, args),
            safety_level="memory_write",
        )
    )
    registry.register(
        ToolDefinition(
            name=DELETE_MEMORY_TOOL_NAME,
            description=(
                "Permanently delete one global memory by its exact memory_id. Use only "
                "when the memory is obsolete, incorrect, or the user asks to forget it. "
                "This destructive action requires user approval."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "Exact memory ID shown in the global memory context.",
                    }
                },
                "required": ["memory_id"],
                "additionalProperties": False,
            },
            handler=lambda args: _delete_memory(memories, args),
            safety_level="advanced",
            requires_approval=True,
        )
    )


def _save_memory(memories: MemoryStore, args: dict[str, Any]) -> dict[str, str]:
    record = memories.add(args["content"])
    return {"memoryId": record.memory_id, "content": record.content}


def _delete_memory(memories: MemoryStore, args: dict[str, Any]) -> dict[str, Any]:
    memory_id = args["memory_id"]
    memories.delete(memory_id)
    return {"memoryId": memory_id, "deleted": True}
