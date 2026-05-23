"""
Hamlib transceiver and rotator control module

RigController          — Abstract base class for transceiver control
HamlibDirectController — Direct serial port connection via python-hamlib
HamlibNetController    — TCP connection to rigctld (compatible with GPredict NET Control)
RotatorController      — Abstract base class for rotator control
HamlibRotatorController — Hamlib rotator control
HamlibVersionChecker   — Check the installed Hamlib version

Automatically falls back to a mock when Hamlib is not installed,
so tests pass even in CI environments without python-hamlib.
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import os
import socket
import sys
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ensure only Hamlib 4.7.1 is loaded — loading 4.5.5 and 4.7.1 simultaneously
# causes a "Hash collision" fatal error in Hamlib's internal rig registry.
# Remove the system dist-packages entry so Python cannot find the old _Hamlib.so,
# then prepend the 4.7.1 path. LD_LIBRARY_PATH is not touched; _Hamlib.so's
# RUNPATH already resolves libhamlib.so to /opt/hamlib/4.7/lib.
# ---------------------------------------------------------------------------
_HAMLIB_471_PY = "/opt/hamlib/4.7/lib/python3.12/site-packages"
_HAMLIB_SYS_PY = "/usr/lib/python3/dist-packages"
if _HAMLIB_SYS_PY in sys.path:
    sys.path.remove(_HAMLIB_SYS_PY)
if os.path.exists(_HAMLIB_471_PY) and _HAMLIB_471_PY not in sys.path:
    sys.path.insert(0, _HAMLIB_471_PY)

# ---------------------------------------------------------------------------
# Hamlib availability check — import is deferred to connect() to avoid loading
# the shared library at startup, which collides with Qt's thread-local storage.
# ---------------------------------------------------------------------------

HAMLIB_AVAILABLE: bool = importlib.util.find_spec("Hamlib") is not None
if not HAMLIB_AVAILABLE:
    logger.warning(
        "python-hamlib not found — running in mock mode. "
        "Install libhamlib-dev and python3-hamlib to enable real rig control."
    )


# ---------------------------------------------------------------------------
# Mode mapping (SATNOGS mode string → Hamlib constant)
# ---------------------------------------------------------------------------


def _build_mode_map() -> dict[str, int]:
    """SATNOGS mode string → Hamlib RIG_MODE_* integer constant.

    Values are the stable public Hamlib bitmask constants (unchanged across
    versions), so no Hamlib import is needed at module load time.
    USB appears before SSB so SSB wins in the reverse map (last-wins dict
    comprehension), matching the canonical SATNOGS name.
    """
    return {
        "DIGITALVOICE": 32,  # RIG_MODE_FM
        "USB": 4,  # RIG_MODE_USB  (alias; SSB wins in reverse map)
        "FM": 32,  # RIG_MODE_FM
        "SSB": 4,  # RIG_MODE_USB  (canonical SATNOGS name; wins in reverse map)
        "LSB": 8,  # RIG_MODE_LSB
        "CW": 2,  # RIG_MODE_CW
        "CW-R": 128,  # RIG_MODE_CWR
        "BPSK": 2048,  # RIG_MODE_PKTUSB
        "AFSK": 4096,  # RIG_MODE_PKTFM
        "AM": 1,  # RIG_MODE_AM
    }


MODE_MAP: dict[str, int] = _build_mode_map()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


class RigState(Enum):
    """Transceiver connection state."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class RigInfo:
    """Information about the connected transceiver."""

    model_id: int
    model_name: str
    port: str
    baud_rate: int
    state: RigState = RigState.DISCONNECTED


@dataclass
class FrequencyState:
    """Current frequency and mode state."""

    freq_hz: float = 0.0
    mode: str = "FM"
    passband_hz: int = 0
    ctcss_tone: float = 0.0  # Hz (0.0 = off)
    dcs_code: int = 0  # 0 = off


@dataclass
class RotatorState:
    """Rotator state."""

    azimuth_deg: float = 0.0
    elevation_deg: float = 0.0
    is_moving: bool = False


@dataclass
class VersionInfo:
    """Hamlib version information and update check result."""

    installed: str
    latest: str
    is_outdated: bool
    release_url: str = ""
    warning_message: str = field(default="", init=False)

    def __post_init__(self) -> None:
        if self.is_outdated:
            self.warning_message = (
                f"Hamlib {self.installed} is installed, "
                f"but {self.latest} is available. "
                f"Consider upgrading: {self.release_url}"
            )


class RigControlError(Exception):
    """Transceiver control error (raised on rigctld command failure or communication error)."""


# ---------------------------------------------------------------------------
# Abstract base class — RigController
# ---------------------------------------------------------------------------


class RigController(ABC):
    """
    Abstract base class for transceiver control.

    All public methods are thread-safe (protected by an internal lock).
    Called from both the Qt UI thread and the tracking background thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = RigState.DISCONNECTED
        self._freq_state = FrequencyState()

    # -- Connection management --

    @abstractmethod
    def connect(self) -> bool:
        """Establish a connection. Returns True on success."""

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect."""

    @property
    def state(self) -> RigState:
        """Current connection state."""
        with self._lock:
            return self._state

    @property
    def is_connected(self) -> bool:
        """Whether currently connected."""
        return self.state == RigState.CONNECTED

    # -- Frequency and mode --

    @abstractmethod
    def set_frequency(self, freq_hz: float, vfo: str = "VFOA") -> bool:
        """Set the frequency in Hz."""

    @abstractmethod
    def get_frequency(self, vfo: str = "VFOA") -> float:
        """Return the current frequency in Hz. Returns -1.0 on error."""

    @abstractmethod
    def set_mode(self, mode: str, passband_hz: int = 0, vfo: str = "VFOA") -> bool:
        """Set the mode. mode is a SATNOGS format string ("FM", "SSB", etc.)."""

    @abstractmethod
    def get_mode(self, vfo: str = "VFOA") -> str:
        """Return the current mode as a SATNOGS format string."""

    # -- CTCSS / DCS tone --

    @abstractmethod
    def set_ctcss_tone(self, tone_hz: float) -> bool:
        """Set the CTCSS tone (0.0 to disable)."""

    @abstractmethod
    def set_dcs_code(self, code: int) -> bool:
        """Set the DCS code (0 to disable)."""

    # -- VFO --

    @abstractmethod
    def set_vfo(self, vfo: str) -> bool:
        """Switch the active VFO ("VFOA" / "VFOB" / "Main" / "Sub")."""

    def set_vfo_frequencies(
        self,
        vfoa_hz: float | None,
        vfob_hz: float | None,
    ) -> bool:
        """Safely set the VFOA and VFOB frequencies.

        Can be overridden in subclasses. Default calls set_frequency sequentially.
        Returns False when not connected. Raises RigControlError on failure.
        """
        ok = True
        if vfoa_hz is not None:
            ok = self.set_frequency(vfoa_hz, "VFOA") and ok
        if vfob_hz is not None:
            ok = self.set_frequency(vfob_hz, "VFOB") and ok
        return ok

    def send_mode_only(self, dl_mode: str, ul_mode: str) -> None:
        """Set mode on both VFOs without affecting split state.

        Default implementation calls set_mode() for the downlink mode.
        Override in subclasses that support independent per-VFO mode setting.
        """
        self.set_mode(dl_mode)

    # -- Utilities --

    @abstractmethod
    def get_rig_info(self) -> RigInfo | None:
        """Return connected rig info, or None when not connected."""

    def _mode_to_hamlib(self, mode: str) -> int:
        """Convert a SATNOGS mode string to a Hamlib constant. Unknown modes fall back to FM."""
        return MODE_MAP.get(mode, MODE_MAP["FM"])

    def _hamlib_to_mode(self, hamlib_mode: int) -> str:
        """Convert a Hamlib mode constant to a SATNOGS mode string."""
        reverse = {v: k for k, v in MODE_MAP.items()}
        return reverse.get(hamlib_mode, "FM")


# ---------------------------------------------------------------------------
# HamlibDirectController
# ---------------------------------------------------------------------------


class HamlibDirectController(RigController):
    """
    Transceiver controller that connects directly to a serial port via python-hamlib.

    Falls back to mock mode when Hamlib is not installed.
    """

    def __init__(
        self,
        model_id: int,
        port: str,
        baud_rate: int = 9600,
        data_bits: int = 8,
        stop_bits: int = 1,
        handshake: str = "None",
    ) -> None:
        """
        Args:
            model_id:  Hamlib rig model ID (e.g. IC-9700 = 3081)
            port:      Serial port ("/dev/ttyUSB0", "COM3", etc.)
            baud_rate: Baud rate
            data_bits: Data bits
            stop_bits: Stop bits
            handshake: Flow control ("None", "XONXOFF", "Hardware")
        """
        super().__init__()
        self._model_id = model_id
        self._port = port
        self._baud_rate = baud_rate
        self._data_bits = data_bits
        self._stop_bits = stop_bits
        self._handshake = handshake
        self._rig: Any = None  # Hamlib.Rig instance or _MockRig
        self._hamlib: Any = None  # Hamlib module, set lazily in connect()
        self._last_dl_hz: float | None = None
        self._last_ul_hz: float | None = None

    # -- Connection management --

    def connect(self) -> bool:
        """Connect to the serial port."""
        with self._lock:
            if self._state == RigState.CONNECTED:
                return True
            self._state = RigState.CONNECTING

        try:
            if HAMLIB_AVAILABLE:
                import Hamlib as _H  # lazy — avoids Qt TLS collision at startup

                self._hamlib = _H
                rig = _H.Rig(self._model_id)
                # Hamlib 4.x: rigport is a SwigPyObject with no Python attributes;
                # use set_conf() instead of the old rig.state.rigport.pathname API.
                rig.set_conf("rig_pathname", self._port)
                rig.set_conf("serial_speed", str(self._baud_rate))
                rig.set_conf("data_bits", str(self._data_bits))
                rig.set_conf("stop_bits", str(self._stop_bits))
                rig.open()
                self._rig = rig
            else:
                self._rig = _MockRig(self._model_id)

            self._last_dl_hz = None
            self._last_ul_hz = None
            self._init_split()

            with self._lock:
                self._state = RigState.CONNECTED
            logger.info("RigDirect: connected to %s (model %d)", self._port, self._model_id)
            return True

        except Exception as exc:
            with self._lock:
                self._state = RigState.ERROR
            logger.error("RigDirect: connect failed — %s", exc)
            return False

    def disconnect(self) -> None:
        """Disconnect from the serial port."""
        with self._lock:
            if self._state == RigState.DISCONNECTED:
                return
        try:
            if self._rig is not None:
                self._rig.close()
        except Exception as exc:
            logger.warning("RigDirect: disconnect error — %s", exc)
        finally:
            self._rig = None
            self._hamlib = None
            self._last_dl_hz = None
            self._last_ul_hz = None
            with self._lock:
                self._state = RigState.DISCONNECTED

    # -- Frequency and mode --

    def set_frequency(self, freq_hz: float, vfo: str = "VFOA") -> bool:
        """Set the frequency in Hz."""
        if not self.is_connected or self._rig is None:
            return False
        try:
            hamlib_vfo = self._vfo_str_to_const(vfo)
            self._rig.set_freq(hamlib_vfo, freq_hz)
            with self._lock:
                self._freq_state.freq_hz = freq_hz
            return True
        except Exception as exc:
            logger.error("RigDirect.set_frequency: %s", exc)
            return False

    def get_frequency(self, vfo: str = "VFOA") -> float:
        """Return the current frequency in Hz."""
        if not self.is_connected or self._rig is None:
            return -1.0
        try:
            hamlib_vfo = self._vfo_str_to_const(vfo)
            return float(self._rig.get_freq(hamlib_vfo))
        except Exception as exc:
            logger.error("RigDirect.get_frequency: %s", exc)
            return -1.0

    def set_mode(self, mode: str, passband_hz: int = 0, vfo: str = "VFOA") -> bool:
        """Set the mode and passband."""
        if not self.is_connected or self._rig is None:
            return False
        try:
            hamlib_mode = self._mode_to_hamlib(mode)
            hamlib_vfo = self._vfo_str_to_const(vfo)
            self._rig.set_mode(hamlib_vfo, hamlib_mode, passband_hz)
            with self._lock:
                self._freq_state.mode = mode
                self._freq_state.passband_hz = passband_hz
            return True
        except Exception as exc:
            logger.error("RigDirect.set_mode: %s", exc)
            return False

    def get_mode(self, vfo: str = "VFOA") -> str:
        """Return the current mode as a SATNOGS format string."""
        if not self.is_connected or self._rig is None:
            return "FM"
        try:
            hamlib_vfo = self._vfo_str_to_const(vfo)
            mode, _ = self._rig.get_mode(hamlib_vfo)
            return self._hamlib_to_mode(mode)
        except Exception as exc:
            logger.error("RigDirect.get_mode: %s", exc)
            return "FM"

    def set_ctcss_tone(self, tone_hz: float) -> bool:
        """Set the CTCSS tone. Pass tone_hz=0.0 to disable."""
        if not self.is_connected or self._rig is None:
            return False
        if self._hamlib is None:
            with self._lock:
                self._freq_state.ctcss_tone = tone_hz
            return True
        try:
            # Hamlib represents tones as integers scaled by 10 (e.g. 88.5 Hz → 885)
            tone_int = int(round(tone_hz * 10))
            if tone_hz > 0:
                self._rig.set_func(
                    self._hamlib.RIG_VFO_CURR,
                    self._hamlib.RIG_FUNC_TONE,
                    1,
                )
                self._rig.set_level(
                    self._hamlib.RIG_VFO_CURR,
                    self._hamlib.RIG_LEVEL_CTCSS_TONE,
                    tone_int,
                )
            else:
                self._rig.set_func(
                    self._hamlib.RIG_VFO_CURR,
                    self._hamlib.RIG_FUNC_TONE,
                    0,
                )
            with self._lock:
                self._freq_state.ctcss_tone = tone_hz
            return True
        except Exception as exc:
            logger.error("RigDirect.set_ctcss_tone: %s", exc)
            return False

    def set_dcs_code(self, code: int) -> bool:
        """Set the DCS code. Pass code=0 to disable."""
        if not self.is_connected or self._rig is None:
            return False
        if self._hamlib is None:
            with self._lock:
                self._freq_state.dcs_code = code
            return True
        try:
            if code > 0:
                self._rig.set_func(
                    self._hamlib.RIG_VFO_CURR,
                    self._hamlib.RIG_FUNC_TSQL,
                    1,
                )
                self._rig.set_level(
                    self._hamlib.RIG_VFO_CURR,
                    self._hamlib.RIG_LEVEL_CTCSS_SQL,
                    code,
                )
            else:
                self._rig.set_func(
                    self._hamlib.RIG_VFO_CURR,
                    self._hamlib.RIG_FUNC_TSQL,
                    0,
                )
            with self._lock:
                self._freq_state.dcs_code = code
            return True
        except Exception as exc:
            logger.error("RigDirect.set_dcs_code: %s", exc)
            return False

    def set_vfo(self, vfo: str) -> bool:
        """Switch the active VFO."""
        if not self.is_connected or self._rig is None:
            return False
        try:
            self._rig.set_vfo(self._vfo_str_to_const(vfo))
            return True
        except Exception as exc:
            logger.error("RigDirect.set_vfo: %s", exc)
            return False

    def set_vfo_frequencies(
        self,
        vfoa_hz: float | None,
        vfob_hz: float | None,
    ) -> bool:
        """Set DL (VFOA) and UL (VFOB/split) frequencies with 1 Hz delta suppression.

        Uses set_freq for the RX VFO and set_split_freq for the TX VFO.
        Skips the command when the frequency has not changed by 1 Hz or more,
        or when the argument is None.
        """
        if not self.is_connected or self._rig is None:
            return False
        try:
            rx_vfo = self._vfo_str_to_const("VFOA")
            if vfoa_hz is not None:
                last_dl = self._last_dl_hz
                if last_dl is None or abs(vfoa_hz - last_dl) >= 1.0:
                    self._rig.set_freq(rx_vfo, int(vfoa_hz))
                    self._last_dl_hz = vfoa_hz
            if vfob_hz is not None:
                last_ul = self._last_ul_hz
                if last_ul is None or abs(vfob_hz - last_ul) >= 1.0:
                    self._rig.set_split_freq(rx_vfo, int(vfob_hz))
                    self._last_ul_hz = vfob_hz
            return True
        except Exception as exc:
            logger.error("RigDirect.set_vfo_frequencies: %s", exc)
            return False

    def send_mode_only(self, dl_mode: str, ul_mode: str) -> None:
        """Set mode on VFOA (downlink/RX) and VFOB (uplink/TX).

        Opens a dedicated short-lived serial connection so that the mode can be
        set even when the main tracking connection has already been disconnected
        — mirroring HamlibNetController which opens a fresh TCP socket per call.
        Silently ignores all errors (best-effort).
        """
        logger.info("RigDirect: send_mode_only dl=%s ul=%s", dl_mode, ul_mode)
        if not HAMLIB_AVAILABLE:
            return
        dl_hamlib = self._mode_to_hamlib(dl_mode)
        ul_hamlib = self._mode_to_hamlib(ul_mode)
        rig: Any = None
        try:
            import Hamlib as _H  # lazy — avoids Qt TLS collision at startup

            rig = _H.Rig(self._model_id)
            rig.set_conf("rig_pathname", self._port)
            rig.set_conf("serial_speed", str(self._baud_rate))
            rig.open()
            rig.set_mode(_H.RIG_VFO_A, dl_hamlib, 0)
            rig.set_mode(_H.RIG_VFO_B, ul_hamlib, 0)
            logger.info("RigDirect: send_mode_only done")
        except Exception as exc:
            logger.error("RigDirect.send_mode_only: %s", exc)
        finally:
            if rig is not None:
                with contextlib.suppress(Exception):
                    rig.close()

    def get_rig_info(self) -> RigInfo | None:
        """Return info about the connected rig."""
        if not self.is_connected:
            return None
        model_name = f"Model {self._model_id}"
        if self._hamlib is not None and self._rig is not None:
            with contextlib.suppress(Exception):
                model_name = self._rig.caps.model_name
        return RigInfo(
            model_id=self._model_id,
            model_name=model_name,
            port=self._port,
            baud_rate=self._baud_rate,
            state=self.state,
        )

    # -- Internal utilities --

    def _init_split(self) -> None:
        """Enable split mode: RX=VFOA (downlink), TX=VFOB (uplink). Called once at connect."""
        if self._rig is None:
            return
        try:
            rx_vfo = self._vfo_str_to_const("VFOA")
            tx_vfo = self._vfo_str_to_const("VFOB")
            self._rig.set_split_vfo(rx_vfo, 1, tx_vfo)
            logger.info("RigDirect: split enabled (RX=VFOA, TX=VFOB)")
        except Exception as exc:
            logger.warning("RigDirect: set_split_vfo failed — %s", exc)

    def _vfo_str_to_const(self, vfo: str) -> int:
        """Convert a VFO string to the corresponding Hamlib constant (or 0 in mock mode)."""
        if self._hamlib is None:
            return 0
        vfo_map = {
            "VFOA": self._hamlib.RIG_VFO_A,
            "VFOB": self._hamlib.RIG_VFO_B,
            "Main": self._hamlib.RIG_VFO_MAIN,
            "Sub": self._hamlib.RIG_VFO_SUB,
        }
        return int(vfo_map.get(vfo, self._hamlib.RIG_VFO_CURR))


# ---------------------------------------------------------------------------
# HamlibNetController (rigctld TCP connection)
# ---------------------------------------------------------------------------


class HamlibNetController(RigController):
    """
    Transceiver controller that connects to rigctld over TCP.

    Compatible with GPredict NET Control mode — works with any existing
    rigctld setup. Uses the rigctld newline-delimited text protocol.
    """

    _TIMEOUT = 10.0  # seconds — allows for slow CAT backends such as FTX-1

    def __init__(
        self, host: str = "localhost", port: int = 4532, radio_type: str = "full_duplex"
    ) -> None:
        """
        Args:
            host:        Host where rigctld is running
            port:        rigctld port number (default 4532)
            radio_type:  "full_duplex"=send both F and I (default) /
                         "rx_only"=F only / "tx_only"=I only
        """
        super().__init__()
        self._host = host
        self._port = port
        self._radio_type = radio_type
        self._sock: socket.socket | None = None
        self._vfo_mode: bool = False
        self._cmd_lock = threading.Lock()  # serialise send+recv to prevent response misalignment
        self._cached_model_name: str = ""  # fetched once on connect and cached
        self._last_dl_hz: float | None = None  # None = just connected; forces the first F/I send
        self._last_ul_hz: float | None = None

    # -- Connection management --

    @property
    def is_connected(self) -> bool:
        """True only when connected and the socket is valid."""
        with self._lock:
            return self._state == RigState.CONNECTED and self._sock is not None

    def connect(self) -> bool:
        """Establish a TCP connection to rigctld."""
        with self._lock:
            if self._state == RigState.CONNECTED:
                return True
            self._state = RigState.CONNECTING

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self._TIMEOUT)
            sock.connect((self._host, self._port))
            self._sock = sock
            with self._lock:
                self._state = RigState.CONNECTED
            # Reset frequency state so reconnection does not inherit the previous session.
            # Without this, _last_dl_hz is not None → the initial f-check is sent →
            # CAT delay after S 1 Main causes a timeout → immediate disconnect loop.
            self._last_dl_hz = None
            self._last_ul_hz = None
            logger.info("RigNet: connected to %s:%d", self._host, self._port)
            # _ and \chk_vfo are optional info-query commands.
            # Sending them with a 2 s timeout over a raw socket leaves stale data in the
            # receive buffer on slow backends (e.g. FTX-1), causing subsequent _cmd() calls
            # to read the wrong response (command/response misalignment).
            # Only S 1 Main is sent during the connection sequence.
            self._init_vfo()
            # If _init_vfo()'s _cmd() raises OSError (including timeout) and closes the
            # socket, treat the connection as failed and transition to ERROR.
            if self._sock is None:
                with self._lock:
                    self._state = RigState.ERROR
                logger.error("RigNet: S 1 Main timed out or failed — aborting connect")
                return False
            return True
        except OSError as exc:
            with self._lock:
                self._state = RigState.ERROR
            logger.error("RigNet: connect failed — %s", exc)
            return False

    def disconnect(self) -> None:
        """Disconnect the TCP connection."""
        with self._lock:
            if self._state == RigState.DISCONNECTED:
                return
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass
        finally:
            self._sock = None
            self._last_dl_hz = None
            self._last_ul_hz = None
            with self._lock:
                self._state = RigState.DISCONNECTED

    # -- Low-level communication --

    def _cmd_raw(self, command: str) -> str:
        """Send a command and return the response. Caller MUST hold _cmd_lock.

        Reads until the RPRT line appears, which prevents response data from
        read commands (f/i, etc.) from lingering in the buffer and being
        misread as the next command's response.
        On OSError, the socket is closed and the state transitions to DISCONNECTED.
        """
        if self._sock is None:
            return ""
        try:
            self._sock.sendall((command + "\n").encode())
            data = b""
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"RPRT" in data:
                    break
            return data.decode(errors="replace").strip()
        except OSError as exc:
            logger.error("RigNet._cmd(%r): %s", command, exc)
            with contextlib.suppress(OSError):
                if self._sock:
                    self._sock.close()
            self._sock = None
            with self._lock:
                self._state = RigState.DISCONNECTED
            return ""

    def _cmd(self, command: str) -> str:
        """Send a command to rigctld and return the response (thread-safe)."""
        with self._cmd_lock:
            return self._cmd_raw(command)

    def _fetch_model_name(self) -> str:
        """Fetch the model name once at connect time using the _ command.

        Operates on the raw socket directly, bypassing _cmd(), so that an
        unsupported _ command or a timeout does not break the connection —
        it falls back to "host:port" instead.
        """
        if self._sock is None:
            return f"{self._host}:{self._port}"
        prev_timeout = self._sock.gettimeout()
        try:
            self._sock.settimeout(2.0)
            self._sock.sendall(b"_\n")
            data = b""
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"RPRT" in data:
                    break
            resp = data.decode(errors="replace").strip()
            lines = [
                ln.strip() for ln in resp.splitlines() if ln.strip() and not ln.startswith("RPRT")
            ]
            return lines[0] if lines else f"{self._host}:{self._port}"
        except OSError as exc:
            logger.warning("RigNet: _ (get_info) failed (ignored): %s", exc)
            return f"{self._host}:{self._port}"
        finally:
            with contextlib.suppress(OSError):
                if self._sock is not None:
                    self._sock.settimeout(prev_timeout)

    def _init_vfo(self) -> None:
        """Enable split (called once at connect time).

        Sends S 1 Main. On the FTX-1F backend this results in Sub=TX (uplink)
        and Main=RX (downlink) — the opposite of the literal VFO name.
        No M command is sent here; mode is set exclusively via send_mode_only().
        Sent through _cmd() so _cmd_lock serialises it and prevents buffer
        residue from an independent recv loop on the raw socket.
        """
        resp = self._cmd("S 1 Main")
        if "RPRT 0" not in resp:
            logger.warning("RigNet: split setup returned %r", resp)

    # -- Internal utilities --

    def _detect_vfo_mode(self) -> bool:
        r"""Send \chk_vfo to detect the rigctld VFO mode.

        Operates on the raw socket directly so that a timeout or unsupported
        command does not break the connection — returns False in that case.

        rigctld response format:
          vfo_mode=on  → "1\nRPRT 0\n"
          vfo_mode=off → "0\nRPRT 0\n"
          unsupported  → "RPRT -1\n"
          timeout      → OSError (socket.timeout)
        """
        if self._sock is None:
            return False
        prev_timeout = self._sock.gettimeout()
        try:
            self._sock.settimeout(2.0)
            self._sock.sendall(b"\\chk_vfo\n")
            data = b""
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"RPRT" in data:
                    break
            resp = data.decode(errors="replace").strip()
            lines = resp.splitlines()
            return bool(lines and lines[0].strip() == "1")
        except OSError as exc:
            logger.warning("RigNet: \\chk_vfo failed (vfo_mode=False assumed): %s", exc)
            return False
        finally:
            with contextlib.suppress(OSError):
                if self._sock is not None:
                    self._sock.settimeout(prev_timeout)

    @staticmethod
    def _normalize_vfo(vfo: str) -> str:
        """Normalise a VFO string to the form accepted by rigctld."""
        _map = {"VFOA": "VFOA", "VFOB": "VFOB", "Main": "Main", "Sub": "Sub"}
        return _map.get(vfo, vfo)

    # -- Frequency and mode --

    def _set_one_vfo(self, vfo: str, freq_hz: float) -> None:
        """Internal helper to set a single VFO frequency. Raises RigControlError on failure."""
        norm_vfo = self._normalize_vfo(vfo)
        if self._vfo_mode:
            resp = self._cmd(f"\\set_freq {norm_vfo} {int(freq_hz)}")
        else:
            vfo_resp = self._cmd(f"V {norm_vfo}")
            if "RPRT 0" not in vfo_resp:
                raise RigControlError(f"set_vfo({norm_vfo!r}) failed: {vfo_resp!r}")
            resp = self._cmd(f"F {int(freq_hz)}")
        if "RPRT 0" not in resp:
            raise RigControlError(f"set_frequency({freq_hz!r}, {norm_vfo!r}) failed: {resp!r}")
        with self._lock:
            self._freq_state.freq_hz = freq_hz

    def set_frequency(self, freq_hz: float, vfo: str = "VFOA") -> bool:
        """Set the frequency in Hz.

        Returns False when not connected.
        Raises RigControlError when the command fails while connected.
        No split command is sent (avoids split issues on FTX-1 and similar rigs).
        """
        if not self.is_connected:
            return False
        self._set_one_vfo(vfo, freq_hz)
        return True

    def set_vfo_frequencies(
        self,
        vfoa_hz: float | None,
        vfob_hz: float | None,
    ) -> bool:
        """Set RX/TX frequencies in the per-second tracking loop.

        Never sends f/i (get_freq/get_split_freq) commands.
        On slow CAT backends such as the FTX-1, the f command can take more
        than 10 s and trigger a timeout, leading to a per-cycle
        disconnect → reconnect (including S 1 Main) loop.

        Write-only protocol:
          [RX cycle]
            F {dl_hz}  — write to Sub (RX/downlink)
                         only when changed by 1 Hz or more, or on the first call
                         (_last_dl_hz is None).
          [TX cycle]
            After the RX cycle, is_connected is checked; TX is skipped if disconnected.
            I {ul_hz}  — write to Main (TX/uplink)
                         only when changed by 1 Hz or more, or on the first call
                         (_last_ul_hz is None).

        connect() calls _init_vfo() which sends S 1 Sub (split ON, TX VFO=Sub):
          F → Main (RX/downlink)
          I → Sub (TX/uplink)
        The TX cycle is skipped when vfob_hz is None.
        """
        if not self.is_connected:
            return False

        send_rx = self._radio_type != "tx_only"
        send_tx = self._radio_type != "rx_only"

        with self._cmd_lock:
            # RX cycle
            if send_rx and vfoa_hz is not None:
                last_dl = self._last_dl_hz
                if last_dl is None or abs(vfoa_hz - last_dl) >= 1.0:
                    logger.info("RigNet: sending F %d", int(vfoa_hz))
                    resp = self._cmd_raw(f"F {int(vfoa_hz)}")
                    if "RPRT 0" not in resp:
                        raise RigControlError(f"set RX freq failed: {resp!r}")
                    with self._lock:
                        self._freq_state.freq_hz = vfoa_hz
                    self._last_dl_hz = vfoa_hz

            # Skip TX and mode if F caused an OSError and disconnected
            if not self.is_connected:
                return True

            # TX cycle
            if send_tx and vfob_hz is not None:
                last_ul = self._last_ul_hz
                if last_ul is None or abs(vfob_hz - last_ul) >= 1.0:
                    logger.info("RigNet: sending I %d", int(vfob_hz))
                    resp = self._cmd_raw(f"I {int(vfob_hz)}")
                    if "RPRT 0" not in resp:
                        raise RigControlError(f"set TX freq failed: {resp!r}")
                    self._last_ul_hz = vfob_hz

        return True

    def get_frequency(self, vfo: str = "VFOA") -> float:
        resp = self._cmd("f")
        try:
            return float(resp.splitlines()[0])
        except (ValueError, IndexError):
            return -1.0

    def set_mode(self, mode: str, passband_hz: int = 0, vfo: str = "VFOA") -> bool:
        # rigctld M command format: "M <mode> <passband>"
        hamlib_mode_name = _SATNOGS_TO_RIGCTLD_MODE.get(mode, "FM")
        resp = self._cmd(f"M {hamlib_mode_name} {passband_hz}")
        ok = "RPRT 0" in resp
        if ok:
            with self._lock:
                self._freq_state.mode = mode
        return ok

    def get_mode(self, vfo: str = "VFOA") -> str:
        resp = self._cmd("m")
        lines = resp.splitlines()
        if lines:
            rigctld_mode = lines[0].strip()
            return _RIGCTLD_MODE_TO_SATNOGS.get(rigctld_mode, "FM")
        return "FM"

    def set_ctcss_tone(self, tone_hz: float) -> bool:
        tone_int = int(round(tone_hz * 10))
        resp = self._cmd(f"L CTCSS_TONE {tone_int}")
        return "RPRT 0" in resp

    def set_dcs_code(self, code: int) -> bool:
        resp = self._cmd(f"L DCS_CODE {code}")
        return "RPRT 0" in resp

    def set_vfo(self, vfo: str) -> bool:
        resp = self._cmd(f"V {vfo}")
        return "RPRT 0" in resp

    def send_mode_only(self, dl_mode: str, ul_mode: str) -> None:
        """Set mode on both VFOs via an independent TCP connection.

        Opens a new socket to rigctld (separate from the main tracking
        connection), sends V Sub → M {ul_mode} 0 → V Main → M {dl_mode} 0,
        then closes. On the FTX-1F backend (S 1 Main): Sub=TX (uplink),
        Main=RX (downlink). Ending on V Main is consistent with the S 1 Main
        sent during connect(). Does not send S 1 Main itself so the split
        state of any concurrent session is undisturbed.
        Silently ignores all errors (best-effort).
        """
        rigctld_ul = _SATNOGS_TO_RIGCTLD_MODE.get(ul_mode)
        rigctld_dl = _SATNOGS_TO_RIGCTLD_MODE.get(dl_mode)
        if not rigctld_ul and not rigctld_dl:
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self._TIMEOUT)
            sock.connect((self._host, self._port))
            if rigctld_ul:
                sock.sendall(b"V Sub\n")
                sock.recv(64)
                sock.sendall(f"M {rigctld_ul} 0\n".encode())
                sock.recv(64)
            if rigctld_dl:
                sock.sendall(b"V Main\n")
                sock.recv(64)
                sock.sendall(f"M {rigctld_dl} 0\n".encode())
                sock.recv(64)
            sock.close()
        except Exception:
            pass

    def get_rig_info(self) -> RigInfo | None:
        if not self.is_connected:
            return None
        return RigInfo(
            model_id=0,
            model_name=self._cached_model_name or f"{self._host}:{self._port}",
            port=f"{self._host}:{self._port}",
            baud_rate=0,
            state=self.state,
        )


# rigctld mode name mapping
_SATNOGS_TO_RIGCTLD_MODE: dict[str, str] = {
    "DIGITALVOICE": "FM",
    "FM": "FM",
    "USB": "USB",  # rigctld-style name used by some SatNOGS entries; placed first
    "SSB": "USB",  # canonical SatNOGS name; wins in reverse map
    "LSB": "LSB",
    "CW": "CW",
    "CW-R": "CWR",
    "BPSK": "PKTUSB",
    "AFSK": "PKTFM",
    "AM": "AM",
}
_RIGCTLD_MODE_TO_SATNOGS: dict[str, str] = {v: k for k, v in _SATNOGS_TO_RIGCTLD_MODE.items()}


# ---------------------------------------------------------------------------
# Abstract base class — RotatorController
# ---------------------------------------------------------------------------


class RotatorController(ABC):
    """Abstract base class for rotator control."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = RigState.DISCONNECTED
        self._rotor_state = RotatorState()

    @abstractmethod
    def connect(self) -> bool:
        """Establish a connection."""

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect."""

    @property
    def is_connected(self) -> bool:
        """Whether currently connected."""
        with self._lock:
            return self._state == RigState.CONNECTED

    @abstractmethod
    def set_position(self, azimuth_deg: float, elevation_deg: float) -> bool:
        """Set the azimuth and elevation in degrees."""

    @abstractmethod
    def get_position(self) -> RotatorState:
        """Return the current azimuth and elevation."""

    @abstractmethod
    def stop(self) -> bool:
        """Stop rotation."""

    @abstractmethod
    def park(self) -> bool:
        """Return to the home position."""


# ---------------------------------------------------------------------------
# HamlibRotatorController
# ---------------------------------------------------------------------------


class HamlibRotatorController(RotatorController):
    """
    Rotator controller using Hamlib.

    Supports both direct serial connection (equivalent to HamlibDirect) and
    NET connection (rotctld). When net_mode=True, connects to rotctld over TCP.
    """

    def __init__(
        self,
        model_id: int = 1,
        port: str = "/dev/ttyUSB0",
        baud_rate: int = 9600,
        *,
        net_mode: bool = False,
        net_host: str = "localhost",
        net_port: int = 4533,
    ) -> None:
        super().__init__()
        self._model_id = model_id
        self._port = port
        self._baud_rate = baud_rate
        self._net_mode = net_mode
        self._net_host = net_host
        self._net_port = net_port
        self._rot: Any = None
        self._hamlib: Any = None  # Hamlib module, set lazily in connect()
        self._sock: socket.socket | None = None

    def connect(self) -> bool:
        """Connect to the rotator."""
        with self._lock:
            if self._state == RigState.CONNECTED:
                return True
            self._state = RigState.CONNECTING

        try:
            if self._net_mode:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect((self._net_host, self._net_port))
                self._sock = sock
            elif HAMLIB_AVAILABLE:
                import Hamlib as _H  # lazy — avoids Qt TLS collision at startup

                self._hamlib = _H
                rot = _H.Rot(self._model_id)
                rot.set_conf("rig_pathname", self._port)
                rot.set_conf("serial_speed", str(self._baud_rate))
                rot.open()
                self._rot = rot
            else:
                self._rot = _MockRotator()

            with self._lock:
                self._state = RigState.CONNECTED
            logger.info("Rotator: connected")
            return True
        except Exception as exc:
            with self._lock:
                self._state = RigState.ERROR
            logger.error("Rotator: connect failed — %s", exc)
            return False

    def disconnect(self) -> None:
        """Disconnect the rotator."""
        try:
            if self._net_mode and self._sock:
                self._sock.close()
            elif self._rot is not None and self._hamlib is not None:
                self._rot.close()
        except Exception:
            pass
        finally:
            self._rot = None
            self._sock = None
            with self._lock:
                self._state = RigState.DISCONNECTED

    def set_position(self, azimuth_deg: float, elevation_deg: float) -> bool:
        """Rotate to the specified azimuth and elevation."""
        if not self.is_connected:
            return False
        try:
            if self._net_mode and self._sock:
                cmd = f"P {azimuth_deg:.1f} {elevation_deg:.1f}\n"
                self._sock.sendall(cmd.encode())
            elif self._rot is not None:
                self._rot.set_position(azimuth_deg, elevation_deg)

            with self._lock:
                self._rotor_state.azimuth_deg = azimuth_deg
                self._rotor_state.elevation_deg = elevation_deg
                self._rotor_state.is_moving = True
            return True
        except Exception as exc:
            logger.error("Rotator.set_position: %s", exc)
            return False

    def get_position(self) -> RotatorState:
        """Return the current azimuth and elevation."""
        if not self.is_connected:
            return RotatorState()
        try:
            if self._net_mode and self._sock:
                self._sock.sendall(b"p\n")
                data = self._sock.recv(256).decode(errors="replace").strip()
                parts = data.split()
                if len(parts) >= 2:
                    az = float(parts[0])
                    el = float(parts[1])
                    with self._lock:
                        self._rotor_state.azimuth_deg = az
                        self._rotor_state.elevation_deg = el
            elif self._rot is not None:
                az, el = self._rot.get_position()
                with self._lock:
                    self._rotor_state.azimuth_deg = float(az)
                    self._rotor_state.elevation_deg = float(el)
        except Exception as exc:
            logger.error("Rotator.get_position: %s", exc)

        with self._lock:
            return RotatorState(
                azimuth_deg=self._rotor_state.azimuth_deg,
                elevation_deg=self._rotor_state.elevation_deg,
                is_moving=self._rotor_state.is_moving,
            )

    def stop(self) -> bool:
        """Stop rotation."""
        if not self.is_connected:
            return False
        try:
            if self._net_mode and self._sock:
                self._sock.sendall(b"S\n")
            elif self._rot is not None:
                self._rot.stop()
            with self._lock:
                self._rotor_state.is_moving = False
            return True
        except Exception as exc:
            logger.error("Rotator.stop: %s", exc)
            return False

    def park(self) -> bool:
        """Return to the home position (rotctld: K command)."""
        if not self.is_connected:
            return False
        try:
            if self._net_mode and self._sock:
                self._sock.sendall(b"K\n")
            elif self._rot is not None:
                self._rot.park()
            return True
        except Exception as exc:
            logger.error("Rotator.park: %s", exc)
            return False


# ---------------------------------------------------------------------------
# HamlibVersionChecker
# ---------------------------------------------------------------------------


class HamlibVersionChecker:
    """
    Fetches the installed Hamlib version and compares it against the latest
    GitHub release, returning a warning when an upgrade is available.
    """

    _GITHUB_API = "https://api.github.com/repos/Hamlib/Hamlib/releases/latest"

    def get_installed_version(self) -> str:
        """Return the installed Hamlib version string, or "not installed" when absent."""
        if HAMLIB_AVAILABLE:
            try:
                import Hamlib as _H

                return str(_H.cvar.hamlib_version)
            except Exception:
                return "unknown"
        return "not installed"

    async def check_version(self, timeout: float = 10.0) -> VersionInfo:
        """
        Check the latest version via the GitHub API and return a VersionInfo.

        When the network is unavailable, returns the installed version only
        with is_outdated=False (no warning).
        """
        installed = self.get_installed_version()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(
                    self._GITHUB_API,
                    headers={"Accept": "application/vnd.github+json"},
                )
                resp.raise_for_status()
                data = resp.json()
                latest = str(data.get("tag_name", "")).lstrip("v")
                release_url = str(data.get("html_url", ""))
        except Exception as exc:
            logger.warning("HamlibVersionChecker: could not fetch latest version — %s", exc)
            return VersionInfo(installed=installed, latest=installed, is_outdated=False)

        is_outdated = installed not in ("not installed", "unknown") and self._version_lt(
            installed, latest
        )
        return VersionInfo(
            installed=installed,
            latest=latest,
            is_outdated=is_outdated,
            release_url=release_url,
        )

    @staticmethod
    def _version_lt(a: str, b: str) -> bool:
        """Return True when version string a is less than b (semantic versioning assumed)."""

        def _parts(v: str) -> tuple[int, ...]:
            parts = []
            for seg in v.split(".")[:3]:
                try:
                    parts.append(int(seg))
                except ValueError:
                    parts.append(0)
            while len(parts) < 3:
                parts.append(0)
            return tuple(parts)

        return _parts(a) < _parts(b)


# ---------------------------------------------------------------------------
# Internal mock classes (for environments without Hamlib)
# ---------------------------------------------------------------------------


class _MockRig:
    """Stub for environments where python-hamlib is unavailable. Used in tests and CI."""

    def __init__(self, model_id: int) -> None:
        self._model_id = model_id
        self._freq: float = 145_800_000.0
        self._mode: int = 32  # RIG_MODE_FM
        self._passband: int = 15000

    class caps:  # noqa: N801
        model_name = "Mock Rig"

    def set_freq(self, vfo: int, freq: float) -> None:
        self._freq = freq

    def get_freq(self, vfo: int) -> float:
        return self._freq

    def set_mode(self, vfo: int, mode: int, passband: int) -> None:
        self._mode = mode
        self._passband = passband

    def get_mode(self, vfo: int) -> tuple[int, int]:
        return self._mode, self._passband

    def set_split_vfo(self, vfo: int, split: int, tx_vfo: int) -> None:
        pass

    def set_split_freq(self, vfo: int, freq: float) -> None:
        pass

    def set_func(self, vfo: int, func: int, status: int) -> None:
        pass

    def set_level(self, vfo: int, level: int, value: int) -> None:
        pass

    def set_vfo(self, vfo: int) -> None:
        pass

    def close(self) -> None:
        pass


class _MockRotator:
    """Rotator stub for environments where python-hamlib is unavailable."""

    def __init__(self) -> None:
        self._az: float = 0.0
        self._el: float = 0.0

    def set_position(self, az: float, el: float) -> None:
        self._az = az
        self._el = el

    def get_position(self) -> tuple[float, float]:
        return self._az, self._el

    def stop(self) -> None:
        pass

    def park(self) -> None:
        pass

    def close(self) -> None:
        pass
