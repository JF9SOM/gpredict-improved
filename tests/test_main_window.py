"""
メインウィンドウ関連コンポーネントのテスト

WorldMapView   — 世界地図ウィジェット
SatDetailPanel — 衛星詳細パネル
PassListPanel  — パス予測一覧パネル
MainWindow     — メインウィンドウ
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from core.engine import Observation, PassInfo
from data.database import SCHEMA_SQL
from data.tle_manager import TLEManager
from ui.main_window import MainWindow, PassListPanel, SatDetailPanel
from ui.world_map import WorldMapView

# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> sqlite3.Connection:
    """テスト用インメモリ SQLite DB（スキーマ初期化済み）。"""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


@pytest.fixture()
def tle_manager(db: sqlite3.Connection) -> TLEManager:
    return TLEManager(db)


@pytest.fixture()
def populated_db(db: sqlite3.Connection) -> sqlite3.Connection:
    """衛星レコードを 2 件持つ DB。"""
    db.execute(
        "INSERT INTO satellites (norad_cat_id, name) VALUES (?, ?)",
        (25544, "ISS (ZARYA)"),
    )
    db.execute(
        "INSERT INTO satellites (norad_cat_id, name) VALUES (?, ?)",
        (43017, "AO-91"),
    )
    db.commit()
    return db


def _make_observation(
    norad: int = 25544,
    el: float = 45.0,
    az: float = 180.0,
    visible: bool = True,
) -> Observation:
    return Observation(
        norad_cat_id=norad,
        timestamp=datetime.now(UTC),
        elevation_deg=el,
        azimuth_deg=az,
        range_km=412.5,
        range_rate_km_s=-2.134,
        is_above_horizon=visible,
    )


def _make_pass_info(
    norad: int = 25544,
    max_el: float = 45.0,
    duration: float = 600.0,
) -> PassInfo:
    now = datetime.now(UTC)
    return PassInfo(
        norad_cat_id=norad,
        aos=now,
        tca=now + timedelta(minutes=5),
        los=now + timedelta(seconds=duration),
        max_elevation_deg=max_el,
        aos_azimuth_deg=90.0,
        los_azimuth_deg=270.0,
        duration_s=duration,
    )


# ---------------------------------------------------------------------------
# SatDetailPanel
# ---------------------------------------------------------------------------


class TestSatDetailPanel:
    def test_create(self, qtbot) -> None:
        w = SatDetailPanel()
        qtbot.addWidget(w)
        assert w is not None

    def test_initial_state_is_dashes(self, qtbot) -> None:
        w = SatDetailPanel()
        qtbot.addWidget(w)
        assert w._name_label.text() == "—"
        assert w._el_label.text() == "—"

    def test_set_satellite(self, qtbot) -> None:
        w = SatDetailPanel()
        qtbot.addWidget(w)
        w.set_satellite(25544, "ISS (ZARYA)")
        assert w._name_label.text() == "ISS (ZARYA)"
        assert w._norad_label.text() == "25544"

    def test_update_observation_visible(self, qtbot) -> None:
        w = SatDetailPanel()
        qtbot.addWidget(w)
        obs = _make_observation(el=45.12, az=180.34, visible=True)
        w.update_observation(obs)
        assert "45.12" in w._el_label.text()
        assert "180.34" in w._az_label.text()
        assert "412.5" in w._range_label.text()
        assert "-2.134" in w._rate_label.text()

    def test_update_observation_below_horizon(self, qtbot) -> None:
        w = SatDetailPanel()
        qtbot.addWidget(w)
        obs = _make_observation(el=-5.0, visible=False)
        w.update_observation(obs)
        assert "horizon" in w._vis_label.text().lower() or "below" in w._vis_label.text().lower()

    def test_update_observation_none_resets_fields(self, qtbot) -> None:
        w = SatDetailPanel()
        qtbot.addWidget(w)
        obs = _make_observation()
        w.update_observation(obs)
        w.update_observation(None)
        assert w._el_label.text() == "—"
        assert w._az_label.text() == "—"

    def test_clear_resets_all(self, qtbot) -> None:
        w = SatDetailPanel()
        qtbot.addWidget(w)
        w.set_satellite(25544, "ISS")
        w.update_observation(_make_observation())
        w.clear()
        assert w._name_label.text() == "—"
        assert w._norad_label.text() == "—"
        assert w._el_label.text() == "—"


# ---------------------------------------------------------------------------
# PassListPanel
# ---------------------------------------------------------------------------


class TestPassListPanel:
    def test_create(self, qtbot) -> None:
        w = PassListPanel()
        qtbot.addWidget(w)
        assert w is not None

    def test_initial_state_empty(self, qtbot) -> None:
        w = PassListPanel()
        qtbot.addWidget(w)
        assert w._table.rowCount() == 0
        assert w._passes == []

    def test_set_passes_empty(self, qtbot) -> None:
        w = PassListPanel()
        qtbot.addWidget(w)
        w.set_passes([])
        assert w._table.rowCount() == 0

    def test_set_passes_single(self, qtbot) -> None:
        w = PassListPanel()
        qtbot.addWidget(w)
        w.set_passes([_make_pass_info()])
        assert w._table.rowCount() == 1

    def test_set_passes_multiple(self, qtbot) -> None:
        w = PassListPanel()
        qtbot.addWidget(w)
        passes = [_make_pass_info(max_el=float(e)) for e in [15, 35, 65]]
        w.set_passes(passes)
        assert w._table.rowCount() == 3
        assert len(w._passes) == 3

    def test_set_passes_columns_populated(self, qtbot) -> None:
        w = PassListPanel()
        qtbot.addWidget(w)
        w.set_passes([_make_pass_info(max_el=60.0, duration=600.0)])
        assert w._table.item(0, 1) is not None  # Max El
        assert "60.0" in w._table.item(0, 1).text()

    def test_clear(self, qtbot) -> None:
        w = PassListPanel()
        qtbot.addWidget(w)
        w.set_passes([_make_pass_info()])
        w.clear()
        assert w._table.rowCount() == 0
        assert w._passes == []

    def test_pass_selected_signal_exists(self, qtbot) -> None:
        w = PassListPanel()
        qtbot.addWidget(w)
        assert hasattr(w, "pass_selected")

    def test_column_count(self, qtbot) -> None:
        w = PassListPanel()
        qtbot.addWidget(w)
        assert w._table.columnCount() == len(PassListPanel._COLUMNS)


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------


class TestMainWindow:
    """MainWindow の UI 構造・動作テスト。"""

    def _make_window(self, qtbot, db: sqlite3.Connection, tle_manager: TLEManager) -> MainWindow:
        w = MainWindow(conn=db, tle_manager=tle_manager)
        qtbot.addWidget(w)
        return w

    def test_create(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert w is not None

    def test_window_title_contains_app_name(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert "GPredict" in w.windowTitle()

    def test_has_tab_widget_with_three_tabs(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert w._tab_widget is not None
        assert w._tab_widget.count() == 3

    def test_tab_has_world_map(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert isinstance(w._world_map, WorldMapView)

    def test_tab_has_radar_view(self, qtbot, db, tle_manager) -> None:
        from ui.radar_view import RadarView

        w = self._make_window(qtbot, db, tle_manager)
        assert isinstance(w._radar_view, RadarView)

    def test_tab_has_pass_chart(self, qtbot, db, tle_manager) -> None:
        from ui.pass_chart import PassChartView

        w = self._make_window(qtbot, db, tle_manager)
        assert isinstance(w._pass_chart, PassChartView)

    def test_has_satellite_list(self, qtbot, db, tle_manager) -> None:
        from PySide6.QtWidgets import QListWidget

        w = self._make_window(qtbot, db, tle_manager)
        assert isinstance(w._sat_list, QListWidget)

    def test_has_detail_panel(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert isinstance(w._detail_panel, SatDetailPanel)

    def test_has_pass_list(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert isinstance(w._pass_list, PassListPanel)

    def test_timer_is_active(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert w._timer.isActive()

    def test_timer_interval_is_1000ms(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert w._timer.interval() == 1000

    def test_statusbar_qth_label_exists(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert w._qth_label is not None

    def test_statusbar_tle_label_exists(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert w._tle_label is not None

    def test_statusbar_url_label_exists(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert w._url_label is not None

    def test_statusbar_qr_button_exists(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert w._qr_button is not None

    def test_statusbar_rig_label_exists(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert w._rig_label is not None

    def test_menubar_has_file_menu(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        mb = w.menuBar()
        assert mb is not None
        titles = [mb.actions()[i].text() for i in range(len(mb.actions()))]
        assert any("File" in t for t in titles)

    def test_menubar_has_satellite_menu(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        mb = w.menuBar()
        assert mb is not None
        titles = [mb.actions()[i].text() for i in range(len(mb.actions()))]
        assert any("Satellite" in t for t in titles)

    def test_menubar_has_radio_menu(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        mb = w.menuBar()
        assert mb is not None
        titles = [mb.actions()[i].text() for i in range(len(mb.actions()))]
        assert any("Radio" in t for t in titles)

    def test_menubar_has_view_menu(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        mb = w.menuBar()
        assert mb is not None
        titles = [mb.actions()[i].text() for i in range(len(mb.actions()))]
        assert any("View" in t for t in titles)

    def test_menubar_has_help_menu(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        mb = w.menuBar()
        assert mb is not None
        titles = [mb.actions()[i].text() for i in range(len(mb.actions()))]
        assert any("Help" in t for t in titles)

    def test_satellite_list_populates_from_db(self, qtbot, populated_db) -> None:
        tm = TLEManager(populated_db)
        w = MainWindow(conn=populated_db, tle_manager=tm)
        qtbot.addWidget(w)
        assert w._sat_list.count() == 2

    def test_empty_db_gives_empty_satellite_list(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert w._sat_list.count() == 0

    def test_no_crash_with_none_engine(self, qtbot, db, tle_manager) -> None:
        w = MainWindow(conn=db, tle_manager=tle_manager, engine=None)
        qtbot.addWidget(w)
        w._on_tick()  # should not raise

    def test_no_crash_with_none_pass_predictor(self, qtbot, db, tle_manager) -> None:
        w = MainWindow(conn=db, tle_manager=tle_manager, pass_predictor=None)
        qtbot.addWidget(w)
        w._selected_norad = 25544
        w._refresh_passes()  # should not raise

    def test_web_url_empty_without_fastapi_app(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert w._web_server_url == ""
        assert w._url_label.text() == ""

    def test_web_server_is_none_without_fastapi_app(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert w._web_server is None

    def test_close_stops_timer(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert w._timer.isActive()
        w.close()
        assert not w._timer.isActive()

    def test_on_sat_selected_negative_row_clears(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        w._selected_norad = 25544
        w._on_sat_selected(-1)
        assert w._selected_norad is None

    def test_all_norads_populated(self, qtbot, populated_db) -> None:
        tm = TLEManager(populated_db)
        w = MainWindow(conn=populated_db, tle_manager=tm)
        qtbot.addWidget(w)
        assert len(w._all_norads) == 2

    def test_on_tick_no_crash_empty_db(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        w._on_tick()  # should not raise with empty DB
