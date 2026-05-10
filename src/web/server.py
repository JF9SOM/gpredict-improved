"""
uvicorn サーバー起動・停止ヘルパー

Qt6 バックグラウンドスレッドから呼び出し、asyncio イベントループごと
uvicorn を別スレッドで動かす。メインスレッド（Qt6 UI）はブロックしない。
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
from typing import Any

import uvicorn

logger = logging.getLogger(__name__)


def get_lan_ip() -> str:
    """
    LAN 内 IP アドレスを返す。

    UDP ソケットを外部に向けて接続することで、OS に使用するインターフェースを
    選ばせる。実際にはデータを送信しない。
    ネットワーク未接続の場合は "127.0.0.1" を返す。
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return str(s.getsockname()[0])
    except OSError:
        return "127.0.0.1"


class WebServer:
    """
    uvicorn を バックグラウンドスレッドで起動・停止するヘルパークラス。

    Qt6 の QThread または通常の threading.Thread から使う。

    使い方::

        server = WebServer(app, port=8080)
        url = server.start()  # → "http://192.168.1.10:8080"
        # ... アプリ使用 ...
        server.stop()
    """

    def __init__(
        self,
        app: Any,
        host: str = "0.0.0.0",
        port: int = 8080,
        log_level: str = "warning",
    ) -> None:
        """
        Args:
            app:       FastAPI (ASGI) アプリケーション
            host:      バインドアドレス（デフォルト全 IF）
            port:      ポート番号（デフォルト 8080）
            log_level: uvicorn ログレベル
        """
        self._app = app
        self._host = host
        self._port = port
        self._log_level = log_level
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    # 公開 API
    # ------------------------------------------------------------------ #

    def start(self) -> str:
        """
        サーバーをバックグラウンドスレッドで起動する。

        Returns:
            LAN 内アクセス URL（例: "http://192.168.1.10:8080"）
        """
        if self._thread is not None and self._thread.is_alive():
            logger.warning("WebServer.start() called while already running — ignored")
            return self._access_url()

        config = uvicorn.Config(
            self._app,
            host=self._host,
            port=self._port,
            log_level=self._log_level,
            loop="asyncio",
        )
        self._server = uvicorn.Server(config)

        self._thread = threading.Thread(
            target=self._run,
            name="uvicorn-web-server",
            daemon=True,
        )
        self._thread.start()
        logger.info("WebServer: started on %s", self._access_url())
        return self._access_url()

    def stop(self, timeout: float = 5.0) -> None:
        """
        サーバーを停止してスレッドが終了するのを待つ。

        Args:
            timeout: スレッド終了待ちのタイムアウト秒数
        """
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        self._server = None
        logger.info("WebServer: stopped")

    @property
    def is_running(self) -> bool:
        """サーバーが起動中かどうか。"""
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        """スレッドのエントリーポイント。asyncio ループを生成して uvicorn を実行する。"""
        assert self._server is not None
        asyncio.run(self._server.serve())

    def _access_url(self) -> str:
        ip = get_lan_ip()
        return f"http://{ip}:{self._port}"
