"""
自局位置モジュールのテスト

- grid_to_latlon()   — Maidenhead グリッドロケーター変換
- Location           — データクラス
- LocationManager    — 手動設定・保存・読み込み・ブラウザ位置・IP ジオロケーション
- Web API            — /api/location, POST /api/location/browser
"""

from __future__ import annotations

import json
import sqlite3

import pytest
import respx
from httpx import Response

from core.location import Location, LocationManager, LocationSource, grid_to_latlon
from data.database import SCHEMA_SQL

# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> sqlite3.Connection:
    """インメモリ SQLite DB（スキーマ初期化済み）。"""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


@pytest.fixture()
def manager(db: sqlite3.Connection) -> LocationManager:
    """テスト用 LocationManager（インメモリ DB）。"""
    return LocationManager(db)


# ---------------------------------------------------------------------------
# grid_to_latlon() のテスト
# ---------------------------------------------------------------------------


class TestGridToLatlon:
    def test_pm85_latitude(self) -> None:
        """PM85 の緯度が北緯 35 台であることを確認する。"""
        lat, _ = grid_to_latlon("PM85")
        assert 35.0 <= lat < 36.0

    def test_pm85_longitude(self) -> None:
        """PM85 の経度が東経 137 台であることを確認する。"""
        _, lon = grid_to_latlon("PM85")
        assert 137.0 <= lon < 139.0

    def test_fn31_new_york(self) -> None:
        """FN31 はニューヨーク付近（北緯 41.5°、西経 73.0°）。"""
        lat, lon = grid_to_latlon("FN31")
        assert 40.0 < lat < 43.0
        assert -75.0 < lon <= -73.0

    def test_jn45_europe(self) -> None:
        """JN45 はヨーロッパ（北緯 45°、東経 10°付近）。"""
        lat, lon = grid_to_latlon("JN45")
        assert 44.0 < lat < 46.0
        assert 8.0 < lon < 12.0

    def test_aa00_south_pole_region(self) -> None:
        """AA00 は南西端（南緯 89.5°、西経 179.0°）。"""
        lat, lon = grid_to_latlon("AA00")
        assert lat < -89.0
        assert lon <= -179.0

    def test_rr99_north_east_max(self) -> None:
        """RR99 は北東端付近（北緯 89.5°、東経 179.0°）。"""
        lat, lon = grid_to_latlon("RR99")
        assert lat > 89.0
        assert lon >= 179.0

    def test_6char_subsquare(self) -> None:
        """6 文字グリッドは 4 文字より高精度な座標を返す。"""
        lat4, lon4 = grid_to_latlon("PM85")
        lat6, lon6 = grid_to_latlon("PM85ib")
        # 6 文字の結果は 4 文字のスクエア内に収まる
        assert abs(lat6 - lat4) < 1.5
        assert abs(lon6 - lon4) < 3.0

    def test_case_insensitive(self) -> None:
        """大文字・小文字を区別しない。"""
        assert grid_to_latlon("PM85") == grid_to_latlon("pm85")

    def test_whitespace_stripped(self) -> None:
        """前後の空白を無視する。"""
        assert grid_to_latlon("PM85") == grid_to_latlon("  PM85  ")

    def test_returns_tuple(self) -> None:
        result = grid_to_latlon("PM85")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_invalid_length_raises(self) -> None:
        with pytest.raises(ValueError, match="4 または 6 文字"):
            grid_to_latlon("PM8")

    def test_invalid_field_char_raises(self) -> None:
        with pytest.raises(ValueError):
            grid_to_latlon("SM85")  # S はフィールド文字として無効（A-R のみ）

    def test_invalid_square_raises(self) -> None:
        with pytest.raises(ValueError):
            grid_to_latlon("PMAB")  # スクエアが数字でない

    def test_invalid_subsquare_raises(self) -> None:
        with pytest.raises(ValueError):
            grid_to_latlon("PM8599")  # サブスクエアが数字


# ---------------------------------------------------------------------------
# Location データクラスのテスト
# ---------------------------------------------------------------------------


class TestLocation:
    def test_creation(self) -> None:
        loc = Location(
            latitude_deg=35.6895,
            longitude_deg=139.6917,
            elevation_m=40.0,
            source=LocationSource.MANUAL,
        )
        assert loc.latitude_deg == 35.6895
        assert loc.source == LocationSource.MANUAL

    def test_default_accuracy_is_none(self) -> None:
        loc = Location(35.0, 139.0, 0.0, LocationSource.GPS)
        assert loc.accuracy_m is None

    def test_default_city_country_empty(self) -> None:
        loc = Location(35.0, 139.0, 0.0, LocationSource.IP)
        assert loc.city == ""
        assert loc.country == ""

    def test_source_enum_values(self) -> None:
        assert LocationSource.GPS.value == "GPS"
        assert LocationSource.BROWSER.value == "Browser"
        assert LocationSource.IP.value == "IP"
        assert LocationSource.MANUAL.value == "Manual"


# ---------------------------------------------------------------------------
# LocationManager の同期機能テスト
# ---------------------------------------------------------------------------


class TestLocationManagerSync:
    def test_from_manual_sets_current(self, manager: LocationManager) -> None:
        loc = manager.from_manual(35.6895, 139.6917, 40.0)
        assert manager.current is not None
        assert manager.current.latitude_deg == pytest.approx(35.6895)
        assert loc.source == LocationSource.MANUAL

    def test_from_manual_saves_to_db(
        self, manager: LocationManager, db: sqlite3.Connection
    ) -> None:
        manager.from_manual(35.0, 139.0)
        row = db.execute(
            "SELECT value FROM app_settings WHERE key = 'observer_location'"
        ).fetchone()
        assert row is not None
        data = json.loads(row[0])
        assert data["latitude_deg"] == pytest.approx(35.0)

    def test_from_grid(self, manager: LocationManager) -> None:
        loc = manager.from_grid("PM85")
        assert loc.source == LocationSource.MANUAL
        assert 35.0 <= loc.latitude_deg < 36.0

    def test_from_grid_invalid_raises(self, manager: LocationManager) -> None:
        with pytest.raises(ValueError):
            manager.from_grid("INVALID")

    def test_set_browser_location(self, manager: LocationManager) -> None:
        loc = manager.set_browser_location(35.5, 139.5, accuracy_m=15.0)
        assert loc.source == LocationSource.BROWSER
        assert loc.accuracy_m == pytest.approx(15.0)
        assert manager.current is not None
        assert manager.current.source == LocationSource.BROWSER

    def test_save_and_load(self, manager: LocationManager, db: sqlite3.Connection) -> None:
        manager.from_manual(35.6895, 139.6917, 40.0)
        # 新しいマネージャーでロード
        mgr2 = LocationManager(db)
        loaded = mgr2.load_saved()
        assert loaded is not None
        assert loaded.latitude_deg == pytest.approx(35.6895)
        assert loaded.elevation_m == pytest.approx(40.0)
        assert loaded.source == LocationSource.MANUAL

    def test_load_saved_none_when_empty(self, manager: LocationManager) -> None:
        assert manager.load_saved() is None

    def test_load_saved_sets_current(
        self, manager: LocationManager, db: sqlite3.Connection
    ) -> None:
        manager.from_manual(35.0, 139.0)
        mgr2 = LocationManager(db)
        mgr2.load_saved()
        assert mgr2.current is not None

    def test_status_text_not_set(self, manager: LocationManager) -> None:
        assert manager.status_text == "QTH: 未設定"

    def test_status_text_north_east(self, manager: LocationManager) -> None:
        manager.from_manual(35.6895, 139.6917)
        text = manager.status_text
        assert "N" in text
        assert "E" in text
        assert "GPS" not in text
        assert "Manual" in text

    def test_status_text_south_west(self, manager: LocationManager) -> None:
        manager.from_manual(-33.8688, -70.6693)  # サンティアゴ
        text = manager.status_text
        assert "S" in text
        assert "W" in text

    def test_overwrite_saves_latest(self, manager: LocationManager, db: sqlite3.Connection) -> None:
        manager.from_manual(35.0, 139.0)
        manager.from_manual(36.0, 140.0)
        mgr2 = LocationManager(db)
        loaded = mgr2.load_saved()
        assert loaded is not None
        assert loaded.latitude_deg == pytest.approx(36.0)

    def test_current_is_none_initially(self, manager: LocationManager) -> None:
        assert manager.current is None


# ---------------------------------------------------------------------------
# from_ip() のテスト（respx でモック）
# ---------------------------------------------------------------------------


class TestLocationManagerIpGeo:
    @pytest.mark.asyncio()
    @respx.mock
    async def test_from_ip_success(self, manager: LocationManager) -> None:
        respx.get("http://ip-api.com/json/").mock(
            return_value=Response(
                200,
                json={
                    "status": "success",
                    "lat": 35.6895,
                    "lon": 139.6917,
                    "city": "Tokyo",
                    "country": "Japan",
                },
            )
        )
        loc = await manager.from_ip()
        assert loc is not None
        assert loc.source == LocationSource.IP
        assert loc.latitude_deg == pytest.approx(35.6895)
        assert loc.city == "Tokyo"
        assert loc.country == "Japan"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_from_ip_api_failure(self, manager: LocationManager) -> None:
        respx.get("http://ip-api.com/json/").mock(
            return_value=Response(200, json={"status": "fail", "message": "private range"})
        )
        loc = await manager.from_ip()
        assert loc is None

    @pytest.mark.asyncio()
    @respx.mock
    async def test_from_ip_network_error(self, manager: LocationManager) -> None:
        respx.get("http://ip-api.com/json/").mock(side_effect=Exception("connection refused"))
        loc = await manager.from_ip()
        assert loc is None

    @pytest.mark.asyncio()
    @respx.mock
    async def test_from_ip_saves_to_db(
        self, manager: LocationManager, db: sqlite3.Connection
    ) -> None:
        respx.get("http://ip-api.com/json/").mock(
            return_value=Response(
                200,
                json={"status": "success", "lat": 35.0, "lon": 139.0, "city": "", "country": ""},
            )
        )
        await manager.from_ip()
        row = db.execute(
            "SELECT value FROM app_settings WHERE key = 'observer_location'"
        ).fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# detect() のテスト
# ---------------------------------------------------------------------------


class TestLocationManagerDetect:
    @pytest.mark.asyncio()
    @respx.mock
    async def test_detect_uses_cached_before_ip(self, manager: LocationManager) -> None:
        """キャッシュがあれば IP ジオロケーションを呼ばない。"""
        manager.from_manual(35.0, 139.0)
        # IP API は呼ばれないはず（呼ばれると respx が例外を投げる）
        respx.get("http://ip-api.com/json/").mock(side_effect=Exception("should not be called"))
        loc = await manager.detect()
        assert loc is not None
        assert loc.source == LocationSource.MANUAL

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detect_falls_back_to_ip(self, manager: LocationManager) -> None:
        """GPS もキャッシュもない場合は IP ジオロケーションにフォールバックする。"""
        respx.get("http://ip-api.com/json/").mock(
            return_value=Response(
                200,
                json={
                    "status": "success", "lat": 35.0, "lon": 139.0,
                    "city": "Tokyo", "country": "Japan",
                },
            )
        )
        loc = await manager.detect()
        assert loc is not None
        assert loc.source == LocationSource.IP

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detect_loads_saved_before_ip(
        self, manager: LocationManager, db: sqlite3.Connection
    ) -> None:
        """保存済みデータがあれば IP ジオロケーションより優先する。"""
        manager.from_manual(35.0, 139.0)
        mgr2 = LocationManager(db)
        respx.get("http://ip-api.com/json/").mock(side_effect=Exception("should not be called"))
        loc = await mgr2.detect()
        assert loc is not None
        assert loc.source == LocationSource.MANUAL


# ---------------------------------------------------------------------------
# Web API エンドポイントのテスト
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


@pytest.fixture()
def api_client_with_location(api_db: sqlite3.Connection):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from data.tle_manager import TLEManager
    from web.app import create_app

    mgr = LocationManager(api_db)
    app = create_app(conn=api_db, tle_manager=TLEManager(api_db), location_manager=mgr)
    return TestClient(app, raise_server_exceptions=True), mgr


@pytest.fixture()
def api_client_no_location(api_db: sqlite3.Connection):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from data.tle_manager import TLEManager
    from web.app import create_app

    app = create_app(conn=api_db, tle_manager=TLEManager(api_db))
    return TestClient(app, raise_server_exceptions=True)


class TestLocationApi:
    def test_get_location_no_manager_returns_503(self, api_client_no_location) -> None:  # type: ignore[no-untyped-def]
        resp = api_client_no_location.get("/api/location")
        assert resp.status_code == 503

    def test_get_location_not_set_returns_404(self, api_client_with_location) -> None:  # type: ignore[no-untyped-def]
        client, _ = api_client_with_location
        resp = client.get("/api/location")
        assert resp.status_code == 404

    def test_get_location_after_manual_set(self, api_client_with_location) -> None:  # type: ignore[no-untyped-def]
        client, mgr = api_client_with_location
        mgr.from_manual(35.6895, 139.6917, 40.0)
        resp = client.get("/api/location")
        assert resp.status_code == 200
        data = resp.json()
        assert data["latitude_deg"] == pytest.approx(35.6895)
        assert data["source"] == "Manual"

    def test_post_browser_location_no_manager_503(self, api_client_no_location) -> None:  # type: ignore[no-untyped-def]
        resp = api_client_no_location.post(
            "/api/location/browser",
            json={"latitude": 35.0, "longitude": 139.0},
        )
        assert resp.status_code == 503

    def test_post_browser_location_success(self, api_client_with_location) -> None:  # type: ignore[no-untyped-def]
        client, mgr = api_client_with_location
        resp = client.post(
            "/api/location/browser",
            json={"latitude": 35.6895, "longitude": 139.6917, "accuracy_m": 15.0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "Browser"
        assert data["latitude_deg"] == pytest.approx(35.6895)
        assert data["accuracy_m"] == pytest.approx(15.0)

    def test_post_browser_location_sets_manager_current(self, api_client_with_location) -> None:  # type: ignore[no-untyped-def]
        client, mgr = api_client_with_location
        client.post(
            "/api/location/browser",
            json={"latitude": 35.0, "longitude": 139.0},
        )
        assert mgr.current is not None
        assert mgr.current.source == LocationSource.BROWSER

    def test_get_location_returns_status_text(self, api_client_with_location) -> None:  # type: ignore[no-untyped-def]
        client, mgr = api_client_with_location
        mgr.from_manual(35.6895, 139.6917)
        resp = client.get("/api/location")
        data = resp.json()
        assert "QTH" in data["status_text"]
