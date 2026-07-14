"""Approval listing and resolution transport."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict


router = APIRouter(prefix="/api/approvals", tags=["approvals"])


class ResolveApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    reason: str = ""


@router.get("")
async def list_approvals(
    request: Request,
    session_id: str | None = Query(default=None, alias="sessionId"),
    status: Literal["pending", "approved", "denied", "executing", "succeeded", "failed", "expired", "interrupted"] | None = None,
) -> dict[str, list[dict[str, object]]]:
    records = request.app.state.runtime.approval_store.list(
        session_id=session_id,
        status=status,
    )
    return {"approvals": [record.to_dict() for record in records]}


@router.post("/{approval_id}/resolve")
async def resolve_approval(
    approval_id: str,
    payload: ResolveApprovalRequest,
    request: Request,
) -> dict[str, object]:
    record = request.app.state.runtime.approval_coordinator.resolve(
        approval_id,
        approved=payload.approved,
        reason=payload.reason,
    )
    return record.to_dict()
