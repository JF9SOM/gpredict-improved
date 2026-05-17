"""
世界地図ウィジェット

Natural Earth 110m 解像度の陸地ポリゴンデータを使って高精度な世界地図を描画する。
データは初回起動時に自動ダウンロードしてローカルにキャッシュする。
Shapely を使って GeoJSON ジオメトリを解析する。

データソース:
    https://github.com/nvkelso/natural-earth-vector (ne_110m_land.geojson)
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

import httpx
from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPaintEvent, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry import shape as geojson_shape

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

_NE_LAND_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "master/geojson/ne_110m_land.geojson"
)
_CACHE_FILENAME = "ne_110m_land.geojson"

# フォールバック用簡略ポリゴン (lat, lon) — NE データ取得失敗時に使用
_FALLBACK_CONTINENTS: list[list[tuple[float, float]]] = [
    # 北アメリカ
    [
        (71, -162),
        (71, -141),
        (60, -141),
        (59, -136),
        (55, -130),
        (49, -124),
        (37, -122),
        (32, -117),
        (25, -109),
        (22, -97),
        (15, -85),
        (8, -77),
        (10, -84),
        (23, -82),
        (25, -80),
        (35, -75),
        (42, -70),
        (47, -53),
        (52, -56),
        (58, -62),
        (62, -64),
        (60, -80),
        (61, -95),
        (68, -96),
        (72, -106),
        (71, -162),
    ],
    # 南アメリカ
    [
        (12, -72),
        (8, -77),
        (1, -80),
        (-4, -81),
        (-20, -70),
        (-23, -43),
        (-34, -53),
        (-56, -68),
        (-55, -65),
        (-42, -63),
        (-38, -57),
        (-33, -52),
        (-10, -37),
        (-5, -35),
        (0, -51),
        (5, -52),
        (8, -60),
        (10, -62),
        (12, -72),
    ],
    # ヨーロッパ
    [
        (71, 27),
        (65, 14),
        (58, 5),
        (51, 2),
        (43, -9),
        (36, -6),
        (37, 0),
        (43, 6),
        (44, 8),
        (46, 14),
        (42, 20),
        (37, 25),
        (41, 29),
        (47, 38),
        (55, 37),
        (60, 29),
        (65, 26),
        (68, 27),
        (71, 27),
    ],
    # アフリカ
    [
        (37, -6),
        (37, 11),
        (30, 33),
        (22, 37),
        (15, 41),
        (12, 44),
        (11, 51),
        (1, 42),
        (-12, 40),
        (-26, 33),
        (-35, 19),
        (-28, 16),
        (-17, 12),
        (-5, 10),
        (-5, -10),
        (5, -16),
        (15, -17),
        (25, -15),
        (35, -5),
        (37, -6),
    ],
    # アジア（本土）
    [
        (41, 27),
        (42, 35),
        (47, 38),
        (55, 37),
        (65, 40),
        (65, 57),
        (73, 53),
        (72, 68),
        (77, 68),
        (73, 100),
        (72, 130),
        (65, 141),
        (53, 141),
        (50, 140),
        (42, 130),
        (38, 121),
        (35, 121),
        (25, 121),
        (21, 110),
        (18, 110),
        (15, 120),
        (5, 119),
        (5, 115),
        (3, 113),
        (1, 104),
        (2, 102),
        (6, 100),
        (5, 80),
        (8, 77),
        (22, 68),
        (30, 60),
        (38, 57),
        (42, 50),
        (42, 44),
        (41, 27),
    ],
    # オーストラリア
    [
        (-10, 131),
        (-15, 129),
        (-17, 122),
        (-26, 114),
        (-34, 115),
        (-38, 140),
        (-38, 147),
        (-34, 151),
        (-25, 153),
        (-15, 145),
        (-10, 142),
        (-10, 131),
    ],
    # グリーンランド
    [
        (83, -30),
        (77, -18),
        (65, -40),
        (60, -48),
        (68, -52),
        (76, -57),
        (80, -53),
        (83, -30),
    ],
    # 南極大陸
    [
        (-65, -180),
        (-68, -150),
        (-72, -120),
        (-66, -90),
        (-73, -60),
        (-70, -30),
        (-67, 0),
        (-70, 30),
        (-67, 60),
        (-70, 90),
        (-68, 120),
        (-72, 150),
        (-65, 180),
        (-90, 180),
        (-90, -180),
    ],
]

# ---------------------------------------------------------------------------
# データ取得・解析
# ---------------------------------------------------------------------------


def _cache_path() -> Path:
    """GeoJSON キャッシュファイルのパスを返す。"""
    from platformdirs import user_data_dir

    data_dir = Path(user_data_dir("gpredict-improved", "gpredict-improved"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / _CACHE_FILENAME


def _extract_ring_coords(ring: list[list[float]]) -> list[tuple[float, float]]:
    """
    GeoJSON リング座標を (lat, lon) タプルリストに変換する。

    GeoJSON の座標順は [lon, lat] なので反転して返す。
    要素数が 2 未満の座標はスキップする。

    Args:
        ring: [[lon, lat], ...] 形式の座標リスト

    Returns:
        (lat, lon) タプルのリスト
    """
    return [(c[1], c[0]) for c in ring if len(c) >= 2]


def _parse_geojson(data: dict[str, Any]) -> list[list[tuple[float, float]]]:
    """
    GeoJSON データを解析して陸地ポリゴンリストを返す。

    Shapely を使って Polygon / MultiPolygon ジオメトリを解析する。
    座標は内部表現 (lat, lon) タプルのリストとして返す。

    Args:
        data: GeoJSON FeatureCollection 辞書

    Returns:
        (lat, lon) タプルリストのリスト（各要素が1ポリゴン）
    """
    result: list[list[tuple[float, float]]] = []

    for feature in data.get("features", []):
        try:
            geom = geojson_shape(feature.get("geometry", {}))
        except Exception as exc:
            logger.debug("Feature geometry parse error: %s", exc)
            continue

        if isinstance(geom, Polygon):
            coords = _exterior_latlon(geom)
            if coords:
                result.append(coords)
        elif isinstance(geom, MultiPolygon):
            for poly in geom.geoms:
                coords = _exterior_latlon(poly)
                if coords:
                    result.append(coords)

    return result


def _exterior_latlon(poly: Polygon) -> list[tuple[float, float]]:
    """
    Shapely Polygon の外周座標を (lat, lon) タプルリストに変換する。

    GeoJSON / Shapely の座標順は (lon, lat) なので反転して返す。
    有効なポリゴンでない場合は空リストを返す。
    """
    if poly.is_empty:
        return []
    coords = list(poly.exterior.coords)
    if len(coords) < 3:
        return []
    # GeoJSON は (lon, lat) 順 → (lat, lon) に変換
    return [(c[1], c[0]) for c in coords]


def _load_land_polygons() -> list[list[tuple[float, float]]]:
    """
    Natural Earth 110m 陸地ポリゴンを読み込む。

    優先順位:
        1. ローカルキャッシュ（高速）
        2. ネットワークダウンロード → キャッシュ保存
        3. フォールバック簡略データ（オフライン・エラー時）

    Returns:
        (lat, lon) タプルリストのリスト
    """
    cache = _cache_path()

    if not cache.exists():
        logger.info("Downloading Natural Earth 110m land data from GitHub...")
        try:
            resp = httpx.get(_NE_LAND_URL, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            cache.write_bytes(resp.content)
            logger.info("Cached Natural Earth data: %s (%d KB)", cache, len(resp.content) // 1024)
        except Exception as exc:
            logger.warning("Failed to download Natural Earth data: %s — using fallback map.", exc)
            return list(_FALLBACK_CONTINENTS)

    try:
        data: dict[str, Any] = json.loads(cache.read_text(encoding="utf-8"))
        polygons = _parse_geojson(data)
        if not polygons:
            logger.warning("Parsed 0 polygons from cache — using fallback map.")
            return list(_FALLBACK_CONTINENTS)
        logger.info("Loaded %d land polygons from Natural Earth cache.", len(polygons))
        return polygons
    except Exception as exc:
        logger.warning("Failed to parse cached GeoJSON: %s — using fallback map.", exc)
        return list(_FALLBACK_CONTINENTS)


# プロセス内メモリキャッシュ（一度だけ読み込む）
_land_polygons_cache: list[list[tuple[float, float]]] | None = None


def get_land_polygons() -> list[list[tuple[float, float]]]:
    """
    陸地ポリゴンデータを返す（遅延ロード・プロセス内キャッシュ）。

    初回呼び出し時にファイルキャッシュまたはネットワークから読み込む。

    Returns:
        (lat, lon) タプルリストのリスト
    """
    global _land_polygons_cache
    if _land_polygons_cache is None:
        _land_polygons_cache = _load_land_polygons()
    return _land_polygons_cache


def prefetch_land_data() -> None:
    """
    陸地ポリゴンデータをプリフェッチする。

    アプリ起動時（Qt イベントループ開始前）に呼び出すことで、
    初回描画時のブロッキングを回避する。
    キャッシュが存在しない場合はネットワークからダウンロードする（初回のみ）。
    """
    get_land_polygons()


# ---------------------------------------------------------------------------
# WorldMapView ウィジェット
# ---------------------------------------------------------------------------


class WorldMapView(QWidget):
    """
    2D 等緯度経度図（Equirectangular）ウィジェット。

    Natural Earth 110m 解像度の陸地ポリゴンで世界地図を描画し、
    衛星直下点と自局位置（★印）をリアルタイムに表示する。
    上が北、左端が西経 180°、右端が東経 180°。
    """

    sat_clicked: Signal = Signal(int)  # norad_cat_id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(400, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # norad -> (name, lat_deg, lon_deg, QColor)
        self._satellites: dict[int, tuple[str, float, float, QColor]] = {}
        self._observer_lat: float | None = None
        self._observer_lon: float | None = None
        self._dot_radius: float = 5.0
        self._hit_radius: float = 12.0
        # 選択衛星フットプリント: (norad, lat_deg, lon_deg, alt_km)
        self._footprint: tuple[int, float, float, float] | None = None

    def sizeHint(self) -> QSize:
        return QSize(800, 400)

    def set_satellites(
        self,
        satellites: dict[int, tuple[str, float, float, QColor]],
    ) -> None:
        """
        衛星の直下点データを設定して再描画する。

        Args:
            satellites: {norad_cat_id: (name, lat_deg, lon_deg, QColor)}
        """
        self._satellites = satellites
        self.update()

    def draw_footprint(self, norad: int, lat: float, lon: float, alt_km: float) -> None:
        """選択衛星のフットプリント（可視範囲）を更新する。

        次の paintEvent で半透明の青い円として描画される。

        Args:
            norad:   NORAD カタログ番号
            lat:     衛星直下点緯度（度）
            lon:     衛星直下点経度（度）
            alt_km:  衛星高度（km）
        """
        self._footprint = (norad, lat, lon, alt_km)
        self.update()

    def clear_footprint(self) -> None:
        """フットプリント表示をクリアする。"""
        if self._footprint is not None:
            self._footprint = None
            self.update()

    def set_observer_location(self, lat: float, lon: float) -> None:
        """
        自局位置（QTH）を設定して再描画する。地図上に ★ 印で表示する。

        Args:
            lat: 緯度（度、北緯正）
            lon: 経度（度、東経正）
        """
        if self._observer_lat != lat or self._observer_lon != lon:
            self._observer_lat = lat
            self._observer_lon = lon
            self.update()

    def latlon_to_xy(self, lat: float, lon: float, w: float, h: float) -> tuple[float, float]:
        """
        緯度・経度をウィジェット座標に変換する（等緯度経度図）。

        Args:
            lat: 緯度（度、北緯正）
            lon: 経度（度、東経正）
            w:   ウィジェット幅（ピクセル）
            h:   ウィジェット高さ（ピクセル）

        Returns:
            (x, y) ウィジェット座標
        """
        x = (lon + 180.0) / 360.0 * w
        y = (90.0 - lat) / 180.0 * h
        return x, y

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """衛星ドット付近のクリックで sat_clicked を emit する。"""
        w, h = float(self.width()), float(self.height())
        px, py = event.position().x(), event.position().y()
        for norad, sat_info in reversed(list(self._satellites.items())):
            _name, lat, lon, _color = sat_info
            sx, sy = self.latlon_to_xy(lat, lon, w, h)
            if math.hypot(px - sx, py - sy) <= self._hit_radius:
                self.sat_clicked.emit(norad)
                return

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            self._draw(painter)
        finally:
            painter.end()

    def _draw(self, p: QPainter) -> None:
        w, h = float(self.width()), float(self.height())

        # 背景（海: 中青）
        p.fillRect(0, 0, int(w), int(h), QColor("#1565C0"))

        # 陸地ポリゴン（Natural Earth 110m）
        p.setPen(QPen(QColor("#1B5E20"), 1))
        p.setBrush(QColor("#388E3C"))
        for polygon_coords in get_land_polygons():
            # 内部表現は (lat, lon) 順
            points = [QPointF(*self.latlon_to_xy(lat, lon, w, h)) for lat, lon in polygon_coords]
            if len(points) >= 3:
                p.drawPolygon(QPolygonF(points))

        # グリッド線（30° 間隔、明るい水色の破線）
        grid_pen = QPen(QColor("#90CAF9"), 1)
        grid_pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(grid_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for lat in range(-90, 91, 30):
            _, y = self.latlon_to_xy(float(lat), 0.0, w, h)
            p.drawLine(0, int(y), int(w), int(y))
        for lon in range(-180, 181, 30):
            x, _ = self.latlon_to_xy(0.0, float(lon), w, h)
            p.drawLine(int(x), 0, int(x), int(h))

        # 赤道（金色の実線、強調）
        _, eq_y = self.latlon_to_xy(0.0, 0.0, w, h)
        p.setPen(QPen(QColor("#FFD700"), 2))
        p.drawLine(0, int(eq_y), int(w), int(eq_y))

        # 自局位置（★印）
        if self._observer_lat is not None and self._observer_lon is not None:
            ox, oy = self.latlon_to_xy(self._observer_lat, self._observer_lon, w, h)
            p.setPen(QPen(QColor("#FFFFFF"), 1))
            p.setBrush(QColor("#FFFF00"))
            self._draw_star(p, ox, oy, 8.0)

        # フットプリント（衛星ドットより先に描画して重なりを正しく表示）
        self._draw_footprint(p, w, h)

        # 衛星ドット + ラベル
        label_font = QFont()
        label_font.setPointSize(8)
        p.setFont(label_font)
        dr = int(self._dot_radius)
        for info in self._satellites.values():
            sx, sy = self.latlon_to_xy(info[1], info[2], w, h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(info[3])
            p.drawEllipse(int(sx) - dr, int(sy) - dr, dr * 2, dr * 2)
            p.setPen(info[3])
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawText(int(sx) + dr + 2, int(sy) + 4, info[0])

    def _draw_footprint(self, p: QPainter, w: float, h: float) -> None:
        """フットプリント（可視範囲の円）を等緯度経度図上に描画する。

        球面幾何を使って地心角 rho の円周上の点を計算し、
        半透明の青いポリゴンと白い輪郭線で描画する。
        日付変更線をまたぐ場合は経度を連続に正規化して QPainter に渡す。
        """
        if self._footprint is None:
            return

        _norad, lat0, lon0, alt_km = self._footprint
        earth_r = 6371.0
        rho = math.acos(earth_r / (earth_r + max(alt_km, 1.0)))

        lat0_r = math.radians(lat0)
        lon0_r = math.radians(lon0)
        n_pts = 90
        raw: list[tuple[float, float]] = []

        for i in range(n_pts):
            az = 2.0 * math.pi * i / n_pts
            sin_lat = math.sin(lat0_r) * math.cos(rho) + math.cos(lat0_r) * math.sin(
                rho
            ) * math.cos(az)
            lat_r = math.asin(max(-1.0, min(1.0, sin_lat)))
            dlon = math.atan2(
                math.sin(az) * math.sin(rho) * math.cos(lat0_r),
                math.cos(rho) - math.sin(lat0_r) * math.sin(lat_r),
            )
            raw.append((math.degrees(lat_r), math.degrees(lon0_r + dlon)))

        # 日付変更線をまたぐ場合に経度を連続的に正規化する
        normalized: list[tuple[float, float]] = [raw[0]]
        prev_lon = raw[0][1]
        for lat, lon in raw[1:]:
            while lon - prev_lon > 180.0:
                lon -= 360.0
            while lon - prev_lon < -180.0:
                lon += 360.0
            normalized.append((lat, lon))
            prev_lon = lon

        # 半透明の青い塗りつぶし + 白い輪郭線
        p.setBrush(QColor(64, 164, 255, 55))
        p.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
        qpts = [QPointF(*self.latlon_to_xy(lat, lon, w, h)) for lat, lon in normalized]
        if len(qpts) >= 3:
            p.drawPolygon(QPolygonF(qpts))

    def _draw_star(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        """
        5 角星を描画する。

        Args:
            p:  QPainter
            cx: 中心 X 座標（ピクセル）
            cy: 中心 Y 座標（ピクセル）
            r:  外接円半径（ピクセル）
        """
        points: list[QPointF] = []
        for i in range(10):
            angle = math.radians(-90.0 + i * 36.0)
            radius = r if i % 2 == 0 else r * 0.4
            points.append(QPointF(cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        p.drawPolygon(QPolygonF(points))
