"""Serve temporary download snapshots registered by the agent runtime."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse


router = APIRouter(prefix="/api/downloads", tags=["downloads"])


@router.get("/{download_id}")
async def download(download_id: str, request: Request) -> FileResponse:
    record = request.app.state.runtime.download_store.get(download_id)
    return FileResponse(
        record.blob_path,
        filename=record.filename,
        media_type="application/octet-stream",
    )
