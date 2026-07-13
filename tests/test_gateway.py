from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from starlette.testclient import TestClient

from claw.events import AgentEvent
from claw.store.attachments import AttachmentStore
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore
from gateway.app import create_app


class FakeAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def run_turn(self, session_id: str, user_input: str):
        self.calls.append((session_id, user_input))
        yield AgentEvent("turn_start", session_id, {"userInput": user_input})
        yield AgentEvent("llm_delta", session_id, {"delta": "你好"})
        yield AgentEvent("llm_message", session_id, {"content": "你好"})
        yield AgentEvent("turn_end", session_id, {"status": "completed"})


@dataclass
class FakeRuntime:
    session_store: SessionStore
    memory_store: MemoryStore
    attachment_store: AttachmentStore
    agent: FakeAgent


def make_runtime(tmp_path) -> FakeRuntime:
    sessions = SessionStore(tmp_path / "sessions")
    return FakeRuntime(
        session_store=sessions,
        memory_store=MemoryStore(tmp_path / "memory"),
        attachment_store=AttachmentStore(sessions, max_bytes=16),
        agent=FakeAgent(),
    )


def test_rest_sessions_share_the_runtime_store(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    existing = runtime.session_store.create("CLI session")

    with TestClient(create_app(runtime)) as client:
        listed = client.get("/api/sessions")
        created = client.post("/api/sessions", json={"title": "Web session"})
        detail = client.get(f"/api/sessions/{existing.session_id}")

    assert listed.status_code == 200
    assert listed.json()["sessions"][0]["sessionId"] == existing.session_id
    assert created.status_code == 201
    assert created.json()["title"] == "Web session"
    assert detail.json()["messages"] == []


def test_rest_renames_and_deletes_session_with_attachments(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = runtime.session_store.create("Before")
    saved = runtime.attachment_store.save(
        session.session_id,
        "brief.txt",
        "text/plain",
        BytesIO(b"brief"),
    )
    attachment_path = (
        runtime.session_store.root
        / session.session_id
        / "attachments"
        / saved.attachment_id
    )

    with TestClient(create_app(runtime)) as client:
        renamed = client.patch(
            f"/api/sessions/{session.session_id}",
            json={"title": "After"},
        )
        deleted = client.delete(f"/api/sessions/{session.session_id}")
        missing = client.get(f"/api/sessions/{session.session_id}")

    assert renamed.status_code == 200
    assert renamed.json()["title"] == "After"
    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert not attachment_path.exists()


def test_rest_session_mutations_validate_title_and_unknown_id(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = runtime.session_store.create()

    with TestClient(create_app(runtime)) as client:
        empty = client.patch(
            f"/api/sessions/{session.session_id}",
            json={"title": "   "},
        )
        unknown = client.delete("/api/sessions/session_0123456789ab")

    assert empty.status_code == 400
    assert "title" in empty.json()["error"]["message"]
    assert unknown.status_code == 404


def test_websocket_creates_missing_session_and_forwards_agent_events(tmp_path) -> None:
    runtime = make_runtime(tmp_path)

    with TestClient(create_app(runtime)) as client:
        with client.websocket_connect("/ws/chat") as socket:
            socket.send_json(
                {
                    "type": "run_turn",
                    "requestId": "request_1",
                    "message": "hello",
                }
            )
            resolved = socket.receive_json()
            events = [socket.receive_json() for _ in range(4)]

    session_id = resolved["session"]["sessionId"]
    assert resolved["type"] == "session_resolved"
    assert resolved["created"] is True
    assert [item["event"]["type"] for item in events] == [
        "turn_start",
        "llm_delta",
        "llm_message",
        "turn_end",
    ]
    assert runtime.agent.calls == [(session_id, "hello")]


def test_websocket_rejects_unknown_session_but_stays_connected(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    valid = runtime.session_store.create()

    with TestClient(create_app(runtime)) as client:
        with client.websocket_connect("/ws/chat") as socket:
            socket.send_json(
                {
                    "type": "run_turn",
                    "requestId": "bad",
                    "sessionId": "session_0123456789ab",
                    "message": "hello",
                }
            )
            error = socket.receive_json()
            socket.send_json(
                {
                    "type": "run_turn",
                    "requestId": "good",
                    "sessionId": valid.session_id,
                    "message": "again",
                }
            )
            resolved = socket.receive_json()

    assert error["type"] == "gateway_error"
    assert error["requestId"] == "bad"
    assert resolved["type"] == "session_resolved"
    assert resolved["requestId"] == "good"


def test_attachment_api_is_session_scoped_and_reports_limit(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    first = runtime.session_store.create()
    second = runtime.session_store.create()

    with TestClient(create_app(runtime)) as client:
        uploaded = client.post(
            f"/api/sessions/{first.session_id}/attachments",
            files={"file": ("brief.txt", b"hello", "text/plain")},
        )
        first_list = client.get(
            f"/api/sessions/{first.session_id}/attachments"
        )
        second_list = client.get(
            f"/api/sessions/{second.session_id}/attachments"
        )
        oversized = client.post(
            f"/api/sessions/{first.session_id}/attachments",
            files={"file": ("large.txt", b"x" * 17, "text/plain")},
        )

    assert uploaded.status_code == 201
    assert first_list.json()["attachments"][0]["filename"] == "brief.txt"
    assert second_list.json()["attachments"] == []
    assert oversized.status_code == 413
