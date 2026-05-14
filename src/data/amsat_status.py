"""
AMSAT衛星運用状況スクレイパー

https://www.amsat.org/status/ から衛星の運用状況を取得してDBに保存する。
beautifulsoup4 が必要。未インストールの場合はスクレイピングをスキップする。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

AMSAT_STATUS_URL = "https://www.amsat.org/status/"

_STATUS_MAP: dict[str, str] = {
    "operational": "operational",
    "partial": "partial",
    "partially operational": "partial",
    "non-operational": "non_operational",
    "non operational": "non_operational",
    "nonoperational": "non_operational",
    "not operational": "non_operational",
}

# bgcolor values that indicate an active/operational satellite
_OPERATIONAL_BG = {"#648fff", "#785ef0"}
# bgcolor values that mean "no report submitted" (treated as no data)
_EMPTY_BG = {"c0c0c0", "", "white"}

_SETTINGS_KEY = "amsat_status_data"
_TIMESTAMP_KEY = "amsat_status_updated_at"


class AMSATStatusFetcher:
    """AMSAT運用状況の取得・保存・提供を行うクラス。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """
        Args:
            conn: SQLite接続
        """
        self._conn = conn

    # ------------------------------------------------------------------ #
    # 公開API
    # ------------------------------------------------------------------ #

    async def fetch_and_update(self) -> dict[str, str]:
        """
        AMSAT statusページをスクレイピングして衛星名→運用状況の辞書を返す。
        結果はDBに保存する。

        Returns:
            {"satellite_name_lower": "operational"|"partial"|"non_operational"}
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(AMSAT_STATUS_URL)
                resp.raise_for_status()
                html = resp.text
        except Exception as exc:
            logger.warning("AMSAT status fetch failed: %s", exc)
            return self.load_cached() or {}

        status_map = self._parse_html(html)
        if status_map:
            self._save(status_map)
            logger.info("AMSAT status updated: %d satellites", len(status_map))
        return status_map

    def load_cached(self) -> dict[str, str] | None:
        """キャッシュ済み運用状況を返す。未保存の場合は None。"""
        row = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (_SETTINGS_KEY,),
        ).fetchone()
        if row is None:
            return None
        try:
            return dict(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def is_stale(self, max_age_hours: int = 24) -> bool:
        """キャッシュが古い（or 未取得）かどうかを返す。"""
        row = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (_TIMESTAMP_KEY,),
        ).fetchone()
        if row is None:
            return True
        try:
            ts = datetime.fromisoformat(str(row[0]))
            age_h = (datetime.now(UTC) - ts).total_seconds() / 3600
            return age_h >= max_age_hours
        except (ValueError, TypeError):
            return True

    # ------------------------------------------------------------------ #
    # HTMLパーサー
    # ------------------------------------------------------------------ #

    def _parse_html(self, html: str) -> dict[str, str]:
        """HTMLから衛星名→運用状況の辞書を生成する。"""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.warning("beautifulsoup4 not installed; AMSAT status scraping disabled")
            return {}

        soup = BeautifulSoup(html, "html.parser")
        result = self._parse_tables(soup)
        if not result:
            result = self._parse_fallback(soup)
        return result

    def _parse_tables(self, soup: Any) -> dict[str, str]:
        """
        テーブル形式のHTMLから衛星状況を抽出する。

        ページ構造:
          - 衛星ごとに複数の周波数行がある（例: AO-7_[U/v], AO-7_[V/a]）
          - 各行のセル1以降に時系列ステータスが左=最新順で並ぶ
          - bgcolor #648fff（青）= Satellite Active, #785ef0（紫）= ISS Active
          - bgcolor C0C0C0 または空 = 報告なし（スキップ）

        判定: 各周波数の最新（左端）の非空ステータスを確認し、
        1つでも青（Operational）があれば「operational」と判定する。
        """
        result: dict[str, str] = {}

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 3:
                continue

            # 1行目に "Name" セルがあるテーブルが対象
            header_cells = rows[0].find_all(["td", "th"])
            if not header_cells:
                continue
            if header_cells[0].get_text(strip=True).lower() != "name":
                continue

            # sat_name → [operational_count, total_freq_count]
            sat_freq: dict[str, list[int]] = {}

            for row in rows[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) < 2:
                    continue

                name_raw = cells[0].get_text(strip=True)
                if not name_raw:
                    continue

                # "AO-7_[U/v]" → "AO-7"
                sat_name = re.sub(r"_\[.*?\]$", "", name_raw).strip()
                if not sat_name:
                    continue

                if sat_name not in sat_freq:
                    sat_freq[sat_name] = [0, 0]
                sat_freq[sat_name][1] += 1

                # 左端から最初の非空セルのbgcolorを探す
                most_recent_bg: str | None = None
                for cell in cells[1:]:
                    bg = cell.get("bgcolor", "").strip().lower()
                    if bg not in _EMPTY_BG:
                        most_recent_bg = bg
                        break

                if most_recent_bg in {c.lower() for c in _OPERATIONAL_BG}:
                    sat_freq[sat_name][0] += 1

            for sat_name, (op_count, total_count) in sat_freq.items():
                if op_count > 0:
                    status = "operational"
                    print(
                        f"[AMSAT] {sat_name}: operational"
                        f" ({op_count}/{total_count} frequencies active)"
                    )
                else:
                    status = "non_operational"
                    print(f"[AMSAT] {sat_name}: not operational")
                result[sat_name.lower()] = status

            # 対象テーブルを処理したら終了
            break

        return result

    def _parse_fallback(self, soup: Any) -> dict[str, str]:
        """テーブル形式でない場合に文字列マッチングで抽出するフォールバック。"""
        result: dict[str, str] = {}
        for elem in soup.find_all(["li", "p", "div", "tr"]):
            text = elem.get_text(strip=True)
            if not text or len(text) > 200:
                continue
            text_lower = text.lower()
            for status_key, status_val in _STATUS_MAP.items():
                if status_key in text_lower:
                    idx = text_lower.find(status_key)
                    name_candidate = text[:idx].strip(" :-–—/")
                    # セパレーターで分割して最後のトークンを名前候補とする
                    for sep in (":", "-", "–", "—", "/"):
                        if sep in name_candidate:
                            name_candidate = name_candidate.rsplit(sep, 1)[-1].strip()
                    if 2 <= len(name_candidate) <= 30:
                        result[name_candidate.lower()] = status_val
                    break
        return result

    # ------------------------------------------------------------------ #
    # 永続化
    # ------------------------------------------------------------------ #

    def _save(self, status_map: dict[str, str]) -> None:
        """運用状況をapp_settingsに保存する。"""
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = excluded.updated_at""",
            (_SETTINGS_KEY, json.dumps(status_map), now),
        )
        self._conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = excluded.updated_at""",
            (_TIMESTAMP_KEY, now, now),
        )
        self._conn.commit()
