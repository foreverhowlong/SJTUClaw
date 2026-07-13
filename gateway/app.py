"""HTTP and WebSocket Gateway over the shared Claw runtime."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from claw.errors import AttachmentError, ClawError, SessionError
from claw.runtime import ClawRuntime, build_runtime
from claw.session import Session
from claw.store.sessions import SessionSummary


logger = logging.getLogger(__name__)


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = "新会话"


def create_app(runtime: ClawRuntime | None = None) -> FastAPI:
    """Create a Gateway app; tests may inject a runtime without real credentials."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not hasattr(app.state, "runtime"):
            app.state.runtime = build_runtime()
        app.state.session_locks = {}
        yield

    app = FastAPI(
        title="SJTUClaw Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )
    if runtime is not None:
        app.state.runtime = runtime

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(SessionError)
    async def handle_session_error(_request: Request, exc: SessionError):
        return _error_response(404, "session_error", str(exc))

    @app.exception_handler(AttachmentError)
    async def handle_attachment_error(_request: Request, exc: AttachmentError):
        status = 413 if "大小限制" in str(exc) else 400
        return _error_response(status, "attachment_error", str(exc))

    @app.exception_handler(ClawError)
    async def handle_claw_error(_request: Request, exc: ClawError):
        return _error_response(400, "claw_error", str(exc))

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/sessions")
    async def list_sessions(request: Request) -> dict[str, list[dict[str, Any]]]:
        items = _runtime(request).session_store.list()
        return {"sessions": [_session_summary(item) for item in items]}

    @app.post("/api/sessions", status_code=201)
    async def create_session(
        payload: CreateSessionRequest,
        request: Request,
    ) -> dict[str, Any]:
        session = _runtime(request).session_store.create(payload.title)
        return _session_detail(session)

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str, request: Request) -> dict[str, Any]:
        session = _runtime(request).session_store.load(session_id)
        return _session_detail(session)

    @app.get("/api/sessions/{session_id}/attachments")
    async def list_attachments(
        session_id: str,
        request: Request,
    ) -> dict[str, list[dict[str, object]]]:
        records = _runtime(request).attachment_store.list(session_id)
        return {"attachments": [record.to_dict() for record in records]}

    @app.post("/api/sessions/{session_id}/attachments", status_code=201)
    async def upload_attachment(
        session_id: str,
        request: Request,
        file: UploadFile = File(...),
    ) -> dict[str, object]:
        record = await asyncio.to_thread(
            _runtime(request).attachment_store.save,
            session_id,
            file.filename or "",
            file.content_type,
            file.file,
        )
        return record.to_dict()

    @app.websocket("/ws/chat")
    async def chat(websocket: WebSocket) -> None:
        await websocket.accept()
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                return

            request_id = f"request_{uuid4().hex[:12]}"
            try:
                value = json.loads(raw)
                request_id, session_id, message = _parse_turn_request(
                    value,
                    request_id,
                )
                active_runtime: ClawRuntime = websocket.app.state.runtime
                created = session_id is None
                if created:
                    session = active_runtime.session_store.create()
                    session_id = session.session_id
                else:
                    session = active_runtime.session_store.load(session_id)

                await websocket.send_json(
                    {
                        "type": "session_resolved",
                        "requestId": request_id,
                        "created": created,
                        "session": _session_detail(session),
                    }
                )
                locks: dict[str, asyncio.Lock] = websocket.app.state.session_locks
                lock = locks.setdefault(session_id, asyncio.Lock())
                async with lock:
                    async for event in active_runtime.agent.run_turn(
                        session_id,
                        message,
                    ):
                        await websocket.send_json(
                            {
                                "type": "agent_event",
                                "requestId": request_id,
                                "event": event.to_dict(),
                            }
                        )
            except WebSocketDisconnect:
                return
            except (ClawError, ValueError, TypeError, json.JSONDecodeError) as exc:
                await _send_gateway_error(
                    websocket,
                    request_id,
                    "invalid_request",
                    str(exc),
                )
            except Exception:
                logger.exception("gateway websocket request failed: %s", request_id)
                await _send_gateway_error(
                    websocket,
                    request_id,
                    "gateway_error",
                    "Gateway 处理请求时发生内部错误。",
                )

    web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")

    return app


def _runtime(request: Request) -> ClawRuntime:
    return request.app.state.runtime


def _session_summary(item: SessionSummary) -> dict[str, Any]:
    return {
        "sessionId": item.session_id,
        "title": item.title,
        "messageCount": item.message_count,
        "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
    }


def _session_detail(session: Session) -> dict[str, Any]:
    return {
        "sessionId": session.session_id,
        "title": session.title,
        "messageCount": session.message_count,
        "createdAt": session.created_at.isoformat(),
        "updatedAt": session.updated_at.isoformat(),
        "revision": session.revision,
        "summary": session.summary,
        "messages": session.messages,
    }


def _parse_turn_request(
    value: Any,
    fallback_request_id: str,
) -> tuple[str, str | None, str]:
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
    return request_id.strip(), session_id, message.strip()


async def _send_gateway_error(
    websocket: WebSocket,
    request_id: str,
    code: str,
    message: str,
) -> None:
    await websocket.send_json(
        {
            "type": "gateway_error",
            "requestId": request_id,
            "error": {"code": code, "message": message},
        }
    )


def _error_response(
    status: int,
    code: str,
    message: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
    )


def _cors_origins() -> list[str]:
    configured = os.environ.get("CLAW_CORS_ORIGINS", "").strip()
    if configured:
        return [item.strip() for item in configured.split(",") if item.strip()]
    return ["http://localhost:5173", "http://127.0.0.1:5173"]


app = create_app()
