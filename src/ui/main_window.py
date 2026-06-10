"""
Main window

MainWindow     — Qt6 application main window (QMainWindow)
SatDetailPanel — selected satellite detail info panel
PassListPanel  — pass prediction list panel
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

import httpx
from PySide6.QtCore import QPoint, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QPixmap,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.autotrack import AutotrackManager
from core.engine import DopplerCalculator, Observation, PassPredictor, SatelliteEngine
from core.location import LocationManager
from core.notifier import PassNotifier
from data.amsat_status import AMSATStatusFetcher
from data.ctcss_db import get_ctcss
from data.tle_manager import TLEManager
from data.transmitter_manager import TransmitterManager
from i18n import _
from rig.controller import (
    CTCSS_PRESET_TEMPLATES,
    HamlibDirectController,
    HamlibNetController,
    HamlibRotatorController,
    RigControlError,
    RigController,
    RotatorController,
)
from ui.dashboard_view import DashboardView
from ui.pass_chart import GroupPassChartView, PassChartView
from ui.pass_panel import PassPanel
from ui.radar_view import SAT_COLORS, RadarView, SatTrackData
from ui.radio_control_widget import RadioControlWidget
from ui.world_map import WorldMapView

logger = logging.getLogger(__name__)


class _SatData(TypedDict):
    """Satellite list display data (used for filtering)."""

    norad: int
    name: str
    alt_names: str  # JSON array of alias strings from SatNOGS
    is_favorite: bool
    is_hidden: int  # 0=visible, 1=user-hidden, 2=system-hidden
    status: str
    tle_group: str
    amsat_status: str | None
    tle_no_result_since: str | None  # set when no TLE found; shown yellow in list
    favorite_group: int  # 0 = not in any group, 1..N = custom group id


# Regular expression to extract AMSAT designators like AO-91, FO-29, CAS-4A
# 2-4 character prefix + optional separator + 1-3 digit number + optional trailing character
_DESIG_RE = re.compile(r"\b([A-Za-z]{2,4})[-\s]?(\d{1,3}[A-Za-z]?)\b")

# Mode inversion table for inverting transponders (invert=True).
# Downlink and uplink use opposite sidebands so that when the operator
# tunes up on one VFO the other VFO moves in the opposite direction.
_MODE_INVERT: dict[str, str] = {
    "USB": "LSB",
    "LSB": "USB",
    "CW": "CW-R",
    "CW-R": "CW",
}


# Oscar designator prefixes (e.g. AO-7, FO-29, IO-86, QO-100, RS-44, RS95S)
# Hyphen is optional to handle SatNOGS alt_names stored without it (e.g. "RS95S").
# Two capturing groups: (prefix, number+suffix) so the display can normalise to "RS-95S".
_OSCAR_RE = re.compile(
    r"\b((?:AO|BO|CO|DO|EO|FO|GO|HO|IO|JO|KO|LO|MO|NO|PO|QO|RS|SO|TO|UO|VO|XO|ZO))"
    r"-?(\d+[A-Z]?)\b",
    re.IGNORECASE,
)


def _extract_designators(name: str) -> set[str]:
    """Extract and normalize AMSAT designators from a satellite name (e.g. 'AO-91' -> {'ao91'})."""
    return {(m.group(1) + m.group(2)).lower() for m in _DESIG_RE.finditer(name)}


def _amsat_key_in_sat_name(amsat_key: str, sat_name_lower: str) -> bool:
    """Check whether an AMSAT key appears as a complete token within a satellite name.

    Only matches where the key is not adjacent to alphanumeric characters are accepted.
    Example: "iss" -> "iss (zarya)" matches; "ao-7" -> "ao-73" does not.
    """
    pattern = r"(?<![a-z0-9])" + re.escape(amsat_key) + r"(?![a-z0-9])"
    return bool(re.search(pattern, sat_name_lower))


# ---------------------------------------------------------------------------
# SatDetailPanel
# ---------------------------------------------------------------------------


class SatDetailPanel(QWidget):
    """
    Panel that displays selected satellite details (elevation, azimuth, range,
    range rate, visibility) using a QFormLayout.
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
        """Set basic info for the selected satellite."""
        self._name_label.setText(name)
        self._norad_label.setText(str(norad))

    def update_observation(self, obs: Observation | None) -> None:
        """Update observation values. Sets '—' for all fields when obs is None."""
        if obs is None:
            self._clear_obs_fields()
            return
        self._el_label.setText(f"{obs.elevation_deg:.2f}°")
        self._az_label.setText(f"{obs.azimuth_deg:.2f}°")
        self._range_label.setText(f"{obs.range_km:.1f} km")
        self._rate_label.setText(f"{obs.range_rate_km_s:.3f} km/s")
        self._vis_label.setText(_("Visible") if obs.is_above_horizon else _("Below horizon"))

    def clear(self) -> None:
        """Reset all fields."""
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
# MainWindow
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    """
    GPredict-Improved main window.

    Layout:
        Left   — satellite list (with TLE quality indicator)
        Centre — tabs (World Map / Radar / Pass Chart)
        Right  — selected satellite detail panel
        Bottom — pass prediction list
    """

    # Signal used to safely call _load_satellites from a background thread.
    # QTimer.singleShot does not fire in threads without an event loop, so a Signal is used.
    _satellite_list_refresh: Signal = Signal()
    # Signal used to pass rig control errors from a background thread to the UI thread.
    _rig_error: Signal = Signal(str)
    # Signal used to pass SATNOGS sync results from a background thread to the status bar.
    _satnogs_status: Signal = Signal(str)
    # Signals used by the SatNOGS UUID background fetch to update the UI thread.
    _satnogs_open_url: Signal = Signal(str)
    _satnogs_not_found: Signal = Signal()
    # Signal used to pass rotator position from a background thread to the UI thread.
    _rot_pos_updated: Signal = Signal(float, float)
    # Signal fired from the download thread when the default NASA map has been saved.
    _map_downloaded: Signal = Signal()
    # Signal to update sync progress label from a background thread (empty string = hide).
    _sync_progress: Signal = Signal(str)

    def __init__(
        self,
        conn: sqlite3.Connection,
        tle_manager: TLEManager,
        engine: SatelliteEngine | None = None,
        pass_predictor: PassPredictor | None = None,
        location_manager: LocationManager | None = None,
        fastapi_app: Any | None = None,
        web_port: int = 8080,
        rig_state: Any | None = None,
    ) -> None:
        """
        Args:
            conn:             SQLite connection
            tle_manager:      TLE manager
            engine:           satellite engine (no position updates if None)
            pass_predictor:   pass predictor (no pass prediction if None)
            location_manager: location manager (QTH shown as unset if None)
            fastapi_app:      FastAPI app (web server not started if None)
            web_port:         web server port number
            rig_state:        shared RigWebState (written every tick for mobile UI)
        """
        super().__init__()
        self._conn = conn
        self._tle_manager = tle_manager
        self._engine = engine
        self._pass_predictor = pass_predictor
        self._location_manager = location_manager
        self._selected_norad: int | None = None
        self._all_norads: list[int] = []  # ALL non-hidden norads (for pass predictor)
        self._visible_norads: list[int] = []  # norads currently shown in the list widget
        self._all_sat_data: list[_SatData] = []
        self._current_passes: list[Any] = []
        # Satellite name cache — rebuilt in _load_satellites, used every tick
        self._sat_name_cache: dict[int, str] = {}
        # World-map update throttle: only redraw every N ticks (default 5 s at 1 Hz)
        self._map_tick_counter: int = 0
        _MAP_UPDATE_INTERVAL: int = 5
        self._MAP_UPDATE_INTERVAL = _MAP_UPDATE_INTERVAL
        # Latest elevations computed in _update_world_map, reused by _check_autotrack
        self._last_elevations: dict[int, float] = {}
        self._current_transmitter: dict[str, Any] | None = None
        self._web_server: Any | None = None
        self._web_server_url: str = ""
        self._scheduler: Any | None = None
        # Set to True in closeEvent so background threads stop gracefully
        self._shutdown_flag = threading.Event()
        self._amsat_fetcher = AMSATStatusFetcher(conn)
        self._transmitter_manager = TransmitterManager(conn)
        self._rig_controller: RigController | None = None
        self._rig2_controller: RigController | None = None
        self._rotator_controller: RotatorController | None = None
        self._ctcss_method: str = "hamlib"
        self._ctcss_cat_on: str = ""
        self._ctcss_cat_off: str = ""
        # Lock indicating whether the rig control thread is currently running.
        # If acquire(blocking=False) fails, the previous cycle is still executing.
        self._rig_busy_lock = threading.Lock()
        # Non-blocking lock for Rig 2 (same pattern as Rig 1).
        self._rig2_busy_lock = threading.Lock()
        # Same pattern for rotator set_position calls.
        self._rot_busy_lock = threading.Lock()
        # When True, AZ sent to the rotator is offset by 180° (south-initialized rotator).
        self._rotator_south_init: bool = False
        # Cache for forced frequency transmission when the Tune button resets to centre frequency.
        # None -> use the Doppler-corrected value as-is.
        # A value -> transmit it once then reset to None.
        self._tune_dl_override: float | None = None
        self._tune_ul_override: float | None = None
        # Passband tune offset applied to SDR (Rig 2) DL, and mirrored to
        # Rig 1 UL when Lock is active (sign reversed for inverted transponders).
        self._sdr_tune_offset: float = 0.0
        # L button: when True, uplink is slaved to downlink.
        self._trsp_lock: bool = False
        # Override for CTCSS label: set when a button is pressed, reset on transponder change.
        # None -> show the transmitter's ctcss_tone; float -> persist the last-sent tone.
        self._current_ctcss_tone: float | None = None
        # Resolved CTCSS tone for the current transmitter (SatNOGS or CTCSS_DB fallback).
        self._ctcss_tone_hz: float | None = None
        # Activation tone for the current satellite (from CTCSS_DB; None if not applicable).
        self._ctcss_activation_hz: float | None = None

        # Shared rig state for mobile web UI
        self._rig_state = rig_state

        # AOS/LOS desktop notifier
        self._notifier = PassNotifier(conn)
        # Group pass results cache for notifier
        self._group_pass_results: list[object] = []
        # Sequential autotrack engine
        self._autotrack = AutotrackManager(conn)
        self._autotrack_enabled: bool = False

        from PySide6.QtWidgets import QApplication

        _ver = QApplication.applicationVersion() or "0.1.0"
        self.setWindowTitle(f"GPredict-Improved  v{_ver}")
        self.resize(1280, 800)
        self._set_app_icon()
        self._sync_progress.connect(self._on_sync_progress)

        self._build_ui()
        self._build_menu()
        self._build_statusbar()
        # Connect PassPanel signals
        self._pass_list.target_search_requested.connect(self._on_target_search_requested)
        self._pass_list.highlight_satellite.connect(self._on_highlight_satellite)
        self._pass_list.group_results_ready.connect(self._on_group_results_ready)
        self._radio_control.autotrack_toggled.connect(self._on_autotrack_toggled)
        self._radio_control.autotrack_list_changed.connect(self._on_autotrack_list_changed)
        self._pass_list.set_pass_predictor(self._pass_predictor)
        # Connect signal that receives satellite list refresh requests from background threads
        self._satellite_list_refresh.connect(self._load_satellites)
        self._rig_error.connect(self._on_rig_error)
        self._satnogs_status.connect(self._on_satnogs_status)
        self._map_downloaded.connect(self._apply_world_map)
        self._satnogs_open_url.connect(self._open_url_app_mode)
        self._satnogs_not_found.connect(
            lambda: QMessageBox.information(self, "SatNOGS", "SatNOGS page not found")
        )
        self._radio_control.transmitter_changed.connect(self._on_transmitter_changed)
        self._radio_control.cycle_changed.connect(self._on_cycle_changed)
        self._radio_control.tune_requested.connect(self._on_tune_requested)
        self._radio_control.lock_changed.connect(self._on_lock_changed)
        self._rot_pos_updated.connect(self._on_rotator_pos_updated)
        self._radio_control.ctcss_send_requested.connect(self._on_ctcss_send)
        self._radio_control.ctcss_activate_requested.connect(self._on_ctcss_activate)
        self._radio_control.rotator_connected.connect(self._on_rotator_connected)
        self._radio_control.south_init_changed.connect(self._on_south_init_changed)
        self._radio_control.rig_connected.connect(lambda: self._on_rig_slot_connected(1))
        self._radio_control.rig2_connected.connect(lambda: self._on_rig_slot_connected(2))
        self._radio_control.rig_disconnected.connect(lambda: self._on_rig_slot_disconnected(1))
        self._radio_control.rig2_disconnected.connect(lambda: self._on_rig_slot_disconnected(2))
        self._restore_satellite_filter()
        # Load bundled community transmitters immediately (no network required).
        # This runs on the main thread so satellites are visible before any
        # background sync completes — important on first launch.
        try:
            self._transmitter_manager.load_community_transmitters()
        except Exception as exc:
            logger.warning("Community transmitter load failed at startup: %s", exc)
        self._load_satellites()
        self._load_rig_settings()
        self._load_rotator_settings()
        self._reload_autotrack_lists()
        self._apply_world_map()
        self._apply_time_zone()

        if fastapi_app is not None:
            self._start_web_server(fastapi_app, web_port)

        self._start_scheduler()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(1000)
        self._load_cycle_setting()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        """Build widgets and layout."""
        v_splitter = QSplitter(Qt.Orientation.Vertical)
        self.setCentralWidget(v_splitter)

        h_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: satellite list
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(2, 2, 2, 2)
        left_layout.setSpacing(2)
        left_layout.addWidget(QLabel(_("Satellites")))

        self._filter_combo = QComboBox()
        self._rebuild_filter_combo()
        self._filter_combo.currentTextChanged.connect(self._on_filter_changed)
        left_layout.addWidget(self._filter_combo)

        # Link to the AMSAT Live Oscar Status page — visible only for the AMSAT filter
        self._amsat_link = QLabel(
            '<a href="https://www.amsat.org/status/"'
            ' style="color:#2980b9; font-size:10px;">'
            "↗ AMSAT Status Page</a>"
        )
        self._amsat_link.setOpenExternalLinks(False)
        self._amsat_link.linkActivated.connect(self._open_url_app_mode)
        self._amsat_link.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._amsat_link.setVisible(False)
        left_layout.addWidget(self._amsat_link)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText(_("Search satellites..."))
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self._on_search_changed)
        left_layout.addWidget(self._search_box)

        self._sat_list = QListWidget()
        self._sat_list.currentRowChanged.connect(self._on_sat_selected)
        self._sat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._sat_list.customContextMenuRequested.connect(self._on_sat_context_menu)
        left_layout.addWidget(self._sat_list)
        left.setMinimumWidth(140)
        left.setMaximumWidth(240)
        h_splitter.addWidget(left)

        # Centre: tabs (Dashboard / World Map / Radar / Pass Chart / Group Pass Chart / Radio)
        self._tab_widget = QTabWidget()
        self._dashboard_view = DashboardView()
        self._world_map = WorldMapView()
        self._world_map.sat_clicked.connect(self._select_satellite_by_norad)
        self._radar_view = RadarView()
        self._pass_chart = PassChartView()
        self._group_pass_chart = GroupPassChartView()
        self._radio_control = RadioControlWidget()
        self._pass_chart.range_changed.connect(self._on_chart_range_changed)
        self._dashboard_tab_idx = self._tab_widget.addTab(self._dashboard_view, _("Dashboard"))
        self._tab_widget.addTab(self._world_map, _("World Map"))
        self._tab_widget.addTab(self._radar_view, _("Radar"))
        self._tab_widget.addTab(self._pass_chart, _("Pass Chart"))
        # Group Pass Chart tab — hidden until first group search completes
        self._group_chart_tab_idx = self._tab_widget.addTab(
            self._group_pass_chart, _("Group Pass Chart")
        )
        self._tab_widget.setTabVisible(self._group_chart_tab_idx, False)
        self._tab_widget.addTab(self._radio_control, _("Radio Control"))

        # SDR Control tab — always visible; content greys out until SDR connects
        from ui.sdr_control_widget import SdrControlWidget

        self._sdr_control = SdrControlWidget()
        self._sdr_control_tab_idx = self._tab_widget.addTab(self._sdr_control, _("SDR Control"))
        self._sdr_control.tune_offset_changed.connect(self._on_sdr_tune_offset)

        self._tab_widget.currentChanged.connect(self._on_tab_changed)
        h_splitter.addWidget(self._tab_widget)

        # Right: satellite detail panel (hidden when Dashboard tab is active)
        self._detail_panel = SatDetailPanel()
        self._detail_panel.setMinimumWidth(160)
        self._detail_panel.setMaximumWidth(260)
        h_splitter.addWidget(self._detail_panel)

        h_splitter.setStretchFactor(0, 0)
        h_splitter.setStretchFactor(1, 1)
        h_splitter.setStretchFactor(2, 0)

        # Apply initial visibility: Dashboard is the first tab so currentChanged
        # won't fire on startup — hide the detail panel explicitly here.
        self._detail_panel.setVisible(False)

        # Bottom: pass prediction list (PassPanel)
        self._pass_list = PassPanel()
        self._pass_list.setMinimumHeight(200)

        v_splitter.addWidget(h_splitter)
        v_splitter.addWidget(self._pass_list)
        v_splitter.setStretchFactor(0, 3)
        v_splitter.setStretchFactor(1, 2)
        v_splitter.setSizes([600, 400])

    def _build_menu(self) -> None:
        """Build the menu bar."""
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
            sat_menu.addAction(_("Edit Transmitter..."), self._on_edit_transmitter)
            sat_menu.addAction(_("Delete Transmitter..."), self._on_delete_transmitter)
            sat_menu.addAction(_("Hide Satellite"), self._on_hide_satellite)
            sat_menu.addSeparator()
            sat_menu.addAction(_("Add Manual TLE..."), self._on_add_manual_tle)
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
                ja_action = lang_menu.addAction(
                    "Japanese",
                    lambda: QMessageBox.information(self, "Language", "To be prepared later."),
                )
                ja_action.setEnabled(True)

            tz_menu = view_menu.addMenu(_("Time Zone"))
            if tz_menu:
                tz_group = QActionGroup(self)
                tz_group.setExclusive(True)
                self._tz_utc_action = QAction("UTC", self, checkable=True)
                self._tz_local_action = QAction(_("Local Time"), self, checkable=True)
                tz_group.addAction(self._tz_utc_action)
                tz_group.addAction(self._tz_local_action)
                tz_menu.addAction(self._tz_utc_action)
                tz_menu.addAction(self._tz_local_action)
                self._tz_utc_action.triggered.connect(lambda: self._on_time_zone_changed(True))
                self._tz_local_action.triggered.connect(lambda: self._on_time_zone_changed(False))

        # Help
        help_menu = mb.addMenu(_("Help"))
        if help_menu:
            help_menu.addAction(_("Satellite Color"), self._on_satellite_color)
            help_menu.addSeparator()
            help_menu.addAction(_("Check for Updates…"), self._on_check_updates)
            help_menu.addAction(_("SDR Device Installation…"), self._on_sdr_install)
            help_menu.addAction(_("Hamlib Update…"), self._on_hamlib_update)
            help_menu.addSeparator()
            help_menu.addAction(_("About"), self._on_about)
            help_menu.addAction(_("GitHub"), self._on_github)

    def _set_app_icon(self) -> None:
        """Set the application window icon from the bundled assets."""
        from pathlib import Path

        # Locate icon: PyInstaller bundle uses _MEIPASS, dev uses assets/ in repo root
        if getattr(sys, "frozen", False):
            icon_path = Path(getattr(sys, "_MEIPASS", "")) / "assets" / "icon_256.png"
        else:
            icon_path = Path(__file__).parent.parent.parent / "assets" / "icon_256.png"

        if icon_path.exists():
            icon = QIcon(str(icon_path))
            self.setWindowIcon(icon)
            from PySide6.QtWidgets import QApplication

            QApplication.setWindowIcon(icon)

    def _on_sync_progress(self, text: str) -> None:
        """Update the sync progress label in the status bar (called on UI thread)."""
        if text:
            self._sync_label.setText(text)
            self._sync_label.setVisible(True)
        else:
            self._sync_label.setVisible(False)

    def _build_statusbar(self) -> None:
        """Build the status bar."""
        sb = self.statusBar()

        self._qth_label = QLabel("QTH: Not set")
        self._tle_label = QLabel("")
        self._filter_label = QLabel("Showing: All")
        self._sync_label = QLabel("")
        self._sync_label.setStyleSheet("color: #f0a500; font-style: italic;")
        self._sync_label.setVisible(False)
        self._url_label = QLabel("")
        self._qr_button = QPushButton("QR")
        self._qr_button.setFlat(True)
        self._qr_button.setMaximumWidth(32)
        self._qr_button.setToolTip(_("Show QR code for web access"))
        self._qr_button.clicked.connect(self._on_show_qr)
        self._rig_label = QLabel(_("RIG: Off"))
        self._rot_label = QLabel(_("ROT: Off"))

        if sb:
            sb.addWidget(self._qth_label)
            sb.addWidget(self._tle_label)
            sb.addWidget(self._filter_label)
            sb.addWidget(self._sync_label)
            sb.addPermanentWidget(self._url_label)
            sb.addPermanentWidget(self._qr_button)
            sb.addPermanentWidget(self._rig_label)
            sb.addPermanentWidget(self._rot_label)

    # ------------------------------------------------------------------ #
    # Data loading
    # ------------------------------------------------------------------ #

    def _restore_satellite_filter(self) -> None:
        """Restore the satellite filter combo to the last saved selection."""
        try:
            row = self._conn.execute(
                "SELECT value FROM app_settings WHERE key = 'satellite_filter'"
            ).fetchone()
            if row:
                idx = self._filter_combo.findText(row[0])
                if idx >= 0:
                    self._filter_combo.blockSignals(True)
                    self._filter_combo.setCurrentIndex(idx)
                    self._filter_combo.blockSignals(False)
                    # Sync the AMSAT link visibility (signal was blocked)
                    self._amsat_link.setVisible(row[0] == "Operational (AMSAT)")
        except Exception:
            pass

    def _load_satellites(self) -> None:
        """Load satellite data from the DB, build the internal list, and apply filters."""
        amsat_map: dict[str, str] = self._amsat_fetcher.load_cached() or {}

        designator_status: dict[str, str] = {}
        for amsat_name, status in amsat_map.items():
            for desig in _extract_designators(amsat_name):
                designator_status[desig] = status

        amsat_keys_by_len = sorted(amsat_map.keys(), key=len, reverse=True)

        rows = self._conn.execute(
            """
            SELECT s.norad_cat_id, s.name, s.alt_names, s.is_favorite, s.is_hidden, s.status,
                   COALESCE(t.tle_group, 'amateur') AS tle_group,
                   s.tle_no_result_since,
                   COALESCE(s.favorite_group, 0) AS favorite_group
            FROM satellites s
            LEFT JOIN tle_data t ON s.norad_cat_id = t.norad_cat_id
            ORDER BY s.name
            """
        ).fetchall()

        self._all_sat_data = []
        self._all_norads = []
        self._sat_name_cache = {}

        for row in rows:
            norad: int = int(row["norad_cat_id"])
            name: str = str(row["name"])
            self._sat_name_cache[norad] = name

            # Parse alt_names once; used for both AMSAT matching and display
            try:
                alt_list: list[str] = json.loads(str(row["alt_names"] or "[]"))
            except (json.JSONDecodeError, ValueError):
                alt_list = []

            # Match AMSAT status against primary name then each alt_name in order.
            # This covers renamed satellites (e.g. DOSAAF-85 a.k.a. RS-44).
            amsat_status: str | None = None
            for candidate in [name, *alt_list]:
                cand_lower = candidate.lower()
                amsat_status = amsat_map.get(cand_lower)
                if amsat_status is not None:
                    break
                for desig in _extract_designators(candidate):
                    if desig in designator_status:
                        amsat_status = designator_status[desig]
                        break
                if amsat_status is not None:
                    break
                for amsat_key in amsat_keys_by_len:
                    if _amsat_key_in_sat_name(amsat_key, cand_lower):
                        amsat_status = amsat_map[amsat_key]
                        break
                if amsat_status is not None:
                    break

            self._all_sat_data.append(
                _SatData(
                    norad=norad,
                    name=name,
                    alt_names=str(row["alt_names"] or "[]"),
                    is_favorite=bool(row["is_favorite"]),
                    is_hidden=int(row["is_hidden"] or 0),
                    status=str(row["status"] or "unknown"),
                    tle_group=str(row["tle_group"]),
                    amsat_status=amsat_status,
                    tle_no_result_since=(
                        str(row["tle_no_result_since"]) if row["tle_no_result_since"] else None
                    ),
                    favorite_group=int(row["favorite_group"] or 0),
                )
            )
            self._all_norads.append(norad)

        self._apply_filter()

    def _apply_filter(self) -> None:
        """Redraw the satellite list according to the filter combo and search box."""
        filter_text = self._filter_combo.currentText()
        search_query = self._search_box.text().strip().lower()

        # Pause signals to prevent currentRowChanged(-1) from firing during list rebuild
        # (which would clear the selection and display), then restore the selection afterward
        prev_item = self._sat_list.currentItem()
        current_norad: int | None = (
            prev_item.data(Qt.ItemDataRole.UserRole) if prev_item is not None else None
        )

        self._sat_list.blockSignals(True)
        self._sat_list.clear()
        count = 0
        restore_row = -1
        filtered_sats: list[tuple[int, str]] = []

        for d in self._all_sat_data:
            # Visibility filter:
            #   is_hidden=1 (user-hidden)   -> shown only under the "Hidden" filter
            #   is_hidden=2 (system-hidden) -> hidden from all filters
            if filter_text == "Hidden":
                if d["is_hidden"] != 1:
                    continue
            elif d["is_hidden"] != 0:
                continue
            # Category filter — custom groups (e.g. "★ Favorite 1")
            if filter_text.startswith("★ "):
                group_name = filter_text[2:]  # strip leading "★ "
                grp_row = self._conn.execute(
                    "SELECT id FROM custom_groups WHERE name = ?", (group_name,)
                ).fetchone()
                if grp_row is None or d["favorite_group"] != grp_row["id"]:
                    continue
            if filter_text == "Amateur" and d["tle_group"] != "amateur":
                continue
            if filter_text == "CubeSat" and d["tle_group"] != "cubesat":
                continue
            if filter_text == "Weather" and d["tle_group"] != "weather":
                continue
            if filter_text == "Earth Observation" and d["tle_group"] != "earth-obs":
                continue
            if filter_text == "Science" and d["tle_group"] != "science":
                continue
            if filter_text == "Space Stations" and d["tle_group"] != "stations":
                continue
            if filter_text == "Operational (AMSAT)" and d["amsat_status"] != "operational":
                continue
            # Search filter (case-insensitive substring match on name and alt_names)
            if search_query and search_query not in d["name"].lower():
                try:
                    alts_lower = " ".join(json.loads(d["alt_names"])).lower()
                except (json.JSONDecodeError, ValueError):
                    alts_lower = ""
                if search_query not in alts_lower:
                    continue

            prefix = "★ " if d["favorite_group"] > 0 else ""
            # Append Oscar designator (e.g. "(IO-86)") when not already in the name
            oscar_suffix = ""
            try:
                alt_list: list[str] = json.loads(d["alt_names"])
            except (json.JSONDecodeError, ValueError):
                alt_list = []
            name_upper = d["name"].upper()
            for alt in alt_list:
                m = _OSCAR_RE.search(alt)
                if m:
                    oscar_str = f"{m.group(1)}-{m.group(2)}".upper()
                    if oscar_str not in name_upper:
                        oscar_suffix = f" ({oscar_str})"
                        break
            item = QListWidgetItem(prefix + d["name"] + oscar_suffix)
            item.setData(Qt.ItemDataRole.UserRole, d["norad"])

            amsat_status = d["amsat_status"]

            if amsat_status == "operational":
                item.setForeground(QColor("#2ecc71"))
                font: QFont = item.font()
                font.setBold(True)
                item.setFont(font)
            elif amsat_status == "partial":
                item.setForeground(QColor("#f1c40f"))
            elif amsat_status == "non_operational":
                item.setForeground(QColor("#e74c3c"))
            elif d["tle_no_result_since"] is not None:
                # Alive satellite in 30-day TLE grace period → purple caution
                item.setForeground(QColor("#9b59b6"))
                font = item.font()
                font.setItalic(True)
                item.setFont(font)
            elif d["status"] == "alive":
                item.setForeground(QColor("#e67e22"))
            else:
                item.setForeground(QColor("#7f8c8d"))

            self._sat_list.addItem(item)
            if current_norad is not None and d["norad"] == current_norad:
                restore_row = count
            filtered_sats.append((d["norad"], d["name"]))
            count += 1

        if restore_row >= 0:
            self._sat_list.setCurrentRow(restore_row)
        self._sat_list.blockSignals(False)

        if search_query:
            self._filter_label.setText(f"Search: '{search_query}' — {count} matches")
        elif filter_text == "All Satellites":
            self._filter_label.setText(f"Showing: All ({count})")
        else:
            self._filter_label.setText(f"Showing: {filter_text} ({count})")

        self._pass_list.set_satellites(filtered_sats)

        # Update the visible norads list (used for world-map computation)
        if filter_text == "All Satellites" and not search_query:
            self._visible_norads = list(self._all_norads)
            self._world_map.set_visible_norads(None)
        else:
            self._visible_norads = [n for n, _ in filtered_sats]
            self._world_map.set_visible_norads(set(self._visible_norads))

    # ------------------------------------------------------------------ #
    # Background processing
    # ------------------------------------------------------------------ #

    def _start_web_server(self, fastapi_app: Any, port: int) -> None:
        """Start the FastAPI app with uvicorn in the background."""
        try:
            from web.server import WebServer

            self._web_server = WebServer(fastapi_app, port=port)
            url = self._web_server.start()
            self._web_server_url = url
            self._url_label.setText(url)
        except Exception as exc:
            logger.warning("Web server start failed: %s", exc)

    def _start_scheduler(self) -> None:
        """Register and start TLE and AMSAT auto-update jobs with APScheduler."""
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
            self._scheduler.add_job(
                self._refresh_provisional_tle_sync,
                "interval",
                hours=12,
                id="provisional_tle_refresh",
                misfire_grace_time=600,
            )
            self._scheduler.add_job(
                self._refresh_active_tle_sync,
                "interval",
                hours=24,
                id="active_tle_refresh",
                misfire_grace_time=1800,
            )
            self._scheduler.start()
            logger.debug("APScheduler started")
        except Exception as exc:
            logger.warning("APScheduler start failed: %s", exc)
            self._scheduler = None

        # On startup, refresh AMSAT status in the background if stale
        if self._amsat_fetcher.is_stale():
            threading.Thread(target=self._refresh_amsat_sync, daemon=True).start()

        # On startup, auto-sync SATNOGS transmitters if none have been fetched from SATNOGS yet.
        # Community transmitters (source='community') are always present on first launch,
        # so check specifically for SATNOGS-sourced transmitters instead of total count.
        satnogs_count = self._conn.execute(
            "SELECT COUNT(*) FROM transmitters WHERE source = 'satnogs'"
        ).fetchone()[0]
        if satnogs_count == 0:
            threading.Thread(target=self._refresh_satnogs_sync, daemon=True).start()

        # Always sync satellite names (inserts new satellites too) and then fetch TLEs.
        # Active-TLE fetch runs inside _refresh_satellite_names_sync so it is guaranteed
        # to start only after satellite rows are present in the DB.
        threading.Thread(target=self._refresh_satellite_names_sync, daemon=True).start()

    @staticmethod
    def _sort_sources_by_priority(sources: list[str]) -> list[str]:
        """Sort TLE source names by their priority in TLE_SOURCES (ascending).

        Running sources in ascending priority order ensures that more-specific
        groups (e.g. cubesat priority=2) always execute *after* more-general ones
        (amateur priority=1), so their tle_group value wins for overlapping satellites.
        Sources not found in TLE_SOURCES are appended at the end.
        """
        from data.tle_manager import TLE_SOURCES

        priority_map = {s["name"]: int(s.get("priority", 99)) for s in TLE_SOURCES}
        return sorted(sources, key=lambda n: priority_map.get(n, 99))

    def _refresh_tle_sync(self) -> None:
        """Update all enabled TLE sources from a background thread (APScheduler job)."""
        from ui.settings_dialog import SettingsDialog

        enabled = self._sort_sources_by_priority(SettingsDialog.get_enabled_sources(self._conn))
        for source_name in enabled:
            try:
                asyncio.run(self._tle_manager.fetch_and_update(source_name))
                logger.info("TLE refresh completed: %s", source_name)
            except Exception as exc:
                logger.warning("TLE refresh failed: %s — %s", source_name, exc)
        # Refresh the satellite list so tle_group changes are visible in the UI
        # without requiring an app restart.
        self._satellite_list_refresh.emit()

    def _refresh_amsat_sync(self) -> None:
        """Update AMSAT operational status from a background thread."""
        try:
            asyncio.run(self._amsat_fetcher.fetch_and_update())
            logger.info("AMSAT status refresh completed")
            self._satellite_list_refresh.emit()
        except Exception as exc:
            logger.warning("AMSAT status refresh failed: %s", exc)

    def _refresh_satellite_names_sync(self) -> None:
        """Sync satellite names from SATNOGS, then fetch provisional and legacy TLEs.

        Execution order:
          1. sync_satellite_names() — updates names/status, runs migration pipelines
          2. fetch_provisional_tles() — TLEs for visible NORAD >= 90000 satellites
          3. fetch_legacy_tles() — one-time check for NORAD < 10000 satellites;
             hides those no longer tracked by CelesTrak (fast no-op after first run)
        """
        from ui.settings_dialog import SettingsDialog  # local import to avoid circular dep

        if self._shutdown_flag.is_set():
            return
        self._sync_progress.emit("🛰 Syncing satellites from SATNOGS...")

        def _sat_progress(n: int) -> None:
            self._sync_progress.emit(f"🛰 Syncing satellites... ({n:,})")

        try:
            result = asyncio.run(
                self._transmitter_manager.sync_satellite_names(progress_callback=_sat_progress)
            )
            logger.info("SATNOGS satellite names sync completed: %s", result)
        except Exception as exc:
            logger.warning("SATNOGS satellite names sync failed: %s: %s", type(exc).__name__, exc)

        if self._shutdown_flag.is_set():
            return

        # Fetch TLEs for remaining visible provisional satellites (NORAD >= 90000).
        try:
            prov = asyncio.run(self._tle_manager.fetch_provisional_tles())
            logger.info("Provisional TLE fetch completed: %s", prov)
        except Exception as exc:
            logger.warning("Provisional TLE fetch failed: %s", exc)

        if self._shutdown_flag.is_set():
            return

        # One-time cleanup for very old satellites (NORAD < 10000).
        # Hides those no longer in CelesTrak; fast no-op once all are resolved.
        try:
            legacy = asyncio.run(self._tle_manager.fetch_legacy_tles())
            if legacy.get("found", 0) + legacy.get("hidden", 0) > 0:
                logger.info("Legacy satellite TLE check completed: %s", legacy)
        except Exception as exc:
            logger.warning("Legacy satellite TLE check failed: %s", exc)

        if self._shutdown_flag.is_set():
            return

        # Load bundled community transmitters (FT4/FT8 calling freqs, etc.)
        try:
            comm = self._transmitter_manager.load_community_transmitters()
            if comm["inserted"] + comm["updated"] > 0:
                logger.info("Community transmitters loaded: %s", comm)
        except Exception as exc:
            logger.warning("Community transmitter load failed: %s", exc)

        # On first launch (fresh install) the APScheduler group-specific jobs
        # (celestrak-cubesat, celestrak-weather, etc.) haven't fired yet because
        # they are scheduled with interval hours=2/4/6/12.  Without this initial
        # fetch, every satellite ends up with tle_group='amateur' and CubeSat /
        # Weather / Science / Earth-Obs / Space-Stations groups appear empty.
        # Run this BEFORE fetch_active_tles() (which can take 20-30 min on first
        # run) so the user sees correct group counts as soon as the satellite list
        # refreshes — without waiting for the long Phase 2 SATNOGS fallback.
        #
        # Also handles the upgrade case (e.g. Windows): a previous beta may have
        # left sync_log entries so is_source_stale() returns False, but without the
        # CASE WHEN protection that beta introduced, all tle_group values were
        # overwritten back to 'amateur'.  We detect this by checking whether the
        # expected tle_group has 0 satellites in tle_data and treat it as stale.
        enabled = self._sort_sources_by_priority(SettingsDialog.get_enabled_sources(self._conn))
        stale_sources = [
            s
            for s in enabled
            if self._tle_manager.is_source_stale(s) or self._tle_manager.is_group_empty(s)
        ]
        if stale_sources:
            logger.info("First-run group TLE fetch: %s", stale_sources)
            self._sync_progress.emit(_("Fetching group TLEs (first run)..."))
            for source_name in stale_sources:
                if self._shutdown_flag.is_set():
                    break
                try:
                    result = asyncio.run(self._tle_manager.fetch_and_update(source_name))
                    logger.info("First-run TLE fetch done: %s -> %s", source_name, result)
                except Exception as exc:
                    logger.warning("First-run TLE fetch failed: %s - %s", source_name, exc)
            self._sync_progress.emit("")

        # Refresh the satellite list now that names and group TLEs are synced.
        self._satellite_list_refresh.emit()
        self._sync_progress.emit("")  # Hide sync label once satellite list is ready

        if self._shutdown_flag.is_set():
            return

        # Fetch active TLEs last; Phase 2 SATNOGS fallback can take 20-30 min.
        # The satellite list has already been refreshed above so the user sees
        # correct group assignments without waiting for this to complete.
        if self._tle_manager.is_active_tle_stale():
            self._refresh_active_tle_sync()
        else:
            logger.info("Active TLE cache is fresh — skipping fetch.")

    # ------------------------------------------------------------------ #
    # Timer callback (every 1 second)
    # ------------------------------------------------------------------ #

    def _on_tick(self) -> None:
        """Timer callback that updates satellite positions and the status bar."""
        try:
            # World map position update is throttled to every MAP_UPDATE_INTERVAL ticks
            # (default 5 seconds) to reduce Skyfield SGP4 computation load.
            self._map_tick_counter += 1
            if self._map_tick_counter >= self._MAP_UPDATE_INTERVAL:
                self._map_tick_counter = 0
                self._update_world_map()

            self._update_selected_satellite()
            self._update_statusbar()
            self._check_notifications()
            self._check_autotrack()
            self._update_rig_web_state()
        except Exception:
            logger.exception("_on_tick error")

    def _update_rig_web_state(self) -> None:
        """Push current rig/rotator state to the shared RigWebState for the mobile web UI."""
        if self._rig_state is None:
            return
        rs = self._rig_state

        # Rig 1
        rig = self._rig_controller
        rs.rig_connected = rig is not None and rig.is_connected
        rs.rig_engaged = rs.rig_connected and self._current_transmitter is not None

        # Frequencies from current transmitter + Doppler
        if self._current_transmitter is not None and self._selected_norad is not None:
            obs = self._engine.observe(self._selected_norad) if self._engine else None
            if obs is not None:
                dl_nom = self._current_transmitter.get("downlink_low")
                ul_nom = self._current_transmitter.get("uplink_low")
                rr = obs.range_rate_km_s
                if dl_nom:
                    dl_hz = float(dl_nom)
                    doppler_dl = -dl_hz * rr / 299792.458
                    rs.dl_hz = dl_hz + doppler_dl
                    rs.dl_doppler_hz = doppler_dl
                else:
                    rs.dl_hz = rs.dl_doppler_hz = None
                if ul_nom:
                    ul_hz = float(ul_nom)
                    invert = bool(self._current_transmitter.get("invert", False))
                    doppler_ul = ul_hz * rr / 299792.458 if invert else -ul_hz * rr / 299792.458
                    rs.ul_hz = ul_hz + doppler_ul
                    rs.ul_doppler_hz = doppler_ul
                else:
                    rs.ul_hz = rs.ul_doppler_hz = None
            rs.mode = str(self._current_transmitter.get("mode") or "")
        else:
            rs.dl_hz = rs.ul_hz = rs.dl_doppler_hz = rs.ul_doppler_hz = None
            rs.mode = ""

        # Rotator
        rot = self._rotator_controller
        rs.rot_connected = rot is not None and rot.is_connected
        rs.rot_engaged = rs.rot_connected

        # Handle toggle requests from mobile UI
        if rs.rig_toggle_requested:
            rs.rig_toggle_requested = False
            # Toggle by changing _current_transmitter to None or restoring
            if rs.rig_engaged:
                self._current_transmitter = None
            # (re-engage handled by user selecting transponder again)
        if rs.rot_toggle_requested:
            rs.rot_toggle_requested = False
            if self._rotator_controller is not None:
                if self._rotator_controller.is_connected:
                    self._rotator_controller.disconnect()
                else:
                    self._rotator_controller.connect()

        # Connect request from mobile UI (satellite + transponder already chosen)
        if rs.rig_connect_requested:
            rs.rig_connect_requested = False
            norad = rs.requested_norad
            xpdr_uuid = rs.requested_xpdr_uuid
            if norad is not None and xpdr_uuid is not None:
                self._mobile_rig_connect(norad, xpdr_uuid)

        # Disconnect request from mobile UI
        if rs.rig_disconnect_requested:
            rs.rig_disconnect_requested = False
            self._disconnect_rig()
            self._radio_control.refresh_status()

    def _mobile_rig_connect(self, norad: int, xpdr_uuid: str) -> None:
        """Select satellite+transponder and connect rig, triggered from mobile UI."""
        # 1. Select satellite in list widget (fires _on_sat_selected)
        self._select_satellite_by_norad(norad)

        # 2. Select transponder by UUID
        transmitters = self._transmitter_manager.get_transmitters(norad)
        try:
            idx = next(i for i, t in enumerate(transmitters) if t["uuid"] == xpdr_uuid)
        except StopIteration:
            idx = 0
        if transmitters:
            self._radio_control.set_transmitters(transmitters, default_index=idx)
            # _on_transmitter_changed is triggered by set_transmitters

        # 3. Connect rig if not already connected
        if self._rig_controller is not None and not self._rig_controller.is_connected:
            self._rig_controller.connect()
            self._radio_control.refresh_status()

    def _check_notifications(self) -> None:
        """Fire AOS/LOS desktop notifications for Target and Group passes."""
        # Target satellite passes
        if self._current_passes and self._selected_norad is not None:
            sat_name = self._sat_name_cache.get(self._selected_norad, str(self._selected_norad))
            self._notifier.check(self._current_passes, sat_name)

        # Group search passes
        if self._group_pass_results:
            self._notifier.check_group(self._group_pass_results)

    def _check_autotrack(self) -> None:
        """Run one autotrack evaluation cycle (called every second from _on_tick)."""
        if not self._autotrack_enabled or self._engine is None:
            return
        if not self._autotrack.is_ready:
            return

        if self._pass_predictor is None:
            return
        result = self._autotrack.check(
            self._engine,
            self._pass_predictor,
            cached_elevations=self._last_elevations,
        )
        if result is None:
            # Update status label with next satellite info
            info = self._autotrack.next_satellite_info(self._engine, self._pass_predictor)
            if info is not None:
                next_name, next_aos = info
                if next_aos is not None:
                    from datetime import UTC  # noqa: PLC0415

                    now = datetime.now(UTC)
                    mins = int((next_aos - now).total_seconds() / 60)
                    self._radio_control.set_autotrack_status(
                        f"Next: {next_name} in {mins} min", ok=True
                    )
            return

        next_norad, xpdr_uuid = result

        # Switch satellite in the UI
        self._select_satellite_by_norad(next_norad)

        # Switch transponder to the registered one
        xpdr_row = self._conn.execute(
            "SELECT * FROM transmitters WHERE uuid = ?", (xpdr_uuid,)
        ).fetchone()
        if xpdr_row:
            # Find the index in the current transmitter list
            transmitters = self._transmitter_manager.get_transmitters(next_norad)
            try:
                idx = next(i for i, t in enumerate(transmitters) if t["uuid"] == xpdr_uuid)
            except StopIteration:
                idx = 0
            self._radio_control.set_transmitters(transmitters, default_index=idx)

        sat_name = self._sat_name_cache.get(next_norad, str(next_norad))
        self._radio_control.set_autotrack_status(f"Tracking: {sat_name}", ok=True)

    def _select_satellite_by_norad(self, norad: int) -> None:
        """Select a satellite in the list widget by NORAD id (autotrack helper)."""
        for i in range(self._sat_list.count()):
            item = self._sat_list.item(i)
            if item is not None and int(item.data(Qt.ItemDataRole.UserRole)) == norad:
                self._sat_list.setCurrentRow(i)
                return

    def _reload_autotrack_lists(self) -> None:
        """Reload Autotrack Lists from DB and refresh the Radio Control combo."""
        lists = AutotrackManager.get_all_lists(self._conn)
        self._radio_control.populate_autotrack_lists(lists)

    def _on_autotrack_toggled(self, enabled: bool) -> None:
        """Called when the user toggles the Autotrack checkbox."""
        self._autotrack_enabled = enabled
        if not enabled:
            self._autotrack.reset()
            self._radio_control.set_autotrack_status("—")
        else:
            if not self._autotrack.is_ready:
                self._radio_control.set_autotrack_status(_("Run a pass search first"), ok=False)

    def _on_autotrack_list_changed(self, list_id: object) -> None:
        """Called when the user selects a different Autotrack List."""
        lid = int(list_id) if isinstance(list_id, int) else None
        self._autotrack.set_list(lid)
        self._autotrack_enabled = False
        self._radio_control.set_autotrack_enabled(False)
        self._radio_control.set_autotrack_status("—")

    def _update_world_map(self) -> None:
        """Fetch satellite subpoints for visible satellites and update the world map.

        Only computes positions for the satellites currently shown in the list widget
        (_visible_norads) instead of all non-hidden satellites, reducing SGP4 load
        significantly when a filter is active.  Uses _sat_name_cache to avoid a DB
        round-trip every 5 seconds.
        """
        # Update the observer location star marker (regardless of whether the engine is set)
        if self._location_manager is not None and self._location_manager.current is not None:
            loc = self._location_manager.current
            self._world_map.set_observer_location(loc.latitude_deg, loc.longitude_deg)
            self._dashboard_view.set_observer(loc.latitude_deg, loc.longitude_deg)

        if self._engine is None or not self._visible_norads:
            return

        # Use cached name map — rebuilt in _load_satellites, no DB hit here
        name_map = self._sat_name_cache

        subpoints = self._engine.subpoints(self._visible_norads)
        sat_data: dict[int, tuple[str, float, float, QColor]] = {}
        new_elevations: dict[int, float] = {}

        for i, norad in enumerate(self._visible_norads):
            if norad in subpoints:
                lat, lon = subpoints[norad]
                color = SAT_COLORS[i % len(SAT_COLORS)]
                sat_data[norad] = (name_map.get(norad, str(norad)), lat, lon, color)
            # Cache elevation for autotrack reuse (observe is cheap after subpoint)
            obs = self._engine.observe(norad)
            if obs is not None:
                new_elevations[norad] = obs.elevation_deg

        self._last_elevations = new_elevations
        self._world_map.set_satellites(sat_data)

        # Update the selected satellite's footprint (moves dynamically every second)
        if self._selected_norad is not None:
            swa = self._engine.subpoint_with_alt(self._selected_norad)
            if swa is not None:
                fp_lat, fp_lon, alt_km = swa
                self._world_map.draw_footprint(self._selected_norad, fp_lat, fp_lon, alt_km)
            else:
                self._world_map.clear_footprint()
        else:
            self._world_map.clear_footprint()

    def _update_selected_satellite(self) -> None:
        """Update the observation values and radar view for the currently selected satellite."""
        if self._engine is None or self._selected_norad is None:
            return

        obs = self._engine.observe(self._selected_norad)
        self._detail_panel.update_observation(obs)

        if obs is not None:
            item = self._sat_list.currentItem()
            name = item.text() if item else str(self._selected_norad)

            # Choose next pass info depending on whether a pass is currently active
            now = datetime.now(UTC)
            next_pass = next(
                (p for p in self._current_passes if p.los > now),
                None,
            )
            aos_t = next_pass.aos if next_pass is not None else None
            los_t = next_pass.los if next_pass is not None else None
            next_max_el = next_pass.max_elevation_deg if next_pass is not None else None
            next_dur = next_pass.duration_s if next_pass is not None else None

            # Compute track points from AOS to LOS at ~30-second intervals
            # for the next (or current) pass.
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

            # Dashboard: update map+radar even without a transmitter
            if self._current_transmitter is None:
                swa = self._engine.subpoint_with_alt(self._selected_norad)
                self._dashboard_view.update_observation(obs, subpoint=swa, track_data=track)

        # Radio Control: update Doppler correction in real time.
        # Always compute and transmit as long as TLE and frequency data are
        # available, regardless of elevation.
        if obs is not None and self._current_transmitter is not None:
            rr = obs.range_rate_km_s
            dl_nom = self._current_transmitter.get("downlink_low")
            ul_nom = self._current_transmitter.get("uplink_low")
            invert = bool(self._current_transmitter.get("invert", False))
            mode = self._current_transmitter.get("mode")
            dl_corr, dl_shift = (
                DopplerCalculator.correct_downlink(float(dl_nom), rr)
                if dl_nom is not None
                else (None, None)
            )
            if self._trsp_lock and dl_corr is not None:
                # Lock ON: calculate uplink from the downlink offset.
                ul_low = self._current_transmitter.get("uplink_low")
                ul_high = self._current_transmitter.get("uplink_high")
                dl_low_nom = self._current_transmitter.get("downlink_low")
                if ul_low is not None and dl_low_nom is not None:
                    delta = dl_corr - float(dl_low_nom)
                    if invert and ul_high is not None:
                        ul_calc = float(ul_high) - delta
                    else:
                        ul_calc = float(ul_low) + delta
                    ul_corr, ul_shift = ul_calc, None
                else:
                    ul_corr, ul_shift = (None, None)
            else:
                ul_corr, ul_shift = (
                    DopplerCalculator.correct_uplink(float(ul_nom), rr, invert=invert)
                    if ul_nom is not None
                    else (None, None)
                )
            # If the Tune button has set an override, use the centre frequency,
            # then reset to None afterward (subsequent cycles return to Doppler-corrected values).
            if self._tune_dl_override is not None:
                dl_corr = self._tune_dl_override
                dl_shift = None
                self._tune_dl_override = None
            if self._tune_ul_override is not None:
                ul_corr = self._tune_ul_override
                ul_shift = None
                self._tune_ul_override = None

            ctcss_display = (
                self._current_ctcss_tone
                if self._current_ctcss_tone is not None
                else self._ctcss_tone_hz
            )
            self._radio_control.update_doppler(
                dl_nom,
                dl_corr,
                dl_shift,
                ul_nom,
                ul_corr,
                ul_shift,
                mode,
                ctcss_display,
            )
            # Update Dashboard status bar with Doppler frequencies
            swa = self._engine.subpoint_with_alt(self._selected_norad)
            sat_color = SAT_COLORS[0]
            self._dashboard_view.update_observation(
                obs,
                subpoint=swa,
                sat_color=sat_color,
                dl_hz=dl_corr,
                ul_hz=ul_corr,
                dl_doppler=dl_shift,
                ul_doppler=ul_shift,
                track_data=track,
            )
            # Transmit Doppler-corrected frequencies to the connected rig (regardless of elevation).
            # set_vfo_frequencies() involves TCP communication with recv(), so calling it on the
            # UI thread directly would block and freeze the display.
            # Use _rig_busy_lock: if the previous cycle has finished, transmit on a background
            # thread; if the previous cycle is still running, skip this tick.
            # Passband tune offset: apply to the SDR rig's DL, and when Lock is
            # ON mirror it to the other rig's TX (sign inverted for inverted
            # transponders).  Works regardless of whether SDR is Rig 1 or Rig 2.
            tune = self._sdr_tune_offset
            sdr_is_rig1 = (
                self._rig_controller is not None
                and getattr(self._rig_controller, "is_sdr", False)
                and self._rig_controller.is_connected
            )
            sdr_is_rig2 = (
                self._rig2_controller is not None
                and getattr(self._rig2_controller, "is_sdr", False)
                and self._rig2_controller.is_connected
            )
            # DL for Rig 1: add tune offset when SDR is Rig 1
            dl_rig1 = (dl_corr + tune) if (dl_corr is not None and sdr_is_rig1) else dl_corr
            # UL for Rig 1: mirror tune offset when SDR is Rig 2 and Lock is ON
            ul_rig1 = ul_corr
            if sdr_is_rig2 and self._trsp_lock and tune != 0.0 and ul_rig1 is not None:
                ul_rig1 = ul_rig1 + (-tune if invert else tune)

            if self._rig_controller is not None and self._rig_controller.is_connected:
                if self._rig_busy_lock.acquire(blocking=False):
                    rig = self._rig_controller
                    dl = dl_rig1
                    ul = ul_rig1

                    def _rig_send() -> None:
                        try:
                            rig.set_vfo_frequencies(dl, ul)
                        except RigControlError as exc:
                            self._rig_error.emit(str(exc))
                        except Exception as exc:
                            logger.error("RigNet: unexpected error in send thread: %s", exc)
                            self._rig_error.emit(str(exc))
                        finally:
                            self._rig_busy_lock.release()

                    threading.Thread(target=_rig_send, daemon=True).start()
                else:
                    logger.debug("RigNet: previous cycle still running, skipping tick")

            # Transmit Doppler-corrected frequencies to Rig 2 (same non-blocking pattern).
            if self._rig2_controller is not None and self._rig2_controller.is_connected:
                if self._rig2_busy_lock.acquire(blocking=False):
                    rig2 = self._rig2_controller
                    # DL for Rig 2: add tune offset when SDR is Rig 2
                    dl2 = (dl_corr + tune) if (dl_corr is not None and sdr_is_rig2) else dl_corr
                    # UL for Rig 2: mirror tune offset when SDR is Rig 1 and Lock is ON
                    ul2 = ul_corr
                    if sdr_is_rig1 and self._trsp_lock and tune != 0.0 and ul2 is not None:
                        ul2 = ul2 + (-tune if invert else tune)

                    def _rig2_send() -> None:
                        try:
                            rig2.set_vfo_frequencies(dl2, ul2)
                        except RigControlError as exc:
                            self._rig_error.emit(str(exc))
                        except Exception as exc:
                            logger.error("Rig2: unexpected error in send thread: %s", exc)
                            self._rig_error.emit(str(exc))
                        finally:
                            self._rig2_busy_lock.release()

                    threading.Thread(target=_rig2_send, daemon=True).start()
                else:
                    logger.debug("Rig2: previous cycle still running, skipping tick")

        # Send AZ/EL to the rotator every tick (same non-blocking pattern as rig).
        if (
            obs is not None
            and self._rotator_controller is not None
            and self._rotator_controller.is_connected
        ):
            if self._rot_busy_lock.acquire(blocking=False):
                rot = self._rotator_controller
                az = self._apply_south_offset(obs.azimuth_deg)
                el = obs.elevation_deg

                def _rot_send() -> None:
                    try:
                        rot.set_position(az, el)
                        logger.info("Rotator: set position az=%.1f el=%.1f", az, el)
                        pos = rot.get_position()
                        self._rot_pos_updated.emit(pos.azimuth_deg, pos.elevation_deg)
                    except Exception as exc:
                        logger.error("Rotator: set_position error: %s", exc)
                    finally:
                        self._rot_busy_lock.release()

                threading.Thread(target=_rot_send, daemon=True).start()
            else:
                logger.debug("Rotator: previous cycle still running, skipping tick")
        elif self._rotator_controller is None or not self._rotator_controller.is_connected:
            self._radar_view.set_rotator_position(None, None)

        self._radio_control.refresh_status()
        self._update_rig_label()
        self._update_rot_label()

    def _on_rig_error(self, msg: str) -> None:
        """Display an error from the background rig thread in the status bar (UI thread)."""
        logger.warning("RigControlError: %s", msg)
        sb = self.statusBar()
        if sb:
            sb.showMessage(f"RIG: {msg}", 3000)

    def _on_rotator_pos_updated(self, rot_az: float, rot_el: float) -> None:
        """Update the radar rotator marker with the actual rotator position (UI thread)."""
        display_az = (rot_az - 180.0) % 360.0 if self._rotator_south_init else rot_az
        self._radar_view.set_rotator_position(display_az, rot_el)

    def _update_statusbar(self) -> None:
        """Update the QTH text and TLE last-updated timestamp in the status bar."""
        if self._location_manager is not None:
            self._qth_label.setText(self._location_manager.status_text)

        row = self._conn.execute("SELECT MAX(fetched_at) AS last_fetch FROM tle_data").fetchone()
        if row and row["last_fetch"]:
            self._tle_label.setText(f"TLE: {str(row['last_fetch'])[:16]}")

    # ------------------------------------------------------------------ #
    # Satellite selection callbacks
    # ------------------------------------------------------------------ #

    def _on_tab_changed(self, index: int) -> None:
        """Hide the Satellite Detail panel when Dashboard tab is active (more space for map)."""
        is_dashboard = index == self._dashboard_tab_idx
        self._detail_panel.setVisible(not is_dashboard)

    def _on_filter_changed(self, text: str) -> None:
        """Redraw the satellite list when the filter combo changes."""
        self._amsat_link.setVisible(text == "Operational (AMSAT)")
        self._apply_filter()

    def _on_search_changed(self, _text: str) -> None:
        """Re-filter the satellite list when the search box text changes."""
        self._apply_filter()

    def _on_sat_context_menu(self, pos: QPoint) -> None:
        """Show the right-click context menu for the satellite list."""
        item = self._sat_list.itemAt(pos)
        if item is None:
            return
        norad = int(item.data(Qt.ItemDataRole.UserRole))

        row_data = self._conn.execute(
            "SELECT name, is_favorite, is_hidden, favorite_group FROM satellites"
            " WHERE norad_cat_id = ?",
            (norad,),
        ).fetchone()
        if row_data is None:
            return

        name = str(row_data["name"])
        is_hidden = bool(row_data["is_hidden"])
        current_group: int = int(row_data["favorite_group"] or 0)

        # Load custom groups for submenu
        groups = self._conn.execute(
            "SELECT id, name FROM custom_groups ORDER BY sort_order, id"
        ).fetchall()

        menu = QMenu(self)
        fav_menu = menu.addMenu("★ Favorite Groups")
        fav_actions: dict[int, QAction] = {}
        for grp in groups:
            grp_id = int(grp["id"])
            grp_name = str(grp["name"])
            act = fav_menu.addAction(f"★ {grp_name}")
            act.setCheckable(True)
            act.setChecked(current_group == grp_id)
            fav_actions[grp_id] = act
        if current_group > 0:
            fav_menu.addSeparator()
            remove_fav_action: QAction | None = fav_menu.addAction("Remove from Favorites")
        else:
            remove_fav_action = None

        hide_label = _("Unhide Satellite") if is_hidden else _("Hide Satellite")
        hide_action = menu.addAction(hide_label)
        info_action = menu.addAction("Satellite Info...")
        satnogs_action = menu.addAction("Open in SatNOGS")

        action = menu.exec(self._sat_list.mapToGlobal(pos))
        if action is not None and action in fav_actions.values():
            chosen_id = next(k for k, v in fav_actions.items() if v == action)
            new_group = 0 if current_group == chosen_id else chosen_id
            self._set_favorite_group(norad, new_group)
        elif action is not None and action == remove_fav_action:
            self._set_favorite_group(norad, 0)
        elif action == hide_action:
            self._set_hidden(norad, not is_hidden)
        elif action == info_action:
            self._show_sat_info(norad, name)
        elif action == satnogs_action:
            self._open_in_satnogs(norad, name)

    def _open_in_satnogs(self, norad: int, name: str) -> None:
        """Open the SatNOGS satellite page. Uses DB cache; fetches UUID in background if needed."""
        row = self._conn.execute(
            "SELECT satnogs_uuid FROM satellites WHERE norad_cat_id = ?",
            (norad,),
        ).fetchone()
        cached = row["satnogs_uuid"] if row else None
        if cached:
            self._open_url_app_mode(f"https://db.satnogs.org/satellite/{cached}")
            return
        threading.Thread(
            target=self._fetch_satnogs_uuid_bg,
            args=(norad, name),
            daemon=True,
        ).start()

    def _fetch_satnogs_uuid_bg(self, norad: int, name: str) -> None:
        """Background thread: fetch SatNOGS UUID by NORAD, fall back to name search."""
        _SATNOGS_SAT_API = "https://db.satnogs.org/api/satellites/"
        sat_id: str | None = None
        try:
            with httpx.Client(timeout=10.0) as client:
                # Primary: look up by NORAD ID
                r = client.get(_SATNOGS_SAT_API, params={"format": "json", "norad_cat_id": norad})
                r.raise_for_status()
                data = r.json()
                results = data.get("results", data) if isinstance(data, dict) else data
                if results:
                    sat_id = str(results[0]["sat_id"])

                # Fallback: search by satellite name
                if not sat_id:
                    r2 = client.get(_SATNOGS_SAT_API, params={"format": "json", "search": name})
                    r2.raise_for_status()
                    data2 = r2.json()
                    results2 = data2.get("results", data2) if isinstance(data2, dict) else data2
                    if results2:
                        sat_id = str(results2[0]["sat_id"])
        except Exception:
            logger.exception("SatNOGS UUID fetch failed for NORAD %s / name %r", norad, name)

        if sat_id:
            with contextlib.suppress(Exception):
                self._conn.execute(
                    "UPDATE satellites SET satnogs_uuid = ? WHERE norad_cat_id = ?",
                    (sat_id, norad),
                )
                self._conn.commit()
            self._satnogs_open_url.emit(f"https://db.satnogs.org/satellite/{sat_id}")
        else:
            self._satnogs_not_found.emit()

    def _toggle_favorite(self, norad: int, favorite: bool) -> None:
        """Save the favorite state to the DB and reload the satellite list (legacy)."""
        self._conn.execute(
            "UPDATE satellites SET is_favorite = ? WHERE norad_cat_id = ?",
            (1 if favorite else 0, norad),
        )
        self._conn.commit()
        self._load_satellites()

    def _set_favorite_group(self, norad: int, group_id: int) -> None:
        """Assign a satellite to a custom favorite group (0 = remove from all groups)."""
        self._conn.execute(
            "UPDATE satellites SET favorite_group = ?, is_favorite = ? WHERE norad_cat_id = ?",
            (group_id, 1 if group_id > 0 else 0, norad),
        )
        self._conn.commit()
        self._load_satellites()

    def _rebuild_filter_combo(self) -> None:
        """Rebuild the filter combo from DB custom_groups + fixed entries.

        Preserves the current selection when possible.
        """
        prev = self._filter_combo.currentText() if self._filter_combo.count() > 0 else ""
        self._filter_combo.blockSignals(True)
        self._filter_combo.clear()

        groups = self._conn.execute(
            "SELECT name FROM custom_groups ORDER BY sort_order, id"
        ).fetchall()

        items = ["All Satellites"]
        for grp in groups:
            items.append(f"★ {grp['name']}")
        items += [
            "Amateur",
            "CubeSat",
            "Weather",
            "Earth Observation",
            "Science",
            "Space Stations",
            "Operational (AMSAT)",
            "Hidden",
        ]
        self._filter_combo.addItems(items)

        idx = self._filter_combo.findText(prev)
        if idx >= 0:
            self._filter_combo.setCurrentIndex(idx)

        self._filter_combo.blockSignals(False)

    def _set_hidden(self, norad: int, hidden: bool) -> None:
        """Save the satellite hidden state to the DB and reload the satellite list."""
        self._conn.execute(
            "UPDATE satellites SET is_hidden = ? WHERE norad_cat_id = ?",
            (1 if hidden else 0, norad),
        )
        self._conn.commit()
        self._load_satellites()

    def _on_hide_satellite(self) -> None:
        """Satellite > Hide Satellite handler."""
        current = self._sat_list.currentItem()
        if current is None:
            QMessageBox.warning(self, _("Hide Satellite"), _("No satellite selected."))
            return
        norad = int(current.data(Qt.ItemDataRole.UserRole))
        name = current.text().lstrip("★ ").strip()
        answer = QMessageBox.question(
            self,
            _("Hide Satellite"),
            _("Hide {name} (NORAD {n}) from the satellite list?").format(name=name, n=norad),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._set_hidden(norad, True)

    def _show_sat_info(self, norad: int, name: str) -> None:
        """Display a satellite info dialog (NORAD number, TLE epoch, quality)."""
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
        """Callback invoked when the satellite list selection changes."""
        if row < 0:
            self._selected_norad = None
            self._current_transmitter = None
            self._detail_panel.clear()
            self._radio_control.clear_satellite()
            self._world_map.clear_footprint()
            return
        item = self._sat_list.item(row)
        if item is None:
            return
        norad = int(item.data(Qt.ItemDataRole.UserRole))
        self._selected_norad = norad
        name = item.text()
        self._detail_panel.set_satellite(norad, name)
        self._radio_control.set_satellite(norad, name)
        self._dashboard_view.set_satellite(norad, name)
        self._refresh_passes()
        self._refresh_radio_control(norad)

    def _refresh_radio_control(self, norad: int) -> None:
        """Fetch the transmitter list for the selected satellite and update the Radio Control panel.

        Priority ORDER BY:
          1. Transponder with bidirectional links below 1 GHz
          2. Transceiver below 1 GHz
          3. Any entry below 1 GHz
          4. downlink_low ASC (lower frequency first)
        """
        rows = self._conn.execute(
            """
            SELECT uuid, description, type,
                   downlink_low, uplink_low, mode, ctcss_tone, invert
            FROM transmitters
            WHERE norad_cat_id = ? AND alive = 1
            ORDER BY
                (CASE WHEN type='Transponder' AND uplink_low IS NOT NULL
                           AND downlink_low < 1000000000 THEN 1 ELSE 0 END) DESC,
                (CASE WHEN type='Transceiver'
                           AND downlink_low < 1000000000 THEN 1 ELSE 0 END) DESC,
                (CASE WHEN downlink_low < 1000000000 THEN 1 ELSE 0 END) DESC,
                downlink_low ASC
            """,
            (norad,),
        ).fetchall()
        transmitters = [dict(r) for r in rows]
        # set_transmitters emits transmitter_changed, which causes _on_transmitter_changed
        # to update _current_transmitter
        self._radio_control.set_transmitters(transmitters)

    def _on_transmitter_changed(self, xpdr: Any) -> None:
        """Update _current_transmitter and refresh the display on transponder selection change."""
        self._current_transmitter = xpdr if isinstance(xpdr, dict) else None
        self._current_ctcss_tone = None  # revert to transponder tone on selection change
        self._dashboard_view.set_transmitter(self._current_transmitter)
        if self._current_transmitter:
            dl = self._current_transmitter.get("downlink_low")
            ul = self._current_transmitter.get("uplink_low")
            mode = self._current_transmitter.get("mode")
            satnogs_tone = self._current_transmitter.get("ctcss_tone")
            db_info = get_ctcss(self._selected_norad) if self._selected_norad else None
            # SatNOGS ctcss_tone takes priority; DB tone is the fallback.
            tone_hz: float | None = (
                float(satnogs_tone)
                if satnogs_tone
                else (db_info["tone_hz"] if db_info and db_info.get("tone_hz") else None)
            )
            activation_hz: float | None = (
                db_info["activation_hz"] if db_info and db_info.get("activation_hz") else None
            )
            self._ctcss_tone_hz = tone_hz
            self._ctcss_activation_hz = activation_hz
            self._radio_control.update_ctcss(tone_hz, activation_hz)
            self._radio_control.update_doppler(dl, dl, None, ul, ul, None, mode, tone_hz)
        else:
            self._ctcss_tone_hz = None
            self._ctcss_activation_hz = None
            self._radio_control.update_ctcss(None, None)
            self._radio_control.update_doppler(None, None, None, None, None, None)
        self._send_mode_only_to_rig()
        self._send_ctcss_cat_to_rig()
        # Auto-select SDR demod mode from transponder; reset passband tune offset
        if self._current_transmitter:
            satnogs_mode = self._current_transmitter.get("mode") or ""
            self._sdr_control.set_transponder_mode(satnogs_mode)
        self._sdr_tune_offset = 0.0
        self._sdr_control.reset_tune_offset()

    def _disconnect_rig(self) -> None:
        """Disconnect the rig and refresh the UI status."""
        if self._rig_controller is not None:
            is_sdr = getattr(self._rig_controller, "is_sdr", False)
            self._rig_controller.disconnect()
            if is_sdr:
                self._sdr_control.set_pipeline(None)
        self._radio_control.refresh_status()
        self._update_rig_label()

    def _send_mode_only_to_rig(self) -> None:
        """Set mode on both VFOs via an independent connection on transponder change.

        Computes dl_mode / ul_mode from the current transponder, applying
        _MODE_INVERT when invert=True (e.g. RS-44 USB↔LSB).

        FT-991: keeps the main connection alive; send_mode_only() opens an
        independent socket for the mode commands, so no disconnect is needed.
        send_mode_only() is run in a background thread to avoid blocking the UI.

        FTX-1F / generic: disconnects first (on the UI thread, so status is
        updated immediately) so the Doppler cycle's F/I commands cannot race
        with the V commands inside send_mode_only().  send_mode_only() itself
        runs in a background thread.  The user must reconnect manually.
        """
        if self._rig_controller is None or self._current_transmitter is None:
            return
        mode = str(self._current_transmitter.get("mode") or "")
        if not mode:
            return
        invert = bool(self._current_transmitter.get("invert", False))
        dl_mode = mode
        ul_mode = _MODE_INVERT.get(mode, mode) if invert else mode
        rig = self._rig_controller
        if rig.is_connected and self._ctcss_method != "ft991":
            self._disconnect_rig()  # UI update must happen on the UI thread
        logger.info(
            "CTCSS: tone=%s method=%s cat_on=%r",
            self._current_transmitter.get("ctcss_tone") if self._current_transmitter else None,
            self._ctcss_method,
            self._ctcss_cat_on,
        )

        def _do_send() -> None:
            rig.send_mode_only(dl_mode, ul_mode)

        threading.Thread(target=_do_send, daemon=True).start()

    # Methods that use custom CAT commands for CTCSS (not handled by Hamlib itself).
    _CAT_CTCSS_METHODS: frozenset[str] = frozenset({"custom_cat", "ftx1", "ft991"})

    def _send_ctcss_cat_to_rig(self, tone_hz: float | None = None) -> None:
        """Send custom CAT CTCSS command for methods that bypass Hamlib CTCSS.

        Handles "custom_cat", "ftx1", and "ft991" methods.
        Runs in a background thread so the UI is not blocked.

        Args:
            tone_hz: Tone to send in Hz.  When None (automatic mode — called on
                     transponder change), reads ctcss_tone from _current_transmitter.
                     When explicitly provided (button press), the caller's value
                     takes precedence so the Activate button can force 74.4 Hz
                     regardless of what the transmitter record says.
        """
        if self._ctcss_method not in self._CAT_CTCSS_METHODS:
            return
        if self._rig_controller is None:
            return
        if tone_hz is None:
            tone_hz = float(self._ctcss_tone_hz or 0.0)
        rig = self._rig_controller
        cat_on = self._ctcss_cat_on
        cat_off = self._ctcss_cat_off

        def _send() -> None:
            try:
                rig.send_ctcss_cat(tone_hz, cat_on, cat_off)
            except Exception as exc:
                self._rig_error.emit(f"send_ctcss_cat: {exc}")

        threading.Thread(target=_send, daemon=True).start()

    def _refresh_passes(self) -> None:
        """Fetch pass predictions for the selected satellite and update the pass list and chart."""
        if self._selected_norad is None or self._pass_predictor is None:
            self._pass_list.clear()
            return
        now = datetime.now(UTC)
        passes = self._pass_predictor.get_passes(
            self._selected_norad,
            now,
            now + timedelta(hours=24),
        )
        # get_passes(start=now) misses a pass already in progress because its AOS
        # is in the past and Skyfield emits no retroactive AOS event.  Prepend the
        # ongoing pass when the satellite is currently above the horizon.
        current_pass = self._pass_predictor.get_current_pass(self._selected_norad, now)
        if current_pass is not None:
            passes = [current_pass, *passes]
        self._current_passes = passes
        self._pass_list.set_passes(passes)

        item = self._sat_list.currentItem()
        name = item.text() if item else ""
        self._pass_chart.set_passes(passes, sat_name=name)

    def _on_chart_range_changed(self, hours: float) -> None:
        """Immediately call PassPredictor when the pass chart time range changes."""
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

    def _on_target_search_requested(self, start: Any, end: Any) -> None:
        """Called when the Search button on the Target tab is pressed."""
        if self._selected_norad is None or self._pass_predictor is None:
            return
        start_dt: datetime = start
        end_dt: datetime = end
        passes = self._pass_predictor.get_passes(self._selected_norad, start_dt, end_dt)
        self._current_passes = passes
        self._pass_list.set_passes(passes)
        item = self._sat_list.currentItem()
        name = item.text() if item else ""
        self._pass_chart.set_passes(passes, sat_name=name)

    def _on_highlight_satellite(self, norad: int) -> None:
        """Highlight the satellite in the left list when its row is clicked on the Group tab."""
        for i in range(self._sat_list.count()):
            item = self._sat_list.item(i)
            if item is not None and int(item.data(Qt.ItemDataRole.UserRole)) == norad:
                self._sat_list.setCurrentRow(i)
                break

    def _on_group_results_ready(self, results: object) -> None:
        """Populate the Group Pass Chart tab and make it visible on first group search."""
        self._group_pass_chart.set_results(results)  # type: ignore[arg-type]
        self._tab_widget.setTabVisible(self._group_chart_tab_idx, True)
        # Cache for AOS/LOS notification checks
        self._group_pass_results = results  # type: ignore[assignment]
        # Pass search is now done — autotrack may proceed
        self._autotrack.mark_searches_ready()

    # ------------------------------------------------------------------ #
    # Menu handlers
    # ------------------------------------------------------------------ #

    def _on_set_qth(self) -> None:
        """File > Set QTH... handler."""
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
            self._pass_list.set_pass_predictor(self._pass_predictor)
            self._update_statusbar()

    def _on_settings(self) -> None:
        from ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(self._conn, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._on_settings_accepted()

    def _apply_world_map(self) -> None:
        """Apply the world map image selected in Settings to the WorldMapView.

        On first launch (no explicit selection), automatically download the
        NASA Topographic 1024px map so users see a nice map out of the box.
        """
        from ui.settings_dialog import SettingsDialog, _maps_dir

        # Auto-download the default map on first launch if not yet present.
        row = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = 'world_map_file'"
        ).fetchone()
        if not (row and row["value"]):
            default_path = _maps_dir() / "nasa-topo_1024.jpg"
            if not default_path.exists():
                import threading

                def _download() -> None:
                    try:
                        import httpx

                        url = (
                            "https://raw.githubusercontent.com/csete/gpredict/"
                            "master/pixmaps/maps/nasa-topo_1024.jpg"
                        )
                        with httpx.Client(follow_redirects=True, timeout=30) as client:
                            resp = client.get(url)
                            if resp.status_code == 200:
                                default_path.write_bytes(resp.content)
                                # Emit signal so the Qt UI thread re-applies the map.
                                self._map_downloaded.emit()
                    except Exception:
                        pass

                threading.Thread(target=_download, daemon=True).start()

        path = SettingsDialog.get_world_map_path(self._conn)
        self._world_map.set_map_image(path)
        self._dashboard_view.set_map_image(path)

    def _apply_time_zone(self) -> None:
        """Load the saved time zone preference and apply it to all time-display widgets."""
        row = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = 'time_zone_mode'"
        ).fetchone()
        use_utc = (row["value"] if row and row["value"] else "utc") != "local"

        # Sync menu checkmarks
        if hasattr(self, "_tz_utc_action"):
            self._tz_utc_action.setChecked(use_utc)
            self._tz_local_action.setChecked(not use_utc)

        self._pass_list.set_use_utc(use_utc)
        self._pass_chart.set_use_utc(use_utc)
        self._group_pass_chart.set_use_utc(use_utc)
        self._radar_view.set_use_utc(use_utc)
        self._dashboard_view._radar.set_use_utc(use_utc)

    def _on_time_zone_changed(self, use_utc: bool) -> None:
        """Persist the time zone preference and propagate to all display widgets."""
        value = "utc" if use_utc else "local"
        self._conn.execute(
            """
            INSERT OR REPLACE INTO app_settings (key, value, updated_at)
            VALUES ('time_zone_mode', ?, CURRENT_TIMESTAMP)
            """,
            (value,),
        )
        self._conn.commit()
        self._pass_list.set_use_utc(use_utc)
        self._pass_chart.set_use_utc(use_utc)
        self._group_pass_chart.set_use_utc(use_utc)
        self._radar_view.set_use_utc(use_utc)
        self._dashboard_view._radar.set_use_utc(use_utc)

    def _open_url_app_mode(self, url: str) -> None:
        """Open *url* in Chrome/Chromium app mode (no browser chrome/tabs).

        Searches for Chrome-family browsers in order of preference and launches
        with the ``--app=URL`` flag.  Falls back to ``QDesktopServices.openUrl``
        when no compatible browser is found.

        Args:
            url: the URL to open (may be passed as a raw string from QLabel.linkActivated)
        """
        _LINUX_CANDIDATES = [
            "google-chrome",
            "google-chrome-stable",
            "chromium-browser",
            "chromium",
            "brave-browser",
            "microsoft-edge",
        ]
        _MAC_CANDIDATES = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
        _WIN_CANDIDATES = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            str(
                __import__("pathlib").Path.home()
                / "AppData"
                / "Local"
                / "Google"
                / "Chrome"
                / "Application"
                / "chrome.exe"
            ),
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]

        browser_exe: str | None = None

        if sys.platform.startswith("linux"):
            for candidate in _LINUX_CANDIDATES:
                found = shutil.which(candidate)
                if found:
                    browser_exe = found
                    break
        elif sys.platform == "darwin":
            for candidate in _MAC_CANDIDATES:
                if shutil.which(candidate) or __import__("os.path").path.isfile(candidate):
                    browser_exe = candidate
                    break
        elif sys.platform == "win32":
            import os

            for candidate in _WIN_CANDIDATES:
                if os.path.isfile(candidate):
                    browser_exe = candidate
                    break

        if browser_exe:
            try:
                subprocess.Popen([browser_exe, f"--app={url}"])
                return
            except OSError:
                pass

        # Fallback: open with the system default browser
        QDesktopServices.openUrl(QUrl(url))

    def _on_settings_accepted(self) -> None:
        """After Settings OK, sync the enabled TLE sources and redraw the satellite list."""
        from ui.settings_dialog import SettingsDialog

        # Rebuild filter combo so any group additions/renames/removals are reflected
        self._rebuild_filter_combo()

        # Reload notification settings (warn_minutes / los_enabled etc. may have changed)
        self._notifier.reload_settings()

        # Reload autotrack lists (user may have added/removed lists in Settings)
        self._reload_autotrack_lists()

        self._apply_world_map()

        enabled = SettingsDialog.get_enabled_sources(self._conn)

        def _fetch_all() -> None:
            for source_name in enabled:
                print(f"[TLE] Fetching {source_name}...")
                try:
                    result = asyncio.run(self._tle_manager.fetch_and_update(source_name))
                    print(f"[TLE] Result: {result}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[TLE] Error fetching {source_name}: {exc}")
            # Signal emit is thread-safe; Qt automatically queues it to the main thread.
            self._satellite_list_refresh.emit()

        threading.Thread(target=_fetch_all, daemon=True).start()

    def _on_add_transmitter(self) -> None:
        """Satellite > Add Transmitter... handler."""
        from ui.transmitter_dialog import TransmitterDialog

        norad = self._selected_norad
        dialog = TransmitterDialog(self._transmitter_manager, norad_cat_id=norad, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            QMessageBox.information(
                self,
                _("Add Transmitter"),
                _("Transmitter added successfully."),
            )

    def _on_edit_transmitter(self) -> None:
        """Satellite > Edit Transmitter... handler."""
        from ui.transmitter_dialog import TransmitterDialog

        current = self._sat_list.currentItem()
        if current is None:
            QMessageBox.warning(self, _("Edit Transmitter"), _("No satellite selected."))
            return
        norad = int(current.data(Qt.ItemDataRole.UserRole))

        rows = self._conn.execute(
            "SELECT * FROM transmitters WHERE norad_cat_id = ? AND alive = 1 ORDER BY description",
            (norad,),
        ).fetchall()
        if not rows:
            QMessageBox.information(
                self, _("Edit Transmitter"), _("No transmitters found for this satellite.")
            )
            return

        items = [
            f"{dict(r)['description']}  [{(dict(r).get('downlink_low') or 0) / 1e6:.3f} MHz]"
            for r in rows
        ]
        from PySide6.QtWidgets import QInputDialog

        item, ok = QInputDialog.getItem(
            self, _("Edit Transmitter"), _("Select transmitter to edit:"), items, 0, False
        )
        if not ok:
            return

        idx = items.index(item)
        existing = dict(rows[idx])
        dialog = TransmitterDialog(self._transmitter_manager, existing=existing, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and self._selected_norad is not None:
            self._refresh_radio_control(self._selected_norad)

    def _on_delete_transmitter(self) -> None:
        """Satellite > Delete Transmitter... handler."""
        current = self._sat_list.currentItem()
        if current is None:
            QMessageBox.warning(self, _("Delete Transmitter"), _("No satellite selected."))
            return
        norad = int(current.data(Qt.ItemDataRole.UserRole))

        rows = self._conn.execute(
            "SELECT * FROM transmitters WHERE norad_cat_id = ? ORDER BY description",
            (norad,),
        ).fetchall()
        if not rows:
            QMessageBox.information(
                self, _("Delete Transmitter"), _("No transmitters found for this satellite.")
            )
            return

        items = [
            f"{dict(r)['description']}  [{(dict(r).get('downlink_low') or 0) / 1e6:.3f} MHz]"
            for r in rows
        ]
        from PySide6.QtWidgets import QInputDialog

        item, ok = QInputDialog.getItem(
            self, _("Delete Transmitter"), _("Select transmitter to delete:"), items, 0, False
        )
        if not ok:
            return

        idx = items.index(item)
        rec = dict(rows[idx])
        answer = QMessageBox.question(
            self,
            _("Delete Transmitter"),
            _("Delete transmitter '{desc}'?").format(desc=rec["description"]),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._transmitter_manager.delete_transmitter(rec["uuid"])
        if self._selected_norad is not None:
            self._refresh_radio_control(self._selected_norad)

    def _on_add_manual_tle(self) -> None:
        """Satellite > Add Manual TLE... handler."""
        from ui.manual_tle_dialog import ManualTLEDialog

        dialog = ManualTLEDialog(self._tle_manager, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.added_norad is not None:
            self._load_satellites()
            QMessageBox.information(
                self,
                _("Add Manual TLE"),
                _("Satellite TLE added successfully (NORAD {n}).").format(n=dialog.added_norad),
            )

    def _on_update_tle(self) -> None:
        QMessageBox.information(
            self, _("Update TLE"), _("TLE update has been queued in the background.")
        )

    def _on_sync_satnogs(self) -> None:
        """Data > Sync Frequencies from SATNOGS handler.

        Runs sync_from_satnogs() in a background thread and displays
        the result count in the status bar on completion.
        """
        threading.Thread(target=self._refresh_satnogs_sync, daemon=True).start()
        sb = self.statusBar()
        if sb:
            sb.showMessage(_("Syncing transmitter frequencies from SATNOGS..."), 5000)

    def _refresh_active_tle_sync(self) -> None:
        """Fetch CelesTrak GROUP=active TLEs and fill gaps for SATNOGS satellites."""
        try:
            result = asyncio.run(self._tle_manager.fetch_active_tles())
            logger.info("CelesTrak active TLE fetch completed: %s", result)
            self._satellite_list_refresh.emit()
        except Exception as exc:
            logger.warning("CelesTrak active TLE fetch failed: %s", exc)

    def _refresh_provisional_tle_sync(self) -> None:
        """Fetch TLEs for provisional (NORAD >= 90000) satellites from a background thread."""
        try:
            result = asyncio.run(self._tle_manager.fetch_provisional_tles())
            logger.info("Provisional TLE refresh completed: %s", result)
            self._satellite_list_refresh.emit()
        except Exception as exc:
            logger.warning("Provisional TLE refresh failed: %s", exc)

    def _refresh_satnogs_sync(self) -> None:
        """Sync SATNOGS transponders from a background thread."""
        try:
            result = asyncio.run(self._transmitter_manager.sync_from_satnogs())
            msg = _("SATNOGS sync: {ins} inserted, {upd} updated, {skp} skipped").format(
                ins=result["inserted"],
                upd=result["updated"],
                skp=result["skipped"],
            )
            logger.info("SATNOGS sync completed: %s", result)
        except Exception as exc:  # noqa: BLE001
            msg = _("SATNOGS sync failed: {err}").format(err=exc)
            logger.warning("SATNOGS sync failed: %s", exc)
        self._satnogs_status.emit(msg)

    def _on_satnogs_status(self, msg: str) -> None:
        """Show the SATNOGS sync thread status in the status bar (called on UI thread)."""
        sb = self.statusBar()
        if sb:
            sb.showMessage(msg, 8000)

    def _on_rig_settings(self) -> None:
        from ui.rig_dialog import RigSettingsDialog

        dialog = RigSettingsDialog(self._conn, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._load_rig_settings()

    def _load_rig_settings(self) -> None:
        """Load Rig 1 and Rig 2 settings from the DB and instantiate controllers.

        Key priority for Rig 1:
          1. 'rig1_settings' (new key written by the tabbed dialog)
          2. 'rig_settings'  (legacy key — backward compatibility)
        Rig 2 is loaded only when its 'enabled' flag is True.
        If an SDR device is assigned to a slot in 'sdr_settings', that slot
        gets a SdrRigAdapter instead of a Hamlib controller.
        """
        # Load SDR settings once so both rig slots can check assigned_rig
        sdr_cfg: dict[str, Any] = {}
        try:
            sdr_row = self._conn.execute(
                "SELECT value FROM app_settings WHERE key = 'sdr_settings'"
            ).fetchone()
            if sdr_row and sdr_row["value"]:
                sdr_cfg = json.loads(sdr_row["value"])
        except Exception as exc:
            logger.warning("Failed to load SDR settings: %s", exc)

        # ---------- Rig 1 ----------
        try:
            # If SDR is assigned to slot 1, build an SdrRigAdapter
            if sdr_cfg.get("assigned_rig") == 1 and sdr_cfg.get("enabled", False):
                self._rig_controller = self._build_sdr_rig_adapter(sdr_cfg)
                logger.info("Rig1: SDR assigned — %s", sdr_cfg.get("device_label", ""))
                self._radio_control.set_rig(self._rig_controller)
            else:
                row = self._conn.execute(
                    "SELECT value FROM app_settings WHERE key = 'rig1_settings'"
                ).fetchone()
                if row is None:
                    # Fallback: legacy key written by the old single-rig dialog
                    row = self._conn.execute(
                        "SELECT value FROM app_settings WHERE key = 'rig_settings'"
                    ).fetchone()
                if row is not None:
                    settings: dict[str, Any] = json.loads(row["value"])
                    self._rig_controller = self._build_rig_controller(settings)
                    self._ctcss_method = str(settings.get("ctcss_method", "hamlib"))
                    # For preset methods, always use the current authoritative template from
                    # CTCSS_PRESET_TEMPLATES rather than the DB value, which may be stale.
                    if self._ctcss_method in CTCSS_PRESET_TEMPLATES:
                        self._ctcss_cat_on, self._ctcss_cat_off = CTCSS_PRESET_TEMPLATES[
                            self._ctcss_method
                        ]
                    else:
                        self._ctcss_cat_on = str(settings.get("ctcss_cat_on", ""))
                        self._ctcss_cat_off = str(settings.get("ctcss_cat_off", ""))
                    logger.info(
                        "Rig1Settings: method=%s cat_on=%r",
                        self._ctcss_method,
                        self._ctcss_cat_on,
                    )
                    self._radio_control.set_rig(self._rig_controller)
        except Exception as exc:
            logger.warning("Failed to load Rig 1 settings: %s", exc)

        # ---------- Rig 2 ----------
        try:
            # If SDR is assigned to slot 2, build an SdrRigAdapter
            if sdr_cfg.get("assigned_rig") == 2 and sdr_cfg.get("enabled", False):
                self._rig2_controller = self._build_sdr_rig_adapter(sdr_cfg)
                logger.info("Rig2: SDR assigned — %s", sdr_cfg.get("device_label", ""))
                self._radio_control.set_rig2(self._rig2_controller)
            else:
                row2 = self._conn.execute(
                    "SELECT value FROM app_settings WHERE key = 'rig2_settings'"
                ).fetchone()
                if row2 is not None:
                    s2: dict[str, Any] = json.loads(row2["value"])
                    if s2.get("enabled", False):
                        self._rig2_controller = self._build_rig_controller(s2)
                        logger.info("Rig2Settings: loaded, radio_type=%s", s2.get("radio_type"))
                    else:
                        self._rig2_controller = None
                    self._radio_control.set_rig2(self._rig2_controller)
        except Exception as exc:
            logger.warning("Failed to load Rig 2 settings: %s", exc)

        self._update_rig_label()

    def _build_rig_controller(self, settings: dict[str, Any]) -> RigController:
        """Instantiate a RigController from a settings dictionary.

        Args:
            settings: dict with keys 'mode', 'host', 'net_port', 'model_id', 'port',
                      'baud_rate', 'radio_type', 'direct_cat_port', 'direct_cat_baud',
                      'ctcss_method'.

        Returns:
            Configured (but not yet connected) RigController instance.
        """
        mode = settings.get("mode", "net")
        radio_type = str(settings.get("radio_type", "full_duplex"))
        if mode == "net":
            return HamlibNetController(
                host=str(settings.get("host", "localhost")),
                port=int(settings.get("net_port", 4532)),
                radio_type=radio_type,
                direct_cat_port=str(settings.get("direct_cat_port", "")),
                direct_cat_baud=int(settings.get("direct_cat_baud", 38400)),
                ctcss_method=str(settings.get("ctcss_method", "hamlib")),
            )
        return HamlibDirectController(
            model_id=int(settings.get("model_id", 1)),
            port=str(settings.get("port", "/dev/ttyUSB0")),
            baud_rate=int(settings.get("baud_rate", 9600)),
        )

    def _build_sdr_rig_adapter(self, sdr_cfg: dict[str, Any]) -> RigController:
        """Build a SdrRigAdapter from the sdr_settings dict.

        The adapter is returned unconfigured (connect() has not been called).
        device_info and audio settings are stored so that connect() opens the
        correct SoapySDR device with the right sample rate / gain.

        Args:
            sdr_cfg: dict as written by _SdrPanel.collect() in rig_dialog.py.

        Returns:
            A SdrRigAdapter with device_info set.
        """
        from rig.controller import SdrRigAdapter
        from sdr.device import SdrDeviceInfo

        adapter = SdrRigAdapter()

        device_args: dict[str, str] = {}
        raw = sdr_cfg.get("device_args")
        if isinstance(raw, dict):
            device_args = {str(k): str(v) for k, v in raw.items()}

        # SdrDeviceInfo stores identity (args / label); audio params are set
        # on the SdrDevice after open() via adapter.set_audio_params().
        info = SdrDeviceInfo(
            driver=device_args.get("driver"),
            label=str(sdr_cfg.get("device_label", "SDR")),
            serial=device_args.get("serial", ""),
            hardware=device_args.get("hardware", ""),
            args=device_args,
        )
        adapter.set_device_info(info)

        # Store audio params so the pipeline can apply them after open()
        adapter.set_audio_params(
            sample_rate_hz=float(sdr_cfg.get("sample_rate_hz") or 2_400_000),
            ppm=float(sdr_cfg.get("ppm") or 0),
            gain_auto=bool(sdr_cfg.get("gain_auto", True)),
            gain_db=float(sdr_cfg.get("gain_db") or 40.0),
            bias_tee=bool(sdr_cfg.get("bias_tee", False)),
        )
        return adapter

    def _on_lock_changed(self, locked: bool) -> None:
        """Update the _trsp_lock flag when the L button is toggled."""
        self._trsp_lock = locked

    @Slot(float)
    def _on_sdr_tune_offset(self, offset_hz: float) -> None:
        """Store the passband tune offset emitted by SdrControlWidget."""
        self._sdr_tune_offset = offset_hz

    def _on_ctcss_activate(self) -> None:
        """Send the satellite's activation tone (tone_hz from CTCSS_DB)."""
        if self._ctcss_activation_hz is not None:
            self._on_ctcss_send(self._ctcss_activation_hz)

    def _on_ctcss_send(self, tone_hz: float) -> None:
        """Send a CTCSS tone to the rig (background thread); errors shown in status bar."""
        self._current_ctcss_tone = tone_hz  # persist label until next transponder change
        # Custom CAT methods bypass Hamlib CTCSS; route through send_ctcss_cat().
        # Pass tone_hz explicitly so Activate (74.4 Hz) overrides the transmitter tone.
        if self._ctcss_method in self._CAT_CTCSS_METHODS:
            self._send_ctcss_cat_to_rig(tone_hz=tone_hz)
            return
        if self._rig_controller is None or not self._rig_controller.is_connected:
            return
        rig = self._rig_controller

        def _send() -> None:
            try:
                ok = rig.set_ctcss_tone(tone_hz)
                if not ok:
                    self._rig_error.emit(f"set_ctcss_tone({tone_hz} Hz): rig returned failure")
            except Exception as exc:
                self._rig_error.emit(f"set_ctcss_tone: {exc}")

        threading.Thread(target=_send, daemon=True).start()

    def _on_tune_requested(self) -> None:
        """T button pressed: reset to the centre frequency of the current transponder band."""
        if self._current_transmitter is None:
            return
        dl_low = self._current_transmitter.get("downlink_low")
        dl_high = self._current_transmitter.get("downlink_high")
        ul_low = self._current_transmitter.get("uplink_low")
        ul_high = self._current_transmitter.get("uplink_high")

        if dl_low is not None and dl_high is not None:
            self._tune_dl_override = (float(dl_low) + float(dl_high)) / 2
        elif dl_low is not None:
            self._tune_dl_override = float(dl_low)

        if ul_low is not None and ul_high is not None:
            self._tune_ul_override = (float(ul_low) + float(ul_high)) / 2
        elif ul_low is not None:
            self._tune_ul_override = float(ul_low)

    def _load_cycle_setting(self) -> None:
        """Load rig_cycle_ms from the DB and apply it to the timer and UI."""
        try:
            row = self._conn.execute(
                "SELECT value FROM app_settings WHERE key = 'rig_cycle_ms'"
            ).fetchone()
            if row is not None:
                ms = int(row["value"])
                ms = max(10, min(10000, ms))
                self._timer.setInterval(ms)
                self._radio_control.set_cycle(ms)
        except Exception as exc:
            logger.warning("Failed to load cycle setting: %s", exc)

    def _on_cycle_changed(self, ms: int) -> None:
        """Update the timer interval and save to DB when the Cycle spinbox changes."""
        ms = max(10, min(10000, ms))
        self._timer.setInterval(ms)
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('rig_cycle_ms', ?)",
                (str(ms),),
            )
            self._conn.commit()
        except Exception as exc:
            logger.warning("Failed to save cycle setting: %s", exc)

    def _update_rig_label(self) -> None:
        """Update the RIG status label in the status bar.

        Shows "RIG: Off" when neither rig is configured or connected.
        Shows "RIG: 1" / "RIG: 1+2" to indicate which rigs are active.
        """
        r1 = self._rig_controller is not None and self._rig_controller.is_connected
        r2 = self._rig2_controller is not None and self._rig2_controller.is_connected
        if r1 and r2:
            self._rig_label.setText(_("RIG: 1+2"))
        elif r1:
            self._rig_label.setText(_("RIG: 1"))
        elif r2:
            self._rig_label.setText(_("RIG: 2"))
        else:
            self._rig_label.setText(_("RIG: Off"))

    def _on_rotator_settings(self) -> None:
        from ui.rotator_dialog import RotatorSettingsDialog

        dialog = RotatorSettingsDialog(self._conn, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._load_rotator_settings()

    def _load_rotator_settings(self) -> None:
        """Load rotator settings from the DB and instantiate the controller."""
        try:
            row = self._conn.execute(
                "SELECT value FROM app_settings WHERE key = 'rotator_settings'"
            ).fetchone()
            if row is None:
                return
            settings: dict[str, Any] = json.loads(row["value"])
            mode = settings.get("mode", "net")
            if mode == "net":
                host = str(settings.get("host", "localhost"))
                port = int(settings.get("net_port", 4533))
                self._rotator_controller = HamlibRotatorController(
                    net_mode=True,
                    net_host=host,
                    net_port=port,
                )
            else:
                model_id = int(settings.get("model_id", 1))
                serial_port = str(settings.get("port", "/dev/ttyUSB0"))
                baud = int(settings.get("baud_rate", 9600))
                self._rotator_controller = HamlibRotatorController(
                    model_id=model_id,
                    port=serial_port,
                    baud_rate=baud,
                )
            self._radio_control.set_rotator(self._rotator_controller)
            self._update_rot_label()
        except Exception as exc:
            logger.warning("Failed to load rotator settings: %s", exc)
        try:
            south_row = self._conn.execute(
                "SELECT value FROM app_settings WHERE key = 'rotator_south_init'"
            ).fetchone()
            checked = bool(int(south_row["value"])) if south_row else False
            self._rotator_south_init = checked
            self._radio_control.set_south_init(checked)
        except Exception as exc:
            logger.warning("Failed to load rotator_south_init: %s", exc)

    def _on_rotator_connected(self) -> None:
        """Send the current satellite position to the rotator immediately after connect."""
        if self._rotator_controller is None or not self._rotator_controller.is_connected:
            return

        # Fetch actual rotator position and show on radar; fall back to (0, 0)
        # if get_position() returns the default RotatorState.
        rot_ctrl = self._rotator_controller

        def _fetch_init_pos() -> None:
            pos = rot_ctrl.get_position()
            self._rot_pos_updated.emit(pos.azimuth_deg, pos.elevation_deg)

        threading.Thread(target=_fetch_init_pos, daemon=True).start()

        if self._selected_norad is None or self._engine is None:
            return
        obs = self._engine.observe(self._selected_norad)
        if obs is None:
            return
        rot = self._rotator_controller
        az = self._apply_south_offset(obs.azimuth_deg)
        el = obs.elevation_deg
        threading.Thread(target=lambda: rot.set_position(az, el), daemon=True).start()
        self._update_rot_label()

    def _apply_south_offset(self, az: float) -> float:
        """Apply 180-degree offset when the rotator is south-initialized."""
        if not self._rotator_south_init:
            return az
        return (az + 180) % 360

    def _on_south_init_changed(self, checked: bool) -> None:
        """Update state and persist south_init setting when the checkbox is toggled."""
        self._rotator_south_init = checked
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('rotator_south_init', ?)",
                ("1" if checked else "0",),
            )
            self._conn.commit()
        except Exception as exc:
            logger.warning("Failed to save rotator_south_init: %s", exc)

    def _update_rot_label(self) -> None:
        """Update the ROT label in the status bar."""
        if self._rotator_controller is None:
            self._rot_label.setText(_("ROT: Off"))
        elif self._rotator_controller.is_connected:
            self._rot_label.setText(_("ROT: On"))
        else:
            self._rot_label.setText(_("ROT: Off"))

    def _on_set_language(self, lang: str) -> None:
        from i18n import set_language

        set_language(lang)
        QMessageBox.information(
            self,
            _("Language"),
            _("Please restart the application to apply the language change."),
        )

    def _on_satellite_color(self) -> None:
        """Show the satellite list color legend dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle(_("Satellite Color Legend"))
        dialog.setMinimumWidth(480)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(0)

        # Build the legend using an HTML table rendered in a QLabel
        rows_html = [
            (
                "#2ecc71",
                "bold",
                _("Green (bold)"),
                _("AMSAT status: Operational — confirmed working by AMSAT."),
            ),
            (
                "#f1c40f",
                "normal",
                _("Yellow"),
                _("AMSAT status: Partially operational — degraded but active."),
            ),
            (
                "#e74c3c",
                "normal",
                _("Red"),
                _("AMSAT status: Non-operational — confirmed failed by AMSAT."),
            ),
            (
                "#e67e22",
                "normal",
                _("Orange"),
                _("SATNOGS alive — reception reported, no AMSAT data available."),
            ),
            (
                "#9b59b6",
                "italic",
                _("Purple (italic)"),
                _(
                    "TLE pending — alive satellite awaiting TLE assignment "
                    "(provisional NORAD ≥ 90000, within 30-day grace period). "
                    "Position cannot be displayed yet."
                ),
            ),
            (
                "#7f8c8d",
                "normal",
                _("Gray"),
                _("Status unknown — not confirmed operational by any source."),
            ),
        ]

        table_rows = ""
        for color, style, label, desc in rows_html:
            weight = "bold" if style == "bold" else "normal"
            fstyle = "italic" if style == "italic" else "normal"
            swatch = (
                f'<span style="display:inline-block; width:14px; height:14px;'
                f" background:{color}; border:1px solid #555;"
                f' vertical-align:middle; margin-right:6px;"></span>'
            )
            label_html = (
                f'<span style="color:{color}; font-weight:{weight};'
                f' font-style:{fstyle};">{label}</span>'
            )
            table_rows += (
                f"<tr>"
                f"<td style='padding:6px 8px; white-space:nowrap;'>"
                f"{swatch}{label_html}</td>"
                f"<td style='padding:6px 8px; color:#111;'>{desc}</td>"
                f"</tr>"
            )

        html = (
            "<html><body style='color:#111;'>"
            "<table cellspacing='0' cellpadding='0' style='border-collapse:collapse;'>"
            + table_rows
            + "</table></body></html>"
        )

        legend_label = QLabel(html)
        legend_label.setWordWrap(True)
        legend_label.setTextFormat(Qt.TextFormat.RichText)
        legend_label.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(legend_label)

        from PySide6.QtWidgets import QDialogButtonBox  # noqa: PLC0415

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        dialog.exec()

    def _on_about(self) -> None:
        from PySide6.QtWidgets import QApplication

        ver = QApplication.applicationVersion() or "0.1.0"
        QMessageBox.information(
            self,
            _("About GPredict-Improved"),
            f"GPredict-Improved  v{ver}\n\n"
            + _("Modern satellite tracking software for amateur radio operators.\n")
            + "https://github.com/JF9SOM/gpredict-improved",
        )

    def _on_github(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl("https://github.com/JF9SOM/gpredict-improved"))

    def _on_sdr_install(self) -> None:
        from ui.sdr_install_dialog import SdrInstallDialog

        dlg = SdrInstallDialog(self)
        dlg.exec()

    def _on_check_updates(self) -> None:
        from PySide6.QtWidgets import QApplication

        from ui.app_update_dialog import AppUpdateDialog

        dlg = AppUpdateDialog(self)
        dlg.quit_requested.connect(QApplication.quit)
        dlg.exec()

    def _on_hamlib_update(self) -> None:
        from ui.hamlib_update_dialog import HamlibUpdateDialog

        dlg = HamlibUpdateDialog(self)
        dlg.exec()

    def _on_rig_slot_connected(self, slot: int) -> None:
        """Called when Rig 1 or Rig 2 connects.  Starts SDR pipeline if the slot is an SDR."""
        from rig.controller import SdrRigAdapter
        from sdr import SOAPY_AVAILABLE

        if not SOAPY_AVAILABLE:
            return

        rig = self._rig_controller if slot == 1 else self._rig2_controller
        if not isinstance(rig, SdrRigAdapter):
            return

        device = rig.sdr_device
        if device is None:
            return

        # Load IQ save dir from settings
        try:
            row = self._conn.execute(
                "SELECT value FROM app_settings WHERE key='sdr_settings'"
            ).fetchone()
            import json as _json

            sdr_cfg = _json.loads(row["value"]) if row and row["value"] else {}
        except Exception:
            sdr_cfg = {}

        iq_dir = sdr_cfg.get("iq_save_dir", "")
        self._sdr_control.set_iq_save_dir(str(iq_dir))

        from sdr.pipeline import SDRPipeline

        pipeline = SDRPipeline(device, parent=self)
        rig.attach_pipeline(pipeline)
        self._sdr_control.set_pipeline(pipeline)
        pipeline.start()
        self._update_rig_label()

    def _on_rig_slot_disconnected(self, slot: int) -> None:
        """Called when Rig 1 or Rig 2 disconnects via the UI button."""
        rig = self._rig_controller if slot == 1 else self._rig2_controller
        if getattr(rig, "is_sdr", False):
            self._sdr_control.set_pipeline(None)
        self._update_rig_label()

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
    # Window lifecycle
    # ------------------------------------------------------------------ #

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop the timer, web server, and scheduler when the window is closed."""
        with contextlib.suppress(Exception):
            self._conn.execute(
                "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('satellite_filter', ?)",
                (self._filter_combo.currentText(),),
            )
            self._conn.commit()
        # Signal background threads to exit before tearing down other resources.
        self._shutdown_flag.set()
        self._timer.stop()
        if self._web_server is not None:
            with contextlib.suppress(Exception):
                self._web_server.stop()
        if self._scheduler is not None:
            with contextlib.suppress(Exception):
                self._scheduler.shutdown(wait=False)
        event.accept()
