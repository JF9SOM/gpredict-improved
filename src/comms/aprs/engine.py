"""APRS engine — ties DirewolfManager, KissClient, and APRS parser together.

Connects to RadioControlWidget signals to start/stop automatically when the
rig or SDR connects or disconnects.  Emits ``packet_received`` for each
decoded APRS packet so the UI tab can display it without coupling to the
backend.
"""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QObject, Signal

from comms.aprs.direwolf import DirewolfManager, find_direwolf
from comms.aprs.parser import AprsPacket, Ax25Frame, decode_ax25, parse_aprs


class AprsEngine(QObject):
    """Coordinates Direwolf, KISS, and APRS parsing for the APRS tab.

    Signals
    -------
    packet_received(AprsPacket)
        Emitted on the Qt main thread for each decoded APRS packet.
    status_changed(str)
        Short human-readable status string ("Connected", "Stopped", …).
    error_occurred(str)
        Emitted when a non-fatal error occurs (e.g. Direwolf crash).
    """

    packet_received: Signal = Signal(object)
    status_changed: Signal = Signal(str)
    error_occurred: Signal = Signal(str)

    def __init__(self, conn: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._conn = conn
        self._mgr = DirewolfManager()
        self._running = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @property
    def is_running(self) -> bool:
        return self._running

    @staticmethod
    def direwolf_available() -> bool:
        """Return True when a direwolf binary can be located."""
        return find_direwolf() is not None

    def start_rig(
        self,
        callsign: str,
        ssid: int,
        via: str,
    ) -> tuple[bool, str]:
        """Start Direwolf using the configured Sound Card audio devices.

        Reads ``soundcard_settings`` from the DB to pick the right
        input / output device indices.
        """
        if self._running:
            return True, ""

        in_dev, out_dev = self._load_soundcard_devices()
        ok, err = self._mgr.start(
            callsign=callsign,
            ssid=ssid,
            via=via,
            in_device=in_dev,
            out_device=out_dev,
        )
        if not ok:
            self.error_occurred.emit(err)
            return False, err

        self._wire_kiss()
        self._running = True
        self.status_changed.emit("Connected (Rig + Direwolf)")
        return True, ""

    def stop(self) -> None:
        """Stop Direwolf and all associated threads."""
        self._mgr.stop()
        self._running = False
        self.status_changed.emit("Stopped")

    def send_message(
        self,
        src_callsign: str,
        src_ssid: int,
        via: str,
        dest: str,
        message: str,
    ) -> None:
        """Build an APRS message packet and transmit it via KISS."""
        kiss = self._mgr.kiss_client
        if kiss is None:
            return
        frame = _build_aprs_message(src_callsign, src_ssid, via, dest, message)
        kiss.send_frame(frame)

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    def _wire_kiss(self) -> None:
        """Connect KissClient signals after Direwolf starts."""
        kiss = self._mgr.kiss_client
        if kiss is None:
            return
        kiss.frame_received.connect(self._on_kiss_frame)
        kiss.connection_lost.connect(self._on_kiss_lost)

    def _on_kiss_frame(self, raw: bytes) -> None:
        """Decode an AX.25 frame and emit packet_received."""
        frame: Ax25Frame | None = decode_ax25(raw)
        if frame is None:
            return
        packet: AprsPacket = parse_aprs(frame)
        self.packet_received.emit(packet)

    def _on_kiss_lost(self) -> None:
        self._running = False
        self.error_occurred.emit("Direwolf connection lost.")
        self.status_changed.emit("Disconnected")

    def _load_soundcard_devices(
        self,
    ) -> tuple[int | None, int | None]:
        """Read soundcard_settings from DB and return (in_idx, out_idx)."""
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


# ---------------------------------------------------------------------------
# AX.25 frame builder for APRS message packets
# ---------------------------------------------------------------------------


def _encode_addr(callsign: str, ssid: int, last: bool = False) -> bytes:
    """Encode one AX.25 address field (7 bytes)."""
    cs = callsign.upper().ljust(6)[:6]
    addr = bytes(ord(c) << 1 for c in cs)
    ssid_byte = ((ssid & 0x0F) << 1) | 0x60
    if last:
        ssid_byte |= 0x01
    return addr + bytes([ssid_byte])


def _build_aprs_message(
    src_call: str,
    src_ssid: int,
    via: str,
    dest_call: str,
    message: str,
) -> bytes:
    """Build a raw AX.25 UI frame containing an APRS message packet.

    The destination is set to ``APRS`` per convention.  The via path is
    encoded as a single digipeater address (e.g. "ARISS").
    """
    via_call = via.strip().upper() or "ARISS"
    via_ssid = 0

    dest_field = _encode_addr("APRS", 0)
    src_field = _encode_addr(src_call, src_ssid)
    via_field = _encode_addr(via_call, via_ssid, last=True)

    # Pad destination callsign to 6 chars in APRS info
    dest_padded = dest_call.upper().ljust(9)[:9]
    info = f":{dest_padded}:{message}"

    frame = (
        dest_field
        + src_field
        + via_field
        + bytes([0x03, 0xF0])  # UI frame, no layer 3
        + info.encode("ascii", errors="replace")
    )
    return frame
