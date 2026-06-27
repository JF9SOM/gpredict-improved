"""
Automatic station location detection module

Priority order:
    1. GPS device (via gpsd daemon / python-gps)
    2. Browser Geolocation API (pre-set via POST /api/location/browser)
    3. IP geolocation (ip-api.com)
    4. Manual input (latitude/longitude/elevation or Maidenhead grid locator)

The retrieved coordinates are saved to SQLite app_settings and reused on the next startup.
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
# Constants
# ---------------------------------------------------------------------------

_IP_API_URL = "http://ip-api.com/json/?fields=status,lat,lon,city,country"
_GPSD_HOST = "localhost"
_GPSD_PORT = 2947
_GPSD_MAX_PACKETS = 20


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


class LocationSource(StrEnum):
    """Source of the location information"""

    GPS = "GPS"
    BROWSER = "Browser"
    IP = "IP"
    MANUAL = "Manual"


@dataclass
class Location:
    """
    Station location information.

    Attributes:
        latitude_deg:  Latitude (degrees, positive north)
        longitude_deg: Longitude (degrees, positive east)
        elevation_m:   Elevation (m)
        source:        Source of the location
        accuracy_m:    Accuracy (m). None if unknown.
        city:          City name (set when using IP geolocation)
        country:       Country name (set when using IP geolocation)
    """

    latitude_deg: float
    longitude_deg: float
    elevation_m: float
    source: LocationSource
    accuracy_m: float | None = None
    city: str = field(default="")
    country: str = field(default="")


# ---------------------------------------------------------------------------
# Maidenhead grid locator conversion
# ---------------------------------------------------------------------------


def grid_to_latlon(grid: str) -> tuple[float, float]:
    """
    Convert a Maidenhead grid locator to latitude and longitude.

    Args:
        grid: Grid locator string (4 or 6 characters, e.g. "PM85", "PM85ib")

    Returns:
        Tuple of (latitude_deg, longitude_deg). Positive north and east.

    Raises:
        ValueError: If the format is invalid
    """
    g = grid.upper().strip()
    if len(g) not in (4, 6):
        raise ValueError(f"Grid locator must be 4 or 6 characters: {grid!r}")

    if not (g[0].isalpha() and g[1].isalpha()):
        raise ValueError(f"Invalid grid locator (field characters are not alphabetic): {grid!r}")

    f0 = ord(g[0]) - ord("A")
    f1 = ord(g[1]) - ord("A")
    if f0 > 17 or f1 > 17:
        raise ValueError(f"Field characters out of range (only A-R are valid): {grid!r}")

    if not (g[2].isdigit() and g[3].isdigit()):
        raise ValueError(f"Invalid grid locator (square digits are not numeric): {grid!r}")

    s0 = int(g[2])
    s1 = int(g[3])

    lon = f0 * 20.0 - 180.0 + s0 * 2.0
    lat = f1 * 10.0 - 90.0 + s1 * 1.0

    if len(g) == 6:
        if not (g[4].isalpha() and g[5].isalpha()):
            raise ValueError(
                f"Invalid grid locator (subsquare characters are not alphabetic): {grid!r}"
            )
        ss0 = ord(g[4]) - ord("A")
        ss1 = ord(g[5]) - ord("A")
        if ss0 > 23 or ss1 > 23:
            raise ValueError(f"Subsquare characters out of range (only A-X are valid): {grid!r}")
        # Subsquare resolution: 5' longitude, 2.5' latitude
        lon += ss0 * (5.0 / 60.0) + (2.5 / 60.0)  # + center offset
        lat += ss1 * (2.5 / 60.0) + (1.25 / 60.0)
    else:
        # Square center
        lon += 1.0
        lat += 0.5

    return lat, lon


# ---------------------------------------------------------------------------
# LocationManager
# ---------------------------------------------------------------------------


class LocationManager:
    """
    Retrieves, saves, and manages the station location.

    Priority order: GPS → cached location (Browser/Manual) → IP
    The retrieved location is persisted in SQLite app_settings and reused on the next startup.
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
    # Properties
    # ------------------------------------------------------------------ #

    @property
    def current(self) -> Location | None:
        """Current location (None if not yet retrieved)."""
        return self._current

    @property
    def status_text(self) -> str:
        """
        Return the text to display in the status bar.

        Example: "JF9SOM / QTH: 35.6895°N 139.6917°E (Manual)"
        """
        loc = self._current
        if loc is None:
            return "QTH: Not set"
        ns = "N" if loc.latitude_deg >= 0 else "S"
        ew = "E" if loc.longitude_deg >= 0 else "W"
        lat = abs(loc.latitude_deg)
        lon = abs(loc.longitude_deg)
        qth = f"QTH: {lat:.4f}°{ns} {lon:.4f}°{ew} ({loc.source.value})"
        callsign = self.get_callsign()
        return f"{callsign} / {qth}" if callsign else qth

    # ------------------------------------------------------------------ #
    # Public API — synchronous (setting and saving)
    # ------------------------------------------------------------------ #

    def from_manual(
        self,
        latitude_deg: float,
        longitude_deg: float,
        elevation_m: float = 0.0,
    ) -> Location:
        """
        Set and save the location from manual input.

        Args:
            latitude_deg:  Latitude (degrees, positive north)
            longitude_deg: Longitude (degrees, positive east)
            elevation_m:   Elevation (m)

        Returns:
            The configured Location
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
        Set and save the location from a Maidenhead grid locator.

        Args:
            grid:        Grid locator string (e.g. "PM85")
            elevation_m: Elevation (m)

        Returns:
            The configured Location

        Raises:
            ValueError: If the format is invalid
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
        Set and save the coordinates received from the browser Geolocation API.

        Returns:
            The configured Location
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

    def get_callsign(self) -> str:
        """Return the saved callsign from app_settings. Returns an empty string if not set."""
        row = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = 'callsign'",
        ).fetchone()
        if row is None:
            return ""
        return str(row[0])

    def save_callsign(self, callsign: str) -> None:
        """Save the callsign to app_settings."""
        self._conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES ('callsign', ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = excluded.updated_at""",
            (callsign,),
        )
        self._conn.commit()

    def get_grid(self) -> str:
        """Return the saved grid locator string from app_settings. Returns '' if not set."""
        row = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = 'grid_locator'",
        ).fetchone()
        if row is None:
            return ""
        return str(row[0])

    def save_grid(self, grid: str) -> None:
        """Save the grid locator string to app_settings."""
        self._conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES ('grid_locator', ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = excluded.updated_at""",
            (grid.upper().strip(),),
        )
        self._conn.commit()

    def save(self, loc: Location) -> None:
        """Persist the location to the app_settings table."""
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
        Load the saved location from app_settings.

        Returns:
            The saved Location, or None if not saved or loading fails.
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
            logger.warning("Failed to load saved location: %s", exc)
            return None

    # ------------------------------------------------------------------ #
    # Public API — asynchronous (network retrieval)
    # ------------------------------------------------------------------ #

    async def from_gps(self) -> Location | None:
        """
        Retrieve GPS coordinates via the gpsd daemon.

        Returns None if gpsd is not running or python-gps is not installed.
        Delegates blocking I/O to asyncio.to_thread.

        Returns:
            Retrieved Location, or None if unavailable.
        """
        return await asyncio.to_thread(self._from_gps_sync)

    def _from_gps_sync(self) -> Location | None:
        """GPS retrieval from gpsd (synchronous, blocking)."""
        try:
            import gps as gpsd_lib
        except ImportError:
            logger.debug("python-gps is not installed; skipping GPS retrieval")
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
            logger.debug("GPS retrieval failed: %s", exc)
        return None

    async def from_ip(self) -> Location | None:
        """
        Retrieve location via IP geolocation using ip-api.com (city-level accuracy).

        Returns None when offline or when the API is unavailable.

        Returns:
            Retrieved Location, or None if unavailable.
        """
        try:
            resp = await self._http.get(_IP_API_URL)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            if data.get("status") != "success":
                logger.warning("IP geolocation failed: %s", data)
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
            logger.warning("IP geolocation exception: %s", exc)
            return None

    async def detect(self) -> Location | None:
        """
        Automatically retrieve the location according to the priority order.

        Priority order:
            1. GPS (gpsd)
            2. Cached location (including Browser/Manual)
            3. Saved location (loaded from DB)
            4. IP geolocation

        Returns:
            Retrieved Location, or None if all sources fail.
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
