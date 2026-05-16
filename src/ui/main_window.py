"""
メインウィンドウ

MainWindow     — Qt6 アプリケーションのメインウィンドウ (QMainWindow)
SatDetailPanel — 選択衛星の詳細情報パネル
PassListPanel  — パス予測一覧パネル
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QFont,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.engine import Observation, PassInfo, PassPredictor, SatelliteEngine
from core.location import LocationManager
from data.amsat_status import AMSATStatusFetcher
from data.tle_manager import TLEManager
from data.transmitter_manager import TransmitterManager
from i18n import _
from ui.pass_chart import QUALITY_COLORS, PassChartView, pass_quality
from ui.radar_view import SAT_COLORS, RadarView, SatTrackData
from ui.world_map import WorldMapView

logger = logging.getLogger(__name__)


class _SatData(TypedDict):
    """衛星リスト表示用データ（フィルタリングに使用）。"""

    norad: int
    name: str
    is_favorite: bool
    tle_group: str
    quality: str | None
    amsat_status: str | None


_QUALITY_DOT_COLORS: dict[str | None, str] = {
    "excellent": "#2ecc71",
    "good": "#3498db",
    "fair": "#f1c40f",
    "poor": "#e74c3c",
    None: "#7f8c8d",
}

# AO-91, FO-29, CAS-4A などの AMSAT 識別子を抽出する正規表現
# 2〜4 文字のプレフィックス + 任意の区切り + 1〜3 桁数字 + 任意の末尾文字
_DESIG_RE = re.compile(r"\b([A-Za-z]{2,4})[-\s]?(\d{1,3}[A-Za-z]?)\b")


def _extract_designators(name: str) -> set[str]:
    """衛星名から AMSAT 識別子を正規化して返す（例: 'AO-91' → {'ao91'}）。"""
    return {(m.group(1) + m.group(2)).lower() for m in _DESIG_RE.finditer(name)}


def _amsat_key_in_sat_name(amsat_key: str, sat_name_lower: str) -> bool:
    """AMSAT キーが衛星名に完全なトークンとして含まれているか判定する。

    前後に英数字が連続しない位置でのマッチのみ認める。
    例: "iss" → "iss (zarya)" ✓  /  "ao-7" → "ao-73" ✗
    """
    pattern = r"(?<![a-z0-9])" + re.escape(amsat_key) + r"(?![a-z0-9])"
    return bool(re.search(pattern, sat_name_lower))


# ---------------------------------------------------------------------------
# SatDetailPanel
# ---------------------------------------------------------------------------


class SatDetailPanel(QWidget):
    """
    選択衛星の詳細情報（仰角・方位角・距離・視線速度・可視状態）を
    QFormLayout で表示するパネル。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        group = QGroupBox(_("Satellite Detail"))
        form = QFormLayout(group)

        self._name_label = QLabel("—")
        self._norad_label = QLabel("—")
        self._el_label = QLabel("—")
        self._az_label = QLabel("—")
        self._range_label = QLabel("—")
        self._rate_label = QLabel("—")
        self._vis_label = QLabel("—")

        form.addRow(_("Name:"), self._name_label)
        form.addRow(_("NORAD:"), self._norad_label)
        form.addRow(_("Elevation:"), self._el_label)
        form.addRow(_("Azimuth:"), self._az_label)
        form.addRow(_("Range:"), self._range_label)
        form.addRow(_("Range rate:"), self._rate_label)
        form.addRow(_("Visible:"), self._vis_label)

        layout.addWidget(group)
        layout.addStretch()

    def set_satellite(self, norad: int, name: str) -> None:
        """選択された衛星の基本情報を設定する。"""
        self._name_label.setText(name)
        self._norad_label.setText(str(norad))

    def update_observation(self, obs: Observation | None) -> None:
        """観測値を更新する。obs が None なら '—' をセットする。"""
        if obs is None:
            self._clear_obs_fields()
            return
        self._el_label.setText(f"{obs.elevation_deg:.2f}°")
        self._az_label.setText(f"{obs.azimuth_deg:.2f}°")
        self._range_label.setText(f"{obs.range_km:.1f} km")
        self._rate_label.setText(f"{obs.range_rate_km_s:.3f} km/s")
        self._vis_label.setText(_("Visible") if obs.is_above_horizon else _("Below horizon"))

    def clear(self) -> None:
        """すべてのフィールドをリセットする。"""
        self._name_label.setText("—")
        self._norad_label.setText("—")
        self._clear_obs_fields()

    def _clear_obs_fields(self) -> None:
        for label in (
            self._el_label,
            self._az_label,
            self._range_label,
            self._rate_label,
            self._vis_label,
        ):
            label.setText("—")


# ---------------------------------------------------------------------------
# PassListPanel
# ---------------------------------------------------------------------------


class PassListPanel(QWidget):
    """
    パス予測一覧を QTableWidget で表示するパネル。
    行クリックで pass_selected シグナルを emit する。
    """

    pass_selected: Signal = Signal(object)  # PassInfo

    _COLUMNS: tuple[str, ...] = ("AOS (UTC)", "Max El", "Duration", "AZ In", "Quality")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._passes: list[PassInfo] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        layout.addWidget(QLabel(_("Upcoming Passes")))

        self._table = QTableWidget(0, len(self._COLUMNS))
        self._table.setHorizontalHeaderLabels(list(self._COLUMNS))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._table)

    def set_passes(self, passes: list[PassInfo]) -> None:
        """
        パスリストを設定してテーブルを更新する。

        Args:
            passes: PassInfo のリスト（空でクリア）
        """
        self._passes = passes
        self._table.setRowCount(0)
        for p in passes:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(p.aos.strftime("%m/%d %H:%M")))
            self._table.setItem(row, 1, QTableWidgetItem(f"{p.max_elevation_deg:.1f}°"))
            mins, secs = divmod(int(p.duration_s), 60)
            self._table.setItem(row, 2, QTableWidgetItem(f"{mins}m {secs:02d}s"))
            self._table.setItem(row, 3, QTableWidgetItem(f"{p.aos_azimuth_deg:.0f}°"))
            quality = pass_quality(p.max_elevation_deg)
            quality_item = QTableWidgetItem(quality)
            quality_item.setForeground(QUALITY_COLORS[quality])
            self._table.setItem(row, 4, quality_item)

    def clear(self) -> None:
        """テーブルをクリアする。"""
        self._passes = []
        self._table.setRowCount(0)

    def _on_selection_changed(self) -> None:
        selected = self._table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        if 0 <= row < len(self._passes):
            self.pass_selected.emit(self._passes[row])


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    """
    GPredict-Improved のメインウィンドウ。

    レイアウト:
        左  — 衛星リスト（TLE品質インジケーター付き）
        中央 — タブ（世界地図 / レーダー / パスチャート）
        右  — 選択衛星の詳細情報
        下  — パス予測一覧
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        tle_manager: TLEManager,
        engine: SatelliteEngine | None = None,
        pass_predictor: PassPredictor | None = None,
        location_manager: LocationManager | None = None,
        fastapi_app: Any | None = None,
        web_port: int = 8080,
    ) -> None:
        """
        Args:
            conn:             SQLite 接続
            tle_manager:      TLE マネージャー
            engine:           衛星エンジン（None なら位置更新なし）
            pass_predictor:   パス予測器（None ならパス予測なし）
            location_manager: 位置情報マネージャー（None なら QTH 未設定表示）
            fastapi_app:      FastAPI アプリ（None なら Web サーバー起動なし）
            web_port:         Web サーバーポート番号
        """
        super().__init__()
        self._conn = conn
        self._tle_manager = tle_manager
        self._engine = engine
        self._pass_predictor = pass_predictor
        self._location_manager = location_manager
        self._selected_norad: int | None = None
        self._all_norads: list[int] = []
        self._all_sat_data: list[_SatData] = []
        self._current_passes: list[Any] = []
        self._web_server: Any | None = None
        self._web_server_url: str = ""
        self._scheduler: Any | None = None
        self._amsat_fetcher = AMSATStatusFetcher(conn)
        self._transmitter_manager = TransmitterManager(conn)

        self.setWindowTitle("GPredict-Improved")
        self.resize(1280, 800)

        self._build_ui()
        self._build_menu()
        self._build_statusbar()
        self._load_satellites()

        if fastapi_app is not None:
            self._start_web_server(fastapi_app, web_port)

        self._start_scheduler()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(1000)

    # ------------------------------------------------------------------ #
    # UI 構築
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        """ウィジェット・レイアウトを構築する。"""
        v_splitter = QSplitter(Qt.Orientation.Vertical)
        self.setCentralWidget(v_splitter)

        h_splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左: 衛星リスト
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(2, 2, 2, 2)
        left_layout.setSpacing(2)
        left_layout.addWidget(QLabel(_("Satellites")))

        self._filter_combo = QComboBox()
        self._filter_combo.addItems(
            [
                "All Satellites",
                "★ Favorites",
                "Amateur",
                "CubeSat",
                "Weather",
                "Operational (AMSAT)",
            ]
        )
        self._filter_combo.currentTextChanged.connect(self._on_filter_changed)
        left_layout.addWidget(self._filter_combo)

        self._sat_list = QListWidget()
        self._sat_list.currentRowChanged.connect(self._on_sat_selected)
        self._sat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._sat_list.customContextMenuRequested.connect(self._on_sat_context_menu)
        left_layout.addWidget(self._sat_list)
        left.setMinimumWidth(140)
        left.setMaximumWidth(240)
        h_splitter.addWidget(left)

        # 中央: タブ（世界地図 / レーダー / パスチャート）
        self._tab_widget = QTabWidget()
        self._world_map = WorldMapView()
        self._radar_view = RadarView()
        self._pass_chart = PassChartView()
        self._pass_chart.range_changed.connect(self._on_chart_range_changed)
        self._tab_widget.addTab(self._world_map, _("World Map"))
        self._tab_widget.addTab(self._radar_view, _("Radar"))
        self._tab_widget.addTab(self._pass_chart, _("Pass Chart"))
        h_splitter.addWidget(self._tab_widget)

        # 右: 衛星詳細パネル
        self._detail_panel = SatDetailPanel()
        self._detail_panel.setMinimumWidth(160)
        self._detail_panel.setMaximumWidth(260)
        h_splitter.addWidget(self._detail_panel)

        h_splitter.setStretchFactor(0, 0)
        h_splitter.setStretchFactor(1, 1)
        h_splitter.setStretchFactor(2, 0)

        # 下: パス予測一覧
        self._pass_list = PassListPanel()
        self._pass_list.setMaximumHeight(200)
        self._pass_list.setMinimumHeight(80)

        v_splitter.addWidget(h_splitter)
        v_splitter.addWidget(self._pass_list)
        v_splitter.setStretchFactor(0, 1)
        v_splitter.setStretchFactor(1, 0)

    def _build_menu(self) -> None:
        """メニューバーを構築する。"""
        mb = self.menuBar()

        # File
        file_menu = mb.addMenu(_("File"))
        if file_menu:
            file_menu.addAction(_("Set QTH..."), self._on_set_qth)
            file_menu.addAction(_("Settings"), self._on_settings)
            file_menu.addSeparator()
            file_menu.addAction(_("Exit"), self.close)

        # Satellite
        sat_menu = mb.addMenu(_("Satellite"))
        if sat_menu:
            sat_menu.addAction(_("Add Transmitter..."), self._on_add_transmitter)
            sat_menu.addSeparator()
            sat_menu.addAction(_("Add Satellite..."), self._on_add_satellite)
            sat_menu.addAction(_("Update TLE"), self._on_update_tle)
            sat_menu.addAction(_("Sync SATNOGS"), self._on_sync_satnogs)

        # Radio
        radio_menu = mb.addMenu(_("Radio"))
        if radio_menu:
            radio_menu.addAction(_("Rig Settings..."), self._on_rig_settings)
            radio_menu.addAction(_("Rotator Settings..."), self._on_rotator_settings)

        # View
        view_menu = mb.addMenu(_("View"))
        if view_menu:
            lang_menu = view_menu.addMenu(_("Language"))
            if lang_menu:
                lang_menu.addAction("English", lambda: self._on_set_language("en"))
                lang_menu.addAction("日本語", lambda: self._on_set_language("ja"))
            view_menu.addSeparator()
            view_menu.addAction(_("Radar"), lambda: self._tab_widget.setCurrentIndex(1))
            view_menu.addAction(_("Pass Chart"), lambda: self._tab_widget.setCurrentIndex(2))

        # Help
        help_menu = mb.addMenu(_("Help"))
        if help_menu:
            help_menu.addAction(_("About"), self._on_about)
            help_menu.addAction(_("GitHub"), self._on_github)

    def _build_statusbar(self) -> None:
        """ステータスバーを構築する。"""
        sb = self.statusBar()

        self._qth_label = QLabel("QTH: 未設定")
        self._tle_label = QLabel("")
        self._filter_label = QLabel("Showing: All")
        self._url_label = QLabel("")
        self._qr_button = QPushButton("QR")
        self._qr_button.setFlat(True)
        self._qr_button.setMaximumWidth(32)
        self._qr_button.setToolTip(_("Show QR code for web access"))
        self._qr_button.clicked.connect(self._on_show_qr)
        self._rig_label = QLabel(_("RIG: 未接続"))

        if sb:
            sb.addWidget(self._qth_label)
            sb.addWidget(self._tle_label)
            sb.addWidget(self._filter_label)
            sb.addPermanentWidget(self._url_label)
            sb.addPermanentWidget(self._qr_button)
            sb.addPermanentWidget(self._rig_label)

    # ------------------------------------------------------------------ #
    # データ読み込み
    # ------------------------------------------------------------------ #

    def _load_satellites(self) -> None:
        """衛星データをDBから読み込んで内部リストを構築し、フィルターを適用する。"""
        quality_map: dict[int, str | None] = {
            r["norad_cat_id"]: r.get("quality_score")
            for r in self._tle_manager.get_all_quality_status()
        }
        amsat_map: dict[str, str] = self._amsat_fetcher.load_cached() or {}

        designator_status: dict[str, str] = {}
        for amsat_name, status in amsat_map.items():
            for desig in _extract_designators(amsat_name):
                designator_status[desig] = status

        amsat_keys_by_len = sorted(amsat_map.keys(), key=len, reverse=True)

        rows = self._conn.execute(
            """
            SELECT s.norad_cat_id, s.name, s.is_favorite,
                   COALESCE(t.tle_group, 'amateur') AS tle_group
            FROM satellites s
            LEFT JOIN tle_data t ON s.norad_cat_id = t.norad_cat_id
            ORDER BY s.name
            """
        ).fetchall()

        self._all_sat_data = []
        self._all_norads = []

        for row in rows:
            norad: int = int(row["norad_cat_id"])
            name: str = str(row["name"])
            quality: str | None = quality_map.get(norad)
            name_lower = name.lower()

            amsat_status: str | None = amsat_map.get(name_lower)
            if amsat_status is None:
                for desig in _extract_designators(name):
                    if desig in designator_status:
                        amsat_status = designator_status[desig]
                        break
            if amsat_status is None:
                for amsat_key in amsat_keys_by_len:
                    if _amsat_key_in_sat_name(amsat_key, name_lower):
                        amsat_status = amsat_map[amsat_key]
                        break

            self._all_sat_data.append(
                _SatData(
                    norad=norad,
                    name=name,
                    is_favorite=bool(row["is_favorite"]),
                    tle_group=str(row["tle_group"]),
                    quality=quality,
                    amsat_status=amsat_status,
                )
            )
            self._all_norads.append(norad)

        self._apply_filter()

    def _apply_filter(self) -> None:
        """フィルターコンボの選択に従って衛星リストを再描画する。"""
        filter_text = self._filter_combo.currentText()
        self._sat_list.clear()
        count = 0

        for d in self._all_sat_data:
            if filter_text == "★ Favorites" and not d["is_favorite"]:
                continue
            if filter_text == "Amateur" and d["tle_group"] != "amateur":
                continue
            if filter_text == "CubeSat" and d["tle_group"] != "cubesat":
                continue
            if filter_text == "Weather" and d["tle_group"] != "weather":
                continue
            if filter_text == "Operational (AMSAT)" and d["amsat_status"] != "operational":
                continue

            prefix = "★ " if d["is_favorite"] else ""
            item = QListWidgetItem(prefix + d["name"])
            item.setData(Qt.ItemDataRole.UserRole, d["norad"])

            amsat_status = d["amsat_status"]
            quality = d["quality"]

            if amsat_status == "operational":
                item.setForeground(QColor("#2ecc71"))
                font: QFont = item.font()
                font.setBold(True)
                item.setFont(font)
            elif amsat_status == "partial":
                item.setForeground(QColor("#f1c40f"))
            elif amsat_status == "non_operational":
                item.setForeground(QColor("#7f8c8d"))
                font = item.font()
                font.setItalic(True)
                item.setFont(font)
            else:
                color_hex = _QUALITY_DOT_COLORS.get(quality, _QUALITY_DOT_COLORS[None])
                item.setForeground(QColor(color_hex))

            self._sat_list.addItem(item)
            count += 1

        if filter_text == "All Satellites":
            self._filter_label.setText(f"Showing: All ({count})")
        else:
            self._filter_label.setText(f"Showing: {filter_text} ({count})")

    # ------------------------------------------------------------------ #
    # バックグラウンド処理
    # ------------------------------------------------------------------ #

    def _start_web_server(self, fastapi_app: Any, port: int) -> None:
        """FastAPI アプリを uvicorn でバックグラウンド起動する。"""
        try:
            from web.server import WebServer

            self._web_server = WebServer(fastapi_app, port=port)
            url = self._web_server.start()
            self._web_server_url = url
            self._url_label.setText(url)
        except Exception as exc:
            logger.warning("Web server start failed: %s", exc)

    def _start_scheduler(self) -> None:
        """APScheduler で TLE・AMSAT自動更新ジョブを登録・起動する。"""
        try:
            from apscheduler.schedulers.background import BackgroundScheduler

            self._scheduler = BackgroundScheduler()
            self._scheduler.add_job(
                self._refresh_tle_sync,
                "interval",
                hours=2,
                id="tle_refresh",
                misfire_grace_time=300,
            )
            self._scheduler.add_job(
                self._refresh_amsat_sync,
                "interval",
                hours=24,
                id="amsat_refresh",
                misfire_grace_time=600,
            )
            self._scheduler.start()
            logger.debug("APScheduler started")
        except Exception as exc:
            logger.warning("APScheduler start failed: %s", exc)
            self._scheduler = None

        # 起動時にAMSATステータスが古ければバックグラウンドで更新
        if self._amsat_fetcher.is_stale():
            import threading

            threading.Thread(target=self._refresh_amsat_sync, daemon=True).start()

    def _refresh_tle_sync(self) -> None:
        """バックグラウンドスレッドから TLE を更新する（APScheduler ジョブ）。"""
        try:
            asyncio.run(self._tle_manager.fetch_and_update())
            logger.info("TLE refresh completed")
        except Exception as exc:
            logger.warning("TLE refresh failed: %s", exc)

    def _refresh_amsat_sync(self) -> None:
        """バックグラウンドスレッドから AMSAT 運用状況を更新する。"""
        try:
            asyncio.run(self._amsat_fetcher.fetch_and_update())
            logger.info("AMSAT status refresh completed")
            QTimer.singleShot(0, self._load_satellites)
        except Exception as exc:
            logger.warning("AMSAT status refresh failed: %s", exc)

    # ------------------------------------------------------------------ #
    # タイマーコールバック（1 秒ごと）
    # ------------------------------------------------------------------ #

    def _on_tick(self) -> None:
        """衛星位置更新・ステータスバー更新を行うタイマーコールバック。"""
        self._update_world_map()
        self._update_selected_satellite()
        self._update_statusbar()

    def _update_world_map(self) -> None:
        """全衛星の直下点と自局位置を取得して世界地図を更新する。"""
        # 自局位置 ★ マーカーを更新（エンジン有無にかかわらず）
        if self._location_manager is not None and self._location_manager.current is not None:
            loc = self._location_manager.current
            self._world_map.set_observer_location(loc.latitude_deg, loc.longitude_deg)

        if self._engine is None or not self._all_norads:
            return

        rows = self._conn.execute("SELECT norad_cat_id, name FROM satellites").fetchall()
        name_map: dict[int, str] = {int(r["norad_cat_id"]): str(r["name"]) for r in rows}

        subpoints = self._engine.subpoints(self._all_norads)
        sat_data: dict[int, tuple[str, float, float, QColor]] = {}
        for i, norad in enumerate(self._all_norads):
            if norad in subpoints:
                lat, lon = subpoints[norad]
                color = SAT_COLORS[i % len(SAT_COLORS)]
                sat_data[norad] = (name_map.get(norad, str(norad)), lat, lon, color)

        self._world_map.set_satellites(sat_data)

    def _update_selected_satellite(self) -> None:
        """選択中衛星の観測値・レーダービューを更新する。"""
        if self._engine is None or self._selected_norad is None:
            return

        obs = self._engine.observe(self._selected_norad)
        self._detail_panel.update_observation(obs)

        if obs is not None:
            item = self._sat_list.currentItem()
            name = item.text() if item else str(self._selected_norad)

            # 現在パス中かどうかに応じて次パス情報を選択する
            now = datetime.now(UTC)
            next_pass = next(
                (p for p in self._current_passes if p.los > now),
                None,
            )
            aos_t = next_pass.aos if next_pass is not None else None
            los_t = next_pass.los if next_pass is not None else None
            next_max_el = next_pass.max_elevation_deg if next_pass is not None else None
            next_dur = next_pass.duration_s if next_pass is not None else None

            # 次パス（または現パス）のAOS〜LOSを30秒刻みで計算して軌跡を作成
            pass_track: list[tuple[float, float]] = []
            if next_pass is not None:
                n_steps = max(20, min(40, int(next_pass.duration_s / 15)))
                step_s = next_pass.duration_s / n_steps
                for i in range(n_steps + 1):
                    t = next_pass.aos + timedelta(seconds=i * step_s)
                    pt = self._engine.observe(self._selected_norad, at=t)
                    if pt is not None:
                        pass_track.append((pt.azimuth_deg, pt.elevation_deg))

            track = SatTrackData(
                name=name,
                norad_cat_id=self._selected_norad,
                azimuth_deg=obs.azimuth_deg,
                elevation_deg=obs.elevation_deg,
                is_visible=obs.is_above_horizon,
                track=pass_track,
                aos_time=aos_t,
                los_time=los_t,
                next_max_el=next_max_el,
                next_duration_s=next_dur,
            )
            self._radar_view.set_tracks([track])

    def _update_statusbar(self) -> None:
        """ステータスバーの QTH テキストと TLE 最終更新日時を更新する。"""
        if self._location_manager is not None:
            self._qth_label.setText(self._location_manager.status_text)

        row = self._conn.execute("SELECT MAX(fetched_at) AS last_fetch FROM tle_data").fetchone()
        if row and row["last_fetch"]:
            self._tle_label.setText(f"TLE: {str(row['last_fetch'])[:16]}")

    # ------------------------------------------------------------------ #
    # 衛星選択コールバック
    # ------------------------------------------------------------------ #

    def _on_filter_changed(self, _: str) -> None:
        """フィルターコンボ変更時に衛星リストを再描画する。"""
        self._apply_filter()

    def _on_sat_context_menu(self, pos: QPoint) -> None:
        """衛星リストの右クリックコンテキストメニューを表示する。"""
        item = self._sat_list.itemAt(pos)
        if item is None:
            return
        norad = int(item.data(Qt.ItemDataRole.UserRole))

        row_data = self._conn.execute(
            "SELECT name, is_favorite FROM satellites WHERE norad_cat_id = ?",
            (norad,),
        ).fetchone()
        if row_data is None:
            return

        name = str(row_data["name"])
        is_fav = bool(row_data["is_favorite"])

        menu = QMenu(self)
        fav_label = "★ Remove from Favorites" if is_fav else "★ Add to Favorites"
        fav_action = menu.addAction(fav_label)
        info_action = menu.addAction("Satellite Info...")

        action = menu.exec(self._sat_list.mapToGlobal(pos))
        if action == fav_action:
            self._toggle_favorite(norad, not is_fav)
        elif action == info_action:
            self._show_sat_info(norad, name)

    def _toggle_favorite(self, norad: int, favorite: bool) -> None:
        """お気に入り状態をDBに保存して衛星リストを再読み込みする。"""
        self._conn.execute(
            "UPDATE satellites SET is_favorite = ? WHERE norad_cat_id = ?",
            (1 if favorite else 0, norad),
        )
        self._conn.commit()
        self._load_satellites()

    def _show_sat_info(self, norad: int, name: str) -> None:
        """衛星情報ダイアログを表示する（NORAD番号・TLE epoch・品質）。"""
        tle_row = self._conn.execute(
            "SELECT epoch, quality_score, source, tle_group FROM tle_data WHERE norad_cat_id = ?",
            (norad,),
        ).fetchone()

        info_parts = [f"Name: {name}", f"NORAD: {norad}"]
        if tle_row:
            epoch = str(tle_row["epoch"])[:16] if tle_row["epoch"] else "N/A"
            info_parts += [
                f"TLE Epoch: {epoch} UTC",
                f"TLE Quality: {tle_row['quality_score']}",
                f"Source: {tle_row['source']}",
                f"Group: {tle_row['tle_group'] or 'amateur'}",
            ]
        else:
            info_parts.append("TLE: Not available")

        QMessageBox.information(self, f"Satellite Info — {name}", "\n".join(info_parts))

    def _on_sat_selected(self, row: int) -> None:
        """衛星リストで選択が変わったときのコールバック。"""
        if row < 0:
            self._selected_norad = None
            self._detail_panel.clear()
            return
        item = self._sat_list.item(row)
        if item is None:
            return
        norad = int(item.data(Qt.ItemDataRole.UserRole))
        self._selected_norad = norad
        self._detail_panel.set_satellite(norad, item.text())
        self._refresh_passes()

    def _refresh_passes(self) -> None:
        """選択衛星のパス予測を取得してパスリストとチャートを更新する。"""
        if self._selected_norad is None or self._pass_predictor is None:
            self._pass_list.clear()
            return
        now = datetime.now(UTC)
        passes = self._pass_predictor.get_passes(
            self._selected_norad,
            now,
            now + timedelta(hours=24),
        )
        self._current_passes = passes
        self._pass_list.set_passes(passes)

        item = self._sat_list.currentItem()
        name = item.text() if item else ""
        self._pass_chart.set_passes(passes, sat_name=name)

    def _on_chart_range_changed(self, hours: float) -> None:
        """パスチャートの時間範囲変更時に PassPredictor を即時呼び出す。"""
        if self._selected_norad is None or self._pass_predictor is None:
            return
        now = datetime.now(UTC)
        passes = self._pass_predictor.get_passes(
            self._selected_norad,
            now,
            now + timedelta(hours=hours),
        )
        self._current_passes = passes
        self._pass_list.set_passes(passes)
        item = self._sat_list.currentItem()
        name = item.text() if item else ""
        self._pass_chart.set_passes(passes, sat_name=name)

    # ------------------------------------------------------------------ #
    # メニューハンドラー
    # ------------------------------------------------------------------ #

    def _on_set_qth(self) -> None:
        """File > Set QTH... ハンドラー。"""
        if self._location_manager is None:
            QMessageBox.warning(self, _("Set QTH"), _("Location manager not initialized."))
            return
        from ui.qth_dialog import QTHDialog

        dialog = QTHDialog(self._location_manager, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            loc = self._location_manager.current
            if loc is None:
                return
            if self._engine is not None:
                self._engine.update_observer(loc.latitude_deg, loc.longitude_deg, loc.elevation_m)
            else:
                self._engine = SatelliteEngine(
                    self._tle_manager, loc.latitude_deg, loc.longitude_deg, loc.elevation_m
                )
            if self._pass_predictor is not None:
                self._pass_predictor.update_observer(
                    loc.latitude_deg, loc.longitude_deg, loc.elevation_m
                )
            else:
                self._pass_predictor = PassPredictor(
                    self._tle_manager, loc.latitude_deg, loc.longitude_deg, loc.elevation_m
                )
            self._update_statusbar()

    def _on_settings(self) -> None:
        from ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(self._conn, parent=self)
        dialog.exec()

    def _on_add_transmitter(self) -> None:
        """Satellite > Add Transmitter... ハンドラー。"""
        from ui.transmitter_dialog import TransmitterDialog

        norad = self._selected_norad
        dialog = TransmitterDialog(self._transmitter_manager, norad_cat_id=norad, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            QMessageBox.information(
                self,
                _("Add Transmitter"),
                _("Transmitter added successfully."),
            )

    def _on_add_satellite(self) -> None:
        QMessageBox.information(
            self, _("Add Satellite"), _("Add satellite dialog not yet implemented.")
        )

    def _on_update_tle(self) -> None:
        QMessageBox.information(
            self, _("Update TLE"), _("TLE update has been queued in the background.")
        )

    def _on_sync_satnogs(self) -> None:
        QMessageBox.information(self, _("Sync SATNOGS"), _("SATNOGS sync not yet implemented."))

    def _on_rig_settings(self) -> None:
        from ui.rig_dialog import RigSettingsDialog

        dialog = RigSettingsDialog(self._conn, parent=self)
        dialog.exec()

    def _on_rotator_settings(self) -> None:
        QMessageBox.information(
            self, _("Rotator Settings"), _("Rotator settings dialog not yet implemented.")
        )

    def _on_set_language(self, lang: str) -> None:
        from i18n import set_language

        set_language(lang)
        QMessageBox.information(
            self,
            _("Language"),
            _("Please restart the application to apply the language change."),
        )

    def _on_about(self) -> None:
        QMessageBox.information(
            self,
            _("About GPredict-Improved"),
            "GPredict-Improved v0.1.0\n\n"
            + _("Modern satellite tracking software for amateur radio operators.\n")
            + "https://github.com/JF9SOM/gpredict-improved",
        )

    def _on_github(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl("https://github.com/JF9SOM/gpredict-improved"))

    def _on_show_qr(self) -> None:
        if not self._web_server_url:
            QMessageBox.information(self, _("QR Code"), _("Web server is not running."))
            return
        try:
            from web.qrcode_helper import generate_qr_png

            png_bytes = generate_qr_png(self._web_server_url)
            dialog = QDialog(self)
            dialog.setWindowTitle(f"QR — {self._web_server_url}")
            dlg_layout = QVBoxLayout(dialog)
            img_label = QLabel()
            pixmap = QPixmap()
            pixmap.loadFromData(png_bytes)
            img_label.setPixmap(pixmap)
            dlg_layout.addWidget(img_label)
            dlg_layout.addWidget(QLabel(self._web_server_url))
            dialog.exec()
        except Exception as exc:
            logger.warning("QR code generation failed: %s", exc)
            QMessageBox.warning(self, _("QR Code"), _("Failed to generate QR code."))

    # ------------------------------------------------------------------ #
    # ウィンドウライフサイクル
    # ------------------------------------------------------------------ #

    def closeEvent(self, event: QCloseEvent) -> None:
        """ウィンドウクローズ時にタイマー・Webサーバー・スケジューラを停止する。"""
        self._timer.stop()
        if self._web_server is not None:
            with contextlib.suppress(Exception):
                self._web_server.stop()
        if self._scheduler is not None:
            with contextlib.suppress(Exception):
                self._scheduler.shutdown(wait=False)
        event.accept()
