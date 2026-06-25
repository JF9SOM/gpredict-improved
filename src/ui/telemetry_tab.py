"""Telemetry tab widget — Communications > Telemetry.

Receives AX.25 frames from:
  - Direwolf / KISS (via RigConnect + Sound Card)
  - [future] Bell 202 AFSK Python demodulator (SDR receive path)

Decodes frames using JSON format definitions in
src/data/telemetry_formats/{norad}.json.
Satellites without a definition show raw hex.

All received frames are persisted to the ``telemetry_log`` SQLite table
and can be exported as CSV.
"""

from __future__ import annotations

import csv
import datetime
import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from comms.aprs.afsk_demod import AfskDemodulator
from comms.aprs.direwolf import DirewolfManager, find_direwolf
from comms.aprs.parser import decode_ax25
from comms.telemetry.decoder import TelemetryFrame, decode_telemetry, list_formats
from i18n import _


class TelemetryTab(QWidget):
    """Non-resident tab opened from Communications > Telemetry."""

    def __init__(
        self,
        conn: Any,
        radio_control: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._conn = conn
        self._radio_control = radio_control
        self._mgr: DirewolfManager | None = None
        self._demod: AfskDemodulator | None = None
        self._sdr_pipeline: object | None = None
        self._rig_connected = False
        self._sdr_connected = False
        self._frame_count = 0

        self._ensure_db_table()
        self._setup_ui()
        self._connect_signals()
        self._refresh_status()

    # ------------------------------------------------------------------ #
    # DB
    # ------------------------------------------------------------------ #

    def _ensure_db_table(self) -> None:
        if not hasattr(self._conn, "execute"):
            return
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at   DATETIME NOT NULL,
                norad_cat_id  INTEGER,
                callsign      TEXT NOT NULL,
                raw_hex       TEXT NOT NULL,
                parsed_json   TEXT,
                signal_db     REAL
            )
        """)
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)

        # --- Status bar ---
        status_row = QHBoxLayout()
        self._lbl_status = QLabel(_("Input: —"))
        self._lbl_status.setStyleSheet("color: #aaa;")
        self._lbl_count = QLabel(_("Frames: 0 received"))
        status_row.addWidget(self._lbl_status)
        status_row.addStretch()
        status_row.addWidget(self._lbl_count)
        root.addLayout(status_row)

        # --- Supported satellites ---
        sat_box = QGroupBox(_("Supported Satellites (format definitions)"))
        sat_layout = QHBoxLayout(sat_box)
        self._lbl_formats = QLabel()
        self._lbl_formats.setWordWrap(True)
        sat_layout.addWidget(self._lbl_formats)
        root.addWidget(sat_box)
        self._populate_format_list()

        # --- Receive log ---
        log_box = QGroupBox(_("Received Frames"))
        log_layout = QVBoxLayout(log_box)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            [_("Time (UTC)"), _("Callsign"), _("Satellite"), _("Data")]
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        log_layout.addWidget(self._table)
        root.addWidget(log_box, 1)

        # --- Footer ---
        footer = QHBoxLayout()
        self._btn_clear = QPushButton(_("Clear Log"))
        self._btn_clear.clicked.connect(self._on_clear)
        self._btn_export = QPushButton(_("Export CSV…"))
        self._btn_export.clicked.connect(self._on_export_csv)
        footer.addWidget(self._btn_clear)
        footer.addStretch()
        footer.addWidget(self._btn_export)
        root.addLayout(footer)

    def _populate_format_list(self) -> None:
        fmts = list_formats()
        names = ", ".join(f["name"] for f in fmts) if fmts else _("None bundled yet")
        self._lbl_formats.setText(names)

    # ------------------------------------------------------------------ #
    # Signals from RadioControlWidget
    # ------------------------------------------------------------------ #

    def _connect_signals(self) -> None:
        try:
            self._radio_control.rig_connected.connect(  # type: ignore[attr-defined]
                self._on_rig_connected
            )
            self._radio_control.rig_disconnected.connect(  # type: ignore[attr-defined]
                self._on_rig_disconnected
            )
            self._radio_control.rig2_connected.connect(  # type: ignore[attr-defined]
                self._on_rig2_connected
            )
            self._radio_control.rig2_disconnected.connect(  # type: ignore[attr-defined]
                self._on_rig2_disconnected
            )
        except AttributeError:
            pass

    def _on_rig_connected(self) -> None:
        """Rig 1 connected — may be a Hamlib rig or an SDR adapter."""
        rc = self._radio_control
        rig1 = getattr(rc, "_rig1", None)
        if rig1 is not None and getattr(rig1, "is_sdr", False):
            self._sdr_connected = True
            self._try_start_sdr(rig1)
        else:
            self._rig_connected = True
            self._try_start()
        self._refresh_status()

    def _on_rig_disconnected(self) -> None:
        rc = self._radio_control
        rig1 = getattr(rc, "_rig1", None)
        if rig1 is not None and getattr(rig1, "is_sdr", False):
            self._sdr_connected = False
            self._stop_sdr()
        else:
            self._rig_connected = False
            self._stop_direwolf()
        self._refresh_status()

    def _on_rig2_connected(self) -> None:
        """Rig 2 connected — may be a Hamlib rig or an SDR adapter."""
        rc = self._radio_control
        rig2 = getattr(rc, "_rig2", None)
        if rig2 is not None and getattr(rig2, "is_sdr", False):
            self._sdr_connected = True
            self._try_start_sdr(rig2)
        else:
            self._rig_connected = True
            self._try_start()
        self._refresh_status()

    def _on_rig2_disconnected(self) -> None:
        rc = self._radio_control
        rig2 = getattr(rc, "_rig2", None)
        if rig2 is not None and getattr(rig2, "is_sdr", False):
            self._sdr_connected = False
            self._stop_sdr()
        else:
            self._rig_connected = False
            self._stop_direwolf()
        self._refresh_status()

    # ------------------------------------------------------------------ #
    # Direwolf lifecycle
    # ------------------------------------------------------------------ #

    def _try_start(self) -> None:
        if not self._rig_connected:
            return
        if not find_direwolf():
            self._lbl_status.setText(
                _("Input: ⚠ Direwolf not found — use Help > Direwolf… to install")
            )
            return

        in_dev, out_dev = self._load_soundcard_devices()
        if in_dev is None:
            self._lbl_status.setText(
                _("Input: ⚠ Sound Card not configured — open Rig Settings > Sound Card")
            )
            return

        self._mgr = DirewolfManager()
        ok, err = self._mgr.start(
            callsign="N0CALL",
            ssid=0,
            in_device=in_dev,
            out_device=out_dev,
        )
        if not ok:
            self._lbl_status.setText(_("Input: ⚠ ") + err)
            return

        kiss = self._mgr.kiss_client
        if kiss:
            kiss.frame_received.connect(self._on_ax25_frame)
            kiss.connection_lost.connect(self._on_kiss_lost)

        self._refresh_status()

    def _stop_direwolf(self) -> None:
        if self._mgr:
            self._mgr.stop()
            self._mgr = None

    def _try_start_sdr(self, rig2: object) -> None:
        """Start Bell 202 AFSK demodulator on the SDR pipeline (receive only)."""
        pipeline = getattr(rig2, "_pipeline", None)
        if pipeline is None:
            self._lbl_status.setText(_("Input: ⚠ SDR pipeline not ready"))
            return
        try:
            sr = int(pipeline._device.sample_rate)
        except AttributeError:
            self._lbl_status.setText(_("Input: ⚠ Cannot determine SDR sample rate"))
            return
        self._sdr_pipeline = pipeline
        self._demod = AfskDemodulator(sample_rate=sr, parent=self)
        self._demod.frame_received.connect(self._on_ax25_frame)
        self._demod.start()
        pipeline.subscribe(self._demod.push_samples)
        self._refresh_status()

    def _stop_sdr(self) -> None:
        if self._demod is not None and self._sdr_pipeline is not None:
            self._sdr_pipeline.unsubscribe(  # type: ignore[attr-defined]
                self._demod.push_samples
            )
            self._demod.stop()
            self._demod = None
        self._sdr_pipeline = None

    def _on_kiss_lost(self) -> None:
        self._lbl_status.setText(_("Input: ⚠ Direwolf connection lost"))

    # ------------------------------------------------------------------ #
    # Frame reception
    # ------------------------------------------------------------------ #

    def _on_ax25_frame(self, raw: bytes) -> None:
        frame = decode_ax25(raw)
        if frame is None:
            return

        # Try to resolve NORAD from callsign via DB
        norad = self._callsign_to_norad(frame.src)
        tf = decode_telemetry(frame.src, frame.payload, norad)
        self._append_frame(tf)

    def _callsign_to_norad(self, callsign: str) -> int | None:
        """Look up NORAD by matching callsign in format definitions."""
        call_upper = callsign.upper().split("-")[0]
        for fmt in list_formats():
            if fmt.get("callsign", "").upper() == call_upper:
                return int(fmt["norad"])
        if not hasattr(self._conn, "execute"):
            return None
        row = self._conn.execute(
            "SELECT norad_cat_id FROM satellites WHERE name LIKE ?",
            (f"%{call_upper}%",),
        ).fetchone()
        return int(row["norad_cat_id"]) if row else None

    def _append_frame(self, tf: TelemetryFrame) -> None:
        now = datetime.datetime.now(datetime.UTC)
        ts = now.strftime("%H:%M:%S")

        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(ts))
        self._table.setItem(row, 1, QTableWidgetItem(tf.callsign))
        self._table.setItem(row, 2, QTableWidgetItem(tf.satellite_name))
        data_item = QTableWidgetItem(tf.summary())
        if not tf.has_fields:
            data_item.setForeground(Qt.GlobalColor.gray)
        self._table.setItem(row, 3, data_item)
        self._table.scrollToBottom()

        self._frame_count += 1
        self._lbl_count.setText(_("Frames: ") + str(self._frame_count) + _(" received"))

        self._persist_frame(tf, now)

    def _persist_frame(self, tf: TelemetryFrame, ts: datetime.datetime) -> None:
        if not hasattr(self._conn, "execute"):
            return
        parsed = (
            json.dumps({f.name: {"value": f.scaled_value, "unit": f.unit} for f in tf.fields})
            if tf.fields
            else None
        )
        self._conn.execute(
            """INSERT INTO telemetry_log
               (received_at, norad_cat_id, callsign, raw_hex, parsed_json)
               VALUES (?, ?, ?, ?, ?)""",
            (ts.isoformat(), tf.norad, tf.callsign, tf.raw_hex, parsed),
        )
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #

    def _refresh_status(self) -> None:
        if self._mgr and self._mgr.is_running:
            self._lbl_status.setText(_("Input: Rig + Direwolf (receiving)"))
            self._lbl_status.setStyleSheet("color: #27ae60;")
        elif self._demod is not None and self._sdr_connected:
            self._lbl_status.setText(_("Input: SDR (receive only — Bell 202 AFSK)"))
            self._lbl_status.setStyleSheet("color: #4a9eff;")
        elif self._rig_connected:
            pass  # message already set in _try_start
        else:
            self._lbl_status.setText(_("Input: — (connect Rig or SDR to start)"))
            self._lbl_status.setStyleSheet("color: #aaa;")

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _load_soundcard_devices(self) -> tuple[int | None, int | None]:
        if not hasattr(self._conn, "execute"):
            return None, None
        row = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = 'soundcard_settings'"
        ).fetchone()
        if not row or not row["value"]:
            return None, None
        try:
            data = json.loads(row["value"])
            in_idx = data.get("input_device_index")
            out_idx = data.get("output_device_index")
            return (
                int(in_idx) if in_idx is not None else None,
                int(out_idx) if out_idx is not None else None,
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            return None, None

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def _on_clear(self) -> None:
        self._table.setRowCount(0)
        self._frame_count = 0
        self._lbl_count.setText(_("Frames: 0 received"))

    def _on_export_csv(self) -> None:
        default_name = (
            "telemetry_" + datetime.datetime.now(datetime.UTC).strftime("%Y%m%d") + ".csv"
        )
        path, _filter = QFileDialog.getSaveFileName(
            self,
            _("Export Telemetry CSV"),
            str(Path.home() / default_name),
            "CSV (*.csv)",
        )
        if not path:
            return

        rows_count = self._table.rowCount()
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Time (UTC)", "Callsign", "Satellite", "Data"])
            for r in range(rows_count):
                writer.writerow(
                    [(item.text() if (item := self._table.item(r, c)) else "") for c in range(4)]
                )

    # ------------------------------------------------------------------ #
    # Cleanup
    # ------------------------------------------------------------------ #

    def closeEvent(self, event: Any) -> None:
        self._stop_direwolf()
        self._stop_sdr()
        super().closeEvent(event)
