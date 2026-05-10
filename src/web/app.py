"""
FastAPI アプリケーション

create_app() で依存オブジェクトを受け取り、設定済み FastAPI インスタンスを返す。
テスト時はインメモリ DB と None エンジンで生成できる。

エンドポイント一覧:
    GET  /api/satellites                    — 衛星一覧
    GET  /api/satellites/{norad}/transmitters — トランスポンダ一覧
    GET  /api/satellites/{norad}/passes     — パス予測
    GET  /api/tle/status                    — TLE 品質一覧
    GET  /api/status                        — サーバー状態・バージョン
    WS   /ws/tracking?norad=XXXXX          — リアルタイム追尾データ
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from core.engine import PassPredictor, SatelliteEngine
from core.location import Location, LocationManager
from data.tle_manager import TLEManager
from web.websocket import ConnectionManager

logger = logging.getLogger(__name__)

APP_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Pydantic レスポンスモデル
# ---------------------------------------------------------------------------


class SatelliteOut(BaseModel):
    """衛星基本情報レスポンス"""

    norad_cat_id: int
    name: str
    alt_names: list[str]
    status: str
    updated_at: str | None


class TransmitterOut(BaseModel):
    """トランスポンダ情報レスポンス"""

    uuid: str
    norad_cat_id: int
    description: str
    type: str | None
    downlink_low: int | None
    downlink_high: int | None
    uplink_low: int | None
    uplink_high: int | None
    mode: str | None
    invert: bool
    baud: int | None
    ctcss_tone: float | None
    ctcss_tone_type: str | None
    alive: bool
    source: str
    manual_override: bool
    notes: str


class PassOut(BaseModel):
    """パス予測レスポンス（時刻は ISO 8601 UTC 文字列）"""

    norad_cat_id: int
    aos: str
    tca: str
    los: str
    max_elevation_deg: float
    max_elevation_time: str  # TCA と同値（API 利便性のための別名）
    aos_azimuth_deg: float
    los_azimuth_deg: float
    duration_s: float
    duration_seconds: float  # duration_s と同値（フロントエンド利便性のための別名）
    quality: str  # "excellent" | "good" | "fair" | "low"


class TLEStatusOut(BaseModel):
    """TLE 品質情報レスポンス"""

    norad_cat_id: int
    name: str
    quality_score: str | None
    epoch: str | None
    fetched_at: str | None
    source: str | None


class ServerStatusOut(BaseModel):
    """サーバー状態レスポンス"""

    version: str
    status: str
    satellite_count: int
    tle_count: int
    uptime_s: float


class BrowserLocationIn(BaseModel):
    """ブラウザ Geolocation API からの位置情報リクエスト"""

    latitude: float
    longitude: float
    accuracy_m: float | None = None
    elevation_m: float = 0.0


class LocationOut(BaseModel):
    """自局位置情報レスポンス"""

    latitude_deg: float
    longitude_deg: float
    elevation_m: float
    source: str
    accuracy_m: float | None
    city: str
    country: str
    status_text: str


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------


def pass_quality(max_elevation_deg: float) -> str:
    """
    最大仰角からパスの品質ランクを返す。

    Returns:
        "excellent" (>=60°) / "good" (>=30°) / "fair" (>=10°) / "low" (<10°)
    """
    if max_elevation_deg >= 60.0:
        return "excellent"
    if max_elevation_deg >= 30.0:
        return "good"
    if max_elevation_deg >= 10.0:
        return "fair"
    return "low"


def _parse_alt_names(raw: Any) -> list[str]:
    """alt_names カラム（JSON 文字列 or None）を list[str] に変換する。"""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        parsed = json.loads(raw)
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def _build_tracking_payload(norad: int, engine: SatelliteEngine | None) -> dict[str, Any]:
    """WebSocket に送る追尾データ辞書を生成する。"""
    if engine is None:
        return {"norad": norad, "error": "engine not available"}
    obs = engine.observe(norad)
    if obs is None:
        return {"norad": norad, "error": "no TLE data"}
    return {
        "norad": norad,
        "timestamp": obs.timestamp.isoformat(),
        "elevation_deg": round(obs.elevation_deg, 4),
        "azimuth_deg": round(obs.azimuth_deg, 4),
        "range_km": round(obs.range_km, 3),
        "range_rate_km_s": round(obs.range_rate_km_s, 6),
        "is_above_horizon": obs.is_above_horizon,
    }


# ---------------------------------------------------------------------------
# アプリファクトリー
# ---------------------------------------------------------------------------


def _location_to_out(loc: Location, mgr: LocationManager) -> LocationOut:
    """Location オブジェクトを LocationOut レスポンスモデルに変換する。"""
    return LocationOut(
        latitude_deg=loc.latitude_deg,
        longitude_deg=loc.longitude_deg,
        elevation_m=loc.elevation_m,
        source=loc.source.value,
        accuracy_m=loc.accuracy_m,
        city=loc.city,
        country=loc.country,
        status_text=mgr.status_text,
    )


def create_app(
    conn: sqlite3.Connection,
    tle_manager: TLEManager,
    pass_predictor: PassPredictor | None = None,
    engine: SatelliteEngine | None = None,
    start_time: datetime | None = None,
    location_manager: LocationManager | None = None,
) -> FastAPI:
    """
    FastAPI アプリケーションを生成して返す。

    Args:
        conn:             SQLite 接続（衛星・トランスポンダ・TLE クエリ用）
        tle_manager:      TLE マネージャー（品質一覧取得用）
        pass_predictor:   パス予測器。None の場合はパス予測エンドポイントが空リストを返す
        engine:           衛星エンジン。None の場合は WebSocket がエラーを返す
        start_time:       アップタイム計算の起点。None なら現在時刻
        location_manager: 位置情報マネージャー。None の場合は位置エンドポイントが 503 を返す

    Returns:
        設定済み FastAPI インスタンス
    """
    _start = start_time or datetime.now(UTC)
    manager = ConnectionManager()

    app = FastAPI(
        title="GPredict-Improved API",
        version=APP_VERSION,
        description="衛星追尾ソフトウェア GPredict-Improved の REST / WebSocket API",
    )

    # ------------------------------------------------------------------ #
    # 依存関数（クロージャでキャプチャ）
    # ------------------------------------------------------------------ #

    def get_conn() -> sqlite3.Connection:
        """SQLite 接続を返す依存関数。"""
        return conn

    def get_tle_manager() -> TLEManager:
        """TLEManager を返す依存関数。"""
        return tle_manager

    # ------------------------------------------------------------------ #
    # REST エンドポイント
    # ------------------------------------------------------------------ #

    @app.get("/api/satellites", response_model=list[SatelliteOut])
    async def list_satellites(
        db: sqlite3.Connection = Depends(get_conn),
    ) -> list[SatelliteOut]:
        """衛星一覧を名前順で返す。"""
        rows = db.execute(
            "SELECT norad_cat_id, name, alt_names, status, updated_at FROM satellites ORDER BY name"
        ).fetchall()
        return [
            SatelliteOut(
                norad_cat_id=row["norad_cat_id"],
                name=row["name"],
                alt_names=_parse_alt_names(row["alt_names"]),
                status=row["status"] or "unknown",
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    @app.get(
        "/api/satellites/{norad}/transmitters",
        response_model=list[TransmitterOut],
    )
    async def list_transmitters(
        norad: int,
        db: sqlite3.Connection = Depends(get_conn),
    ) -> list[TransmitterOut]:
        """指定衛星のトランスポンダ一覧を返す。衛星が存在しなければ 404。"""
        if (
            db.execute("SELECT 1 FROM satellites WHERE norad_cat_id = ?", (norad,)).fetchone()
            is None
        ):
            raise HTTPException(status_code=404, detail=f"Satellite {norad} not found")

        rows = db.execute(
            "SELECT * FROM transmitters WHERE norad_cat_id = ? ORDER BY description",
            (norad,),
        ).fetchall()
        return [
            TransmitterOut(
                uuid=row["uuid"],
                norad_cat_id=row["norad_cat_id"],
                description=row["description"],
                type=row["type"],
                downlink_low=row["downlink_low"],
                downlink_high=row["downlink_high"],
                uplink_low=row["uplink_low"],
                uplink_high=row["uplink_high"],
                mode=row["mode"],
                invert=bool(row["invert"]),
                baud=row["baud"],
                ctcss_tone=row["ctcss_tone"],
                ctcss_tone_type=row["ctcss_tone_type"],
                alive=bool(row["alive"]),
                source=row["source"] or "satnogs",
                manual_override=bool(row["manual_override"]),
                notes=row["notes"] or "",
            )
            for row in rows
        ]

    @app.get("/api/satellites/{norad}/passes", response_model=list[PassOut])
    async def get_passes(
        norad: int,
        hours: float = Query(default=24.0, gt=0, le=168, description="予測時間幅（時間）"),
        min_el: float = Query(default=5.0, ge=0, le=90, description="最低仰角（度）"),
        db: sqlite3.Connection = Depends(get_conn),
    ) -> list[PassOut]:
        """
        指定衛星のパス予測を返す。

        pass_predictor が None の場合は常に空リストを返す（エンジンなし状態）。
        """
        if (
            db.execute("SELECT 1 FROM satellites WHERE norad_cat_id = ?", (norad,)).fetchone()
            is None
        ):
            raise HTTPException(status_code=404, detail=f"Satellite {norad} not found")

        if pass_predictor is None:
            return []

        now = datetime.now(UTC)
        passes = pass_predictor.get_passes(
            norad, now, now + timedelta(hours=hours), min_elevation_deg=min_el
        )
        return [
            PassOut(
                norad_cat_id=p.norad_cat_id,
                aos=p.aos.isoformat(),
                tca=p.tca.isoformat(),
                los=p.los.isoformat(),
                max_elevation_deg=p.max_elevation_deg,
                max_elevation_time=p.tca.isoformat(),
                aos_azimuth_deg=p.aos_azimuth_deg,
                los_azimuth_deg=p.los_azimuth_deg,
                duration_s=p.duration_s,
                duration_seconds=p.duration_s,
                quality=pass_quality(p.max_elevation_deg),
            )
            for p in passes
        ]

    @app.get("/api/tle/status", response_model=list[TLEStatusOut])
    async def tle_status(
        tm: TLEManager = Depends(get_tle_manager),
    ) -> list[TLEStatusOut]:
        """全衛星の TLE 品質一覧を返す。品質スコア昇順（poor が先）。"""
        rows = tm.get_all_quality_status()
        return [
            TLEStatusOut(
                norad_cat_id=r["norad_cat_id"],
                name=r["name"],
                quality_score=r.get("quality_score"),
                epoch=r.get("epoch"),
                fetched_at=r.get("fetched_at"),
                source=r.get("source"),
            )
            for r in rows
        ]

    @app.get("/api/status", response_model=ServerStatusOut)
    async def server_status(
        db: sqlite3.Connection = Depends(get_conn),
    ) -> ServerStatusOut:
        """サーバー状態・バージョン・DB 件数・アップタイムを返す。"""
        sat_count: int = db.execute("SELECT COUNT(*) FROM satellites").fetchone()[0]
        tle_count: int = db.execute("SELECT COUNT(*) FROM tle_data").fetchone()[0]
        return ServerStatusOut(
            version=APP_VERSION,
            status="ok",
            satellite_count=sat_count,
            tle_count=tle_count,
            uptime_s=(datetime.now(UTC) - _start).total_seconds(),
        )

    @app.get("/api/location", response_model=LocationOut)
    async def get_location() -> LocationOut:
        """現在の自局位置情報を返す。位置が未設定の場合は 503 を返す。"""
        if location_manager is None:
            raise HTTPException(status_code=503, detail="location manager not configured")
        loc = location_manager.current or location_manager.load_saved()
        if loc is None:
            raise HTTPException(status_code=404, detail="location not set")
        return _location_to_out(loc, location_manager)

    @app.post("/api/location/browser", response_model=LocationOut)
    async def post_browser_location(body: BrowserLocationIn) -> LocationOut:
        """
        ブラウザ Geolocation API から受け取った座標を保存する。

        フロントエンドの navigator.geolocation.getCurrentPosition() で取得した
        座標をこのエンドポイントに POST することで自局位置を設定できる。
        """
        if location_manager is None:
            raise HTTPException(status_code=503, detail="location manager not configured")
        loc = location_manager.set_browser_location(
            latitude_deg=body.latitude,
            longitude_deg=body.longitude,
            accuracy_m=body.accuracy_m,
            elevation_m=body.elevation_m,
        )
        return _location_to_out(loc, location_manager)

    # ------------------------------------------------------------------ #
    # WebSocket — /ws/tracking
    # ------------------------------------------------------------------ #

    @app.websocket("/ws/tracking")
    async def ws_tracking(
        websocket: WebSocket,
        norad: int = Query(default=25544, description="追尾する衛星の NORAD カタログ番号"),
    ) -> None:
        """
        衛星追尾 WebSocket エンドポイント。

        接続後、1 秒ごとに仰角・方位角・距離・視線速度を JSON で送信する。
        エンジンが未設定または TLE がない場合はエラーペイロードを送信する。

        送信ペイロード例::

            {
                "norad": 25544,
                "timestamp": "2026-05-10T12:00:00+00:00",
                "elevation_deg": 45.12,
                "azimuth_deg": 180.34,
                "range_km": 412.5,
                "range_rate_km_s": -2.134567,
                "is_above_horizon": true
            }
        """
        await manager.connect(websocket)
        try:
            while True:
                await asyncio.sleep(1.0)
                payload = _build_tracking_payload(norad, engine)
                await manager.send_json(websocket, payload)
        except WebSocketDisconnect:
            await manager.disconnect(websocket)

    return app
