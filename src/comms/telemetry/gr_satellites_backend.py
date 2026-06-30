"""gr-satellites subprocess backend for the Telemetry tab.

Launches gr_satellites as a subprocess, forwards IQ samples from the SDR
pipeline via UDP, and parses decoded telemetry text from stdout.

Environment note:
  gr-satellites (apt) requires NumPy 1.x (system).  The FBSAT59 venv has
  NumPy 2.x, so we always pass PYTHONPATH=/usr/lib/python3/dist-packages
  when launching the subprocess so it picks up the system NumPy first.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Signal

# Path that makes gr_satellites find system gnuradio + NumPy 1.x
_GR_PYTHONPATH = "/usr/lib/python3/dist-packages"
_SATYAML_DIR = Path(_GR_PYTHONPATH) / "satellites" / "satyaml"

# UDP port used to send IQ from the SDR pipeline to gr_satellites
_UDP_PORT = 7356


def detect_gr_satellites() -> bool:
    """Return True if the gr_satellites CLI is on PATH."""
    return shutil.which("gr_satellites") is not None


def list_gr_satellites_norads() -> set[int]:
    """Return the set of NORAD IDs supported by the installed gr-satellites."""
    if not _SATYAML_DIR.exists():
        return set()
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return set()
    norads: set[int] = set()
    for yml in _SATYAML_DIR.glob("*.yml"):
        try:
            with open(yml) as fh:
                data = yaml.safe_load(fh)
            if isinstance(data, dict) and isinstance(data.get("norad"), int):
                norads.add(int(data["norad"]))
        except Exception:
            pass
    return norads


def list_gr_satellites_with_names() -> list[tuple[int, str]]:
    """Return sorted list of (norad, name) for all gr-satellites supported satellites."""
    if not _SATYAML_DIR.exists():
        return []
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return []
    result: list[tuple[int, str]] = []
    for yml in _SATYAML_DIR.glob("*.yml"):
        try:
            with open(yml) as fh:
                data = yaml.safe_load(fh)
            if isinstance(data, dict) and isinstance(data.get("norad"), int):
                norad = int(data["norad"])
                name = str(data.get("name", str(norad)))
                result.append((norad, name))
        except Exception:
            pass
    result.sort(key=lambda t: t[1].upper())
    return result


def get_satellite_info(norad: int) -> dict[str, object] | None:
    """Return {'name': str, 'transmitters': list} from the YAML, or None."""
    if not _SATYAML_DIR.exists():
        return None
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return None
    for yml in _SATYAML_DIR.glob("*.yml"):
        try:
            with open(yml) as fh:
                data = yaml.safe_load(fh)
            if isinstance(data, dict) and data.get("norad") == norad:
                txs = list(data.get("transmitters", {}).keys())
                return {"name": str(data.get("name", "")), "transmitters": txs}
        except Exception:
            pass
    return None


class _UdpIqForwarder:
    """Sends IQ chunks from the SDR pipeline to gr_satellites via UDP."""

    def __init__(self, port: int = _UDP_PORT) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._port = port
        self._active = False

    def start(self) -> None:
        self._active = True

    def stop(self) -> None:
        self._active = False

    def push_samples(self, samples: np.ndarray) -> None:
        if not self._active:
            return
        data = samples.view(np.float32).tobytes()
        chunk = 32768
        for i in range(0, len(data), chunk):
            with contextlib.suppress(OSError):
                self._sock.sendto(data[i : i + chunk], ("127.0.0.1", self._port))

    def close(self) -> None:
        self._active = False
        with contextlib.suppress(OSError):
            self._sock.close()


class GrSatellitesBackend(QObject):
    """Manages a gr_satellites subprocess and emits decoded telemetry."""

    # Emitted with a formatted multi-line text block per received frame
    telemetry_received = Signal(str)
    status_changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._forwarder: _UdpIqForwarder | None = None
        self._pipeline: object | None = None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(
        self,
        norad: int,
        samp_rate: int,
        pipeline: object,
    ) -> tuple[bool, str]:
        """Start gr_satellites for *norad* and attach to *pipeline*.

        Returns (ok, error_message).
        """
        if self.is_running:
            self.stop()

        if not detect_gr_satellites():
            return False, "gr_satellites not found — install via Help > gr-satellites…"

        env = os.environ.copy()
        env["PYTHONPATH"] = _GR_PYTHONPATH + os.pathsep + env.get("PYTHONPATH", "")

        cmd = [
            "gr_satellites",
            str(norad),
            "--udp",
            "--udp_port",
            str(_UDP_PORT),
            "--iq",
            "--samp_rate",
            str(samp_rate),
        ]

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                env=env,
            )
        except OSError as exc:
            return False, str(exc)

        self._reader = threading.Thread(target=self._read_stdout, daemon=True, name="gr-sat-reader")
        self._reader.start()

        self._forwarder = _UdpIqForwarder(_UDP_PORT)
        self._forwarder.start()
        self._pipeline = pipeline
        with contextlib.suppress(AttributeError):
            pipeline.subscribe(self._forwarder.push_samples)  # type: ignore[attr-defined]

        self.status_changed.emit(f"gr-satellites running (NORAD {norad})")
        return True, ""

    def stop(self) -> None:
        """Stop the subprocess and detach from the SDR pipeline."""
        if self._forwarder is not None and self._pipeline is not None:
            with contextlib.suppress(AttributeError):
                self._pipeline.unsubscribe(self._forwarder.push_samples)  # type: ignore[attr-defined]
            self._forwarder.stop()
            self._forwarder.close()
            self._forwarder = None
            self._pipeline = None

        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        self.status_changed.emit("gr-satellites stopped")

    # ------------------------------------------------------------------
    # stdout parser
    # ------------------------------------------------------------------

    def _read_stdout(self) -> None:
        """Read gr_satellites stdout and emit one signal per frame block."""
        if self._proc is None or self._proc.stdout is None:
            return
        buf: list[str] = []
        for raw_line in self._proc.stdout:
            line = raw_line.rstrip()
            if not line:
                if buf:
                    self.telemetry_received.emit("\n".join(buf))
                    buf = []
            else:
                buf.append(line)
        if buf:
            self.telemetry_received.emit("\n".join(buf))
