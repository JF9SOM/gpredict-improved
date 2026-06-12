"""APRS tab widget — Communications > APRS.

Displays a settings bar (callsign, SSID, via path), a received-packet log,
a message-send form, and an ADIF export button.

Input source is determined automatically by the Rig Control state:
  - SDR connected  → SDR (receive-only, Python Bell 202 demodulation)
  - Rig connected  → Sound Card + Direwolf (send + receive)
  - Neither        → tab shows a "no audio source" notice

The actual demodulation / Direwolf backend is wired in subsequent commits.
This commit provides the complete UI and settings persistence.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from i18n import _

# SSID range 0-15 per AX.25 spec
_SSID_MIN = 0
_SSID_MAX = 15

# Default via path for ISS digipeater
_DEFAULT_VIA = "ARISS"


class AprsTab(QWidget):
    """Non-resident tab opened from Communications > APRS.

    Persists callsign / SSID / via settings in ``app_settings`` under the
    key ``aprs_settings``.  Received packets are stored in ``aprs_log`` (DB
    table created on first open if absent).
    """

    def __init__(
        self,
        conn: Any,
        radio_control: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._conn = conn
        self._radio_control = radio_control

        # Connection state tracked via RadioControlWidget signals
        self._rig_connected = False
        self._sdr_connected = False
        self._rig_label = ""
        self._sdr_label = ""

        # APRS engine (Direwolf backend)
        from comms.aprs.engine import AprsEngine

        self._engine = AprsEngine(conn, parent=self)
        self._engine.packet_received.connect(self._on_packet_received)
        self._engine.status_changed.connect(self._on_engine_status)
        self._engine.error_occurred.connect(self._on_engine_error)

        self._ensure_db_table()
        self._setup_ui()
        self._load_settings()
        self._connect_signals()
        self._refresh_input_source()

    # ------------------------------------------------------------------ #
    # DB helpers
    # ------------------------------------------------------------------ #

    def _ensure_db_table(self) -> None:
        """Create aprs_log table if it does not yet exist."""
        if not hasattr(self._conn, "execute"):
            return
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS aprs_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at   DATETIME NOT NULL,
                callsign      TEXT NOT NULL,
                via           TEXT,
                latitude_deg  REAL,
                longitude_deg REAL,
                comment       TEXT,
                raw_frame     TEXT,
                norad_sat     INTEGER
            )
            """
        )
        self._conn.commit()

    def _load_log_from_db(self) -> None:
        """Populate the receive log with the most recent 200 entries."""
        if not hasattr(self._conn, "execute"):
            return
        self._log_list.clear()
        rows = self._conn.execute(
            "SELECT received_at, callsign, via, comment, raw_frame "
            "FROM aprs_log ORDER BY id DESC LIMIT 200"
        ).fetchall()
        for row in reversed(rows):
            self._append_log_item(
                ts=row["received_at"],
                callsign=row["callsign"],
                via=row["via"] or "",
                comment=row["comment"] or row["raw_frame"] or "",
            )

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)

        # -- Settings bar --
        settings_group = QGroupBox(_("Station Settings"))
        settings_form = QFormLayout(settings_group)
        settings_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        # Row 1: Callsign + SSID + Via
        row1 = QHBoxLayout()
        self._callsign_edit = QLineEdit()
        self._callsign_edit.setPlaceholderText("JF9SOM")
        self._callsign_edit.setMaxLength(6)
        self._callsign_edit.setFixedWidth(90)
        row1.addWidget(QLabel(_("Callsign:")))
        row1.addWidget(self._callsign_edit)

        row1.addSpacing(12)
        self._ssid_spin = QSpinBox()
        self._ssid_spin.setRange(_SSID_MIN, _SSID_MAX)
        self._ssid_spin.setValue(9)
        self._ssid_spin.setFixedWidth(55)
        row1.addWidget(QLabel("SSID:"))
        row1.addWidget(self._ssid_spin)

        row1.addSpacing(12)
        self._via_edit = QLineEdit()
        self._via_edit.setPlaceholderText(_DEFAULT_VIA)
        self._via_edit.setText(_DEFAULT_VIA)
        self._via_edit.setFixedWidth(120)
        row1.addWidget(QLabel(_("Via:")))
        row1.addWidget(self._via_edit)
        row1.addStretch()
        settings_form.addRow(row1)

        # Row 2: input source (read-only display)
        self._input_label = QLabel(_("—"))
        self._input_label.setStyleSheet("color: #aaa;")
        settings_form.addRow(_("Input:"), self._input_label)

        root.addWidget(settings_group)

        # -- Receive log --
        log_group = QGroupBox(_("Received Packets"))
        log_layout = QVBoxLayout(log_group)
        self._log_list = QListWidget()
        self._log_list.setAlternatingRowColors(True)
        self._log_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        log_layout.addWidget(self._log_list)
        root.addWidget(log_group, stretch=1)

        # -- Send form --
        send_group = QGroupBox(_("Send Message"))
        send_layout = QHBoxLayout(send_group)
        send_layout.addWidget(QLabel(_("To:")))
        self._to_edit = QLineEdit()
        self._to_edit.setPlaceholderText("JA1XYZ")
        self._to_edit.setFixedWidth(100)
        send_layout.addWidget(self._to_edit)
        send_layout.addSpacing(8)
        send_layout.addWidget(QLabel(_("Message:")))
        self._msg_edit = QLineEdit()
        self._msg_edit.setPlaceholderText(_("Text message (max 67 chars)"))
        self._msg_edit.setMaxLength(67)
        send_layout.addWidget(self._msg_edit, stretch=1)
        self._send_btn = QPushButton(_("Send"))
        self._send_btn.setEnabled(False)
        self._send_btn.clicked.connect(self._on_send)
        send_layout.addWidget(self._send_btn)
        root.addWidget(send_group)

        # -- Footer: export + QSO count --
        footer = QHBoxLayout()
        self._export_btn = QPushButton(_("Export ADIF…"))
        self._export_btn.clicked.connect(self._on_export_adif)
        footer.addWidget(self._export_btn)
        self._qso_count_label = QLabel("")
        self._qso_count_label.setStyleSheet("color: #aaa;")
        footer.addWidget(self._qso_count_label)
        footer.addStretch()
        root.addLayout(footer)

        self._refresh_qso_count()

    # ------------------------------------------------------------------ #
    # Signal wiring
    # ------------------------------------------------------------------ #

    def _connect_signals(self) -> None:
        """Connect to RadioControlWidget connection state signals."""
        rc = self._radio_control
        if hasattr(rc, "rig_connected"):
            rc.rig_connected.connect(self._on_rig_connected)
        if hasattr(rc, "rig_disconnected"):
            rc.rig_disconnected.connect(self._on_rig_disconnected)
        # SDR connect/disconnect — emitted by RadioControlWidget when SDR rig connects
        if hasattr(rc, "rig2_connected"):
            rc.rig2_connected.connect(self._on_rig2_connected)
        if hasattr(rc, "rig2_disconnected"):
            rc.rig2_disconnected.connect(self._on_rig2_disconnected)

    # ------------------------------------------------------------------ #
    # Connection state slots
    # ------------------------------------------------------------------ #

    def _on_rig_connected(self) -> None:
        self._rig_connected = True
        self._refresh_input_source()
        self._try_start_engine()

    def _on_rig_disconnected(self) -> None:
        self._rig_connected = False
        self._engine.stop()
        self._refresh_input_source()

    def _on_rig2_connected(self) -> None:
        """Rig 2 connected — may be an SDR adapter."""
        rc = self._radio_control
        rig2 = getattr(rc, "_rig2", None)
        if rig2 is not None and getattr(rig2, "is_sdr", False):
            self._sdr_connected = True
            dev = getattr(rig2, "device_label", "SDR")
            self._sdr_label = str(dev)
        else:
            self._rig_connected = True
        self._refresh_input_source()

    def _on_rig2_disconnected(self) -> None:
        rc = self._radio_control
        rig2 = getattr(rc, "_rig2", None)
        if rig2 is not None and getattr(rig2, "is_sdr", False):
            self._sdr_connected = False
            self._sdr_label = ""
        else:
            self._rig_connected = False
        self._refresh_input_source()

    # ------------------------------------------------------------------ #
    # Input source display
    # ------------------------------------------------------------------ #

    def _refresh_input_source(self) -> None:
        """Update the input-source label and send-button state."""
        sc_ok = self._is_soundcard_configured()

        if self._rig_connected and sc_ok:
            self._input_label.setText(_("Sound Card + Direwolf  (send + receive)"))
            self._input_label.setStyleSheet("color: #7bed9f;")
            self._send_btn.setEnabled(True)
        elif self._sdr_connected:
            label = self._sdr_label or "SDR"
            self._input_label.setText(_("{dev}  (receive only — SDR)").format(dev=label))
            self._input_label.setStyleSheet("color: #4a9eff;")
            self._send_btn.setEnabled(False)
        elif self._rig_connected and not sc_ok:
            self._input_label.setText(
                _("Sound Card not configured — open Rig Settings > Sound Card")
            )
            self._input_label.setStyleSheet("color: orange;")
            self._send_btn.setEnabled(False)
        else:
            self._input_label.setText(_("No audio source — connect a Rig or SDR in Radio Control"))
            self._input_label.setStyleSheet("color: #666;")
            self._send_btn.setEnabled(False)

    def _try_start_engine(self) -> None:
        """Start Direwolf engine when rig is connected and Sound Card is configured."""
        if not self._rig_connected or not self._is_soundcard_configured():
            return
        if self._engine.is_running:
            return
        cs = self._callsign_edit.text().strip().upper()
        ssid = self._ssid_spin.value()
        via = self._via_edit.text().strip()
        if cs:
            self._engine.start_rig(cs, ssid, via)

    # ------------------------------------------------------------------ #
    # Engine signal slots
    # ------------------------------------------------------------------ #

    def _on_packet_received(self, packet: object) -> None:
        """Handle a decoded APRS packet from the engine."""
        from comms.aprs.parser import AprsPacket

        if not isinstance(packet, AprsPacket):
            return
        self.append_packet(
            callsign=packet.callsign,
            via=packet.via,
            comment=packet.comment,
            raw_frame=packet.raw_info,
            lat=packet.latitude,
            lon=packet.longitude,
        )

    def _on_engine_status(self, status: str) -> None:
        self._input_label.setText(status)

    def _on_engine_error(self, msg: str) -> None:
        self._input_label.setText(f"⚠ {msg}")
        self._input_label.setStyleSheet("color: orange;")

    def _is_soundcard_configured(self) -> bool:
        """Return True when Sound Card settings have been saved."""
        if not hasattr(self._conn, "execute"):
            return False
        row = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = 'soundcard_settings'"
        ).fetchone()
        if not row or not row["value"]:
            return False
        try:
            data = json.loads(row["value"])
            return bool(data.get("configured", False))
        except (json.JSONDecodeError, TypeError):
            return False

    # ------------------------------------------------------------------ #
    # Settings persistence
    # ------------------------------------------------------------------ #

    def _load_settings(self) -> None:
        """Restore callsign / SSID / via from app_settings."""
        if not hasattr(self._conn, "execute"):
            return
        row = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = 'aprs_settings'"
        ).fetchone()
        if not row or not row["value"]:
            return
        try:
            data = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return
        if cs := data.get("callsign"):
            self._callsign_edit.setText(str(cs).upper())
        self._ssid_spin.setValue(int(data.get("ssid", 9)))
        if via := data.get("via"):
            self._via_edit.setText(str(via))
        self._load_log_from_db()

    def _save_settings(self) -> None:
        """Persist callsign / SSID / via to app_settings."""
        if not hasattr(self._conn, "execute"):
            return
        data = {
            "callsign": self._callsign_edit.text().strip().upper(),
            "ssid": self._ssid_spin.value(),
            "via": self._via_edit.text().strip(),
        }
        self._conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) "
            "VALUES ('aprs_settings', ?, CURRENT_TIMESTAMP)",
            (json.dumps(data),),
        )
        self._conn.commit()

    def closeEvent(self, event: Any) -> None:
        """Stop engine and save settings when the tab is closed."""
        self._engine.stop()
        self._save_settings()
        super().closeEvent(event)

    # ------------------------------------------------------------------ #
    # Receive log helpers (called by APRS engine in future commits)
    # ------------------------------------------------------------------ #

    def append_packet(
        self,
        callsign: str,
        via: str,
        comment: str,
        raw_frame: str,
        lat: float | None = None,
        lon: float | None = None,
        norad: int | None = None,
    ) -> None:
        """Add a decoded APRS packet to the log and persist it to the DB."""
        ts = datetime.now(tz=UTC).strftime("%H:%M:%S")
        self._append_log_item(ts, callsign, via, comment)

        if hasattr(self._conn, "execute"):
            self._conn.execute(
                "INSERT INTO aprs_log "
                "(received_at, callsign, via, latitude_deg, longitude_deg, "
                " comment, raw_frame, norad_sat) "
                "VALUES (CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?)",
                (callsign, via, lat, lon, comment, raw_frame, norad),
            )
            self._conn.commit()
        self._refresh_qso_count()

    def _append_log_item(self, ts: str, callsign: str, via: str, comment: str) -> None:
        via_str = f",{via}" if via else ""
        text = f"{ts}  {callsign}{via_str}: {comment}"
        item = QListWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._log_list.addItem(item)
        self._log_list.scrollToBottom()

    def _refresh_qso_count(self) -> None:
        if not hasattr(self._conn, "execute"):
            return
        row = self._conn.execute("SELECT COUNT(*) AS n FROM aprs_log").fetchone()
        n = row["n"] if row else 0
        self._qso_count_label.setText(_("QSOs logged: {n}").format(n=n))

    # ------------------------------------------------------------------ #
    # Send slot (backend wired in future commit)
    # ------------------------------------------------------------------ #

    def _on_send(self) -> None:
        """Transmit an APRS message via Direwolf KISS TX."""
        to_call = self._to_edit.text().strip().upper()
        msg = self._msg_edit.text().strip()
        if not to_call or not msg:
            return
        self._save_settings()
        my_call = self._callsign_edit.text().strip().upper()
        ssid = self._ssid_spin.value()
        via = self._via_edit.text().strip()

        if self._engine.is_running:
            self._engine.send_message(my_call, ssid, via, to_call, msg)

        # Echo to receive log as sent marker
        src = f"{my_call}-{ssid}" if ssid else my_call
        self.append_packet(
            callsign=f"{src}>APRS",
            via=via,
            comment=f"[TX→{to_call}] {msg}",
            raw_frame="",
        )
        self._msg_edit.clear()

    # ------------------------------------------------------------------ #
    # ADIF export
    # ------------------------------------------------------------------ #

    def _on_export_adif(self) -> None:
        """Export the full aprs_log to an ADIF (.adi) file."""
        if not hasattr(self._conn, "execute"):
            return

        rows = self._conn.execute(
            "SELECT received_at, callsign, via, latitude_deg, longitude_deg, "
            "       comment, raw_frame, norad_sat "
            "FROM aprs_log ORDER BY id ASC"
        ).fetchall()

        default_name = f"aprs_log_{datetime.now(tz=UTC).strftime('%Y%m%d')}.adi"
        path, _filter = QFileDialog.getSaveFileName(
            self,
            _("Export ADIF"),
            os.path.expanduser(f"~/{default_name}"),
            "ADIF (*.adi);;All files (*)",
        )
        if not path:
            return

        my_call = self._callsign_edit.text().strip().upper()
        ssid = self._ssid_spin.value()
        my_station = f"{my_call}-{ssid}" if ssid else my_call

        with open(path, "w", encoding="utf-8") as f:
            f.write("<ADIF_VER:5>3.1.4\n")
            f.write("<PROGRAMID:18>GPredict-Improved\n")
            f.write("<EOH>\n\n")

            for row in rows:
                # Parse timestamp — stored as ISO-like string from SQLite
                ts_raw = str(row["received_at"] or "")
                try:
                    dt = datetime.fromisoformat(ts_raw.replace(" ", "T"))
                except ValueError:
                    dt = datetime.now(tz=UTC)

                qso_date = dt.strftime("%Y%m%d")
                time_on = dt.strftime("%H%M%S")

                callsign = str(row["callsign"] or "").split(">")[0].split("-")[0]
                comment = str(row["comment"] or "")
                via = str(row["via"] or "")
                sat_name = ""
                if row["norad_sat"] and hasattr(self._conn, "execute"):
                    sat_row = self._conn.execute(
                        "SELECT name FROM satellites WHERE norad_cat_id = ?",
                        (row["norad_sat"],),
                    ).fetchone()
                    if sat_row:
                        sat_name = str(sat_row["name"])

                def field(tag: str, value: str) -> str:
                    v = value.strip()
                    return f"<{tag}:{len(v)}>{v}\n" if v else ""

                f.write(field("CALL", callsign))
                f.write(field("QSO_DATE", qso_date))
                f.write(field("TIME_ON", time_on))
                f.write(field("BAND", "2M"))
                f.write(field("MODE", "APRS"))
                f.write(field("COMMENT", comment))
                f.write(field("MY_CALL", my_station))
                if via:
                    f.write(field("VIA", via))
                if sat_name:
                    f.write(field("SAT_NAME", sat_name))
                    f.write(field("PROP_MODE", "SAT"))
                if row["latitude_deg"] is not None:
                    f.write(
                        field(
                            "GRIDSQUARE",
                            _latlon_to_grid(
                                float(row["latitude_deg"]),
                                float(row["longitude_deg"] or 0),
                            ),
                        )
                    )
                f.write("<EOR>\n\n")

        self._qso_count_label.setText(
            _("Exported {n} QSOs → {f}").format(n=len(rows), f=os.path.basename(path))
        )
        # Reset label after 5 s
        QTimer.singleShot(5000, self._refresh_qso_count)


# ---------------------------------------------------------------------------
# Maidenhead grid helper (lat/lon → 4-char grid square)
# ---------------------------------------------------------------------------


def _latlon_to_grid(lat: float, lon: float) -> str:
    """Convert latitude / longitude to a 4-character Maidenhead locator."""
    lon_adj = lon + 180.0
    lat_adj = lat + 90.0
    field_lon = int(lon_adj / 20)
    field_lat = int(lat_adj / 10)
    sq_lon = int((lon_adj % 20) / 2)
    sq_lat = int(lat_adj % 10)
    return chr(ord("A") + field_lon) + chr(ord("A") + field_lat) + str(sq_lon) + str(sq_lat)
