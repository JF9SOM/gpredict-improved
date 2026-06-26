"""Communications > Q65 tab — EME weak-signal digital mode receiver.

Phase 1: RX only.  Decodes Q65 signals from SDR audio or rig soundcard.
TX / QSO state machine will be added in Phase 2.

Requires libq65 (built from WSJT-X source).  Without it the tab shows
an installation banner.  See Help > Q65 (libq65) Installation…

Tab is non-resident: opened via Communications > Q65, closed with ×.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import UTC, datetime

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from comms.q65.codec import (
    Q65_PERIODS,
    Q65_SUBMODE,
    SAMPLE_RATE,
    Q65Codec,
    Q65Message,
    is_available,
    lib_version,
)
from comms.q65.scheduler import Q65Scheduler
from i18n import _

# ---------------------------------------------------------------------------
# Table column indices
# ---------------------------------------------------------------------------
_COL_UTC = 0
_COL_DB = 1
_COL_DT = 2
_COL_FREQ = 3
_COL_MSG = 4
_COL_COUNT = 5

_Q65_SETTINGS_KEY = "q65_settings"

# Available mode presets shown in the combo box
_MODE_PRESETS: list[str] = [
    "Q65-60A",  # 144 MHz EME standard
    "Q65-60B",
    "Q65-30B",  # 50/70 MHz EME
    "Q65-30C",
    "Q65-15C",
    "Q65-15D",
    "Q65-15E",
]


class Q65Tab(QWidget):
    """Non-resident Q65 RX tab.

    Args:
        conn: SQLite DB connection (shared).
        radio_control: RadioControlWidget for SDR audio access.
        parent: Parent widget.
    """

    _decoded_signal: Signal = Signal(str, list)

    def __init__(
        self,
        conn: sqlite3.Connection,
        radio_control: object = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._conn = conn
        self._radio_control = radio_control

        self._scheduler = Q65Scheduler(period_seconds=60)
        self._codec: Q65Codec | None = None
        self._audio_buffer: list[NDArray[np.float32]] = []
        self._buffer_lock = threading.Lock()
        self._capture_active = False
        self._sdr_connected = False

        self._build_ui()
        self._decoded_signal.connect(self._on_decoded)
        self._load_settings()
        self._connect_sdr_audio()

        # Timer: update countdown display every 500 ms
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(500)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start()

        # Timer: fire near period boundary to trigger decode
        self._decode_timer = QTimer(self)
        self._decode_timer.setInterval(200)
        self._decode_timer.timeout.connect(self._check_period_boundary)
        self._decode_timer.start()

        self._last_period_start: float = 0.0

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Installation banner (shown when libq65 is missing)
        if not is_available():
            banner = QLabel(
                _(
                    "⚠ libq65 is not installed — decoding is disabled.\n"
                    "See Help > Q65 (libq65) Installation… to install."
                )
            )
            banner.setStyleSheet("background:#8b0000;color:white;padding:6px;border-radius:4px;")
            banner.setWordWrap(True)
            root.addWidget(banner)
        else:
            ver = lib_version()
            info = QLabel(_("libq65 ready") + (f"  ({ver})" if ver else ""))
            info.setStyleSheet("color:green;font-weight:bold;")
            root.addWidget(info)

        # Config row
        cfg = QGroupBox(_("Configuration"))
        cfg_lay = QHBoxLayout(cfg)

        cfg_lay.addWidget(QLabel(_("My Call:")))
        self._call_edit = QLineEdit()
        self._call_edit.setMaximumWidth(100)
        self._call_edit.setPlaceholderText("JF9SOM")
        cfg_lay.addWidget(self._call_edit)

        cfg_lay.addWidget(QLabel(_("Grid:")))
        self._grid_edit = QLineEdit()
        self._grid_edit.setMaximumWidth(70)
        self._grid_edit.setPlaceholderText("PM86")
        cfg_lay.addWidget(self._grid_edit)

        cfg_lay.addWidget(QLabel(_("Mode:")))
        self._mode_combo = QComboBox()
        for m in _MODE_PRESETS:
            self._mode_combo.addItem(m)
        self._mode_combo.currentTextChanged.connect(self._on_mode_changed)
        cfg_lay.addWidget(self._mode_combo)

        cfg_lay.addWidget(QLabel(_("RX Input:")))
        self._input_combo = QComboBox()
        self._input_combo.addItem(_("SDR"))
        self._input_combo.addItem(_("Rig Soundcard"))
        cfg_lay.addWidget(self._input_combo)

        cfg_lay.addStretch()
        root.addWidget(cfg)

        # Period / status row
        status_row = QHBoxLayout()

        self._period_label = QLabel("Q65-60A")
        self._period_label.setStyleSheet("font-weight:bold;font-size:14px;")
        status_row.addWidget(self._period_label)

        self._countdown_label = QLabel("-- s / 60")
        self._countdown_label.setStyleSheet("font-size:14px;")
        status_row.addWidget(self._countdown_label)

        status_row.addStretch()

        self._rx_indicator = QLabel(_("● RX"))
        self._rx_indicator.setStyleSheet("color:green;font-weight:bold;")
        status_row.addWidget(self._rx_indicator)

        root.addLayout(status_row)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        # Decoded messages table
        root.addWidget(QLabel(_("Decoded Messages")))
        self._table = QTableWidget(0, _COL_COUNT)
        self._table.setHorizontalHeaderLabels([_("UTC"), _("dB"), _("DT"), _("Hz"), _("Message")])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(_COL_UTC, 80)
        self._table.setColumnWidth(_COL_DB, 50)
        self._table.setColumnWidth(_COL_DT, 50)
        self._table.setColumnWidth(_COL_FREQ, 60)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        mono = QFont("Courier New", 9)
        self._table.setFont(mono)
        root.addWidget(self._table, stretch=1)

        # Bottom row
        bot = QHBoxLayout()
        self._clear_btn = QPushButton(_("Clear"))
        self._clear_btn.clicked.connect(self._table.clearContents)
        self._clear_btn.clicked.connect(lambda: self._table.setRowCount(0))
        bot.addWidget(self._clear_btn)
        bot.addStretch()
        self._status_label = QLabel(_("Waiting for period boundary…"))
        bot.addWidget(self._status_label)
        root.addLayout(bot)

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------

    def _load_settings(self) -> None:
        try:
            import json

            from core.app_settings import AppSettings

            s = AppSettings()
            raw = s.get(_Q65_SETTINGS_KEY, "{}")
            d = json.loads(raw) if isinstance(raw, str) else {}
            if "callsign" in d:
                self._call_edit.setText(d["callsign"])
            if "grid" in d:
                self._grid_edit.setText(d["grid"])
            if "mode" in d and d["mode"] in _MODE_PRESETS:
                self._mode_combo.setCurrentText(d["mode"])
            if "rx_input" in d:
                idx = self._input_combo.findText(d["rx_input"])
                if idx >= 0:
                    self._input_combo.setCurrentIndex(idx)
        except Exception:
            pass

    def _save_settings(self) -> None:
        try:
            import json

            from core.app_settings import AppSettings

            s = AppSettings()
            d = {
                "callsign": self._call_edit.text().strip(),
                "grid": self._grid_edit.text().strip(),
                "mode": self._mode_combo.currentText(),
                "rx_input": self._input_combo.currentText(),
            }
            s.set(_Q65_SETTINGS_KEY, json.dumps(d))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Mode change
    # ------------------------------------------------------------------

    def _on_mode_changed(self, mode: str) -> None:
        period = Q65_PERIODS.get(mode, 60)
        self._scheduler = Q65Scheduler(period_seconds=period)
        self._period_label.setText(mode)
        self._last_period_start = 0.0
        with self._buffer_lock:
            self._audio_buffer.clear()

    # ------------------------------------------------------------------
    # SDR audio connection
    # ------------------------------------------------------------------

    def _connect_sdr_audio(self) -> None:
        """Hook into SDR pipeline audio_ready signal if available."""
        if self._radio_control is None:
            return
        try:
            sdr_ctrl = getattr(self._radio_control, "_sdr_control", None)
            if sdr_ctrl is None:
                return
            pipeline = getattr(sdr_ctrl, "_pipeline", None)
            if pipeline is None:
                return
            pipeline.audio_ready.connect(self._on_audio_chunk)
            self._sdr_connected = True
        except Exception:
            pass

    @Slot(object)
    def _on_audio_chunk(self, chunk: NDArray[np.float32]) -> None:
        """Receive audio from SDR pipeline and accumulate into buffer."""
        if self._input_combo.currentText() != _("SDR"):
            return
        with self._buffer_lock:
            self._audio_buffer.append(chunk.astype(np.float32))

    # ------------------------------------------------------------------
    # Period boundary / decode
    # ------------------------------------------------------------------

    @Slot()
    def _on_tick(self) -> None:
        """Update countdown display."""
        self._countdown_label.setText(self._scheduler.countdown_str())

    @Slot()
    def _check_period_boundary(self) -> None:
        """Fire decode when a new period has just started."""
        now = time.time()
        phase = self._scheduler.period_phase()
        period = self._scheduler.period_seconds

        # Within the first 0.4 s after a boundary
        if phase > 0.4:
            return
        boundary = now - phase
        if boundary <= self._last_period_start:
            return
        self._last_period_start = boundary

        # Collect buffered audio from the previous period
        with self._buffer_lock:
            chunks = list(self._audio_buffer)
            self._audio_buffer.clear()

        if not is_available():
            return
        if not chunks:
            self._status_label.setText(_("No audio received in last period"))
            return

        samples = np.concatenate(chunks)
        expected = period * SAMPLE_RATE
        # Zero-pad or trim to exact period length
        if len(samples) < expected:
            samples = np.concatenate([samples, np.zeros(expected - len(samples), dtype=np.float32)])
        else:
            samples = samples[:expected]

        # Run decode in background thread to avoid blocking UI
        t = threading.Thread(
            target=self._decode_thread,
            args=(samples, period, boundary),
            daemon=True,
        )
        t.start()

    def _decode_thread(self, samples: NDArray[np.float32], period: int, boundary: float) -> None:
        """Run Q65 decode in background and post results to UI thread via signal."""
        mode_str = self._mode_combo.currentText()
        submode = mode_str[-1] if mode_str and mode_str[-1] in Q65_SUBMODE else "A"
        codec = Q65Codec(submode=submode)
        messages = codec.decode(samples, period_seconds=period)
        utc_str = datetime.fromtimestamp(boundary, UTC).strftime("%H:%M")
        # Use signal to safely cross thread boundary
        self._decoded_signal.emit(utc_str, messages)

    @Slot(str, list)
    def _on_decoded(self, utc_str: str, messages: list[Q65Message]) -> None:
        if not messages:
            self._status_label.setText(_("Period {utc}: no decodes").format(utc=utc_str))
            return

        self._status_label.setText(
            _("Period {utc}: {n} decode(s)").format(utc=utc_str, n=len(messages))
        )
        for msg in messages:
            self._append_row(utc_str, msg)

    def _append_row(self, utc_str: str, msg: Q65Message) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)

        def _item(text: str, align: int = 0) -> QTableWidgetItem:
            it = QTableWidgetItem(text)
            if align:
                it.setTextAlignment(align)
            return it

        from PySide6.QtCore import Qt

        self._table.setItem(row, _COL_UTC, _item(utc_str))
        self._table.setItem(
            row,
            _COL_DB,
            _item(
                f"{msg.snr_db:+.0f}", Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            ),
        )
        self._table.setItem(
            row,
            _COL_DT,
            _item(
                f"{msg.dt_sec:+.1f}", Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            ),
        )
        self._table.setItem(
            row,
            _COL_FREQ,
            _item(
                str(int(msg.freq_hz)), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            ),
        )
        self._table.setItem(row, _COL_MSG, _item(msg.text))

        # Highlight CQ messages
        if "CQ" in msg.text:
            for col in range(_COL_COUNT):
                item = self._table.item(row, col)
                if item:
                    item.setBackground(QColor("#1a3a1a"))

        self._table.scrollToBottom()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        self._tick_timer.stop()
        self._decode_timer.stop()
        self._save_settings()
        super().closeEvent(event)
