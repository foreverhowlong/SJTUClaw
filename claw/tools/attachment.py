"""Session-scoped read-only attachment tool."""

from __future__ import annotations

from claw.store.attachments import AttachmentStore
from claw.tools.registry import ToolDefinition


READ_ATTACHMENT_TOOL_NAME = "read_attachment"


def build_read_attachment_tool(
    store: AttachmentStore,
    session_id: str,
) -> ToolDefinition:
    """Bind attachment access to one session without exposing its id to the model."""
    return ToolDefinition(
        name=READ_ATTACHMENT_TOOL_NAME,
        description=(
            "Read one uploaded UTF-8 text attachment from the current session by "
            "attachment_id. Use this instead of read_file for session attachments. "
            "Binary or non-UTF-8 attachments cannot be read as text, and content "
            "beyond 64 KiB is truncated. Returns text to the model for reasoning; "
            "it does not create a user-visible download."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "attachment_id": {
                    "type": "string",
                    "description": (
                        "ID of an attachment listed in the current session context."
                    ),
                }
            },
            "required": ["attachment_id"],
            "additionalProperties": False,
        },
        handler=lambda args: store.read_text(session_id, args["attachment_id"]),
    )
