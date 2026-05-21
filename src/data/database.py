"""
データベーススキーマ定義と初期化
SQLite + alembic によるマイグレーション管理
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- 衛星基本情報
CREATE TABLE IF NOT EXISTS satellites (
    norad_cat_id    INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    alt_names       TEXT DEFAULT '[]',   -- JSON配列
    status          TEXT DEFAULT 'unknown'
                    CHECK(status IN ('alive','dead','unknown')),
    is_favorite     INTEGER DEFAULT 0,
    is_hidden       INTEGER DEFAULT 0,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- トランスポンダ・送信機情報（SATNOGS + 手動追加）
CREATE TABLE IF NOT EXISTS transmitters (
    uuid            TEXT PRIMARY KEY,
    norad_cat_id    INTEGER NOT NULL REFERENCES satellites(norad_cat_id)
                    ON DELETE CASCADE,
    description     TEXT NOT NULL,
    type            TEXT DEFAULT 'Transponder'
                    CHECK(type IN ('Transmitter','Transponder','Beacon','Transceiver')),
    uplink_low      INTEGER,            -- Hz (Noneなら受信専用)
    uplink_high     INTEGER,            -- Hz (バンド型トランスポンダの上端)
    downlink_low    INTEGER,            -- Hz
    downlink_high   INTEGER,            -- Hz
    mode            TEXT,               -- 'FM','SSB','CW','DIGITALVOICE',...
    invert          INTEGER DEFAULT 0,  -- 反転トランスポンダ (0/1)
    baud            INTEGER,            -- デジタルモードのボーレート
    ctcss_tone      REAL,               -- CTCSS/DCSトーン周波数 (Hz)
    ctcss_tone_type TEXT DEFAULT NULL
                    CHECK(ctcss_tone_type IN ('CTCSS','DCS',NULL)),
    alive           INTEGER DEFAULT 1,
    source          TEXT DEFAULT 'satnogs'
                    CHECK(source IN ('satnogs','manual')),
    manual_override INTEGER DEFAULT 0,  -- 1ならSATNOGS同期で上書きしない
    notes           TEXT DEFAULT '',    -- ユーザーメモ
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- TLE最新データ
CREATE TABLE IF NOT EXISTS tle_data (
    norad_cat_id    INTEGER PRIMARY KEY REFERENCES satellites(norad_cat_id)
                    ON DELETE CASCADE,
    name            TEXT,
    line1           TEXT NOT NULL,
    line2           TEXT NOT NULL,
    epoch           DATETIME,
    source          TEXT DEFAULT 'celestrak'
                    CHECK(source IN ('celestrak','space-track','amsat','manual')),
    tle_group       TEXT DEFAULT 'amateur',
    fetched_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    quality_score   TEXT DEFAULT 'unknown'
                    CHECK(quality_score IN ('excellent','good','fair','poor','unknown'))
);

-- TLE履歴（過去のTLEを保持・品質トレンド分析用）
CREATE TABLE IF NOT EXISTS tle_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    norad_cat_id    INTEGER NOT NULL,
    name            TEXT,
    line1           TEXT NOT NULL,
    line2           TEXT NOT NULL,
    epoch           DATETIME,
    source          TEXT,
    fetched_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- アプリ設定・状態
CREATE TABLE IF NOT EXISTS app_settings (
    key             TEXT PRIMARY KEY,
    value           TEXT,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 同期ログ
CREATE TABLE IF NOT EXISTS sync_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_type       TEXT NOT NULL,  -- 'satnogs','celestrak','space-track'
    started_at      DATETIME,
    finished_at     DATETIME,
    status          TEXT,           -- 'success','error','partial'
    records_updated INTEGER DEFAULT 0,
    error_message   TEXT
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_transmitters_norad
    ON transmitters(norad_cat_id);
CREATE INDEX IF NOT EXISTS idx_tle_history_norad
    ON tle_history(norad_cat_id);
CREATE INDEX IF NOT EXISTS idx_tle_history_epoch
    ON tle_history(epoch DESC);
"""


def get_db_path() -> Path:
    """プラットフォーム別のデータベースファイルパスを返す"""
    from platformdirs import user_data_dir

    data_dir = Path(user_data_dir("gpredict-improved", "gpredict-improved"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "gpredict-improved.db"


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """既存DBに不足カラムを追加するマイグレーションを適用する。"""
    migrations = [
        "ALTER TABLE satellites ADD COLUMN is_favorite INTEGER DEFAULT 0",
        "ALTER TABLE satellites ADD COLUMN is_hidden INTEGER DEFAULT 0",
        "ALTER TABLE tle_data ADD COLUMN tle_group TEXT DEFAULT 'amateur'",
    ]
    for stmt in migrations:
        with contextlib.suppress(Exception):
            conn.execute(stmt)
    conn.commit()

    # transmitters.type CHECK制約に'Transceiver'を追加（テーブル再作成）
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='transmitters'"
    ).fetchone()
    if row and "'Transceiver'" not in row[0]:
        conn.execute("DROP TABLE IF EXISTS _transmitters_backup")
        conn.execute("ALTER TABLE transmitters RENAME TO _transmitters_backup")
        conn.execute("""
            CREATE TABLE transmitters (
                uuid            TEXT PRIMARY KEY,
                norad_cat_id    INTEGER NOT NULL REFERENCES satellites(norad_cat_id)
                                ON DELETE CASCADE,
                description     TEXT NOT NULL,
                type            TEXT DEFAULT 'Transponder'
                                CHECK(type IN ('Transmitter','Transponder','Beacon','Transceiver')),
                uplink_low      INTEGER,
                uplink_high     INTEGER,
                downlink_low    INTEGER,
                downlink_high   INTEGER,
                mode            TEXT,
                invert          INTEGER DEFAULT 0,
                baud            INTEGER,
                ctcss_tone      REAL,
                ctcss_tone_type TEXT DEFAULT NULL
                                CHECK(ctcss_tone_type IN ('CTCSS','DCS',NULL)),
                alive           INTEGER DEFAULT 1,
                source          TEXT DEFAULT 'satnogs'
                                CHECK(source IN ('satnogs','manual')),
                manual_override INTEGER DEFAULT 0,
                notes           TEXT DEFAULT '',
                updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("INSERT INTO transmitters SELECT * FROM _transmitters_backup")
        conn.execute("DROP TABLE _transmitters_backup")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_transmitters_norad ON transmitters(norad_cat_id)"
        )
        conn.commit()


def init_database(db_path: Path | None = None) -> sqlite3.Connection:
    """
    データベースを初期化して接続を返す。
    既存DBの場合はスキーマを検証して不足テーブルのみ作成する。
    """
    path = db_path or get_db_path()
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _apply_migrations(conn)
    conn.commit()
    return conn
