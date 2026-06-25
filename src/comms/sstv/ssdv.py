"""SSDV decoder — wraps the ssdv CLI tool as a subprocess.

SSDV (Slow Scan Digital Video) transmits JPEG images as fixed-size 256-byte
packets over radio.  Each packet carries image data for one of 256 × 256
pixel tiles; the ssdv tool reassembles them into a JPEG file.

Two reception paths are supported:
  - Audio path: SSDV packets encoded as FM audio tones (some CubeSats).
    The audio is piped to the ssdv binary via stdin.
  - AX.25 path: SSDV packets carried inside AX.25 UI frames.
    Raw packet bytes are fed directly to ssdv via stdin.

The ssdv binary is located using the same priority chain as Direwolf:
  1. User-installed version in the user data directory.
  2. System-installed version found via PATH.
  3. Bundled version shipped with the app.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, Signal
from PySide6.QtGui import QImage


def find_ssdv() -> str | None:
    """Return path to the ssdv binary, or None if not found."""
    # 1. User-installed
    data_dir = (
        Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
        / "ssdv"
    )
    for candidate in (data_dir / "ssdv", data_dir / "ssdv.exe"):
        if candidate.is_file():
            return str(candidate)
    # 2. System PATH
    found = shutil.which("ssdv")
    if found:
        return found
    return None


class SsdvDecoder(QObject):
    """Reassemble SSDV packets into images using the ssdv CLI tool.

    Signals
    -------
    image_updated(QImage)
        Emitted each time a new image is fully reassembled.
    status_changed(str)
        Short human-readable status string.
    error_occurred(str)
        Emitted when the ssdv binary is missing or returns an error.
    """

    image_updated: Signal = Signal(object)
    status_changed: Signal = Signal(str)
    error_occurred: Signal = Signal(str)

    _PACKET_SIZE: int = 256  # fixed SSDV packet size in bytes

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._packets: list[bytes] = []
        self._ssdv_path: str | None = find_ssdv()

    @property
    def is_available(self) -> bool:
        """Return True when the ssdv binary can be located."""
        return self._ssdv_path is not None

    def push_packet(self, data: bytes) -> None:
        """Feed one raw SSDV packet (256 bytes) to the decoder.

        Partial packets are ignored.  When a complete image boundary is
        detected the assembled image is emitted via image_updated.
        """
        if len(data) != self._PACKET_SIZE:
            return
        self._packets.append(data)
        # Attempt reassembly every 16 packets
        if len(self._packets) % 16 == 0:
            self._try_decode()

    def flush(self) -> None:
        """Force a decode attempt with whatever packets have been received."""
        if self._packets:
            self._try_decode()
        self._packets.clear()

    # ------------------------------------------------------------------ #

    def _try_decode(self) -> None:
        """Run ssdv -d on buffered packets and emit result if successful."""
        if not self._ssdv_path:
            self.error_occurred.emit("ssdv binary not found. Install via Help > SSDV Installation…")
            return

        raw = b"".join(self._packets)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            out_path = tmp.name

        try:
            result = subprocess.run(
                [self._ssdv_path, "-d", "-", out_path],
                input=raw,
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                qimg = QImage(out_path)
                if not qimg.isNull():
                    self.image_updated.emit(qimg.copy())
                    self.status_changed.emit(f"SSDV: decoded {len(self._packets)} packets")
        except subprocess.TimeoutExpired:
            self.error_occurred.emit("ssdv decode timed out.")
        except FileNotFoundError:
            self.error_occurred.emit(f"ssdv binary not executable: {self._ssdv_path}")
        finally:
            Path(out_path).unlink(missing_ok=True)
