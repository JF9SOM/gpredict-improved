"""
メインウィンドウ関連コンポーネントのテスト

WorldMapView   — 世界地図ウィジェット
SatDetailPanel — 衛星詳細パネル
PassPanel      — パス予測パネル（タブ構成）
MainWindow     — メインウィンドウ
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from core.engine import Observation, PassInfo
from data.database import SCHEMA_SQL
from data.tle_manager import TLEManager
from ui.main_window import MainWindow, SatDetailPanel
from ui.pass_panel import PassPanel  # noqa: F401  (used in isinstance checks)
from ui.radio_control_widget import RadioControlWidget
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
# PassPanel
# ---------------------------------------------------------------------------


class TestPassListPanel:
    def test_create(self, qtbot) -> None:
        w = PassPanel()
        qtbot.addWidget(w)
        assert w is not None

    def test_initial_state_empty(self, qtbot) -> None:
        w = PassPanel()
        qtbot.addWidget(w)
        assert w._target_table.rowCount() == 0
        assert w._passes == []

    def test_set_passes_empty(self, qtbot) -> None:
        w = PassPanel()
        qtbot.addWidget(w)
        w.set_passes([])
        assert w._target_table.rowCount() == 0

    def test_set_passes_single(self, qtbot) -> None:
        w = PassPanel()
        qtbot.addWidget(w)
        w.set_passes([_make_pass_info()])
        assert w._target_table.rowCount() == 1

    def test_set_passes_multiple(self, qtbot) -> None:
        w = PassPanel()
        qtbot.addWidget(w)
        passes = [_make_pass_info(max_el=float(e)) for e in [15, 35, 65]]
        w.set_passes(passes)
        assert w._target_table.rowCount() == 3
        assert len(w._passes) == 3

    def test_set_passes_columns_populated(self, qtbot) -> None:
        w = PassPanel()
        qtbot.addWidget(w)
        w.set_passes([_make_pass_info(max_el=60.0, duration=600.0)])
        assert w._target_table.item(0, 1) is not None  # Max El
        assert "60.0" in w._target_table.item(0, 1).text()

    def test_clear(self, qtbot) -> None:
        w = PassPanel()
        qtbot.addWidget(w)
        w.set_passes([_make_pass_info()])
        w.clear()
        assert w._target_table.rowCount() == 0
        assert w._passes == []

    def test_pass_selected_signal_exists(self, qtbot) -> None:
        w = PassPanel()
        qtbot.addWidget(w)
        assert hasattr(w, "pass_selected")

    def test_column_count(self, qtbot) -> None:
        w = PassPanel()
        qtbot.addWidget(w)
        assert w._target_table.columnCount() == len(PassPanel._TARGET_COLS)

    def test_has_two_tabs(self, qtbot) -> None:
        w = PassPanel()
        qtbot.addWidget(w)
        assert w._tabs.count() == 2

    def test_group_tab_has_search_button(self, qtbot) -> None:
        w = PassPanel()
        qtbot.addWidget(w)
        assert w._group_search_btn is not None

    def test_group_tab_has_cancel_button(self, qtbot) -> None:
        w = PassPanel()
        qtbot.addWidget(w)
        assert not w._group_cancel_btn.isEnabled()

    def test_set_satellites_updates_list(self, qtbot) -> None:
        w = PassPanel()
        qtbot.addWidget(w)
        w.set_satellites([(25544, "ISS"), (43017, "AO-91")])
        assert w._sat_list == [(25544, "ISS"), (43017, "AO-91")]

    def test_set_satellites_invalidates_cache(self, qtbot) -> None:
        w = PassPanel()
        qtbot.addWidget(w)
        w._cache_key = ("dummy",)  # type: ignore[assignment]
        w.set_satellites([(25544, "ISS")])
        assert w._cache_key is None

    def test_highlight_satellite_signal_exists(self, qtbot) -> None:
        w = PassPanel()
        qtbot.addWidget(w)
        assert hasattr(w, "highlight_satellite")


# ---------------------------------------------------------------------------
# RadioControlWidget
# ---------------------------------------------------------------------------


class TestRadioControlWidget:
    def test_create(self, qtbot) -> None:
        w = RadioControlWidget()
        qtbot.addWidget(w)
        assert w is not None

    def test_initial_satellite_is_dash(self, qtbot) -> None:
        w = RadioControlWidget()
        qtbot.addWidget(w)
        assert w._sat_name_label.text() == "—"
        assert w._norad_label.text() == "—"

    def test_set_satellite(self, qtbot) -> None:
        w = RadioControlWidget()
        qtbot.addWidget(w)
        w.set_satellite(25544, "ISS (ZARYA)")
        assert w._sat_name_label.text() == "ISS (ZARYA)"
        assert w._norad_label.text() == "25544"

    def test_clear_satellite(self, qtbot) -> None:
        w = RadioControlWidget()
        qtbot.addWidget(w)
        w.set_satellite(25544, "ISS")
        w.clear_satellite()
        assert w._sat_name_label.text() == "—"
        assert w._norad_label.text() == "—"

    def test_update_doppler_downlink(self, qtbot) -> None:
        w = RadioControlWidget()
        qtbot.addWidget(w)
        w.update_doppler(
            downlink_nominal_hz=437_550_000.0,
            downlink_corrected_hz=437_548_000.0,
            downlink_shift_hz=-2000.0,
            uplink_nominal_hz=None,
            uplink_corrected_hz=None,
            uplink_shift_hz=None,
            mode="FM",
            ctcss_hz=67.0,
        )
        assert "437" in w._downlink_label.text()
        assert "-2000" in w._downlink_doppler_label.text()
        assert w._mode_label.text() == "FM"
        assert "67.0" in w._ctcss_label.text()

    def test_update_doppler_none_clears(self, qtbot) -> None:
        w = RadioControlWidget()
        qtbot.addWidget(w)
        w.update_doppler(None, None, None, None, None, None)
        assert w._downlink_label.text() == "—"
        assert w._uplink_label.text() == "—"

    def test_update_rotator(self, qtbot) -> None:
        from rig.controller import RotatorState

        w = RadioControlWidget()
        qtbot.addWidget(w)
        w.update_rotator(RotatorState(azimuth_deg=180.5, elevation_deg=45.0))
        assert "180.5" in w._rot_az_label.text()
        assert "45.0" in w._rot_el_label.text()

    def test_update_rotator_none(self, qtbot) -> None:
        w = RadioControlWidget()
        qtbot.addWidget(w)
        w.update_rotator(None)
        assert w._rot_az_label.text() == "—"
        assert w._rot_el_label.text() == "—"

    def test_no_rig_buttons_disabled(self, qtbot) -> None:
        w = RadioControlWidget()
        qtbot.addWidget(w)
        assert not w._connect_rig_btn.isEnabled()
        assert not w._connect_rot_btn.isEnabled()

    def test_rig_status_not_configured(self, qtbot) -> None:
        w = RadioControlWidget()
        qtbot.addWidget(w)
        assert "configured" in w._rig_status_label.text().lower()

    # -- Transponder combo --

    def test_initial_combo_empty_and_disabled(self, qtbot) -> None:
        w = RadioControlWidget()
        qtbot.addWidget(w)
        assert w._xpdr_combo.count() == 0
        assert not w._xpdr_combo.isEnabled()

    def test_set_transmitters_populates_combo(self, qtbot) -> None:
        w = RadioControlWidget()
        qtbot.addWidget(w)
        xpdrs = [
            {
                "description": "FM Voice",
                "type": "Transponder",
                "downlink_low": 145_800_000,
                "uplink_low": 145_200_000,
                "mode": "FM",
                "ctcss_tone": None,
                "invert": 0,
            },
            {
                "description": "APRS",
                "type": "Transceiver",
                "downlink_low": 437_825_000,
                "uplink_low": 437_825_000,
                "mode": "AFSK",
                "ctcss_tone": None,
                "invert": 0,
            },
        ]
        w.set_transmitters(xpdrs)
        assert w._xpdr_combo.count() == 2
        assert w._xpdr_combo.isEnabled()
        assert "FM Voice" in w._xpdr_combo.itemText(0)
        assert "145.800" in w._xpdr_combo.itemText(0)

    def test_set_transmitters_emits_signal(self, qtbot) -> None:
        w = RadioControlWidget()
        qtbot.addWidget(w)
        xpdr = {
            "description": "FM",
            "type": "Transponder",
            "downlink_low": 145_800_000,
            "uplink_low": None,
            "mode": "FM",
            "ctcss_tone": 67.0,
            "invert": 0,
        }
        received: list[object] = []
        w.transmitter_changed.connect(lambda x: received.append(x))
        w.set_transmitters([xpdr])
        assert len(received) == 1
        assert received[0] == xpdr

    def test_set_transmitters_empty_disables_combo(self, qtbot) -> None:
        w = RadioControlWidget()
        qtbot.addWidget(w)
        w.set_transmitters([])
        assert w._xpdr_combo.count() == 0
        assert not w._xpdr_combo.isEnabled()

    def test_set_transmitters_empty_emits_none(self, qtbot) -> None:
        w = RadioControlWidget()
        qtbot.addWidget(w)
        received: list[object] = []
        w.transmitter_changed.connect(lambda x: received.append(x))
        w.set_transmitters([])
        assert received == [None]

    def test_clear_satellite_clears_combo(self, qtbot) -> None:
        w = RadioControlWidget()
        qtbot.addWidget(w)
        xpdr = {
            "description": "FM",
            "type": "Transponder",
            "downlink_low": 145_800_000,
            "uplink_low": None,
            "mode": "FM",
            "ctcss_tone": None,
            "invert": 0,
        }
        w.set_transmitters([xpdr])
        w.clear_satellite()
        assert w._xpdr_combo.count() == 0
        assert not w._xpdr_combo.isEnabled()

    def test_combo_change_emits_correct_xpdr(self, qtbot) -> None:
        w = RadioControlWidget()
        qtbot.addWidget(w)
        xpdrs = [
            {
                "description": "FM",
                "type": "Transponder",
                "downlink_low": 145_800_000,
                "uplink_low": None,
                "mode": "FM",
                "ctcss_tone": None,
                "invert": 0,
            },
            {
                "description": "APRS",
                "type": "Transceiver",
                "downlink_low": 437_825_000,
                "uplink_low": 437_825_000,
                "mode": "AFSK",
                "ctcss_tone": None,
                "invert": 0,
            },
        ]
        w.set_transmitters(xpdrs)
        received: list[object] = []
        w.transmitter_changed.connect(lambda x: received.append(x))
        w._xpdr_combo.setCurrentIndex(1)
        assert len(received) == 1
        assert received[0] == xpdrs[1]

    def test_xpdr_label_format(self, qtbot) -> None:
        from ui.radio_control_widget import RadioControlWidget

        xpdr = {"description": "ISS Voice", "type": "Transponder", "downlink_low": 145_800_000}
        label = RadioControlWidget._xpdr_label(xpdr)
        assert "ISS Voice" in label
        assert "145.800" in label
        assert "Transponder" in label


# ---------------------------------------------------------------------------
# TransmitterDialog
# ---------------------------------------------------------------------------


class TestTransmitterDialog:
    def _mgr(self, db: sqlite3.Connection) -> object:
        from data.transmitter_manager import TransmitterManager

        return TransmitterManager(db)

    def test_type_list_includes_transceiver(self) -> None:
        from ui.transmitter_dialog import _TYPES

        assert "Transceiver" in _TYPES

    def test_type_list_order(self) -> None:
        from ui.transmitter_dialog import _TYPES

        assert _TYPES == ["Transmitter", "Transponder", "Transceiver", "Beacon"]

    def test_satnogs_norad_spin_exists_and_default_zero(self, qtbot, db) -> None:
        from ui.transmitter_dialog import TransmitterDialog

        w = TransmitterDialog(self._mgr(db), norad_cat_id=25544)
        qtbot.addWidget(w)
        assert hasattr(w, "_satnogs_norad_spin")
        assert w._satnogs_norad_spin.value() == 0

    def test_satnogs_norad_spin_range(self, qtbot, db) -> None:
        from ui.transmitter_dialog import TransmitterDialog

        w = TransmitterDialog(self._mgr(db))
        qtbot.addWidget(w)
        assert w._satnogs_norad_spin.minimum() == 0
        assert w._satnogs_norad_spin.maximum() == 999999

    def test_edit_mode_prefills_fields(self, qtbot, db) -> None:
        from ui.transmitter_dialog import TransmitterDialog

        existing = {
            "uuid": "manual-test",
            "norad_cat_id": 25544,
            "description": "ISS FM",
            "type": "Transceiver",
            "downlink_low": 145_800_000,
            "downlink_high": None,
            "uplink_low": 144_490_000,
            "uplink_high": None,
            "mode": "FM",
            "invert": False,
            "ctcss_tone": 67.0,
            "ctcss_tone_type": "CTCSS",
            "notes": "test note",
        }
        w = TransmitterDialog(self._mgr(db), existing=existing)
        qtbot.addWidget(w)
        assert w._desc_edit.text() == "ISS FM"
        assert abs(w._dl_spin.value() - 145.800) < 0.001
        assert abs(w._ul_spin.value() - 144.490) < 0.001
        assert w._type_combo.currentText() == "Transceiver"
        assert w._mode_combo.currentText() == "FM"
        assert abs(w._ctcss_spin.value() - 67.0) < 0.1
        assert w._notes_edit.text() == "test note"

    def test_edit_mode_title(self, qtbot, db) -> None:
        from ui.transmitter_dialog import TransmitterDialog

        existing = {
            "uuid": "manual-test",
            "norad_cat_id": 25544,
            "description": "ISS FM",
            "type": "Transceiver",
            "downlink_low": 145_800_000,
            "downlink_high": None,
            "uplink_low": None,
            "uplink_high": None,
            "mode": "FM",
            "invert": False,
            "ctcss_tone": None,
            "ctcss_tone_type": None,
            "notes": "",
        }
        w = TransmitterDialog(self._mgr(db), existing=existing)
        qtbot.addWidget(w)
        assert "Edit" in w.windowTitle()

    def test_edit_mode_norad_spin_disabled(self, qtbot, db) -> None:
        from ui.transmitter_dialog import TransmitterDialog

        existing = {
            "uuid": "manual-test",
            "norad_cat_id": 25544,
            "description": "ISS FM",
            "type": "Transponder",
            "downlink_low": 145_800_000,
            "downlink_high": None,
            "uplink_low": None,
            "uplink_high": None,
            "mode": "FM",
            "invert": False,
            "ctcss_tone": None,
            "ctcss_tone_type": None,
            "notes": "",
        }
        w = TransmitterDialog(self._mgr(db), existing=existing)
        qtbot.addWidget(w)
        assert not w._norad_spin.isEnabled()


class TestUpdateTransmitterTypeField:
    """update_transmitter が type フィールドを更新できることを確認する。"""

    def test_update_type_allowed(self, db: sqlite3.Connection) -> None:
        from data.transmitter_manager import TransmitterManager

        db.execute("INSERT OR IGNORE INTO satellites (norad_cat_id, name) VALUES (25544, 'ISS')")
        db.commit()
        mgr = TransmitterManager(db)
        uid = mgr.add_manual_transmitter(
            norad_cat_id=25544,
            description="Test FM",
            downlink_low=145_800_000,
            mode="FM",
            xpdr_type="Transponder",
        )
        mgr.update_transmitter(uid, type="Transceiver")
        row = db.execute("SELECT type FROM transmitters WHERE uuid = ?", (uid,)).fetchone()
        assert row["type"] == "Transceiver"


class TestSyncFromSatnogsTargetNorad:
    """sync_from_satnogs の target_norad_cat_id オーバーライドテスト。"""

    def test_target_norad_remaps_storage(self, db: sqlite3.Connection) -> None:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from data.transmitter_manager import TransmitterManager

        db.execute(
            "INSERT OR IGNORE INTO satellites (norad_cat_id, name) VALUES (68795, 'ORIGAMISAT-2')"
        )
        db.commit()
        mgr = TransmitterManager(db)

        mock_data = [
            {
                "uuid": "test-uuid-remap",
                "norad_cat_id": 98325,
                "description": "Mode U CW",
                "type": "Transmitter",
                "uplink_low": None,
                "uplink_high": None,
                "downlink_low": 437_505_000,
                "downlink_high": None,
                "mode": "CW",
                "invert": False,
                "baud": 24,
                "ctcss_tone": None,
                "alive": True,
            }
        ]

        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_data
        mock_resp.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("data.transmitter_manager.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            result = asyncio.run(
                mgr.sync_from_satnogs(norad_cat_id=98325, target_norad_cat_id=68795)
            )

        assert result["inserted"] == 1
        row = db.execute(
            "SELECT norad_cat_id FROM transmitters WHERE uuid='test-uuid-remap'"
        ).fetchone()
        assert row["norad_cat_id"] == 68795

    def test_no_target_uses_api_norad(self, db: sqlite3.Connection) -> None:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from data.transmitter_manager import TransmitterManager

        db.execute("INSERT OR IGNORE INTO satellites (norad_cat_id, name) VALUES (98325, 'Test')")
        db.commit()
        mgr = TransmitterManager(db)

        mock_data = [
            {
                "uuid": "test-uuid-noop",
                "norad_cat_id": 98325,
                "description": "Test",
                "type": "Transmitter",
                "uplink_low": None,
                "uplink_high": None,
                "downlink_low": 437_000_000,
                "downlink_high": None,
                "mode": "FM",
                "invert": False,
                "baud": None,
                "ctcss_tone": None,
                "alive": True,
            }
        ]

        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_data
        mock_resp.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("data.transmitter_manager.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            asyncio.run(mgr.sync_from_satnogs(norad_cat_id=98325))

        row = db.execute(
            "SELECT norad_cat_id FROM transmitters WHERE uuid='test-uuid-noop'"
        ).fetchone()
        assert row["norad_cat_id"] == 98325

    def test_update_path_no_binding_error(self, db: sqlite3.Connection) -> None:
        """sync_from_satnogs UPDATE ブランチで SQL バインドエラーが発生しないことを確認する。"""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from data.transmitter_manager import TransmitterManager

        db.execute("INSERT OR IGNORE INTO satellites (norad_cat_id, name) VALUES (25544, 'ISS')")
        db.execute(
            """
            INSERT INTO transmitters
            (uuid, norad_cat_id, description, type,
             downlink_low, mode, alive, source, manual_override, updated_at)
            VALUES ('existing-uuid', 25544, 'ISS FM', 'Transponder',
                    145800000, 'FM', 1, 'satnogs', 0, '2024-01-01')
            """
        )
        db.commit()
        mgr = TransmitterManager(db)

        mock_data = [
            {
                "uuid": "existing-uuid",
                "norad_cat_id": 25544,
                "description": "ISS FM updated",
                "type": "Transceiver",
                "uplink_low": 144_490_000,
                "uplink_high": None,
                "downlink_low": 145_800_000,
                "downlink_high": None,
                "mode": "FM",
                "invert": False,
                "baud": None,
                "ctcss_tone": 67.0,
                "alive": True,
            }
        ]

        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_data
        mock_resp.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("data.transmitter_manager.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            result = asyncio.run(mgr.sync_from_satnogs(norad_cat_id=25544))

        assert result["updated"] == 1
        row = db.execute(
            "SELECT description FROM transmitters WHERE uuid='existing-uuid'"
        ).fetchone()
        assert row["description"] == "ISS FM updated"


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

    def test_has_tab_widget_with_four_tabs(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert w._tab_widget is not None
        assert w._tab_widget.count() == 4

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

    def test_tab_has_radio_control(self, qtbot, db, tle_manager) -> None:
        from ui.radio_control_widget import RadioControlWidget

        w = self._make_window(qtbot, db, tle_manager)
        assert isinstance(w._radio_control, RadioControlWidget)

    def test_has_satellite_list(self, qtbot, db, tle_manager) -> None:
        from PySide6.QtWidgets import QListWidget

        w = self._make_window(qtbot, db, tle_manager)
        assert isinstance(w._sat_list, QListWidget)

    def test_has_detail_panel(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert isinstance(w._detail_panel, SatDetailPanel)

    def test_has_pass_list(self, qtbot, db, tle_manager) -> None:
        w = self._make_window(qtbot, db, tle_manager)
        assert isinstance(w._pass_list, PassPanel)

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

    def test_edit_transmitter_warns_when_no_list_selection(self, qtbot, db, tle_manager) -> None:
        """Edit Transmitter: sat_list に選択がなければ警告を表示する。"""
        from unittest.mock import patch

        w = self._make_window(qtbot, db, tle_manager)
        w._selected_norad = 25544  # stale value — should be ignored
        w._sat_list.clearSelection()
        with patch("ui.main_window.QMessageBox.warning") as mock_warn:
            w._on_edit_transmitter()
        mock_warn.assert_called_once()

    def test_delete_transmitter_warns_when_no_list_selection(self, qtbot, db, tle_manager) -> None:
        """Delete Transmitter: sat_list に選択がなければ警告を表示する。"""
        from unittest.mock import patch

        w = self._make_window(qtbot, db, tle_manager)
        w._selected_norad = 25544  # stale value — should be ignored
        w._sat_list.clearSelection()
        with patch("ui.main_window.QMessageBox.warning") as mock_warn:
            w._on_delete_transmitter()
        mock_warn.assert_called_once()


class TestHideSatellite:
    """Hide Satellite 機能のテスト。"""

    def _make_window(self, qtbot, db: sqlite3.Connection, tle_manager: TLEManager) -> MainWindow:
        w = MainWindow(conn=db, tle_manager=tle_manager)
        qtbot.addWidget(w)
        return w

    def test_set_hidden_updates_db(self, qtbot, populated_db) -> None:
        """_set_hidden(True) で is_hidden=1 がDBに保存される。"""
        tle_manager = TLEManager(populated_db)
        w = self._make_window(qtbot, populated_db, tle_manager)
        w._set_hidden(25544, True)
        row = populated_db.execute(
            "SELECT is_hidden FROM satellites WHERE norad_cat_id = ?", (25544,)
        ).fetchone()
        assert row["is_hidden"] == 1

    def test_set_hidden_false_unhides(self, qtbot, populated_db) -> None:
        """_set_hidden(False) で is_hidden=0 に戻る。"""
        tle_manager = TLEManager(populated_db)
        populated_db.execute(
            "UPDATE satellites SET is_hidden = 1 WHERE norad_cat_id = ?", (25544,)
        )
        populated_db.commit()
        w = self._make_window(qtbot, populated_db, tle_manager)
        w._set_hidden(25544, False)
        row = populated_db.execute(
            "SELECT is_hidden FROM satellites WHERE norad_cat_id = ?", (25544,)
        ).fetchone()
        assert row["is_hidden"] == 0

    def test_hidden_satellite_not_in_all_filter(self, qtbot, populated_db) -> None:
        """is_hidden=1 の衛星は 'All Satellites' フィルターに表示されない。"""
        tle_manager = TLEManager(populated_db)
        populated_db.execute(
            "UPDATE satellites SET is_hidden = 1 WHERE norad_cat_id = ?", (25544,)
        )
        populated_db.commit()
        w = self._make_window(qtbot, populated_db, tle_manager)
        w._filter_combo.setCurrentText("All Satellites")
        norads = [
            w._sat_list.item(i).data(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.ItemDataRole.UserRole)
            for i in range(w._sat_list.count())
        ]
        assert 25544 not in norads

    def test_hidden_filter_shows_only_hidden(self, qtbot, populated_db) -> None:
        """'Hidden' フィルターでは is_hidden=1 の衛星だけが表示される。"""
        from PySide6.QtCore import Qt

        tle_manager = TLEManager(populated_db)
        populated_db.execute(
            "UPDATE satellites SET is_hidden = 1 WHERE norad_cat_id = ?", (25544,)
        )
        populated_db.commit()
        w = self._make_window(qtbot, populated_db, tle_manager)
        w._filter_combo.setCurrentText("Hidden")
        assert w._sat_list.count() == 1
        assert w._sat_list.item(0).data(Qt.ItemDataRole.UserRole) == 25544

    def test_hide_satellite_warns_when_no_selection(self, qtbot, db, tle_manager) -> None:
        """Hide Satellite: sat_list に選択がなければ警告を表示する。"""
        from unittest.mock import patch

        w = self._make_window(qtbot, db, tle_manager)
        w._sat_list.clearSelection()
        with patch("ui.main_window.QMessageBox.warning") as mock_warn:
            w._on_hide_satellite()
        mock_warn.assert_called_once()
