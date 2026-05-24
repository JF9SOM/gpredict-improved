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
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

import httpx
from PySide6.QtCore import QPoint, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QDesktopServices,
    QFont,
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

from core.engine import DopplerCalculator, Observation, PassPredictor, SatelliteEngine
from core.location import LocationManager
from data.amsat_status import AMSATStatusFetcher
from data.ctcss_db import get_ctcss
from data.tle_manager import TLEManager
from data.transmitter_manager import TransmitterManager
from i18n import _
from rig.controller import (
    CTCSS_PRESET_TEMPLATES,
    HamlibDirectController,
    HamlibNetController,
    RigControlError,
    RigController,
)
from ui.pass_chart import PassChartView
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


# Oscar designator prefixes (e.g. AO-7, FO-29, IO-86, QO-100, RS-44)
_OSCAR_RE = re.compile(
    r"\b(?:AO|BO|CO|DO|EO|FO|GO|HO|IO|JO|KO|LO|MO|NO|PO|QO|RS|SO|TO|UO|VO|XO|ZO)"
    r"-\d+[A-Z]?\b",
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
            conn:             SQLite connection
            tle_manager:      TLE manager
            engine:           satellite engine (no position updates if None)
            pass_predictor:   pass predictor (no pass prediction if None)
            location_manager: location manager (QTH shown as unset if None)
            fastapi_app:      FastAPI app (web server not started if None)
            web_port:         web server port number
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
        self._current_transmitter: dict[str, Any] | None = None
        self._web_server: Any | None = None
        self._web_server_url: str = ""
        self._scheduler: Any | None = None
        self._amsat_fetcher = AMSATStatusFetcher(conn)
        self._transmitter_manager = TransmitterManager(conn)
        self._rig_controller: RigController | None = None
        self._ctcss_method: str = "hamlib"
        self._ctcss_cat_on: str = ""
        self._ctcss_cat_off: str = ""
        # Lock indicating whether the rig control thread is currently running.
        # If acquire(blocking=False) fails, the previous cycle is still executing.
        self._rig_busy_lock = threading.Lock()
        # Cache for forced frequency transmission when the Tune button resets to centre frequency.
        # None -> use the Doppler-corrected value as-is.
        # A value -> transmit it once then reset to None.
        self._tune_dl_override: float | None = None
        self._tune_ul_override: float | None = None
        # L button: when True, uplink is slaved to downlink.
        self._trsp_lock: bool = False
        # Override for CTCSS label: set when a button is pressed, reset on transponder change.
        # None -> show the transmitter's ctcss_tone; float -> persist the last-sent tone.
        self._current_ctcss_tone: float | None = None
        # Resolved CTCSS tone for the current transmitter (SatNOGS or CTCSS_DB fallback).
        self._ctcss_tone_hz: float | None = None
        # Activation tone for the current satellite (from CTCSS_DB; None if not applicable).
        self._ctcss_activation_hz: float | None = None

        self.setWindowTitle("GPredict-Improved")
        self.resize(1280, 800)

        self._build_ui()
        self._build_menu()
        self._build_statusbar()
        # Connect PassPanel signals
        self._pass_list.target_search_requested.connect(self._on_target_search_requested)
        self._pass_list.highlight_satellite.connect(self._on_highlight_satellite)
        self._pass_list.set_pass_predictor(self._pass_predictor)
        # Connect signal that receives satellite list refresh requests from background threads
        self._satellite_list_refresh.connect(self._load_satellites)
        self._rig_error.connect(self._on_rig_error)
        self._satnogs_status.connect(self._on_satnogs_status)
        self._satnogs_open_url.connect(self._open_satnogs_url)
        self._satnogs_not_found.connect(
            lambda: QMessageBox.information(self, "SatNOGS", "SatNOGS page not found")
        )
        self._radio_control.transmitter_changed.connect(self._on_transmitter_changed)
        self._radio_control.cycle_changed.connect(self._on_cycle_changed)
        self._radio_control.tune_requested.connect(self._on_tune_requested)
        self._radio_control.lock_changed.connect(self._on_lock_changed)
        self._radio_control.ctcss_send_requested.connect(self._on_ctcss_send)
        self._radio_control.ctcss_activate_requested.connect(self._on_ctcss_activate)
        self._restore_satellite_filter()
        self._load_satellites()
        self._load_rig_settings()

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
        self._filter_combo.addItems(
            [
                "All Satellites",
                "★ Favorites",
                "Amateur",
                "CubeSat",
                "Weather",
                "Earth Observation",
                "Science",
                "Space Stations",
                "Operational (AMSAT)",
                "Hidden",
            ]
        )
        self._filter_combo.currentTextChanged.connect(self._on_filter_changed)
        left_layout.addWidget(self._filter_combo)

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

        # Centre: tabs (World Map / Radar / Pass Chart / Radio Control)
        self._tab_widget = QTabWidget()
        self._world_map = WorldMapView()
        self._radar_view = RadarView()
        self._pass_chart = PassChartView()
        self._radio_control = RadioControlWidget()
        self._pass_chart.range_changed.connect(self._on_chart_range_changed)
        self._tab_widget.addTab(self._world_map, _("World Map"))
        self._tab_widget.addTab(self._radar_view, _("Radar"))
        self._tab_widget.addTab(self._pass_chart, _("Pass Chart"))
        self._tab_widget.addTab(self._radio_control, _("Radio Control"))
        h_splitter.addWidget(self._tab_widget)

        # Right: satellite detail panel
        self._detail_panel = SatDetailPanel()
        self._detail_panel.setMinimumWidth(160)
        self._detail_panel.setMaximumWidth(260)
        h_splitter.addWidget(self._detail_panel)

        h_splitter.setStretchFactor(0, 0)
        h_splitter.setStretchFactor(1, 1)
        h_splitter.setStretchFactor(2, 0)

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
        """Build the status bar."""
        sb = self.statusBar()

        self._qth_label = QLabel("QTH: Not set")
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
            # Category filter
            if filter_text == "★ Favorites" and not d["is_favorite"]:
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

            prefix = "★ " if d["is_favorite"] else ""
            # Append Oscar designator (e.g. "(IO-86)") when not already in the name
            oscar_suffix = ""
            try:
                alt_list: list[str] = json.loads(d["alt_names"])
            except (json.JSONDecodeError, ValueError):
                alt_list = []
            name_upper = d["name"].upper()
            for alt in alt_list:
                m = _OSCAR_RE.search(alt)
                if m and m.group(0).upper() not in name_upper:
                    oscar_suffix = f" ({m.group(0).upper()})"
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
                item.setForeground(QColor("#7f8c8d"))
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

        # Sync World Map with current filter:
        # "All Satellites" with no search -> show all satellites (None)
        # otherwise -> show only the filtered NORAD set
        if filter_text == "All Satellites" and not search_query:
            self._world_map.set_visible_norads(None)
        else:
            self._world_map.set_visible_norads({n for n, _ in filtered_sats})

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
            self._scheduler.start()
            logger.debug("APScheduler started")
        except Exception as exc:
            logger.warning("APScheduler start failed: %s", exc)
            self._scheduler = None

        # On startup, refresh AMSAT status in the background if stale
        if self._amsat_fetcher.is_stale():
            threading.Thread(target=self._refresh_amsat_sync, daemon=True).start()

        # On startup, auto-sync if there are no transmitters yet
        count = self._conn.execute("SELECT COUNT(*) FROM transmitters").fetchone()[0]
        if count == 0:
            threading.Thread(target=self._refresh_satnogs_sync, daemon=True).start()

        # Always sync satellite names and status from SATNOGS in the background on startup
        threading.Thread(target=self._refresh_satellite_names_sync, daemon=True).start()

    def _refresh_tle_sync(self) -> None:
        """Update all enabled TLE sources from a background thread (APScheduler job)."""
        from ui.settings_dialog import SettingsDialog

        enabled = SettingsDialog.get_enabled_sources(self._conn)
        for source_name in enabled:
            try:
                asyncio.run(self._tle_manager.fetch_and_update(source_name))
                logger.info("TLE refresh completed: %s", source_name)
            except Exception as exc:
                logger.warning("TLE refresh failed: %s — %s", source_name, exc)

    def _refresh_amsat_sync(self) -> None:
        """Update AMSAT operational status from a background thread."""
        try:
            asyncio.run(self._amsat_fetcher.fetch_and_update())
            logger.info("AMSAT status refresh completed")
            self._satellite_list_refresh.emit()
        except Exception as exc:
            logger.warning("AMSAT status refresh failed: %s", exc)

    def _refresh_satellite_names_sync(self) -> None:
        """Sync satellite names and status from SATNOGS in a background thread."""
        try:
            result = asyncio.run(self._transmitter_manager.sync_satellite_names())
            logger.info("SATNOGS satellite names sync completed: %s", result)
            self._satellite_list_refresh.emit()
        except Exception as exc:
            logger.warning("SATNOGS satellite names sync failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Timer callback (every 1 second)
    # ------------------------------------------------------------------ #

    def _on_tick(self) -> None:
        """Timer callback that updates satellite positions and the status bar."""
        self._update_world_map()
        self._update_selected_satellite()
        self._update_statusbar()

    def _update_world_map(self) -> None:
        """Fetch all satellite subpoints and observer position, then update the world map."""
        # Update the observer location star marker (regardless of whether the engine is set)
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
            # Transmit Doppler-corrected frequencies to the connected rig (regardless of elevation).
            # set_vfo_frequencies() involves TCP communication with recv(), so calling it on the
            # UI thread directly would block and freeze the display.
            # Use _rig_busy_lock: if the previous cycle has finished, transmit on a background
            # thread; if the previous cycle is still running, skip this tick.
            if self._rig_controller is not None and self._rig_controller.is_connected:
                if self._rig_busy_lock.acquire(blocking=False):
                    rig = self._rig_controller
                    dl = dl_corr
                    ul = ul_corr

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
        self._radio_control.refresh_status()
        self._update_rig_label()

    def _on_rig_error(self, msg: str) -> None:
        """Display an error from the background rig thread in the status bar (UI thread)."""
        logger.warning("RigControlError: %s", msg)
        sb = self.statusBar()
        if sb:
            sb.showMessage(f"RIG: {msg}", 3000)

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

    def _on_filter_changed(self, _: str) -> None:
        """Redraw the satellite list when the filter combo changes."""
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
            "SELECT name, is_favorite, is_hidden FROM satellites WHERE norad_cat_id = ?",
            (norad,),
        ).fetchone()
        if row_data is None:
            return

        name = str(row_data["name"])
        is_fav = bool(row_data["is_favorite"])
        is_hidden = bool(row_data["is_hidden"])

        menu = QMenu(self)
        fav_label = "★ Remove from Favorites" if is_fav else "★ Add to Favorites"
        fav_action = menu.addAction(fav_label)
        hide_label = _("Unhide Satellite") if is_hidden else _("Hide Satellite")
        hide_action = menu.addAction(hide_label)
        info_action = menu.addAction("Satellite Info...")
        satnogs_action = menu.addAction("Open in SatNOGS")

        action = menu.exec(self._sat_list.mapToGlobal(pos))
        if action == fav_action:
            self._toggle_favorite(norad, not is_fav)
        elif action == hide_action:
            self._set_hidden(norad, not is_hidden)
        elif action == info_action:
            self._show_sat_info(norad, name)
        elif action == satnogs_action:
            self._open_in_satnogs(norad, name)

    def _open_satnogs_url(self, url: str) -> None:
        """Open a URL in Chrome/Chromium app mode, or fall back to the default browser."""
        for browser in ["google-chrome", "chromium-browser", "chromium"]:
            if shutil.which(browser):
                subprocess.Popen([browser, f"--app={url}"])
                return
        QDesktopServices.openUrl(QUrl(url))

    def _open_in_satnogs(self, norad: int, name: str) -> None:
        """Open the SatNOGS satellite page. Uses DB cache; fetches UUID in background if needed."""
        row = self._conn.execute(
            "SELECT satnogs_uuid FROM satellites WHERE norad_cat_id = ?",
            (norad,),
        ).fetchone()
        cached = row["satnogs_uuid"] if row else None
        if cached:
            self._open_satnogs_url(f"https://db.satnogs.org/satellite/{cached}")
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
        """Save the favorite state to the DB and reload the satellite list."""
        self._conn.execute(
            "UPDATE satellites SET is_favorite = ? WHERE norad_cat_id = ?",
            (1 if favorite else 0, norad),
        )
        self._conn.commit()
        self._load_satellites()

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
        self._detail_panel.set_satellite(norad, item.text())
        self._radio_control.set_satellite(norad, item.text())
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

    def _disconnect_rig(self) -> None:
        """Disconnect the rig and refresh the UI status."""
        if self._rig_controller is not None:
            self._rig_controller.disconnect()
        self._radio_control.refresh_status()

    def _send_mode_only_to_rig(self) -> None:
        """Set mode on both VFOs via an independent connection on transponder change.

        Computes dl_mode / ul_mode from the current transponder, applying
        _MODE_INVERT when invert=True (e.g. RS-44 USB↔LSB).

        When connected, disconnects first so the Doppler cycle's F/I commands
        cannot race with the V commands inside send_mode_only(), then calls
        send_mode_only() immediately.  The user must reconnect manually.
        When not connected, calls send_mode_only() directly.
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
        if rig.is_connected:
            self._disconnect_rig()
        rig.send_mode_only(dl_mode, ul_mode)
        logger.info(
            "CTCSS: tone=%s method=%s cat_on=%r",
            self._current_transmitter.get("ctcss_tone") if self._current_transmitter else None,
            self._ctcss_method,
            self._ctcss_cat_on,
        )

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

    def _on_settings_accepted(self) -> None:
        """After Settings OK, sync the enabled TLE sources and redraw the satellite list."""
        from ui.settings_dialog import SettingsDialog

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
        """Load rig settings from the DB and instantiate the controller."""
        try:
            row = self._conn.execute(
                "SELECT value FROM app_settings WHERE key = 'rig_settings'"
            ).fetchone()
            if row is None:
                return
            settings: dict[str, Any] = json.loads(row["value"])
            mode = settings.get("mode", "net")
            radio_type = str(settings.get("radio_type", "full_duplex"))
            if mode == "net":
                host = str(settings.get("host", "localhost"))
                port = int(settings.get("net_port", 4532))
                self._rig_controller = HamlibNetController(
                    host=host, port=port, radio_type=radio_type
                )
            else:
                model_id = int(settings.get("model_id", 1))
                serial_port = str(settings.get("port", "/dev/ttyUSB0"))
                baud = int(settings.get("baud_rate", 9600))
                self._rig_controller = HamlibDirectController(
                    model_id=model_id, port=serial_port, baud_rate=baud
                )
            self._ctcss_method = str(settings.get("ctcss_method", "hamlib"))
            # For preset methods, always use the current authoritative template from
            # CTCSS_PRESET_TEMPLATES rather than the DB value, which may be stale
            # (e.g. saved before a preset command format correction).
            if self._ctcss_method in CTCSS_PRESET_TEMPLATES:
                self._ctcss_cat_on, self._ctcss_cat_off = CTCSS_PRESET_TEMPLATES[self._ctcss_method]
            else:
                self._ctcss_cat_on = str(settings.get("ctcss_cat_on", ""))
                self._ctcss_cat_off = str(settings.get("ctcss_cat_off", ""))
            logger.info("RigSettings: method=%s cat_on=%r", self._ctcss_method, self._ctcss_cat_on)
            self._radio_control.set_rig(self._rig_controller)
            self._update_rig_label()
        except Exception as exc:
            logger.warning("Failed to load rig settings: %s", exc)

    def _on_lock_changed(self, locked: bool) -> None:
        """Update the _trsp_lock flag when the L button is toggled."""
        self._trsp_lock = locked

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
        """Update the RIG label in the status bar."""
        if self._rig_controller is None:
            self._rig_label.setText(_("RIG: 未接続"))
        elif self._rig_controller.is_connected:
            self._rig_label.setText(_("RIG: 接続中"))
        else:
            self._rig_label.setText(_("RIG: 切断"))

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
        self._timer.stop()
        if self._web_server is not None:
            with contextlib.suppress(Exception):
                self._web_server.stop()
        if self._scheduler is not None:
            with contextlib.suppress(Exception):
                self._scheduler.shutdown(wait=False)
        event.accept()
