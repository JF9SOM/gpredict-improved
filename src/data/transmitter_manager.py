"""
トランスポンダ管理モジュール

SATNOGSからのデータ取得と、手動追加データの統合管理。
manual_override=True のレコードはSATNOGS同期で上書きされない。
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

SATNOGS_API_BASE = "https://db.satnogs.org/api"
SATNOGS_TRANSMITTERS_URL = f"{SATNOGS_API_BASE}/transmitters/"
SATNOGS_SATELLITES_URL   = f"{SATNOGS_API_BASE}/satellites/"


class TransmitterManager:
    """
    トランスポンダ情報のCRUD + SATNOGS同期を管理するクラス。
    UIスレッドとバックグラウンドスレッドの両方から呼ばれるため、
    メソッドごとに独立したDB接続を使う（thread-safe）。
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------ #
    # 読み取り
    # ------------------------------------------------------------------ #

    def get_transmitters(
        self,
        norad_cat_id: int,
        include_dead: bool = False,
    ) -> list[dict[str, Any]]:
        """
        指定衛星のトランスポンダ一覧を返す。
        手動追加データとSATNOGSデータを統合して返す。
        """
        query = """
            SELECT * FROM transmitters
            WHERE norad_cat_id = ?
            {}
            ORDER BY alive DESC, source DESC, description
        """.format("" if include_dead else "AND alive = 1")

        rows = self._conn.execute(query, (norad_cat_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_all_satellites(self) -> list[dict[str, Any]]:
        """追尾可能な衛星一覧（TLEあり）を返す"""
        rows = self._conn.execute("""
            SELECT s.*, t.quality_score, t.fetched_at as tle_fetched_at
            FROM satellites s
            LEFT JOIN tle_data t ON s.norad_cat_id = t.norad_cat_id
            ORDER BY s.name
        """).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # 手動追加・編集・削除
    # ------------------------------------------------------------------ #

    def add_manual_transmitter(
        self,
        norad_cat_id: int,
        description: str,
        downlink_low: int,
        mode: str,
        *,
        uplink_low: int | None = None,
        uplink_high: int | None = None,
        downlink_high: int | None = None,
        invert: bool = False,
        ctcss_tone: float | None = None,
        ctcss_tone_type: str | None = None,
        baud: int | None = None,
        notes: str = "",
        xpdr_type: str = "Transponder",
    ) -> str:
        """
        手動でトランスポンダを追加する。
        返り値: 生成されたUUID
        """
        new_uuid = f"manual-{uuid.uuid4()}"
        now = datetime.now(UTC).isoformat()

        # 衛星レコードがなければ仮登録
        self._conn.execute("""
            INSERT OR IGNORE INTO satellites (norad_cat_id, name, updated_at)
            VALUES (?, ?, ?)
        """, (norad_cat_id, f"Satellite #{norad_cat_id}", now))

        self._conn.execute("""
            INSERT INTO transmitters (
                uuid, norad_cat_id, description, type,
                uplink_low, uplink_high, downlink_low, downlink_high,
                mode, invert, baud,
                ctcss_tone, ctcss_tone_type,
                alive, source, manual_override, notes, updated_at
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                1, 'manual', 1, ?, ?
            )
        """, (
            new_uuid, norad_cat_id, description, xpdr_type,
            uplink_low, uplink_high, downlink_low, downlink_high,
            mode, int(invert), baud,
            ctcss_tone, ctcss_tone_type,
            notes, now,
        ))
        self._conn.commit()
        return new_uuid

    def update_transmitter(
        self,
        xpdr_uuid: str,
        **fields: Any,
    ) -> None:
        """
        トランスポンダを更新する。
        SATNOGS由来でも編集した場合は manual_override=1 にする。
        """
        allowed = {
            "description", "uplink_low", "uplink_high",
            "downlink_low", "downlink_high", "mode", "invert",
            "ctcss_tone", "ctcss_tone_type", "baud",
            "alive", "notes",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return

        updates["manual_override"] = 1
        updates["updated_at"] = datetime.now(UTC).isoformat()

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [xpdr_uuid]
        self._conn.execute(
            f"UPDATE transmitters SET {set_clause} WHERE uuid = ?",
            values,
        )
        self._conn.commit()

    def delete_transmitter(self, xpdr_uuid: str) -> None:
        """トランスポンダを削除する（手動追加分のみ推奨）"""
        self._conn.execute(
            "DELETE FROM transmitters WHERE uuid = ?", (xpdr_uuid,)
        )
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # SATNOGS同期
    # ------------------------------------------------------------------ #

    async def sync_from_satnogs(
        self,
        norad_cat_id: int | None = None,
        progress_callback: Any = None,
    ) -> dict[str, int]:
        """
        SATNOGSからトランスポンダ情報を取得してDBを更新する。
        manual_override=True のレコードは上書きしない。

        Args:
            norad_cat_id: 指定すると1衛星のみ同期。Noneで全件。
            progress_callback: (current, total) を受け取るコールバック

        Returns:
            {"inserted": N, "updated": N, "skipped": N}
        """
        params: dict[str, Any] = {"format": "json", "status": "active"}
        if norad_cat_id:
            params["satellite__norad_cat_id"] = norad_cat_id

        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(SATNOGS_TRANSMITTERS_URL, params=params)
            r.raise_for_status()
            transmitters: list[dict[str, Any]] = r.json()

        stats = {"inserted": 0, "updated": 0, "skipped": 0}
        now = datetime.now(UTC).isoformat()

        for i, xpdr in enumerate(transmitters):
            if progress_callback:
                progress_callback(i + 1, len(transmitters))

            xpdr_uuid = xpdr.get("uuid", "")
            if not xpdr_uuid:
                continue

            # manual_override=1 のレコードはスキップ
            existing = self._conn.execute(
                "SELECT manual_override FROM transmitters WHERE uuid = ?",
                (xpdr_uuid,),
            ).fetchone()

            if existing and existing["manual_override"]:
                stats["skipped"] += 1
                continue

            sat_id = xpdr.get("norad_cat_id") or xpdr.get("satellite__norad_cat_id")
            if not sat_id:
                continue

            # 衛星レコード確保
            self._conn.execute("""
                INSERT OR IGNORE INTO satellites (norad_cat_id, name, updated_at)
                VALUES (?, ?, ?)
            """, (sat_id, xpdr.get("description", f"#{sat_id}"), now))

            row = (
                xpdr_uuid,
                sat_id,
                xpdr.get("description", ""),
                xpdr.get("type", "Transponder"),
                xpdr.get("uplink_low"),
                xpdr.get("uplink_high"),
                xpdr.get("downlink_low"),
                xpdr.get("downlink_high"),
                xpdr.get("mode"),
                int(bool(xpdr.get("invert", False))),
                xpdr.get("baud"),
                xpdr.get("ctcss_tone"),        # SATNOGSがあれば
                None,                           # tone_type: SATNOGSに無い場合
                int(bool(xpdr.get("alive", True))),
                now,
            )

            if existing:
                self._conn.execute("""
                    UPDATE transmitters SET
                        description=?, type=?,
                        uplink_low=?, uplink_high=?,
                        downlink_low=?, downlink_high=?,
                        mode=?, invert=?, baud=?,
                        ctcss_tone=?, alive=?, updated_at=?
                    WHERE uuid=?
                """, row[2:] + (xpdr_uuid,))
                stats["updated"] += 1
            else:
                self._conn.execute("""
                    INSERT INTO transmitters (
                        uuid, norad_cat_id, description, type,
                        uplink_low, uplink_high, downlink_low, downlink_high,
                        mode, invert, baud,
                        ctcss_tone, ctcss_tone_type,
                        alive, source, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'satnogs', ?)
                """, row)
                stats["inserted"] += 1

        self._conn.commit()
        self._log_sync("satnogs", stats)
        return stats

    def _log_sync(self, sync_type: str, stats: dict[str, int]) -> None:
        self._conn.execute("""
            INSERT INTO sync_log (sync_type, started_at, finished_at, status, records_updated)
            VALUES (?, ?, ?, 'success', ?)
        """, (sync_type, datetime.now(UTC).isoformat(),
              datetime.now(UTC).isoformat(),
              stats.get("inserted", 0) + stats.get("updated", 0)))
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # エクスポート / インポート
    # ------------------------------------------------------------------ #

    def export_manual_transmitters(self) -> list[dict[str, Any]]:
        """手動追加トランスポンダをJSONシリアライズ可能な形式で返す"""
        rows = self._conn.execute("""
            SELECT * FROM transmitters WHERE source = 'manual'
        """).fetchall()
        return [dict(r) for r in rows]

    def import_transmitters(self, data: list[dict[str, Any]]) -> int:
        """
        JSONからトランスポンダをインポートする。
        既存UUIDは上書き（upsert）。
        """
        now = datetime.now(UTC).isoformat()
        count = 0
        for item in data:
            item.setdefault("source", "manual")
            item.setdefault("manual_override", 1)
            item["updated_at"] = now
            cols = ", ".join(item.keys())
            placeholders = ", ".join("?" * len(item))
            self._conn.execute(
                f"INSERT OR REPLACE INTO transmitters ({cols}) VALUES ({placeholders})",
                list(item.values()),
            )
            count += 1
        self._conn.commit()
        return count
