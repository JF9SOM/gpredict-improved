"""
WebSocket 接続マネージャー

ConnectionManager が全クライアントへのブロードキャストとセッション管理を担う。
FastAPI WebSocket ハンドラーと 1 秒ブロードキャストループから呼ばれる。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    WebSocket 接続の追加・削除と全クライアントへの一斉送信を管理するクラス。

    すべてのメソッドは FastAPI が動かす asyncio イベントループ内で呼ばれるため、
    asyncio.Lock でアクセスを直列化する。
    """

    def __init__(self) -> None:
        self._active: set[WebSocket] = set()
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # 接続管理
    # ------------------------------------------------------------------ #

    async def connect(self, ws: WebSocket) -> None:
        """WebSocket ハンドシェイクを完了し、接続を登録する。"""
        await ws.accept()
        async with self._lock:
            self._active.add(ws)
        logger.info("WS: client connected  (total=%d)", len(self._active))

    async def disconnect(self, ws: WebSocket) -> None:
        """接続を登録から外す。既に外れていても安全。"""
        async with self._lock:
            self._active.discard(ws)
        logger.info("WS: client disconnected (total=%d)", len(self._active))

    # ------------------------------------------------------------------ #
    # 送信
    # ------------------------------------------------------------------ #

    async def broadcast_json(self, data: dict[str, Any]) -> None:
        """接続中の全クライアントに JSON データを送信する。送信失敗した接続は切断扱い。"""
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
        """特定クライアントに JSON データを送信する。失敗したら接続を切断扱い。"""
        try:
            await ws.send_json(data)
        except Exception:
            await self.disconnect(ws)

    # ------------------------------------------------------------------ #
    # 状態参照
    # ------------------------------------------------------------------ #

    @property
    def connection_count(self) -> int:
        """現在の接続数。"""
        return len(self._active)
