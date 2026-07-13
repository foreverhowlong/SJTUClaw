from datetime import timezone

from claw.events import AgentEvent


def test_agent_event_serializes_shared_wire_shape() -> None:
    event = AgentEvent("tool_call", "session_0123456789ab", {"name": "read_file"})

    encoded = event.to_dict()

    assert encoded["type"] == "tool_call"
    assert encoded["sessionId"] == "session_0123456789ab"
    assert encoded["payload"] == {"name": "read_file"}
    assert event.timestamp.tzinfo == timezone.utc
