"""HTTP and WebSocket Gateway over the shared Claw runtime."""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from claw.errors import (
    AttachmentError,
    ApprovalError,
    ClawError,
    DownloadError,
    MemoryError,
    SessionError,
    SessionConflictError,
    SkillError,
    TaskConflictError,
    TaskError,
    TaskNotFoundError,
)
from claw.runtime import ClawRuntime, build_runtime, serve_runtime
from claw.session_coordination import SessionCoordinator
from claw.session_lifecycle import SessionLifecycleService
from gateway.realtime import GatewayConnectionHub
from gateway.chat import router as chat_router
from gateway.session_views import session_detail, session_summary
from gateway.task_routes import router as task_router
from gateway.approval_routes import router as approval_router
from gateway.download_routes import router as download_router
from gateway.memory_routes import router as memory_router
from gateway.workspace_routes import router as workspace_router
from gateway.skill_routes import router as skill_router


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = "新会话"


class RenameSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str


def create_app(runtime: ClawRuntime | None = None) -> FastAPI:
    """Create a Gateway app; tests may inject a runtime without real credentials."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not hasattr(app.state, "runtime"):
            app.state.runtime = build_runtime()
        active_runtime = app.state.runtime
        if not hasattr(active_runtime, "session_coordinator"):
            active_runtime.session_coordinator = SessionCoordinator(
                active_runtime.session_store.root
            )
        if not hasattr(active_runtime, "session_lifecycle"):
            active_runtime.session_lifecycle = SessionLifecycleService(
                active_runtime.session_store,
                active_runtime.session_coordinator,
                scheduler=getattr(active_runtime, "scheduler", None),
                approvals=getattr(active_runtime, "approval_store", None),
                shells=getattr(active_runtime, "shell_manager", None),
            )
        app.state.connection_hub = GatewayConnectionHub()
        unsubscribe = app.state.runtime.scheduler.subscribe_session_updates(
            lambda session_id: _broadcast_session_updated(app, session_id)
        )
        try:
            async with serve_runtime(app.state.runtime):
                yield
        finally:
            unsubscribe()

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
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(SessionError)
    async def handle_session_error(_request: Request, exc: SessionError):
        message = str(exc)
        status = 404 if "不存在" in message or "无效的 sessionId" in message else 400
        return _error_response(status, "session_error", message)

    @app.exception_handler(SessionConflictError)
    async def handle_session_conflict(_request: Request, exc: SessionConflictError):
        return _error_response(409, "session_conflict", str(exc))

    @app.exception_handler(AttachmentError)
    async def handle_attachment_error(_request: Request, exc: AttachmentError):
        status = 413 if "大小限制" in str(exc) else 400
        return _error_response(status, "attachment_error", str(exc))

    @app.exception_handler(ApprovalError)
    async def handle_approval_error(_request: Request, exc: ApprovalError):
        status = 404 if "不存在" in str(exc) else 409
        return _error_response(status, "approval_error", str(exc))

    @app.exception_handler(DownloadError)
    async def handle_download_error(_request: Request, exc: DownloadError):
        status = 404 if "不存在" in str(exc) or "过期" in str(exc) else 400
        return _error_response(status, "download_error", str(exc))

    @app.exception_handler(MemoryError)
    async def handle_memory_error(_request: Request, exc: MemoryError):
        status = 404 if "不存在" in str(exc) else 400
        return _error_response(status, "memory_error", str(exc))

    @app.exception_handler(TaskNotFoundError)
    async def handle_task_not_found(_request: Request, exc: TaskNotFoundError):
        return _error_response(404, "task_not_found", str(exc))

    @app.exception_handler(TaskConflictError)
    async def handle_task_conflict(_request: Request, exc: TaskConflictError):
        return _error_response(409, "task_conflict", str(exc))

    @app.exception_handler(TaskError)
    async def handle_task_error(_request: Request, exc: TaskError):
        return _error_response(400, "task_error", str(exc))

    @app.exception_handler(SkillError)
    async def handle_skill_error(_request: Request, exc: SkillError):
        status = 404 if "不存在" in str(exc) else 400
        return _error_response(status, "skill_error", str(exc))

    @app.exception_handler(ClawError)
    async def handle_claw_error(_request: Request, exc: ClawError):
        return _error_response(400, "claw_error", str(exc))

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/sessions")
    async def list_sessions(request: Request) -> dict[str, list[dict[str, Any]]]:
        items = _runtime(request).session_store.list()
        return {"sessions": [session_summary(item) for item in items]}

    @app.post("/api/sessions", status_code=201)
    async def create_session(
        payload: CreateSessionRequest,
        request: Request,
    ) -> dict[str, Any]:
        session = _runtime(request).session_store.create(payload.title)
        return session_detail(session)

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str, request: Request) -> dict[str, Any]:
        session = _runtime(request).session_store.load(session_id)
        return session_detail(session)

    @app.patch("/api/sessions/{session_id}")
    async def rename_session(
        session_id: str,
        payload: RenameSessionRequest,
        request: Request,
    ) -> dict[str, Any]:
        async with _runtime(request).session_coordinator.mutation(session_id):
            session = _runtime(request).session_store.rename(session_id, payload.title)
        return session_detail(session)

    @app.post("/api/sessions/{session_id}/compact")
    async def compact_session(session_id: str, request: Request) -> dict[str, Any]:
        runtime = _runtime(request)
        result = await runtime.agent.compact_session(session_id, force=True)
        session = runtime.session_store.load(session_id)
        await _broadcast_session_updated(
            request.app,
            session_id,
            reason="compaction",
        )
        result_payload = asdict(result)
        return {
            "result": {
                "sessionId": result_payload["session_id"],
                "status": result_payload["status"],
                "oldMessageCount": result_payload["old_message_count"],
                "recentMessageCount": result_payload["recent_message_count"],
                "summary": result_payload["summary"],
                "detail": result_payload["detail"],
            },
            "session": session_detail(session),
        }

    @app.delete("/api/sessions/{session_id}", status_code=204)
    async def delete_session(
        session_id: str,
        request: Request,
        cascade: bool = Query(default=False),
    ) -> Response:
        await _runtime(request).session_lifecycle.delete(
            session_id,
            cascade=cascade,
        )
        return Response(status_code=204)

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
        async with _runtime(request).session_coordinator.mutation(session_id):
            record = await asyncio.to_thread(
                _runtime(request).attachment_store.save,
                session_id,
                file.filename or "",
                file.content_type,
                file.file,
            )
        return record.to_dict()

    app.include_router(chat_router)
    app.include_router(task_router)
    app.include_router(memory_router)
    app.include_router(workspace_router)
    app.include_router(approval_router)
    app.include_router(download_router)
    app.include_router(skill_router)

    web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")

    return app


def _runtime(request: Request) -> ClawRuntime:
    return request.app.state.runtime


def _connection_hub(app: Any) -> GatewayConnectionHub:
    return app.state.connection_hub


async def _broadcast_session_updated(
    app: Any,
    session_id: str,
    *,
    reason: str = "scheduled_task",
) -> None:
    await _connection_hub(app).broadcast(
        {
            "type": "session_updated",
            "sessionId": session_id,
            "reason": reason,
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
