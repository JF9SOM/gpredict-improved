"""
WebSocket connection manager.

ConnectionManager handles broadcasting to all clients and session management.
Called from FastAPI WebSocket handlers and the 1-second broadcast loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages WebSocket connection registration/removal and broadcasting to all clients.

    All methods run inside the asyncio event loop driven by FastAPI,
    so access is serialized with asyncio.Lock.
    """

    def __init__(self) -> None:
        self._active: set[WebSocket] = set()
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Connection management
    # ------------------------------------------------------------------ #

    async def connect(self, ws: WebSocket) -> None:
        """Complete the WebSocket handshake and register the connection."""
        await ws.accept()
        async with self._lock:
            self._active.add(ws)
        logger.info("WS: client connected  (total=%d)", len(self._active))

    async def disconnect(self, ws: WebSocket) -> None:
        """Unregister the connection. Safe to call even if already disconnected."""
        async with self._lock:
            self._active.discard(ws)
        logger.info("WS: client disconnected (total=%d)", len(self._active))

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #

    async def broadcast_json(self, data: dict[str, Any]) -> None:
        """Send JSON data to all connected clients. Failed connections are treated as disconnected."""
        async with self._lock:
            targets = set(self._active)

        dead: set[WebSocket] = set()
        for ws in targets:
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)

        if dead:
            async with self._lock:
                self._active -= dead
            logger.info("WS: removed %d dead connection(s)", len(dead))

    async def send_json(self, ws: WebSocket, data: dict[str, Any]) -> None:
        """Send JSON data to a specific client. Treats the connection as disconnected on failure."""
        try:
            await ws.send_json(data)
        except Exception:
            await self.disconnect(ws)

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #

    @property
    def connection_count(self) -> int:
        """Current number of active connections."""
        return len(self._active)
