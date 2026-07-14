"""Workspace configuration transport for session owners."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict


router = APIRouter(prefix="/api/sessions", tags=["workspace"])


class SetWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str | None


@router.get("/{session_id}/workspace")
async def get_workspace(session_id: str, request: Request) -> dict[str, object]:
    session = request.app.state.runtime.session_store.load(session_id)
    return {"sessionId": session_id, "workspace": session.workspace}


@router.put("/{session_id}/workspace")
async def set_workspace(
    session_id: str,
    payload: SetWorkspaceRequest,
    request: Request,
) -> dict[str, object]:
    service = request.app.state.runtime.workspace_service
    session = (
        service.set(session_id, payload.path)
        if payload.path is not None
        else service.clear(session_id)
    )
    return {"sessionId": session_id, "workspace": session.workspace}
