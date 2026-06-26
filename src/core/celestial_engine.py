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
from typing import Any

from skyfield import almanac
from skyfield.api import Loader, wgs84

from core.engine import Observation, PassInfo

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

    d = Path(user_data_dir("fbsat59", "fbsat59")) / "ephemeris"
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
        self._eph: Any = None
        self._ts: Any = None

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

    def moon_subpoint(self, at: datetime | None = None) -> tuple[float, float] | None:
        """Return the geographic sub-lunar point (lat, lon) in degrees.

        The sub-lunar point is the Earth surface location directly beneath the Moon
        (i.e. where the Moon is at zenith).  Computed geocentrically from DE421.

        Returns:
            (latitude_deg, longitude_deg) or None when the ephemeris is not loaded.
        """
        if self._eph is None or self._ts is None:
            return None
        try:
            t_dt = at if at is not None else datetime.now(UTC)
            t = self._ts.from_datetime(t_dt)
            earth = self._eph["earth"]
            moon = self._eph[_MOON_BODY]
            astrometric = earth.at(t).observe(moon)
            sub = wgs84.subpoint(astrometric)
            return float(sub.latitude.degrees), float(sub.longitude.degrees)
        except Exception:
            logger.exception("CelestialEngine.moon_subpoint() failed")
            return None

    def moon_track(
        self,
        observer_lat: float,
        observer_lon: float,
        observer_elev_m: float,
        hours: float = 24.0,
        step_minutes: float = 30.0,
    ) -> list[tuple[float, float]]:
        """Return a list of (azimuth_deg, elevation_deg) points spanning the given hours.

        Samples the Moon's position at regular intervals starting from now.
        Used to draw the 24-hour arc on the radar.

        Returns:
            List of (az, el) tuples, empty when the ephemeris is not loaded.
        """
        if self._eph is None or self._ts is None:
            return []
        now = datetime.now(UTC)
        n_steps = int(hours * 60 / step_minutes) + 1
        result: list[tuple[float, float]] = []
        for i in range(n_steps):
            at = now + timedelta(minutes=i * step_minutes)
            obs = self._observe_body(
                _MOON_BODY, MOON_ID, observer_lat, observer_lon, observer_elev_m, at
            )
            if obs is not None:
                result.append((obs.azimuth_deg, obs.elevation_deg))
        return result

    def moon_events(
        self,
        observer_lat: float,
        observer_lon: float,
        observer_elev_m: float,
        start: datetime,
        end: datetime,
    ) -> list[PassInfo]:
        """Return Moonrise/transit/Moonset events as PassInfo objects.

        Each visible window (Moonrise → Moonset) is represented as one PassInfo
        where aos=Moonrise, tca=transit, los=Moonset.  Events whose Moonrise
        falls before *start* but whose Moonset falls within the window are also
        included (the Moon may already be above the horizon at *start*).

        Returns an empty list when the ephemeris is not loaded.
        """
        if self._eph is None or self._ts is None:
            return []
        try:
            observer = wgs84.latlon(observer_lat, observer_lon, elevation_m=observer_elev_m)
            moon_body = self._eph[_MOON_BODY]

            # Search ±1 day around the window to catch in-progress rises/sets
            search_start = start - timedelta(days=1)
            search_end = end + timedelta(hours=1)
            t0 = self._ts.from_datetime(search_start)
            t1 = self._ts.from_datetime(search_end)

            f = almanac.risings_and_settings(self._eph, moon_body, observer)
            times, is_rise = almanac.find_discrete(t0, t1, f)

            # Build (rise_dt, set_dt) pairs
            events: list[tuple[datetime, bool]] = [
                (t.utc_datetime(), bool(r)) for t, r in zip(times, is_rise, strict=False)
            ]

            pairs: list[tuple[datetime, datetime]] = []
            pending_rise: datetime | None = None
            for ev_dt, rising in events:
                if rising:
                    pending_rise = ev_dt
                elif pending_rise is not None:
                    pairs.append((pending_rise, ev_dt))
                    pending_rise = None

            # If Moon is already above the horizon at start of the search window,
            # there may be a leading set event with no preceding rise — represent
            # it with an artificial AOS at the window start.
            set_times = [ev_dt for ev_dt, r in events if not r]
            rise_times = [ev_dt for ev_dt, r in events if r]
            if set_times and (not rise_times or set_times[0] < rise_times[0]):
                pairs.insert(0, (start, set_times[0]))

            results: list[PassInfo] = []
            for rise_dt, set_dt in pairs:
                # Only include pairs that overlap with [start, end]
                if set_dt < start or rise_dt > end:
                    continue

                # Transit: sample elevation at 15-minute intervals to find the peak
                duration_s = (set_dt - rise_dt).total_seconds()
                n_steps = max(4, int(duration_s / 900))
                step_s = duration_s / n_steps
                best_el = -90.0
                best_t = rise_dt + timedelta(seconds=duration_s / 2)
                for i in range(n_steps + 1):
                    t_sample = rise_dt + timedelta(seconds=i * step_s)
                    obs = self._observe_body(
                        _MOON_BODY,
                        MOON_ID,
                        observer_lat,
                        observer_lon,
                        observer_elev_m,
                        t_sample,
                    )
                    if obs is not None and obs.elevation_deg > best_el:
                        best_el = obs.elevation_deg
                        best_t = t_sample

                # AZ at rise and set
                obs_rise = self._observe_body(
                    _MOON_BODY, MOON_ID, observer_lat, observer_lon, observer_elev_m, rise_dt
                )
                obs_set = self._observe_body(
                    _MOON_BODY, MOON_ID, observer_lat, observer_lon, observer_elev_m, set_dt
                )
                aos_az = obs_rise.azimuth_deg if obs_rise else 0.0
                los_az = obs_set.azimuth_deg if obs_set else 0.0

                results.append(
                    PassInfo(
                        norad_cat_id=MOON_ID,
                        aos=rise_dt,
                        tca=best_t,
                        los=set_dt,
                        max_elevation_deg=max(0.0, best_el),
                        aos_azimuth_deg=aos_az,
                        los_azimuth_deg=los_az,
                        duration_s=duration_s,
                    )
                )
            return results
        except Exception:
            logger.exception("CelestialEngine.moon_events() failed")
            return []

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
            t = self._ts.from_datetime(t_dt)

            earth = self._eph["earth"]
            body = self._eph[body_name]
            observer = earth + wgs84.latlon(observer_lat, observer_lon, elevation_m=observer_elev_m)

            apparent = observer.at(t).observe(body).apparent()
            alt, az, distance = apparent.altaz()

            # Range rate: finite-difference over 1 second (positive = receding)
            t2 = self._ts.from_datetime(t_dt + timedelta(seconds=1))
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
