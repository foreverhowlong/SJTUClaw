from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json

from starlette.testclient import TestClient

from claw.events import AgentEvent
from claw.compaction import CompactionResult
from claw.scheduler import Scheduler
from claw.store.attachments import AttachmentStore
from claw.approval import ApprovalCoordinator
from claw.store.approvals import ApprovalStore
from claw.store.downloads import DownloadStore
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore
from claw.store.tasks import TaskStore
from claw.skills import SkillRegistry, SkillUsage
from claw.workspace import WorkspaceService
from gateway.app import create_app


class FakeAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.skill_requests = []
        self.compactions: list[tuple[str, bool]] = []

    async def run_turn(
        self, session_id: str, user_input: str, *, source=None, skill_request=None
    ):
        self.calls.append((session_id, user_input))
        self.skill_requests.append(skill_request)
        yield AgentEvent("turn_start", session_id, {"userInput": user_input})
        if skill_request is not None:
            yield AgentEvent(
                "skill_selected",
                session_id,
                {
                    "usageId": "usage_test",
                    "name": skill_request.name,
                    "description": "test skill",
                    "source": "explicit",
                    "reason": "用户显式选择了该 Skill。",
                },
            )
        yield AgentEvent("llm_delta", session_id, {"delta": "你好"})
        yield AgentEvent("llm_message", session_id, {"content": "你好"})
        yield AgentEvent("turn_end", session_id, {"status": "completed"})

    async def compact_session(self, session_id: str, *, force: bool = True):
        self.compactions.append((session_id, force))
        return CompactionResult(
            session_id=session_id,
            status="skipped",
            old_message_count=0,
            recent_message_count=0,
            detail="没有足够的完整旧 turns 可压缩。",
        )


class FakeToolAgent(FakeAgent):
    async def run_turn(self, session_id: str, user_input: str, *, source=None):
        yield AgentEvent("turn_start", session_id, {"userInput": user_input})
        yield AgentEvent(
            "tool_call",
            session_id,
            {
                "callId": "call_1",
                "name": "read_file",
                "arguments": '{"path":"README.md"}',
            },
        )
        yield AgentEvent(
            "tool_result",
            session_id,
            {
                "callId": "call_1",
                "name": "read_file",
                "ok": True,
                "result": {
                    "path": "README.md",
                    "charactersRead": 12,
                    "truncated": False,
                },
                "error": "",
            },
        )
        yield AgentEvent("turn_end", session_id, {"status": "completed"})


class PersistingAgent(FakeAgent):
    def __init__(self, sessions: SessionStore) -> None:
        super().__init__()
        self.sessions = sessions

    async def run_turn(self, session_id: str, user_input: str, *, source=None):
        snapshot = self.sessions.load(session_id)
        user_message = {"role": "user", "content": user_input}
        if source is not None:
            user_message["source"] = source
        self.sessions.commit_turn(
            session_id,
            expected_revision=snapshot.revision,
            messages=[
                user_message,
                {"role": "assistant", "content": "scheduled reply"},
            ],
        )
        yield AgentEvent("llm_message", session_id, {"content": "scheduled reply"})
        yield AgentEvent("turn_end", session_id, {"status": "completed"})


@dataclass
class FakeRuntime:
    session_store: SessionStore
    memory_store: MemoryStore
    attachment_store: AttachmentStore
    agent: FakeAgent
    task_store: TaskStore
    scheduler: Scheduler
    skill_registry: SkillRegistry


def make_runtime(tmp_path) -> FakeRuntime:
    sessions = SessionStore(tmp_path / "sessions")
    agent = FakeAgent()
    task_store = TaskStore(tmp_path / "tasks")
    return FakeRuntime(
        session_store=sessions,
        memory_store=MemoryStore(tmp_path / "memory"),
        attachment_store=AttachmentStore(sessions, max_bytes=16),
        agent=agent,
        task_store=task_store,
        scheduler=Scheduler(
            task_store,
            sessions,
            agent,
            poll_interval_seconds=60,
        ),
        skill_registry=SkillRegistry(tmp_path / "skills"),
    )


def test_skill_api_lists_catalog_and_session_usage(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = runtime.session_store.create()
    messages = [
        {"role": "user", "content": "write report"},
        {"role": "assistant", "content": "saved report"},
    ]
    runtime.session_store.commit_turn(
        session.session_id,
        expected_revision=0,
        messages=messages,
        skill_usage=SkillUsage(
            "usage_test",
            "",
            "course-report",
            session.session_id,
            "write report",
            "explicit",
            "用户显式选择了该 Skill。",
            datetime.now(timezone.utc),
            "completed",
            "saved report",
        ),
    )

    with TestClient(create_app(runtime)) as client:
        skills = client.get("/api/skills")
        detail = client.get("/api/skills/course-report")
        usage = client.get(f"/api/sessions/{session.session_id}/skill-usages")

    assert skills.status_code == 200
    assert {item["name"] for item in skills.json()["skills"]} == {
        "course-report",
        "material-summary",
        "presentation-outline",
    }
    assert detail.status_code == 200
    assert detail.json()["origin"] == "builtin"
    assert "assets/template.md" in detail.json()["resources"]
    assert usage.json()["usages"][0]["finalOutput"] == "saved report"


def test_websocket_accepts_explicit_skill_name(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = runtime.session_store.create()

    with TestClient(create_app(runtime)) as client:
        with client.websocket_connect("/ws/chat") as socket:
            socket.send_json(
                {
                    "type": "run_turn",
                    "requestId": "request_skill",
                    "sessionId": session.session_id,
                    "message": "write report",
                    "skillName": "course-report",
                }
            )
            frames = [socket.receive_json() for _ in range(6)]

    assert frames[0]["type"] == "session_resolved"
    assert frames[2]["event"]["type"] == "skill_selected"
    assert runtime.agent.skill_requests[0].name == "course-report"


def add_task8_services(runtime, tmp_path) -> None:
    runtime.workspace_service = WorkspaceService(runtime.session_store)
    runtime.approval_store = ApprovalStore(tmp_path / "approvals")
    runtime.approval_coordinator = ApprovalCoordinator(
        runtime.approval_store,
        timeout_seconds=1,
    )
    runtime.download_store = DownloadStore(tmp_path / "downloads")


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
    assert detail.json()["timeline"] == []


def test_rest_compact_reuses_agent_service_and_returns_refreshed_session(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = runtime.session_store.create("Compact me")

    with TestClient(create_app(runtime)) as client:
        response = client.post(f"/api/sessions/{session.session_id}/compact")

    assert response.status_code == 200
    assert runtime.agent.compactions == [(session.session_id, True)]
    assert response.json()["result"] == {
        "sessionId": session.session_id,
        "status": "skipped",
        "oldMessageCount": 0,
        "recentMessageCount": 0,
        "summary": "",
        "detail": "没有足够的完整旧 turns 可压缩。",
    }
    assert response.json()["session"]["sessionId"] == session.session_id


def test_rest_session_detail_projects_persisted_tool_timeline(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = runtime.session_store.create()
    runtime.session_store.commit_turn(
        session.session_id,
        expected_revision=0,
        messages=[
            {"role": "user", "content": "inspect"},
            {
                "role": "assistant",
                "content": "I will inspect it.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "list_dir",
                            "arguments": '{"path":"."}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "list_dir",
                "content": json.dumps(
                    {"ok": True, "result": [{"name": "README.md"}]}
                ),
            },
            {"role": "assistant", "content": "Done."},
        ],
    )

    with TestClient(create_app(runtime)) as client:
        detail = client.get(f"/api/sessions/{session.session_id}").json()

    assert [item["type"] for item in detail["timeline"]] == [
        "user_message",
        "working_note",
        "tool_activity",
        "assistant_message",
    ]
    assert detail["timeline"][2]["status"] == "succeeded"
    assert detail["timeline"][2]["detail"] == "1 项"


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


def test_websocket_enriches_tool_events_with_shared_timeline_items(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.agent = FakeToolAgent()
    session = runtime.session_store.create()

    with TestClient(create_app(runtime)) as client:
        with client.websocket_connect("/ws/chat") as socket:
            socket.send_json(
                {
                    "type": "run_turn",
                    "requestId": "tools",
                    "sessionId": session.session_id,
                    "message": "inspect",
                }
            )
            socket.receive_json()
            events = [socket.receive_json()["event"] for _ in range(4)]

    call = events[1]["payload"]["timelineItem"]
    result = events[2]["payload"]["timelineItem"]
    assert call["action"] == "读取文件"
    assert call["target"] == "README.md"
    assert call["status"] == "running"
    assert result["status"] == "succeeded"
    assert result["detail"] == "12 字符"


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


def test_scheduler_broadcasts_session_update_to_connected_clients(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    agent = PersistingAgent(runtime.session_store)
    current = [datetime(2026, 7, 14, tzinfo=timezone.utc)]
    runtime.agent = agent
    runtime.scheduler = Scheduler(
        runtime.task_store,
        runtime.session_store,
        agent,
        poll_interval_seconds=0.01,
        now=lambda: current[0],
    )
    session = runtime.session_store.create()

    with TestClient(create_app(runtime)) as client:
        with client.websocket_connect("/ws/chat") as socket:
            response = client.post(
                "/api/tasks",
                json={
                    "sessionId": session.session_id,
                    "content": "scheduled input",
                    "schedule": {
                        "type": "once",
                        "runAt": "2026-07-14T00:00:01Z",
                    },
                },
            )
            current[0] += timedelta(seconds=1)
            notification = socket.receive_json()

    assert response.status_code == 201
    assert notification == {
        "type": "session_updated",
        "sessionId": session.session_id,
        "reason": "scheduled_task",
    }
    assert runtime.session_store.load(session.session_id).messages[0] == {
        "role": "user",
        "content": "scheduled input",
        "source": "scheduled_task",
    }


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


def test_memory_api_manages_the_shared_global_store(tmp_path) -> None:
    runtime = make_runtime(tmp_path)

    with TestClient(create_app(runtime)) as client:
        empty = client.get("/api/memories")
        invalid = client.post("/api/memories", json={"content": "   "})
        created = client.post(
            "/api/memories",
            json={"content": "  Prefer concise technical explanations.  "},
        )
        memory_id = created.json()["memoryId"]
        listed = client.get("/api/memories")
        deleted = client.delete(f"/api/memories/{memory_id}")
        missing = client.delete(f"/api/memories/{memory_id}")
        malformed = client.delete("/api/memories/not-a-memory-id")

    assert empty.json() == {"memories": []}
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "memory_error"
    assert created.status_code == 201
    assert created.json()["content"] == "Prefer concise technical explanations."
    assert listed.json() == {"memories": [created.json()]}
    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert malformed.status_code == 400
    assert runtime.memory_store.list() == []


def test_task_api_creates_lists_reads_and_cancels_persisted_task(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = runtime.session_store.create("Scheduled work")
    run_at = datetime.now(timezone.utc) + timedelta(hours=1)

    with TestClient(create_app(runtime)) as client:
        created = client.post(
            "/api/tasks",
            json={
                "sessionId": session.session_id,
                "content": "summarize progress",
                "schedule": {"type": "once", "runAt": run_at.isoformat()},
            },
        )
        task_id = created.json()["taskId"]
        listed = client.get("/api/tasks")
        detail = client.get(f"/api/tasks/{task_id}")
        cancelled = client.post(f"/api/tasks/{task_id}/cancel")
        conflict = client.post(f"/api/tasks/{task_id}/cancel")

    assert created.status_code == 201
    assert listed.json()["tasks"][0]["content"] == "summarize progress"
    assert detail.json()["sessionId"] == session.session_id
    assert cancelled.json()["status"] == "cancelled"
    assert conflict.status_code == 409
    assert runtime.task_store.get(task_id).status == "cancelled"


def test_task_api_rejects_missing_session_and_past_time(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = runtime.session_store.create()
    past = datetime.now(timezone.utc) - timedelta(minutes=1)

    with TestClient(create_app(runtime)) as client:
        missing = client.post(
            "/api/tasks",
            json={
                "sessionId": "session_0123456789ab",
                "content": "run",
                "schedule": {
                    "type": "interval",
                    "startAt": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                    "intervalSeconds": 60,
                },
            },
        )
        expired = client.post(
            "/api/tasks",
            json={
                "sessionId": session.session_id,
                "content": "run",
                "schedule": {"type": "once", "runAt": past.isoformat()},
            },
        )

    assert missing.status_code == 404
    assert expired.status_code == 400


def test_workspace_approval_and_download_routes_use_runtime_services(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    add_task8_services(runtime, tmp_path)
    session = runtime.session_store.create()
    project = tmp_path / "project"
    project.mkdir()
    output = project / "report.md"
    output.write_text("ready", encoding="utf-8")
    download = runtime.download_store.create(session.session_id, output)

    with TestClient(create_app(runtime)) as client:
        approval = runtime.approval_store.create(
            session.session_id,
            "call_1",
            "create_file",
            {"path": "note.txt", "content": "hello"},
            str(project),
        )
        workspace = client.put(
            f"/api/sessions/{session.session_id}/workspace",
            json={"path": str(project)},
        )
        pending = client.get(
            "/api/approvals",
            params={"sessionId": session.session_id, "status": "pending"},
        )
        resolved = client.post(
            f"/api/approvals/{approval.approval_id}/resolve",
            json={"approved": False, "reason": "not now"},
        )
        downloaded = client.get(f"/api/downloads/{download.download_id}")

    assert workspace.status_code == 200
    assert workspace.json()["workspace"] == str(project.resolve())
    assert pending.json()["approvals"][0]["approvalId"] == approval.approval_id
    assert resolved.json()["status"] == "denied"
    assert downloaded.content == b"ready"
    assert "report.md" in downloaded.headers["content-disposition"]
