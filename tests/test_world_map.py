"""
world_map モジュールのテスト

WorldMapView  — 世界地図ウィジェット
get_land_polygons / _load_land_polygons — Natural Earth データ取得
prefetch_land_data — プリフェッチ関数
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from PySide6.QtGui import QColor

import ui.world_map as world_map_mod
from ui.world_map import (
    _FALLBACK_CONTINENTS,
    WorldMapView,
    _extract_ring_coords,
    _parse_geojson,
    get_land_polygons,
    prefetch_land_data,
)

# ---------------------------------------------------------------------------
# フィクスチャ: テスト間でモジュールキャッシュをリセット
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_land_cache():
    """各テスト前後でポリゴンキャッシュをリセットする。"""
    original = world_map_mod._land_polygons_cache
    yield
    world_map_mod._land_polygons_cache = original


# ---------------------------------------------------------------------------
# WorldMapView ウィジェット
# ---------------------------------------------------------------------------


class TestWorldMapView:
    def test_create(self, qtbot) -> None:
        w = WorldMapView()
        qtbot.addWidget(w)
        assert w is not None

    def test_set_satellites_empty(self, qtbot) -> None:
        w = WorldMapView()
        qtbot.addWidget(w)
        w.set_satellites({})
        assert w._satellites == {}

    def test_set_satellites_single(self, qtbot) -> None:
        w = WorldMapView()
        qtbot.addWidget(w)
        w.set_satellites({25544: ("ISS", 35.0, 139.0, QColor("#e74c3c"))})
        assert 25544 in w._satellites

    def test_set_satellites_multiple(self, qtbot) -> None:
        w = WorldMapView()
        qtbot.addWidget(w)
        w.set_satellites(
            {
                25544: ("ISS", 35.0, 139.0, QColor("#e74c3c")),
                43017: ("AO-91", -5.0, -60.0, QColor("#3498db")),
            }
        )
        assert len(w._satellites) == 2

    def test_latlon_to_xy_equator_prime_meridian(self, qtbot) -> None:
        """緯度 0°・経度 0° はマップ中央になる。"""
        w = WorldMapView()
        qtbot.addWidget(w)
        x, y = w.latlon_to_xy(0.0, 0.0, 360.0, 180.0)
        assert abs(x - 180.0) < 1e-9
        assert abs(y - 90.0) < 1e-9

    def test_latlon_to_xy_north_pole(self, qtbot) -> None:
        """北極はマップ上端になる。"""
        w = WorldMapView()
        qtbot.addWidget(w)
        _, y = w.latlon_to_xy(90.0, 0.0, 360.0, 180.0)
        assert abs(y) < 1e-9

    def test_latlon_to_xy_south_pole(self, qtbot) -> None:
        """南極はマップ下端になる。"""
        w = WorldMapView()
        qtbot.addWidget(w)
        _, y = w.latlon_to_xy(-90.0, 0.0, 360.0, 180.0)
        assert abs(y - 180.0) < 1e-9

    def test_latlon_to_xy_antimeridian_east(self, qtbot) -> None:
        """東経 180° はマップ右端になる。"""
        w = WorldMapView()
        qtbot.addWidget(w)
        x, _ = w.latlon_to_xy(0.0, 180.0, 360.0, 180.0)
        assert abs(x - 360.0) < 1e-9

    def test_latlon_to_xy_antimeridian_west(self, qtbot) -> None:
        """西経 180° はマップ左端になる。"""
        w = WorldMapView()
        qtbot.addWidget(w)
        x, _ = w.latlon_to_xy(0.0, -180.0, 360.0, 180.0)
        assert abs(x) < 1e-9

    def test_latlon_to_xy_returns_floats(self, qtbot) -> None:
        w = WorldMapView()
        qtbot.addWidget(w)
        x, y = w.latlon_to_xy(35.6895, 139.6917, 800.0, 400.0)
        assert isinstance(x, float)
        assert isinstance(y, float)

    def test_size_hint_positive(self, qtbot) -> None:
        w = WorldMapView()
        qtbot.addWidget(w)
        hint = w.sizeHint()
        assert hint.width() > 0
        assert hint.height() > 0

    def test_minimum_size(self, qtbot) -> None:
        w = WorldMapView()
        qtbot.addWidget(w)
        assert w.minimumWidth() >= 400
        assert w.minimumHeight() >= 200

    def test_sat_clicked_signal_exists(self, qtbot) -> None:
        w = WorldMapView()
        qtbot.addWidget(w)
        assert hasattr(w, "sat_clicked")

    def test_set_observer_location_initial_none(self, qtbot) -> None:
        """初期状態では自局位置は None。"""
        w = WorldMapView()
        qtbot.addWidget(w)
        assert w._observer_lat is None
        assert w._observer_lon is None

    def test_set_observer_location_updates_fields(self, qtbot) -> None:
        """set_observer_location で緯度・経度が正しく設定される。"""
        w = WorldMapView()
        qtbot.addWidget(w)
        w.set_observer_location(35.6895, 139.6917)
        assert abs(w._observer_lat - 35.6895) < 1e-9
        assert abs(w._observer_lon - 139.6917) < 1e-9

    def test_set_observer_location_south(self, qtbot) -> None:
        """南緯・西経でも正しく設定される。"""
        w = WorldMapView()
        qtbot.addWidget(w)
        w.set_observer_location(-33.87, 151.21)
        assert w._observer_lat < 0
        assert w._observer_lon > 0

    def test_draw_star_does_not_raise(self, qtbot) -> None:
        """_draw_star が例外を出さないことを確認する。"""
        from PySide6.QtGui import QPainter, QPixmap

        w = WorldMapView()
        qtbot.addWidget(w)
        pixmap = QPixmap(100, 100)
        painter = QPainter(pixmap)
        w._draw_star(painter, 50.0, 50.0, 10.0)
        painter.end()


# ---------------------------------------------------------------------------
# _extract_ring_coords
# ---------------------------------------------------------------------------


class TestExtractRingCoords:
    def test_basic_conversion(self) -> None:
        """(lon, lat) リストを (lat, lon) に変換する。"""
        ring = [[139.0, 35.0], [140.0, 36.0], [138.0, 34.0]]
        result = _extract_ring_coords(ring)
        assert result == [(35.0, 139.0), (36.0, 140.0), (34.0, 138.0)]

    def test_short_coords_skipped(self) -> None:
        """要素数が 2 未満の座標はスキップされる。"""
        ring = [[139.0, 35.0], [140.0], [138.0, 34.0]]
        result = _extract_ring_coords(ring)
        assert len(result) == 2  # [140.0] はスキップ

    def test_empty_ring(self) -> None:
        result = _extract_ring_coords([])
        assert result == []


# ---------------------------------------------------------------------------
# _parse_geojson
# ---------------------------------------------------------------------------


class TestParseGeojson:
    def _make_polygon_feature(self, rings: list) -> dict:
        return {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": rings},
        }

    def _make_multipolygon_feature(self, polys: list) -> dict:
        return {
            "type": "Feature",
            "geometry": {"type": "MultiPolygon", "coordinates": polys},
        }

    def test_polygon_feature(self) -> None:
        """Polygon フィーチャーが正しく解析される。"""
        ring = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
        data = {"features": [self._make_polygon_feature([ring])]}
        result = _parse_geojson(data)
        assert len(result) == 1
        assert len(result[0]) >= 3

    def test_multipolygon_feature(self) -> None:
        """MultiPolygon フィーチャーが各ポリゴンとして展開される。"""
        ring = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]
        data = {"features": [self._make_multipolygon_feature([[ring], [ring]])]}
        result = _parse_geojson(data)
        assert len(result) == 2

    def test_empty_features(self) -> None:
        result = _parse_geojson({"features": []})
        assert result == []

    def test_latlon_order(self) -> None:
        """変換後は (lat, lon) 順になっている。"""
        # ring: [[lon, lat], ...]
        ring = [[139.0, 35.0], [140.0, 36.0], [138.0, 34.0], [139.0, 35.0]]
        data = {"features": [self._make_polygon_feature([ring])]}
        result = _parse_geojson(data)
        # 最初の点: lat=35.0, lon=139.0
        first_lat, first_lon = result[0][0]
        assert abs(first_lat - 35.0) < 1e-6
        assert abs(first_lon - 139.0) < 1e-6


# ---------------------------------------------------------------------------
# get_land_polygons / _load_land_polygons
# ---------------------------------------------------------------------------


class TestGetLandPolygons:
    def test_returns_list(self, tmp_path) -> None:
        """キャッシュが存在するときリストを返す。"""
        sample_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]],
                    },
                }
            ],
        }
        cache_file = tmp_path / "ne_110m_land.geojson"
        cache_file.write_text(json.dumps(sample_geojson), encoding="utf-8")

        world_map_mod._land_polygons_cache = None
        with patch("ui.world_map._cache_path", return_value=cache_file):
            result = get_land_polygons()

        assert isinstance(result, list)
        assert len(result) > 0

    def test_fallback_on_download_error(self, tmp_path) -> None:
        """ダウンロード失敗時はフォールバックデータを返す。"""
        missing = tmp_path / "ne_110m_land.geojson"
        world_map_mod._land_polygons_cache = None
        with (
            patch("ui.world_map._cache_path", return_value=missing),
            patch("httpx.get", side_effect=Exception("network error")),
        ):
            result = get_land_polygons()

        assert result == _FALLBACK_CONTINENTS

    def test_cache_reused_on_second_call(self, tmp_path) -> None:
        """2回目の呼び出しではキャッシュが再利用される。"""
        world_map_mod._land_polygons_cache = [[(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]]
        result1 = get_land_polygons()
        result2 = get_land_polygons()
        assert result1 is result2

    def test_fallback_continents_are_valid(self) -> None:
        """フォールバックデータは有効なポリゴン（≥3点）を持つ。"""
        for poly in _FALLBACK_CONTINENTS:
            assert len(poly) >= 3, f"ポリゴンに3点以上必要: {poly[:2]}..."
            for lat, lon in poly:
                assert -90.0 <= lat <= 90.0, f"緯度範囲外: {lat}"
                assert -180.0 <= lon <= 180.0, f"経度範囲外: {lon}"


class TestPrefetchLandData:
    def test_prefetch_does_not_raise(self) -> None:
        """prefetch_land_data が例外を出さずに実行される。"""
        world_map_mod._land_polygons_cache = _FALLBACK_CONTINENTS
        prefetch_land_data()  # キャッシュ済みなのでネットワーク不要
