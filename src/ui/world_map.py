"""
World map widget.

Draws a high-resolution world map using Natural Earth 110m land polygon data.
Data is automatically downloaded on first launch and cached locally.
Uses Shapely to parse GeoJSON geometry.

Data source:
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
# Constants
# ---------------------------------------------------------------------------

_NE_LAND_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "master/geojson/ne_110m_land.geojson"
)
_CACHE_FILENAME = "ne_110m_land.geojson"

# Simplified fallback polygons (lat, lon) used when NE data download fails
_FALLBACK_CONTINENTS: list[list[tuple[float, float]]] = [
    # North America
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
    # South America
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
    # Europe
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
    # Africa
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
    # Asia (mainland)
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
    # Australia
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
    # Greenland
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
    # Antarctica
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
# Data loading and parsing
# ---------------------------------------------------------------------------


def _cache_path() -> Path:
    """Return the path to the GeoJSON cache file."""
    from platformdirs import user_data_dir

    data_dir = Path(user_data_dir("gpredict-improved", "gpredict-improved"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / _CACHE_FILENAME


def _extract_ring_coords(ring: list[list[float]]) -> list[tuple[float, float]]:
    """
    Convert GeoJSON ring coordinates to a list of (lat, lon) tuples.

    GeoJSON coordinates are in [lon, lat] order, so they are reversed on return.
    Coordinates with fewer than 2 elements are skipped.

    Args:
        ring: Coordinate list in [[lon, lat], ...] format

    Returns:
        List of (lat, lon) tuples
    """
    return [(c[1], c[0]) for c in ring if len(c) >= 2]


def _parse_geojson(data: dict[str, Any]) -> list[list[tuple[float, float]]]:
    """
    Parse GeoJSON data and return a list of land polygons.

    Uses Shapely to parse Polygon / MultiPolygon geometries.
    Coordinates are returned as (lat, lon) tuple lists in internal representation.

    Args:
        data: GeoJSON FeatureCollection dict

    Returns:
        List of (lat, lon) tuple lists (one element per polygon)
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
    Convert the exterior ring of a Shapely Polygon to a list of (lat, lon) tuples.

    GeoJSON / Shapely coordinates are in (lon, lat) order, so they are reversed.
    Returns an empty list for invalid polygons.
    """
    if poly.is_empty:
        return []
    coords = list(poly.exterior.coords)
    if len(coords) < 3:
        return []
    # GeoJSON is (lon, lat) order → convert to (lat, lon)
    return [(c[1], c[0]) for c in coords]


def _load_land_polygons() -> list[list[tuple[float, float]]]:
    """
    Load Natural Earth 110m land polygons.

    Priority:
        1. Local cache (fast)
        2. Network download → save to cache
        3. Simplified fallback data (offline / error)

    Returns:
        List of (lat, lon) tuple lists
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


# In-process memory cache (loaded only once)
_land_polygons_cache: list[list[tuple[float, float]]] | None = None


def get_land_polygons() -> list[list[tuple[float, float]]]:
    """
    Return land polygon data (lazy-loaded, in-process cache).

    Loads from file cache or network on first call.

    Returns:
        List of (lat, lon) tuple lists
    """
    global _land_polygons_cache
    if _land_polygons_cache is None:
        _land_polygons_cache = _load_land_polygons()
    return _land_polygons_cache


def prefetch_land_data() -> None:
    """
    Prefetch land polygon data.

    Call at application startup (before the Qt event loop starts) to avoid
    blocking on the first paint. Downloads from network if no cache exists (first run only).
    """
    get_land_polygons()


# ---------------------------------------------------------------------------
# WorldMapView widget
# ---------------------------------------------------------------------------


class WorldMapView(QWidget):
    """
    2D equirectangular projection widget.

    Draws a world map using Natural Earth 110m land polygons and displays
    satellite sub-satellite points and observer position (★ mark) in real time.
    North is up; left edge is 180°W, right edge is 180°E.
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
        # Selected satellite footprint: (norad, lat_deg, lon_deg, alt_km)
        self._footprint: tuple[int, float, float, float] | None = None
        # Filter: None = show all, set = show only specified NORADs
        self._visible_norads: set[int] | None = None

    def sizeHint(self) -> QSize:
        return QSize(800, 400)

    def set_satellites(
        self,
        satellites: dict[int, tuple[str, float, float, QColor]],
    ) -> None:
        """
        Set satellite sub-satellite point data and repaint.

        Args:
            satellites: {norad_cat_id: (name, lat_deg, lon_deg, QColor)}
        """
        self._satellites = satellites
        self.update()

    def set_visible_norads(self, norads: set[int] | None) -> None:
        """
        Restrict displayed satellites to the specified set of NORAD numbers.

        Args:
            norads: Set of NORAD numbers to display. None shows all satellites.
        """
        self._visible_norads = norads
        self.update()

    def draw_footprint(self, norad: int, lat: float, lon: float, alt_km: float) -> None:
        """Update the footprint (visibility range) of the selected satellite.

        Drawn as a semi-transparent blue circle in the next paintEvent.

        Args:
            norad:   NORAD catalog number
            lat:     Sub-satellite point latitude (degrees)
            lon:     Sub-satellite point longitude (degrees)
            alt_km:  Satellite altitude (km)
        """
        self._footprint = (norad, lat, lon, alt_km)
        self.update()

    def clear_footprint(self) -> None:
        """Clear the footprint display."""
        if self._footprint is not None:
            self._footprint = None
            self.update()

    def set_observer_location(self, lat: float, lon: float) -> None:
        """
        Set the observer (QTH) location and repaint. Displayed as a ★ on the map.

        Args:
            lat: Latitude (degrees, positive = North)
            lon: Longitude (degrees, positive = East)
        """
        if self._observer_lat != lat or self._observer_lon != lon:
            self._observer_lat = lat
            self._observer_lon = lon
            self.update()

    def latlon_to_xy(self, lat: float, lon: float, w: float, h: float) -> tuple[float, float]:
        """
        Convert latitude/longitude to widget coordinates (equirectangular projection).

        Args:
            lat: Latitude (degrees, positive = North)
            lon: Longitude (degrees, positive = East)
            w:   Widget width (pixels)
            h:   Widget height (pixels)

        Returns:
            (x, y) widget coordinates
        """
        x = (lon + 180.0) / 360.0 * w
        y = (90.0 - lat) / 180.0 * h
        return x, y

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Emit sat_clicked when the user clicks near a satellite dot."""
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

        # Background (ocean: medium blue)
        p.fillRect(0, 0, int(w), int(h), QColor("#1565C0"))

        # Land polygons (Natural Earth 110m)
        p.setPen(QPen(QColor("#1B5E20"), 1))
        p.setBrush(QColor("#388E3C"))
        for polygon_coords in get_land_polygons():
            # Internal representation is (lat, lon) order
            points = [QPointF(*self.latlon_to_xy(lat, lon, w, h)) for lat, lon in polygon_coords]
            if len(points) >= 3:
                p.drawPolygon(QPolygonF(points))

        # Grid lines (30° intervals, light cyan dashed)
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

        # Equator (gold solid line, emphasized)
        _, eq_y = self.latlon_to_xy(0.0, 0.0, w, h)
        p.setPen(QPen(QColor("#FFD700"), 2))
        p.drawLine(0, int(eq_y), int(w), int(eq_y))

        # Observer location (star marker)
        if self._observer_lat is not None and self._observer_lon is not None:
            ox, oy = self.latlon_to_xy(self._observer_lat, self._observer_lon, w, h)
            p.setPen(QPen(QColor("#FFFFFF"), 1))
            p.setBrush(QColor("#FFFF00"))
            self._draw_star(p, ox, oy, 8.0)

        # Footprint (drawn before satellite dots so dots render on top)
        self._draw_footprint(p, w, h)

        # Satellite dots + labels
        label_font = QFont()
        label_font.setPointSize(8)
        p.setFont(label_font)
        dr = int(self._dot_radius)
        sel_norad = self._footprint[0] if self._footprint is not None else None
        for norad, info in self._satellites.items():
            if self._visible_norads is not None and norad not in self._visible_norads:
                continue  # satellite is outside the visible filter
            if norad == sel_norad:
                continue  # selected satellite is drawn larger below
            sx, sy = self.latlon_to_xy(info[1], info[2], w, h)
            if math.isnan(sx) or math.isnan(sy):
                continue
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(info[3])
            p.drawEllipse(int(sx) - dr, int(sy) - dr, dr * 2, dr * 2)
            p.setPen(info[3])
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawText(int(sx) + dr + 2, int(sy) + 4, info[0])

        # Draw selected satellite as a larger dot with yellow outline and white label.
        # Always shown even when outside the current filter.
        if sel_norad is not None and sel_norad in self._satellites:
            sel_info = self._satellites[sel_norad]
            sx, sy = self.latlon_to_xy(sel_info[1], sel_info[2], w, h)
            if math.isnan(sx) or math.isnan(sy):
                return
            sel_r = 8
            p.setPen(QPen(QColor(255, 220, 0, 230), 2))
            p.setBrush(sel_info[3])
            p.drawEllipse(int(sx) - sel_r, int(sy) - sel_r, sel_r * 2, sel_r * 2)
            p.setPen(QColor(255, 255, 255))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawText(int(sx) + sel_r + 2, int(sy) + 4, sel_info[0])

    def _draw_footprint(self, p: QPainter, w: float, h: float) -> None:
        """Draw the footprint (visibility circle) on the equirectangular map.

        Computes 361 screen points at 1-degree bearing intervals and skips
        any line segment whose X span exceeds one-third of the widget width,
        which handles date-line crossings and polar regions correctly.
        """
        if self._footprint is None:
            return

        _norad, lat0, lon0, alt_km = self._footprint
        earth_r = 6371.0
        cos_rho = earth_r / (earth_r + max(alt_km, 1.0))
        rho = math.acos(min(cos_rho, 1.0))
        lat0_r = math.radians(lat0)

        # Compute 361 screen coordinates at 1-degree bearing intervals
        screen_pts: list[tuple[float, float]] = []
        for i in range(361):
            bearing = math.radians(i)
            sin_lat = math.sin(lat0_r) * math.cos(rho) + math.cos(lat0_r) * math.sin(
                rho
            ) * math.cos(bearing)
            fp_lat = math.degrees(math.asin(max(-1.0, min(1.0, sin_lat))))
            fp_lon = lon0 + math.degrees(
                math.atan2(
                    math.sin(bearing) * math.sin(rho) * math.cos(lat0_r),
                    math.cos(rho) - math.sin(lat0_r) * math.sin(math.radians(fp_lat)),
                )
            )
            fp_lon = ((fp_lon + 180.0) % 360.0) - 180.0
            screen_pts.append(self.latlon_to_xy(fp_lat, fp_lon, w, h))

        threshold = w / 3.0

        # Fill: split into sub-polygons at each date-line skip and draw each
        sub_polys: list[list[QPointF]] = []
        cur_poly: list[QPointF] = []
        for i, (x, y) in enumerate(screen_pts):
            if i > 0 and abs(x - screen_pts[i - 1][0]) >= threshold:
                if len(cur_poly) >= 3:
                    sub_polys.append(cur_poly)
                cur_poly = []
            cur_poly.append(QPointF(x, y))
        if cur_poly:
            sub_polys.append(cur_poly)

        p.setBrush(QColor(100, 180, 255, 60))
        p.setPen(Qt.PenStyle.NoPen)
        for poly in sub_polys:
            if len(poly) >= 3:
                p.drawPolygon(QPolygonF(poly))

        # Outline: skip segments that cross the date line (abnormally long X span)
        p.setPen(QPen(QColor(255, 255, 255, 220), 2.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(len(screen_pts) - 1):
            x1, y1 = screen_pts[i]
            x2, y2 = screen_pts[i + 1]
            if abs(x2 - x1) < threshold:
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Crosshair at footprint center
        cx, cy = self.latlon_to_xy(lat0, lon0, w, h)
        cross = 10
        p.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
        p.drawLine(int(cx) - cross, int(cy), int(cx) + cross, int(cy))
        p.drawLine(int(cx), int(cy) - cross, int(cx), int(cy) + cross)

    def _draw_star(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        """
        Draw a 5-pointed star.

        Args:
            p:  QPainter
            cx: Center X coordinate (pixels)
            cy: Center Y coordinate (pixels)
            r:  Circumscribed circle radius (pixels)
        """
        points: list[QPointF] = []
        for i in range(10):
            angle = math.radians(-90.0 + i * 36.0)
            radius = r if i % 2 == 0 else r * 0.4
            points.append(QPointF(cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        p.drawPolygon(QPolygonF(points))
