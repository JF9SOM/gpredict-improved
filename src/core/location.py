"""
自局位置の自動取得モジュール

優先順位:
    1. GPS デバイス（gpsd デーモン経由 / python-gps）
    2. ブラウザ Geolocation API（POST /api/location/browser 経由で事前設定）
    3. IP ジオロケーション（ip-api.com）
    4. 手動入力（緯度・経度・標高 または Maidenhead グリッドロケーター）

取得した座標は SQLite の app_settings に保存して次回起動時に再利用する。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

_IP_API_URL = "http://ip-api.com/json/?fields=status,lat,lon,city,country"
_GPSD_HOST = "localhost"
_GPSD_PORT = 2947
_GPSD_MAX_PACKETS = 20


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------


class LocationSource(StrEnum):
    """位置情報の取得元"""

    GPS = "GPS"
    BROWSER = "Browser"
    IP = "IP"
    MANUAL = "Manual"


@dataclass
class Location:
    """
    自局位置情報。

    Attributes:
        latitude_deg:  緯度（度、北緯正）
        longitude_deg: 経度（度、東経正）
        elevation_m:   標高（m）
        source:        取得元
        accuracy_m:    精度（m）。不明な場合は None
        city:          都市名（IP ジオロケーション時に設定）
        country:       国名（IP ジオロケーション時に設定）
    """

    latitude_deg: float
    longitude_deg: float
    elevation_m: float
    source: LocationSource
    accuracy_m: float | None = None
    city: str = field(default="")
    country: str = field(default="")


# ---------------------------------------------------------------------------
# Maidenhead グリッドロケーター変換
# ---------------------------------------------------------------------------


def grid_to_latlon(grid: str) -> tuple[float, float]:
    """
    Maidenhead グリッドロケーターを緯度・経度に変換する。

    Args:
        grid: グリッドロケーター文字列（4 または 6 文字。例: "PM85", "PM85ib"）

    Returns:
        (latitude_deg, longitude_deg) のタプル。北緯・東経が正。

    Raises:
        ValueError: フォーマットが不正な場合
    """
    g = grid.upper().strip()
    if len(g) not in (4, 6):
        raise ValueError(f"グリッドロケーターは 4 または 6 文字で指定してください: {grid!r}")

    if not (g[0].isalpha() and g[1].isalpha()):
        raise ValueError(f"不正なグリッドロケーター（フィールド文字が英字でない）: {grid!r}")

    f0 = ord(g[0]) - ord("A")
    f1 = ord(g[1]) - ord("A")
    if f0 > 17 or f1 > 17:
        raise ValueError(f"フィールド文字が範囲外（A–R のみ有効）: {grid!r}")

    if not (g[2].isdigit() and g[3].isdigit()):
        raise ValueError(f"不正なグリッドロケーター（スクエアが数字でない）: {grid!r}")

    s0 = int(g[2])
    s1 = int(g[3])

    lon = f0 * 20.0 - 180.0 + s0 * 2.0
    lat = f1 * 10.0 - 90.0 + s1 * 1.0

    if len(g) == 6:
        if not (g[4].isalpha() and g[5].isalpha()):
            raise ValueError(f"不正なグリッドロケーター（サブスクエアが英字でない）: {grid!r}")
        ss0 = ord(g[4]) - ord("A")
        ss1 = ord(g[5]) - ord("A")
        if ss0 > 23 or ss1 > 23:
            raise ValueError(f"サブスクエア文字が範囲外（A–X のみ有効）: {grid!r}")
        # サブスクエア解像度: 経度 5′、緯度 2.5′
        lon += ss0 * (5.0 / 60.0) + (2.5 / 60.0)   # + 中心オフセット
        lat += ss1 * (2.5 / 60.0) + (1.25 / 60.0)
    else:
        # スクエア中心
        lon += 1.0
        lat += 0.5

    return lat, lon


# ---------------------------------------------------------------------------
# LocationManager
# ---------------------------------------------------------------------------


class LocationManager:
    """
    自局位置情報の取得・保存・管理を行うクラス。

    取得優先順位: GPS → キャッシュ（Browser/Manual） → IP
    取得した位置は SQLite app_settings に永続化して次回起動時に再利用する。
    """

    _SETTINGS_KEY = "observer_location"

    def __init__(
        self,
        conn: sqlite3.Connection,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._conn = conn
        self._http = http_client or httpx.AsyncClient(timeout=5.0)
        self._current: Location | None = None

    # ------------------------------------------------------------------ #
    # プロパティ
    # ------------------------------------------------------------------ #

    @property
    def current(self) -> Location | None:
        """現在の位置情報（未取得の場合は None）。"""
        return self._current

    @property
    def status_text(self) -> str:
        """
        ステータスバー表示用テキストを返す。

        例: "QTH: 35.6895°N 139.6917°E (GPS)"
        """
        loc = self._current
        if loc is None:
            return "QTH: 未設定"
        ns = "N" if loc.latitude_deg >= 0 else "S"
        ew = "E" if loc.longitude_deg >= 0 else "W"
        lat = abs(loc.latitude_deg)
        lon = abs(loc.longitude_deg)
        return f"QTH: {lat:.4f}°{ns} {lon:.4f}°{ew} ({loc.source.value})"

    # ------------------------------------------------------------------ #
    # 公開 API — 同期（設定・保存）
    # ------------------------------------------------------------------ #

    def from_manual(
        self,
        latitude_deg: float,
        longitude_deg: float,
        elevation_m: float = 0.0,
    ) -> Location:
        """
        手動入力で位置を設定して保存する。

        Args:
            latitude_deg:  緯度（度、北緯正）
            longitude_deg: 経度（度、東経正）
            elevation_m:   標高（m）

        Returns:
            設定した Location
        """
        loc = Location(
            latitude_deg=latitude_deg,
            longitude_deg=longitude_deg,
            elevation_m=elevation_m,
            source=LocationSource.MANUAL,
        )
        self._current = loc
        self.save(loc)
        return loc

    def from_grid(self, grid: str, elevation_m: float = 0.0) -> Location:
        """
        Maidenhead グリッドロケーターから位置を設定して保存する。

        Args:
            grid:        グリッドロケーター文字列（例: "PM85"）
            elevation_m: 標高（m）

        Returns:
            設定した Location

        Raises:
            ValueError: フォーマットが不正な場合
        """
        lat, lon = grid_to_latlon(grid)
        loc = Location(
            latitude_deg=lat,
            longitude_deg=lon,
            elevation_m=elevation_m,
            source=LocationSource.MANUAL,
        )
        self._current = loc
        self.save(loc)
        return loc

    def set_browser_location(
        self,
        latitude_deg: float,
        longitude_deg: float,
        accuracy_m: float | None = None,
        elevation_m: float = 0.0,
    ) -> Location:
        """
        ブラウザ Geolocation API から受け取った座標を設定して保存する。

        Returns:
            設定した Location
        """
        loc = Location(
            latitude_deg=latitude_deg,
            longitude_deg=longitude_deg,
            elevation_m=elevation_m,
            source=LocationSource.BROWSER,
            accuracy_m=accuracy_m,
        )
        self._current = loc
        self.save(loc)
        return loc

    def save(self, loc: Location) -> None:
        """位置情報を app_settings テーブルに永続化する。"""
        data: dict[str, Any] = {
            "latitude_deg": loc.latitude_deg,
            "longitude_deg": loc.longitude_deg,
            "elevation_m": loc.elevation_m,
            "source": loc.source.value,
            "accuracy_m": loc.accuracy_m,
            "city": loc.city,
            "country": loc.country,
        }
        self._conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = excluded.updated_at""",
            (self._SETTINGS_KEY, json.dumps(data)),
        )
        self._conn.commit()

    def load_saved(self) -> Location | None:
        """
        app_settings から保存済み位置情報を読み込む。

        Returns:
            保存されている Location。未保存または読み込み失敗の場合は None。
        """
        row = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (self._SETTINGS_KEY,),
        ).fetchone()
        if row is None:
            return None
        try:
            data: dict[str, Any] = json.loads(row[0])
            loc = Location(
                latitude_deg=float(data["latitude_deg"]),
                longitude_deg=float(data["longitude_deg"]),
                elevation_m=float(data.get("elevation_m", 0.0)),
                source=LocationSource(data.get("source", "Manual")),
                accuracy_m=data.get("accuracy_m"),
                city=str(data.get("city", "")),
                country=str(data.get("country", "")),
            )
            self._current = loc
            return loc
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("保存済み位置情報の読み込みに失敗: %s", exc)
            return None

    # ------------------------------------------------------------------ #
    # 公開 API — 非同期（ネットワーク取得）
    # ------------------------------------------------------------------ #

    async def from_gps(self) -> Location | None:
        """
        gpsd デーモン経由で GPS 座標を取得する。

        gpsd が起動していないか python-gps が未インストールの場合は None を返す。
        ブロッキング I/O を asyncio.to_thread に委譲する。

        Returns:
            取得した Location。取得できない場合は None。
        """
        return await asyncio.to_thread(self._from_gps_sync)

    def _from_gps_sync(self) -> Location | None:
        """gpsd からの GPS 取得（同期・ブロッキング）。"""
        try:
            import gps as gpsd_lib
        except ImportError:
            logger.debug("python-gps が未インストールのため GPS 取得をスキップ")
            return None

        try:
            session = gpsd_lib.gps(
                host=_GPSD_HOST,
                port=_GPSD_PORT,
                mode=gpsd_lib.WATCH_ENABLE | gpsd_lib.WATCH_NEWSTYLE,
            )
            for _ in range(_GPSD_MAX_PACKETS):
                report = session.next()
                if report.get("class") == "TPV":
                    lat = report.get("lat")
                    lon = report.get("lon")
                    alt = report.get("alt", 0.0)
                    if lat is not None and lon is not None:
                        loc = Location(
                            latitude_deg=float(lat),
                            longitude_deg=float(lon),
                            elevation_m=float(alt or 0.0),
                            source=LocationSource.GPS,
                        )
                        self._current = loc
                        self.save(loc)
                        return loc
        except Exception as exc:
            logger.debug("GPS 取得失敗: %s", exc)
        return None

    async def from_ip(self) -> Location | None:
        """
        ip-api.com を使って IP ジオロケーションで位置を取得する（都市レベル精度）。

        オフライン時や API 障害時は None を返す。

        Returns:
            取得した Location。取得できない場合は None。
        """
        try:
            resp = await self._http.get(_IP_API_URL)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            if data.get("status") != "success":
                logger.warning("IP ジオロケーション失敗: %s", data)
                return None
            loc = Location(
                latitude_deg=float(data["lat"]),
                longitude_deg=float(data["lon"]),
                elevation_m=0.0,
                source=LocationSource.IP,
                city=str(data.get("city", "")),
                country=str(data.get("country", "")),
            )
            self._current = loc
            self.save(loc)
            return loc
        except Exception as exc:
            logger.warning("IP ジオロケーション例外: %s", exc)
            return None

    async def detect(self) -> Location | None:
        """
        優先順位に従って自動で位置を取得する。

        優先順位:
            1. GPS（gpsd）
            2. キャッシュ済み位置（Browser/Manual 含む）
            3. 保存済み位置（DB から読み込み）
            4. IP ジオロケーション

        Returns:
            取得した Location。すべて失敗した場合は None。
        """
        gps_loc = await self.from_gps()
        if gps_loc is not None:
            return gps_loc

        if self._current is not None:
            return self._current

        saved = self.load_saved()
        if saved is not None:
            return saved

        return await self.from_ip()
