"""
データベース初期化・基本CRUD動作確認テスト
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from data.database import SCHEMA_SQL, init_database


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """インメモリDBを使う一時接続"""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


@pytest.fixture
def tmp_db(tmp_path: Path) -> sqlite3.Connection:
    """ファイルベースの一時DB接続"""
    return init_database(tmp_path / "test.db")


class TestSchemaInit:
    def test_all_tables_created(self, db_conn: sqlite3.Connection) -> None:
        expected = {
            "satellites",
            "transmitters",
            "tle_data",
            "tle_history",
            "app_settings",
            "sync_log",
        }
        rows = db_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        created = {r["name"] for r in rows}
        assert expected <= created

    def test_indexes_created(self, db_conn: sqlite3.Connection) -> None:
        rows = db_conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        names = {r["name"] for r in rows}
        assert "idx_transmitters_norad" in names
        assert "idx_tle_history_norad" in names
        assert "idx_tle_history_epoch" in names

    def test_init_is_idempotent(self, tmp_db: sqlite3.Connection, tmp_path: Path) -> None:
        """同じDBに2回 init_database を呼んでもエラーにならない"""
        tmp_db.close()
        conn2 = init_database(tmp_path / "test.db")
        rows = conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert len(rows) >= 6
        conn2.close()


class TestSatelliteCRUD:
    def test_insert_and_select(self, db_conn: sqlite3.Connection) -> None:
        db_conn.execute(
            "INSERT INTO satellites (norad_cat_id, name, status) VALUES (?, ?, ?)",
            (25544, "ISS (ZARYA)", "alive"),
        )
        db_conn.commit()
        row = db_conn.execute("SELECT * FROM satellites WHERE norad_cat_id = 25544").fetchone()
        assert row is not None
        assert row["name"] == "ISS (ZARYA)"
        assert row["status"] == "alive"

    def test_status_constraint(self, db_conn: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            db_conn.execute(
                "INSERT INTO satellites (norad_cat_id, name, status) VALUES (?, ?, ?)",
                (99999, "BadSat", "invalid_status"),
            )
            db_conn.commit()


class TestTransmitterCRUD:
    def test_insert_transmitter(self, db_conn: sqlite3.Connection) -> None:
        db_conn.execute(
            "INSERT INTO satellites (norad_cat_id, name) VALUES (?, ?)",
            (25544, "ISS"),
        )
        db_conn.execute(
            """INSERT INTO transmitters
               (uuid, norad_cat_id, description, downlink_low, mode, source)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("test-uuid-001", 25544, "ISS VHF FM", 145800000, "FM", "manual"),
        )
        db_conn.commit()
        row = db_conn.execute("SELECT * FROM transmitters WHERE uuid = 'test-uuid-001'").fetchone()
        assert row["downlink_low"] == 145800000
        assert row["mode"] == "FM"

    def test_cascade_delete(self, db_conn: sqlite3.Connection) -> None:
        db_conn.execute(
            "INSERT INTO satellites (norad_cat_id, name) VALUES (?, ?)",
            (12345, "TestSat"),
        )
        db_conn.execute(
            """INSERT INTO transmitters
               (uuid, norad_cat_id, description, source)
               VALUES (?, ?, ?, ?)""",
            ("del-uuid", 12345, "Test TX", "satnogs"),
        )
        db_conn.commit()
        db_conn.execute("DELETE FROM satellites WHERE norad_cat_id = 12345")
        db_conn.commit()
        row = db_conn.execute("SELECT * FROM transmitters WHERE uuid = 'del-uuid'").fetchone()
        assert row is None, "CASCADE DELETE が機能していない"


class TestTleData:
    _LINE1 = "1 25544U 98067A   24001.50000000  .00016717  00000+0  10270-3 0  9994"
    _LINE2 = "2 25544  51.6400 208.9163 0006828  86.9922 273.1770 15.49212693420559"

    def test_insert_tle(self, db_conn: sqlite3.Connection) -> None:
        db_conn.execute(
            "INSERT INTO satellites (norad_cat_id, name) VALUES (?, ?)",
            (25544, "ISS"),
        )
        db_conn.execute(
            """INSERT INTO tle_data
               (norad_cat_id, name, line1, line2, source, quality_score)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (25544, "ISS (ZARYA)", self._LINE1, self._LINE2, "celestrak", "excellent"),
        )
        db_conn.commit()
        row = db_conn.execute("SELECT * FROM tle_data WHERE norad_cat_id = 25544").fetchone()
        assert row["line1"] == self._LINE1
        assert row["quality_score"] == "excellent"

    def test_quality_score_constraint(self, db_conn: sqlite3.Connection) -> None:
        db_conn.execute(
            "INSERT INTO satellites (norad_cat_id, name) VALUES (?, ?)",
            (99001, "TestSat2"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db_conn.execute(
                """INSERT INTO tle_data
                   (norad_cat_id, name, line1, line2, quality_score)
                   VALUES (?, ?, ?, ?, ?)""",
                (99001, "T", self._LINE1, self._LINE2, "super"),
            )
            db_conn.commit()


class TestAppSettings:
    def test_upsert_setting(self, db_conn: sqlite3.Connection) -> None:
        db_conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("last_sync", "2024-01-01T00:00:00"),
        )
        db_conn.commit()
        row = db_conn.execute("SELECT value FROM app_settings WHERE key = 'last_sync'").fetchone()
        assert row["value"] == "2024-01-01T00:00:00"
