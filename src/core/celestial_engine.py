"""
Celestial body tracking engine

CelestialEngine — Real-time position calculation for the Moon and other
                  solar-system bodies using the JPL DE421 ephemeris via Skyfield.

The DE421 ephemeris file (~17 MB, valid through 2053) is downloaded once to the
user-data directory and reused across sessions.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from skyfield.api import Loader, wgs84

from core.engine import Observation

logger = logging.getLogger(__name__)

# Sentinel value used in place of a NORAD ID throughout the UI
MOON_ID: int = -1

# Skyfield body name for the Moon inside DE421
_MOON_BODY: str = "moon"

# JPL ephemeris filename (covers 1900–2053, ~17 MB)
_EPH_FILENAME: str = "de421.bsp"


def _eph_dir() -> Path:
    """Return the user-data directory used to cache the ephemeris file."""
    from platformdirs import user_data_dir

    d = Path(user_data_dir("gpredict-improved", "gpredict-improved")) / "ephemeris"
    d.mkdir(parents=True, exist_ok=True)
    return d


class CelestialEngine:
    """
    Skyfield wrapper for solar-system bodies (Moon, Sun, planets).

    The DE421 ephemeris is loaded lazily; call load() once from a background
    thread before using observe().  All public methods are thread-safe.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._eph: object | None = None
        self._ts: object | None = None

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def load(self) -> bool:
        """Load (and if necessary download) the DE421 ephemeris.

        Returns True on success, False on failure.
        Safe to call multiple times — subsequent calls are no-ops.
        """
        with self._lock:
            if self._eph is not None:
                return True
            try:
                loader = Loader(str(_eph_dir()))
                self._ts = loader.timescale()
                self._eph = loader(_EPH_FILENAME)
                logger.info("DE421 ephemeris loaded from %s", _eph_dir())
                return True
            except Exception:
                logger.exception("Failed to load DE421 ephemeris")
                return False

    @property
    def is_loaded(self) -> bool:
        """True when the ephemeris has been successfully loaded."""
        return self._eph is not None

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def observe_moon(
        self,
        observer_lat: float,
        observer_lon: float,
        observer_elev_m: float,
        at: datetime | None = None,
    ) -> Observation | None:
        """Return an Observation for the Moon as seen from the ground station.

        Args:
            observer_lat: Observer latitude (degrees, north positive).
            observer_lon: Observer longitude (degrees, east positive).
            observer_elev_m: Observer elevation above WGS84 ellipsoid (metres).
            at: UTC datetime (default: now).

        Returns:
            Observation dataclass with norad_cat_id=MOON_ID, or None when the
            ephemeris is not yet loaded.
        """
        return self._observe_body(
            _MOON_BODY, MOON_ID, observer_lat, observer_lon, observer_elev_m, at
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _observe_body(
        self,
        body_name: str,
        norad_id: int,
        observer_lat: float,
        observer_lon: float,
        observer_elev_m: float,
        at: datetime | None,
    ) -> Observation | None:
        if self._eph is None or self._ts is None:
            return None
        try:
            t_dt = at if at is not None else datetime.now(UTC)
            t = self._ts.from_datetime(t_dt)  # type: ignore[union-attr]

            earth = self._eph["earth"]  # type: ignore[index]
            body = self._eph[body_name]  # type: ignore[index]
            observer = earth + wgs84.latlon(observer_lat, observer_lon, elevation_m=observer_elev_m)

            apparent = observer.at(t).observe(body).apparent()
            alt, az, distance = apparent.altaz()

            # Range rate: finite-difference over 1 second (positive = receding)
            t2 = self._ts.from_datetime(t_dt + timedelta(seconds=1))  # type: ignore[union-attr]
            apparent2 = observer.at(t2).observe(body).apparent()
            _, _, distance2 = apparent2.altaz()
            range_rate_km_s = float(distance2.km) - float(distance.km)

            return Observation(
                norad_cat_id=norad_id,
                timestamp=t_dt,
                elevation_deg=float(alt.degrees),
                azimuth_deg=float(az.degrees),
                range_km=float(distance.km),
                range_rate_km_s=range_rate_km_s,
                is_above_horizon=float(alt.degrees) > 0.0,
            )
        except Exception:
            logger.exception("CelestialEngine._observe_body() failed for %s", body_name)
            return None
