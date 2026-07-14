"""WebSocket renderer for the shared event-streaming AgentService."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from claw.errors import ClawError
from claw.events import AgentEvent
from claw.presentation.timeline import tool_activity
from claw.skills import SkillRequest
from gateway.realtime import GatewayConnection, GatewayConnectionHub
from gateway.session_views import session_detail


router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/ws/chat")
async def chat(websocket: WebSocket) -> None:
    connection = await _connection_hub(websocket.app).connect(websocket)
    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                return

            request_id = f"request_{uuid4().hex[:12]}"
            try:
                value = json.loads(raw)
                request_id, session_id, message, skill_name = parse_turn_request(
                    value, request_id
                )
                runtime = websocket.app.state.runtime
                created = session_id is None
                if created:
                    session = runtime.session_store.create()
                    session_id = session.session_id
                else:
                    session = runtime.session_store.load(session_id)

                await connection.send_json(
                    {
                        "type": "session_resolved",
                        "requestId": request_id,
                        "created": created,
                        "session": session_detail(session),
                    }
                )
                live_tools: dict[str, tuple[str, str]] = {}
                turn_events = (
                    runtime.agent.run_turn(session_id, message)
                    if skill_name is None
                    else runtime.agent.run_turn(
                        session_id,
                        message,
                        skill_request=SkillRequest.explicit(skill_name),
                    )
                )
                async for event in turn_events:
                    await connection.send_json(
                        {
                            "type": "agent_event",
                            "requestId": request_id,
                            "event": web_event(event, live_tools),
                        }
                    )
            except WebSocketDisconnect:
                return
            except (ClawError, ValueError, TypeError, json.JSONDecodeError) as exc:
                await send_gateway_error(
                    connection, request_id, "invalid_request", str(exc)
                )
            except Exception:
                logger.exception("gateway websocket request failed: %s", request_id)
                await send_gateway_error(
                    connection,
                    request_id,
                    "gateway_error",
                    "Gateway 处理请求时发生内部错误。",
                )
    finally:
        _connection_hub(websocket.app).disconnect(connection)


def web_event(
    event: AgentEvent,
    live_tools: dict[str, tuple[str, str]],
) -> dict[str, Any]:
    """Attach shared presentation data while retaining the runtime event contract."""
    rendered = event.to_dict()
    payload = dict(rendered["payload"])
    rendered["payload"] = payload
    if event.type == "tool_call":
        call_id = str(payload["callId"])
        name = str(payload["name"])
        arguments = str(payload["arguments"])
        live_tools[call_id] = (name, arguments)
        payload["timelineItem"] = tool_activity(call_id, name, arguments)
    elif event.type == "tool_result":
        call_id = str(payload["callId"])
        name, arguments = live_tools.get(call_id, (str(payload["name"]), "{}"))
        payload["timelineItem"] = tool_activity(
            call_id,
            name,
            arguments,
            status="succeeded" if payload["ok"] else "failed",
            result=payload.get("result"),
            error=str(payload.get("error", "")),
        )
    elif event.type in {"approval_required", "approval_resolved"}:
        call_id = str(payload["callId"])
        name, arguments = live_tools.get(call_id, (str(payload["name"]), "{}"))
        required = event.type == "approval_required"
        item = tool_activity(
            call_id,
            name,
            arguments,
            status=(
                "awaiting_approval"
                if required
                else ("running" if payload["approved"] else "failed")
            ),
            error="" if required or payload["approved"] else str(payload["reason"]),
        )
        if required:
            item["approval"] = {
                "approvalId": payload.get("approvalId"),
                "arguments": payload.get("arguments", {}),
                "workspace": payload.get("workspace"),
            }
        payload["timelineItem"] = item
    return rendered


def parse_turn_request(
    value: Any,
    fallback_request_id: str,
) -> tuple[str, str | None, str, str | None]:
    if not isinstance(value, dict) or value.get("type") != "run_turn":
        raise ValueError("WebSocket 消息 type 必须是 run_turn。")
    request_id = value.get("requestId", fallback_request_id)
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("requestId 必须是非空字符串。")
    session_id = value.get("sessionId")
    if session_id is not None and (
        not isinstance(session_id, str) or not session_id.strip()
    ):
        raise ValueError("sessionId 必须是非空字符串或 null。")
    message = value.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message 必须是非空字符串。")
    skill_name = value.get("skillName")
    if skill_name is not None and (
        not isinstance(skill_name, str) or not skill_name.strip()
    ):
        raise ValueError("skillName 必须是非空字符串或 null。")
    return (
        request_id.strip(),
        session_id,
        message.strip(),
        skill_name.strip() if isinstance(skill_name, str) else None,
    )


async def send_gateway_error(
    connection: GatewayConnection,
    request_id: str,
    code: str,
    message: str,
) -> None:
    await connection.send_json(
        {
            "type": "gateway_error",
            "requestId": request_id,
            "error": {"code": code, "message": message},
        }
    )


def _connection_hub(app: Any) -> GatewayConnectionHub:
    return app.state.connection_hub
