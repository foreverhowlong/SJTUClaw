"""HTTP transport for global long-term memory management."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict

from claw.runtime import ClawRuntime
from claw.store.memory import MemoryRecord


router = APIRouter(prefix="/api/memories", tags=["memories"])


class CreateMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str


@router.get("")
async def list_memories(request: Request) -> dict[str, list[dict[str, str]]]:
    records = _runtime(request).memory_store.list()
    return {"memories": [_memory_record(record) for record in records]}


@router.post("", status_code=201)
async def create_memory(
    payload: CreateMemoryRequest,
    request: Request,
) -> dict[str, str]:
    record = _runtime(request).memory_store.add(payload.content)
    return _memory_record(record)


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(memory_id: str, request: Request) -> Response:
    _runtime(request).memory_store.delete(memory_id)
    return Response(status_code=204)


def _memory_record(record: MemoryRecord) -> dict[str, str]:
    return {"memoryId": record.memory_id, "content": record.content}


def _runtime(request: Request) -> ClawRuntime:
    return request.app.state.runtime
