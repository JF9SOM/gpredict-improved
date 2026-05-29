"""
TLE (Two-Line Element) automatic update manager

Fetches TLEs from multiple sources (CelesTrak, Space-Track, AMSAT),
applies quality scoring, and saves them to SQLite.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from skyfield.api import EarthSatellite, load

# TLE source definitions (in priority order)
# CelesTrak GP API: https://celestrak.org/NORAD/documentation/gp-data-formats.php
TLE_SOURCES: list[dict[str, Any]] = [
    {
        "name": "celestrak-stations",
        "url": "https://celestrak.org/NORAD/elements/gp.php",
        "params": {"GROUP": "STATIONS", "FORMAT": "TLE"},
        "group": "stations",
        "priority": 0,
        "update_interval_hours": 1,
    },
    {
        "name": "celestrak-amateur",
        "url": "https://celestrak.org/NORAD/elements/gp.php",
        "params": {"GROUP": "AMATEUR", "FORMAT": "TLE"},
        "group": "amateur",
        "priority": 1,
        "update_interval_hours": 2,
    },
    {
        "name": "celestrak-cubesat",
        "url": "https://celestrak.org/NORAD/elements/gp.php",
        "params": {"GROUP": "CUBESAT", "FORMAT": "TLE"},
        "group": "cubesat",
        "priority": 2,
        "update_interval_hours": 4,
    },
    {
        "name": "celestrak-weather",
        "url": "https://celestrak.org/NORAD/elements/gp.php",
        "params": {"GROUP": "WEATHER", "FORMAT": "TLE"},
        "group": "weather",
        "priority": 3,
        "update_interval_hours": 6,
    },
    {
        "name": "celestrak-earth-obs",
        "url": "https://celestrak.org/NORAD/elements/gp.php",
        "params": {"GROUP": "resource", "FORMAT": "TLE"},
        "group": "earth-obs",
        "priority": 4,
        "update_interval_hours": 12,
    },
    {
        "name": "celestrak-science",
        "url": "https://celestrak.org/NORAD/elements/gp.php",
        "params": {"GROUP": "SCIENCE", "FORMAT": "TLE"},
        "group": "science",
        "priority": 5,
        "update_interval_hours": 12,
    },
]


_SOURCE_DB_VALUE: dict[str, str] = {
    "celestrak-stations": "celestrak",
    "celestrak-amateur": "celestrak",
    "celestrak-cubesat": "celestrak",
    "celestrak-weather": "celestrak",
    "celestrak-earth-obs": "celestrak",
    "celestrak-science": "celestrak",
    "celestrak-single": "celestrak",
    "satnogs-provisional": "satnogs",
}

# SATNOGS TLE API endpoint for per-satellite lookup
SATNOGS_TLE_URL = "https://db.satnogs.org/api/tle/"


def _to_db_source(source_name: str) -> str:
    """Convert a source name to a value that satisfies the DB CHECK constraint"""
    return _SOURCE_DB_VALUE.get(source_name, source_name)


def _calc_quality(epoch_dt: datetime) -> str:
    """Return the quality score based on elapsed time since the TLE epoch"""
    age = (
        datetime.now(UTC) - epoch_dt.replace(tzinfo=UTC)
        if epoch_dt.tzinfo is None
        else datetime.now(UTC) - epoch_dt
    )
    hours = age.total_seconds() / 3600
    if hours < 6:
        return "excellent"
    elif hours < 24:
        return "good"
    elif hours < 72:
        return "fair"
    return "poor"


class TLEManager:
    """
    Class responsible for fetching, saving, and quality-managing TLEs.
    Falls back to the cache when offline.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._ts = load.timescale()

    # ------------------------------------------------------------------ #
    # Retrieval
    # ------------------------------------------------------------------ #

    def get_tle(self, norad_cat_id: int) -> dict[str, Any] | None:
        """Retrieve TLE data for a satellite from the DB"""
        row = self._conn.execute(
            "SELECT * FROM tle_data WHERE norad_cat_id = ?",
            (norad_cat_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_earth_satellite(self, norad_cat_id: int) -> EarthSatellite | None:
        """Return an EarthSatellite object usable with Skyfield"""
        tle = self.get_tle(norad_cat_id)
        if not tle:
            return None
        return EarthSatellite(tle["line1"], tle["line2"], tle["name"], self._ts)

    def get_all_quality_status(self) -> list[dict[str, Any]]:
        """Return the TLE quality status list for all satellites"""
        rows = self._conn.execute("""
            SELECT s.norad_cat_id, s.name, t.quality_score,
                   t.fetched_at, t.epoch, t.source
            FROM satellites s
            LEFT JOIN tle_data t ON s.norad_cat_id = t.norad_cat_id
            ORDER BY t.quality_score ASC NULLS FIRST
        """).fetchall()
        return [dict(r) for r in rows]

    def needs_update(self, norad_cat_id: int, max_age_hours: float = 4.0) -> bool:
        """Determine whether the TLE needs to be updated"""
        row = self._conn.execute(
            "SELECT fetched_at FROM tle_data WHERE norad_cat_id = ?",
            (norad_cat_id,),
        ).fetchone()
        if not row:
            return True
        fetched = datetime.fromisoformat(row["fetched_at"])
        return datetime.now(UTC) - fetched > timedelta(hours=max_age_hours)

    # ------------------------------------------------------------------ #
    # Update
    # ------------------------------------------------------------------ #

    async def fetch_and_update(
        self,
        source_name: str = "celestrak-amateur",
        progress_callback: Any = None,
    ) -> dict[str, int]:
        """
        Fetch TLEs from the specified source and update the DB.
        Returns: {"inserted": N, "updated": N, "errors": N}
        """
        source = next((s for s in TLE_SOURCES if s["name"] == source_name), TLE_SOURCES[0])
        tle_group = str(source.get("group", "amateur"))
        stats = {"inserted": 0, "updated": 0, "errors": 0}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(source["url"], params=source.get("params", {}))
                r.raise_for_status()
                text = r.text
        except httpx.HTTPError as e:
            print(f"[TLEManager] fetch error from {source_name}: {e}")
            stats["errors"] = 1
            return stats

        # Parse TLE text format (3-line groups)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        tle_triples = []
        i = 0
        while i < len(lines) - 2:
            if lines[i + 1].startswith("1 ") and lines[i + 2].startswith("2 "):
                tle_triples.append((lines[i], lines[i + 1], lines[i + 2]))
                i += 3
            else:
                i += 1

        now = datetime.now(UTC).isoformat()
        db_source = _to_db_source(source_name)
        for idx, (name, line1, line2) in enumerate(tle_triples):
            if progress_callback:
                progress_callback(idx + 1, len(tle_triples))

            try:
                sat = EarthSatellite(line1, line2, name, self._ts)
                norad = int(line1[2:7])
                epoch_dt = sat.epoch.utc_datetime()
                quality = _calc_quality(epoch_dt)

                # Ensure the satellite record exists
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO satellites (norad_cat_id, name, updated_at)
                    VALUES (?, ?, ?)
                """,
                    (norad, name, now),
                )

                existing = self._conn.execute(
                    "SELECT norad_cat_id FROM tle_data WHERE norad_cat_id = ?",
                    (norad,),
                ).fetchone()

                # Append to history
                self._conn.execute(
                    """
                    INSERT INTO tle_history (norad_cat_id, name, line1, line2, epoch, source)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (norad, name, line1, line2, epoch_dt.isoformat(), source_name),
                )

                if existing:
                    self._conn.execute(
                        """
                        UPDATE tle_data SET
                            name=?, line1=?, line2=?, epoch=?,
                            source=?, tle_group=?, fetched_at=?, quality_score=?
                        WHERE norad_cat_id=?
                    """,
                        (
                            name,
                            line1,
                            line2,
                            epoch_dt.isoformat(),
                            db_source,
                            tle_group,
                            now,
                            quality,
                            norad,
                        ),
                    )
                    stats["updated"] += 1
                else:
                    self._conn.execute(
                        """
                        INSERT INTO tle_data
                            (norad_cat_id, name, line1, line2, epoch,
                             source, tle_group, fetched_at, quality_score)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            norad,
                            name,
                            line1,
                            line2,
                            epoch_dt.isoformat(),
                            db_source,
                            tle_group,
                            now,
                            quality,
                        ),
                    )
                    stats["inserted"] += 1

            except Exception as e:
                print(f"[TLEManager] parse error for {name}: {e}")
                stats["errors"] += 1

        self._conn.commit()
        self._log_sync(source_name, stats)
        return stats

    async def fetch_single(self, norad_cat_id: int) -> bool:
        """Fetch the TLE for a single satellite from CelesTrak and add it to the DB.

        Use this when a satellite is not included in a group fetch
        (e.g. ORIGAMI-2 / NORAD 57168) and needs to be added individually.
        """
        url = "https://celestrak.org/NORAD/elements/gp.php"
        params = {"CATNR": str(norad_cat_id), "FORMAT": "TLE"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                lines = [ln.strip() for ln in r.text.splitlines() if ln.strip()]
                if len(lines) >= 3:
                    name, line1, line2 = lines[0], lines[1], lines[2]
                    return self.add_manual_tle(norad_cat_id, name, line1, line2)
        except httpx.HTTPError as e:
            print(f"[TLEManager] fetch_single error: {e}")
        return False

    def add_manual_tle(
        self,
        norad_cat_id: int,
        name: str,
        line1: str,
        line2: str,
    ) -> bool:
        """Manually add or update a TLE (e.g. when entered via the GUI)"""
        try:
            sat = EarthSatellite(line1, line2, name, self._ts)
            epoch_dt = sat.epoch.utc_datetime()
            quality = _calc_quality(epoch_dt)
            now = datetime.now(UTC).isoformat()

            self._conn.execute(
                """
                INSERT OR IGNORE INTO satellites (norad_cat_id, name, updated_at)
                VALUES (?, ?, ?)
            """,
                (norad_cat_id, name, now),
            )

            self._conn.execute(
                """
                INSERT OR REPLACE INTO tle_data
                    (norad_cat_id, name, line1, line2, epoch,
                     source, fetched_at, quality_score)
                VALUES (?, ?, ?, ?, ?, 'manual', ?, ?)
            """,
                (norad_cat_id, name, line1, line2, epoch_dt.isoformat(), now, quality),
            )
            self._conn.commit()
            return True
        except Exception as e:
            print(f"[TLEManager] invalid TLE: {e}")
            return False

    async def fetch_provisional_tles(
        self,
        progress_callback: Any = None,
    ) -> dict[str, int]:
        """Fetch TLEs for provisional (NORAD >= 90000) satellites via the SATNOGS TLE API.

        For each visible satellite with a provisional NORAD ID (>= 90000), this method
        queries the SATNOGS TLE API which returns the best available TLE regardless of
        whether norad_follow_id is set publicly.  The TLE is stored under the provisional
        ID so the satellite's position can be shown on the map.

        When the TLE line1 contains a *different* NORAD ID (i.e. SATNOGS internally knows
        the official ID), the migration pipeline is triggered automatically if the official
        satellite record already exists in our DB.

        Returns:
            {"inserted": N, "updated": N, "no_tle": N, "errors": N}
        """
        rows = self._conn.execute(
            "SELECT norad_cat_id, name FROM satellites"
            " WHERE norad_cat_id >= 90000 AND is_hidden = 0"
        ).fetchall()

        stats: dict[str, int] = {"inserted": 0, "updated": 0, "no_tle": 0, "errors": 0}
        now = datetime.now(UTC).isoformat()

        async with httpx.AsyncClient(timeout=15.0) as client:
            for idx, row in enumerate(rows):
                fake_id = int(row["norad_cat_id"])
                sat_name = str(row["name"])

                if progress_callback:
                    progress_callback(idx + 1, len(rows))

                try:
                    r = await client.get(
                        SATNOGS_TLE_URL,
                        params={"norad_cat_id": fake_id, "format": "json"},
                        timeout=10.0,
                    )
                    r.raise_for_status()
                    data = r.json()
                except httpx.HTTPError as exc:
                    print(f"[TLEManager] provisional TLE fetch error for {fake_id}: {exc}")
                    stats["errors"] += 1
                    continue
                except Exception as exc:
                    print(f"[TLEManager] provisional TLE unexpected error for {fake_id}: {exc}")
                    stats["errors"] += 1
                    continue

                if not isinstance(data, dict) or "tle1" not in data:
                    stats["no_tle"] += 1
                    continue

                line1: str = str(data["tle1"])
                line2: str = str(data["tle2"])
                # Prefer the name already stored in our DB over Space-Track object names
                name = sat_name

                try:
                    sat_obj = EarthSatellite(line1, line2, name, self._ts)
                    epoch_dt = sat_obj.epoch.utc_datetime()
                    quality = _calc_quality(epoch_dt)
                except Exception as exc:
                    print(f"[TLEManager] provisional TLE parse error for {fake_id}: {exc}")
                    stats["errors"] += 1
                    continue

                # Check whether the TLE line1 encodes a different (official) NORAD ID
                tle_norad = int(line1[2:7])
                if tle_norad != fake_id:
                    # SATNOGS internally resolved this provisional ID to an official one.
                    # Trigger the migration pipeline if the official satellite is already
                    # present in our DB (e.g. fetched earlier from CelesTrak).
                    official_exists = self._conn.execute(
                        "SELECT norad_cat_id FROM satellites WHERE norad_cat_id = ?",
                        (tle_norad,),
                    ).fetchone()
                    if official_exists:
                        # Import lazily to avoid a circular dependency at module level
                        from data.transmitter_manager import TransmitterManager  # noqa: PLC0415

                        TransmitterManager(self._conn)._run_migration_pipeline(fake_id, tle_norad)

                # Never overwrite a manually entered TLE
                existing = self._conn.execute(
                    "SELECT source FROM tle_data WHERE norad_cat_id = ?",
                    (fake_id,),
                ).fetchone()
                if existing and existing["source"] == "manual":
                    continue

                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO tle_data
                        (norad_cat_id, name, line1, line2, epoch,
                         source, tle_group, fetched_at, quality_score)
                    VALUES (?, ?, ?, ?, ?, 'satnogs', 'provisional', ?, ?)
                    """,
                    (fake_id, name, line1, line2, epoch_dt.isoformat(), now, quality),
                )
                if existing:
                    stats["updated"] += 1
                else:
                    stats["inserted"] += 1

        self._conn.commit()
        self._log_sync("satnogs-provisional", stats)
        return stats

    def _log_sync(self, sync_type: str, stats: dict[str, int]) -> None:
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """
            INSERT INTO sync_log
                (sync_type, started_at, finished_at, status, records_updated)
            VALUES (?, ?, ?, ?, ?)
        """,
            (sync_type, now, now, "success", stats.get("inserted", 0) + stats.get("updated", 0)),
        )
        self._conn.commit()
