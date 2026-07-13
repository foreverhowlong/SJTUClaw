"""Concurrency-safe WebSocket delivery owned by the Gateway transport."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket


@dataclass(eq=False)
class GatewayConnection:
    websocket: WebSocket
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send_json(self, message: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.websocket.send_json(message)


class GatewayConnectionHub:
    """Track live clients and serialize concurrent direct/broadcast sends."""

    def __init__(self) -> None:
        self._connections: set[GatewayConnection] = set()

    async def connect(self, websocket: WebSocket) -> GatewayConnection:
        await websocket.accept()
        connection = GatewayConnection(websocket)
        self._connections.add(connection)
        return connection

    def disconnect(self, connection: GatewayConnection) -> None:
        self._connections.discard(connection)

    async def broadcast(self, message: dict[str, Any]) -> None:
        connections = tuple(self._connections)
        if not connections:
            return
        results = await asyncio.gather(
            *(connection.send_json(message) for connection in connections),
            return_exceptions=True,
        )
        for connection, result in zip(connections, results, strict=True):
            if isinstance(result, BaseException):
                self.disconnect(connection)
