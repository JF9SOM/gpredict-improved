"""
TLE（Two-Line Element）自動更新マネージャー

複数ソース（CelesTrak・Space-Track・AMSAT）からTLEを取得し、
品質スコアリングを行ってSQLiteに保存する。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from skyfield.api import EarthSatellite, load

# TLEソース定義 (優先度順)
TLE_SOURCES = [
    {
        "name": "celestrak-amateur",
        "url": "https://celestrak.org/SOCRATES/query.php",
        "params": {"GROUP": "amateur", "FORMAT": "tle"},
        "priority": 1,
        "update_interval_hours": 2,
    },
    {
        "name": "celestrak-stations",
        "url": "https://celestrak.org/SOCRATES/query.php",
        "params": {"GROUP": "stations", "FORMAT": "tle"},
        "priority": 0,
        "update_interval_hours": 1,
    },
    # CelesTrak GP data (JSON/OMM形式、より安定)
    {
        "name": "celestrak-gp-amateur",
        "url": "https://celestrak.org/SOCRATES/query.php",
        "params": {"GROUP": "amateur", "FORMAT": "json"},
        "priority": 2,
        "update_interval_hours": 2,
        "format": "json",
    },
]


def _calc_quality(epoch_dt: datetime) -> str:
    """TLEエポックからの経過時間で品質スコアを返す"""
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
    TLEの取得・保存・品質管理を担当するクラス。
    オフライン時はキャッシュで継続動作する。
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._ts = load.timescale()

    # ------------------------------------------------------------------ #
    # 取得
    # ------------------------------------------------------------------ #

    def get_tle(self, norad_cat_id: int) -> dict[str, Any] | None:
        """衛星のTLEデータをDBから取得する"""
        row = self._conn.execute(
            "SELECT * FROM tle_data WHERE norad_cat_id = ?",
            (norad_cat_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_earth_satellite(self, norad_cat_id: int) -> EarthSatellite | None:
        """Skyfieldで使えるEarthSatelliteオブジェクトを返す"""
        tle = self.get_tle(norad_cat_id)
        if not tle:
            return None
        return EarthSatellite(tle["line1"], tle["line2"], tle["name"], self._ts)

    def get_all_quality_status(self) -> list[dict[str, Any]]:
        """全衛星のTLE品質状況一覧を返す"""
        rows = self._conn.execute("""
            SELECT s.norad_cat_id, s.name, t.quality_score,
                   t.fetched_at, t.epoch, t.source
            FROM satellites s
            LEFT JOIN tle_data t ON s.norad_cat_id = t.norad_cat_id
            ORDER BY t.quality_score ASC NULLS FIRST
        """).fetchall()
        return [dict(r) for r in rows]

    def needs_update(self, norad_cat_id: int, max_age_hours: float = 4.0) -> bool:
        """TLEの更新が必要かどうかを判定する"""
        row = self._conn.execute(
            "SELECT fetched_at FROM tle_data WHERE norad_cat_id = ?",
            (norad_cat_id,),
        ).fetchone()
        if not row:
            return True
        fetched = datetime.fromisoformat(row["fetched_at"])
        return datetime.now(UTC) - fetched > timedelta(hours=max_age_hours)

    # ------------------------------------------------------------------ #
    # 更新
    # ------------------------------------------------------------------ #

    async def fetch_and_update(
        self,
        source_name: str = "celestrak-amateur",
        progress_callback: Any = None,
    ) -> dict[str, int]:
        """
        指定ソースからTLEを取得してDBを更新する。
        Returns: {"inserted": N, "updated": N, "errors": N}
        """
        source = next((s for s in TLE_SOURCES if s["name"] == source_name), TLE_SOURCES[0])
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

        # TLEテキスト形式をパース（3行1組）
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
        for idx, (name, line1, line2) in enumerate(tle_triples):
            if progress_callback:
                progress_callback(idx + 1, len(tle_triples))

            try:
                sat = EarthSatellite(line1, line2, name, self._ts)
                norad = int(line1[2:7])
                epoch_dt = sat.epoch.utc_datetime()
                quality = _calc_quality(epoch_dt)

                # 衛星レコード確保
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

                # 履歴に追加
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
                            source=?, fetched_at=?, quality_score=?
                        WHERE norad_cat_id=?
                    """,
                        (
                            name,
                            line1,
                            line2,
                            epoch_dt.isoformat(),
                            source_name,
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
                             source, fetched_at, quality_score)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            norad,
                            name,
                            line1,
                            line2,
                            epoch_dt.isoformat(),
                            source_name,
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
        """
        1衛星のTLEをSpace-TrackまたはCelesTrakから取得する。
        特定衛星だけ手動更新したいときに使用。
        """
        url = "https://celestrak.org/SOCRATES/query.php"
        params = {"CATNR": str(norad_cat_id), "FORMAT": "tle"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                lines = [ln.strip() for ln in r.text.splitlines() if ln.strip()]
                if len(lines) >= 3:
                    result = await self.fetch_and_update.__func__(  # type: ignore[attr-defined]
                        self, "celestrak-single"
                    )
                    return result["errors"] == 0
        except httpx.HTTPError:
            pass
        return False

    def add_manual_tle(
        self,
        norad_cat_id: int,
        name: str,
        line1: str,
        line2: str,
    ) -> bool:
        """手動でTLEを追加・更新する（GUIから入力した場合など）"""
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
