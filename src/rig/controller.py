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
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sdr.device import SdrDevice, SdrDeviceInfo
    from sdr.pipeline import SDRPipeline

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

# Preset CAT command templates for known rigs that need custom CTCSS commands.
# Keyed by ctcss_method value; value is (cat_on_template, cat_off_template).
# {tone:03d} is replaced at send time with the 3-digit CTCSS_TABLE index.
# Defined here so both the dialog (rig_dialog.py) and the loader
# (_load_rig_settings in main_window.py) always use the same authoritative values,
# avoiding stale DB entries after a preset correction.
CTCSS_PRESET_TEMPLATES: dict[str, tuple[str, str]] = {
    # FTX-1: CN P1 P2 P3P3P3; — P1=1 (Sub), P2=0 (CTCSS), P3=tone index 000-049
    "ftx1": ("CN10{tone:03d};CT11;", "CT10;"),
    # FT-991/FT-991A: CN P1 P2 P3P3P3; — P1=0 (fixed), P2=0 (CTCSS), P3=tone index 000-049
    # CT P1 P2; — P1=0 (fixed), P2=2 (CTCSS ENC only); CT00; to disable
    "ft991": ("CN00{tone:03d};CT02;", "CT00;"),
}

# CTCSS tone frequency (Hz) → rig index used in custom CAT commands.
# Covers the standard 50-tone table; gaps are intentional (some tone numbers
# are omitted from the FTX-1F documentation).
CTCSS_TABLE: dict[float, int] = {
    67.0: 0,
    69.3: 1,
    71.9: 2,
    74.4: 3,
    77.0: 4,
    79.7: 5,
    82.5: 6,
    85.4: 7,
    88.5: 8,
    91.5: 9,
    94.8: 10,
    97.4: 11,
    100.0: 12,
    103.5: 13,
    107.2: 14,
    110.9: 15,
    114.8: 16,
    118.8: 17,
    123.0: 18,
    127.3: 19,
    131.8: 20,
    136.5: 21,
    141.3: 22,
    146.2: 23,
    151.4: 24,
    156.7: 25,
    159.8: 26,
    162.2: 27,
    165.5: 28,
    167.9: 29,
    171.3: 30,
    173.8: 31,
    177.3: 32,
    183.5: 34,
    186.2: 35,
    189.9: 36,
    192.8: 37,
    196.6: 38,
    199.5: 39,
    203.5: 40,
    206.5: 41,
    210.7: 42,
    218.1: 43,
    225.7: 44,
    229.1: 45,
    233.6: 46,
    241.8: 47,
    250.3: 48,
    254.1: 49,
}


# ---------------------------------------------------------------------------
# Icom satmode rig identifiers
# ---------------------------------------------------------------------------
# Direct mode: model IDs used by HamlibDirectController._satmode
_SATMODE_RIG_IDS: frozenset[int] = frozenset(
    [
        3081,  # IC-9700  (rigctl -l verified 2026-06-15)
        3068,  # IC-9100  (rigctl -l verified 2026-06-15)
        3044,  # IC-910   (rigctl -l verified 2026-06-15)
        3034,  # IC-821H  (rigctl -l verified 2026-06-15)
    ]
)

# NET mode: rigctld reports the connected rig name via the _ command.
# HamlibNetController queries this at connect time to auto-detect satmode rigs
# without requiring any user configuration.
_SATMODE_RIG_NAMES: frozenset[str] = frozenset(
    [
        "IC-9700",
        "IC-9100",
        "IC-910",
        "IC-821H",
    ]
)

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

    # -- Custom CAT CTCSS --

    def send_ctcss_cat(  # noqa: B027
        self,
        tone_hz: float,
        cat_on_template: str,
        cat_off_template: str,
    ) -> None:
        """Send a custom CAT CTCSS command bypassing Hamlib's CTCSS API.

        Looks up tone_hz in CTCSS_TABLE to get the rig index, then formats
        cat_on_template with {tone=index} and splits on ';' to send each
        sub-command individually.  Sends cat_off_template when tone_hz <= 0
        or the tone is not in CTCSS_TABLE.  Default implementation is a no-op;
        subclasses override to send via their transport layer.
        """

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
        civ_addr: str = "",
    ) -> None:
        """
        Args:
            model_id:  Hamlib rig model ID (e.g. IC-9700 = 3081)
            port:      Serial port ("/dev/ttyUSB0", "COM3", etc.)
            baud_rate: Baud rate
            data_bits: Data bits
            stop_bits: Stop bits
            handshake: Flow control ("None", "XONXOFF", "Hardware")
            civ_addr:  CI-V address override for Icom rigs (e.g. "0x65").
                       Empty string uses Hamlib's default for the model.
        """
        super().__init__()
        self._model_id = model_id
        self._port = port
        self._baud_rate = baud_rate
        self._data_bits = data_bits
        self._stop_bits = stop_bits
        self._handshake = handshake
        self._civ_addr = civ_addr.strip()
        self._rig: Any = None  # Hamlib.Rig instance or _MockRig
        self._hamlib: Any = None  # Hamlib module, set lazily in connect()
        self._last_dl_hz: float | None = None
        self._last_ul_hz: float | None = None
        self._last_ul_update_time: float = 0.0
        self._ptt_active: bool = False
        self._satmode: bool = model_id in _SATMODE_RIG_IDS
        # True while IC-9100/9700 satmode is actually active on the rig.
        # Dynamically toggled: same-band pairs (V/V, U/U) use normal split
        # because IC-9100 satmode always assigns Main/Sub to different bands.
        self._satmode_active: bool = False
        self._current_dl_mode: str = ""  # updated by send_mode_only; drives UL threshold

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
                if self._civ_addr:
                    # Ensure hex prefix so Hamlib parses it correctly.
                    # Users enter what the rig menu shows (e.g. "65"); without
                    # "0x", strtol() would interpret it as decimal 65 (= 0x41).
                    addr = self._civ_addr
                    if not addr.lower().startswith("0x"):
                        addr = "0x" + addr
                    rig.set_conf("civaddr", addr)
                    logger.info("RigDirect: CI-V address set to %s", addr)
                rig.open()
                self._rig = rig
            else:
                self._rig = _MockRig(self._model_id)

            self._last_dl_hz = None
            self._last_ul_hz = None
            self._last_ul_update_time = 0.0
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
            # Python Hamlib binding: set_mode(mode, passband[, vfo]) — vfo is last
            self._rig.set_mode(hamlib_mode, passband_hz, hamlib_vfo)
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
            # In satmode the current VFO is Main (RX). CTCSS must be set on the
            # TX side, which is Sub (RIG_VFO_SUB_A) in satmode and VFO_CURR otherwise.
            if self._satmode and self._satmode_active:
                tx_vfo = int(self._hamlib.RIG_VFO_SUB_A)
            else:
                tx_vfo = self._hamlib.RIG_VFO_CURR
            logger.info(
                "RigDirect.set_ctcss_tone: tone_hz=%.1f tone_int=%d satmode=%s tx_vfo=%s",
                tone_hz,
                tone_int,
                self._satmode_active,
                tx_vfo,
            )
            if tone_hz > 0:
                self._rig.set_func(tx_vfo, self._hamlib.RIG_FUNC_TONE, 1)
                self._rig.set_level(
                    tx_vfo,
                    self._hamlib.RIG_LEVEL_CTCSS_TONE,
                    tone_int,
                )
            else:
                self._rig.set_func(tx_vfo, self._hamlib.RIG_FUNC_TONE, 0)
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
        """Set DL and UL frequencies with 1 Hz delta suppression.

        Icom satmode rigs (IC-9700 etc.) use RIG_VFO_MAIN for both set_freq
        and set_split_freq; the firmware routes DL→Main and UL→Sub internally.
        Generic rigs use VFOA for DL and set_split_freq for UL (VFOB/split TX).
        Skips the command when the frequency has not changed by 1 Hz or more,
        or when the argument is None.
        """
        if not self.is_connected or self._rig is None:
            return False
        if self._ptt_active:
            return True
        try:
            if self._satmode:
                # IC-9100/9700 satmode: satmode routes Main=RX(DL) and Sub=TX(UL).
                # RIG_VFO_SUB_A (0x00200000) bypasses vfo_fixup so ic9700_set_vfo
                # sends CI-V 07 d1 (Sub Band select) rather than 07 01 (VFO-B of
                # current band) — the latter was the root cause of Sub stuck at 7 MHz.
                #
                # IC-9100 hardware constraint: satmode ALWAYS assigns Main and Sub to
                # DIFFERENT bands.  Same-band satmode (V/V FM, ISS APRS etc.) is not
                # supported by IC-9100 firmware — the rig forces Sub to the opposite
                # band.  For same-band pairs we fall back to conventional VFO-A/B split
                # to get correct frequencies (display alternates during UL updates but
                # at most every 5 s, which is acceptable).
                _H = self._hamlib
                curr_vfo = int(_H.RIG_VFO_CURR)
                sub_vfo = int(_H.RIG_VFO_SUB_A)
                rx_vfo = self._vfo_str_to_const("VFOA")

                # Detect same-band: when DL and UL are in the same frequency band
                # (both VHF, both UHF, etc.) satmode cannot work correctly.
                _is_same_band = (
                    vfoa_hz is not None
                    and vfob_hz is not None
                    and self._freq_band(vfoa_hz) == self._freq_band(vfob_hz)
                )

                if _is_same_band:
                    # Same-band fallback: exit satmode once and use VFO-A/B split.
                    if self._satmode_active:
                        self._satmode_exit()
                    tx_vfo = self._vfo_str_to_const("VFOB")
                    if vfoa_hz is not None:
                        last_dl = self._last_dl_hz
                        if last_dl is None or abs(vfoa_hz - last_dl) >= 1.0:
                            self._rig.set_freq(rx_vfo, int(vfoa_hz))
                            self._last_dl_hz = vfoa_hz
                    if vfob_hz is not None:
                        last_ul = self._last_ul_hz
                        now = time.monotonic()
                        elapsed = now - self._last_ul_update_time
                        is_fm = self._current_dl_mode in ("FM", "AFSK", "DIGITALVOICE")
                        # FM same-band split: VFO-B switch causes display flicker on
                        # IC-9100.  FM/AFSK capture range (±5 kHz) exceeds ISS max
                        # Doppler (±3.5 kHz at 145 MHz), so infrequent UL updates are
                        # fine.  2 kHz threshold + 60 s ceiling minimises flicker while
                        # keeping UL within the capture range throughout the pass.
                        _UL_THRESH = 2000.0 if is_fm else 20.0
                        _UL_MAX_S = 60.0 if is_fm else 15.0
                        if (
                            last_ul is None
                            or abs(vfob_hz - last_ul) >= _UL_THRESH
                            or elapsed >= _UL_MAX_S
                        ):
                            # Use set_freq(VFOB) instead of set_split_freq: Hamlib's
                            # set_split_freq checks an internal tx_freq cache populated
                            # by set_split_vfo and may skip the actual CI-V command
                            # ("freq set not needed") even when VFO-B on the rig still
                            # holds a stale value from a previous session.
                            logger.info("RigDirect same-band UL: set_freq(VFOB, %d)", int(vfob_hz))
                            self._rig.set_freq(tx_vfo, int(vfob_hz))
                            self._last_ul_hz = vfob_hz
                            self._last_ul_update_time = now
                else:
                    # Cross-band: use satmode (IC-9100/9700 firmware routing).
                    # Reinit satmode when first connecting or when DL band changes.
                    if vfoa_hz is not None:
                        last_dl = self._last_dl_hz
                        if last_dl is None or self._freq_band(vfoa_hz) != self._freq_band(last_dl):
                            self._satmode_enter(vfoa_hz)
                        elif abs(vfoa_hz - last_dl) >= 1.0:
                            logger.info("RigDirect satmode DL: set_freq(CURR, %d)", int(vfoa_hz))
                            self._rig.set_freq(curr_vfo, int(vfoa_hz))
                            self._last_dl_hz = vfoa_hz

                    if vfob_hz is None:
                        logger.debug(
                            "RigDirect satmode: vfob_hz is None — no uplink defined, UL skipped"
                        )
                    else:
                        last_ul = self._last_ul_hz
                        now = time.monotonic()
                        elapsed = now - self._last_ul_update_time
                        is_fm = self._current_dl_mode in ("FM", "DIGITALVOICE")
                        _UL_THRESH = 10.0 if is_fm else 20.0
                        _UL_MAX_S = 5.0 if is_fm else 15.0
                        if (
                            last_ul is None
                            or abs(vfob_hz - last_ul) >= _UL_THRESH
                            or elapsed >= _UL_MAX_S
                        ):
                            logger.info("RigDirect satmode UL: set_freq(Sub, %d)", int(vfob_hz))
                            self._rig.set_freq(sub_vfo, int(vfob_hz))
                            self._last_ul_hz = vfob_hz
                            self._last_ul_update_time = now
            else:
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
        """Set mode on the DL (RX) and UL (TX) VFOs.

        Opens a dedicated short-lived serial connection so that the mode can be
        set even when the main tracking connection has already been disconnected
        — mirroring HamlibNetController which opens a fresh TCP socket per call.
        Icom satmode rigs use RIG_VFO_MAIN/SUB; generic rigs use RIG_VFO_A/B.
        Silently ignores all errors (best-effort).

        Icom satmode rigs use RIG_VFO_MAIN for DL and RIG_VFO_SUB for UL;
        generic rigs use RIG_VFO_A and RIG_VFO_B respectively.
        """
        self._current_dl_mode = dl_mode
        logger.info("RigDirect: send_mode_only dl=%s ul=%s", dl_mode, ul_mode)
        if not HAMLIB_AVAILABLE:
            return
        rig: Any = None
        try:
            import Hamlib as _H  # lazy — avoids Qt TLS collision at startup

            # Build mode map from real Hamlib constants (available after import).
            # Python binding: set_mode(mode, passband[, vfo]) — vfo is the last arg.
            hamlib_mode: dict[str, int] = {
                "FM": _H.RIG_MODE_FM,
                "DIGITALVOICE": _H.RIG_MODE_FM,
                "USB": _H.RIG_MODE_USB,
                "SSB": _H.RIG_MODE_USB,
                "LSB": _H.RIG_MODE_LSB,
                "CW": _H.RIG_MODE_CW,
                "CW-R": _H.RIG_MODE_CWR,
                "AM": _H.RIG_MODE_AM,
                # AFSK (e.g. APRS) is carried over FM; PKTFM is not universally
                # supported (IC-9100 ignores it and leaves the rig in the previous
                # mode).  Plain FM is the correct receiver mode for APRS monitoring.
                "AFSK": _H.RIG_MODE_FM,
                "BPSK": _H.RIG_MODE_PKTUSB,
            }
            dl_hamlib = hamlib_mode.get(dl_mode, _H.RIG_MODE_FM)
            ul_hamlib = hamlib_mode.get(ul_mode, _H.RIG_MODE_FM)
            # For satmode rigs: use Main/Sub VFOs only while satmode is active
            # (cross-band operation).  When satmode has been exited (same-band
            # duplex path called _satmode_exit), use VFOA/VFOB so that mode is
            # set on the correct split VFOs.  Using _satmode_active avoids the
            # earlier _last_ul_hz=None race that selected wrong VFOs on the very
            # first mode-set call after connect.
            _use_satmode_vfo = self._satmode and self._satmode_active
            dl_vfo = _H.RIG_VFO_MAIN if _use_satmode_vfo else _H.RIG_VFO_A
            # RIG_VFO_SUB (0x02000000) is remapped to VFOB by vfo_fixup →
            # ic9700_set_vfo sends CI-V 07 01 (VFO-B of Main band) instead of
            # 07 d1 (Sub Band select).  Use RIG_VFO_SUB_A (0x00200000) which
            # bypasses vfo_fixup → CI-V 07 d1 → correct Sub Band mode setting.
            ul_vfo = int(_H.RIG_VFO_SUB_A) if _use_satmode_vfo else _H.RIG_VFO_B
            rig = _H.Rig(self._model_id)
            rig.set_conf("rig_pathname", self._port)
            rig.set_conf("serial_speed", str(self._baud_rate))
            if self._civ_addr:
                addr = self._civ_addr
                if not addr.lower().startswith("0x"):
                    addr = "0x" + addr
                rig.set_conf("civaddr", addr)
            rig.open()
            rig.set_mode(dl_hamlib, 0, dl_vfo)
            rig.set_mode(ul_hamlib, 0, ul_vfo)
            logger.info("RigDirect: send_mode_only done")
        except Exception as exc:
            logger.error("RigDirect.send_mode_only: %s", exc)
        finally:
            if rig is not None:
                with contextlib.suppress(Exception):
                    rig.close()

    def send_ctcss_cat(
        self,
        tone_hz: float,
        cat_on_template: str,
        cat_off_template: str,
    ) -> None:
        """Send a custom CTCSS CAT command directly to the serial port.

        Writes each ';'-separated sub-command to the serial device using a
        direct file write (equivalent to printf '...' > /dev/FTX1CAT).
        Silently ignores errors (best-effort).
        """
        if tone_hz > 0 and cat_on_template:
            tone_number = CTCSS_TABLE.get(tone_hz)
            if tone_number is None:
                logger.warning("RigDirect.send_ctcss_cat: %.1f Hz not in CTCSS_TABLE", tone_hz)
                return
            template = cat_on_template.format(tone=tone_number)
        elif cat_off_template:
            template = cat_off_template
        else:
            return
        logger.info("RigDirect: send_ctcss_cat template=%r port=%s", template, self._port)
        for sub in template.split(";"):
            sub = sub.strip()
            if not sub:
                continue
            raw = (sub + ";").encode()
            try:
                fd = os.open(self._port, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
                try:
                    os.write(fd, raw)
                finally:
                    os.close(fd)
            except OSError as exc:
                logger.error("RigDirect.send_ctcss_cat write(%r): %s", raw, exc)

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

    @staticmethod
    def _freq_band(hz: float) -> str:
        """Return a coarse band label used for satmode band-change detection."""
        if hz < 200e6:
            return "VHF"
        if hz < 500e6:
            return "UHF"
        return "SHF"

    def _satmode_enter(self, dl_hz: float) -> None:
        """Enable satmode with Main on the band of *dl_hz*.

        Sequences: satmode OFF → set_freq(CURR, dl_hz) [band switch] → satmode ON.
        Resets last-frequency state so the next tick re-sends both DL and UL.
        """
        if self._rig is None or self._hamlib is None:
            return
        _H = self._hamlib
        if not hasattr(_H, "RIG_FUNC_SATMODE"):
            return
        vfo_curr = int(_H.RIG_VFO_CURR)
        logger.info(
            "RigDirect: entering satmode, Main → %s (%.3f MHz)",
            self._freq_band(dl_hz),
            dl_hz / 1e6,
        )
        try:
            self._rig.set_func(vfo_curr, _H.RIG_FUNC_SATMODE, 0)
            self._rig.set_freq(vfo_curr, int(dl_hz))
            self._rig.set_func(vfo_curr, _H.RIG_FUNC_SATMODE, 1)
            self._satmode_active = True
            # Record the DL frequency so the next tick does NOT re-trigger
            # _satmode_enter (last_dl is None would loop infinitely).
            self._last_dl_hz = dl_hz
        except Exception as exc:
            logger.warning("RigDirect: _satmode_enter failed — %s", exc)
        finally:
            self._last_ul_hz = None
            self._last_ul_update_time = 0.0

    def _satmode_exit(self) -> None:
        """Disable satmode and enable normal VFO-A/B split (same-band duplex).

        IC-9100 satmode always assigns Main and Sub to *different* bands, so
        same-band pairs (V/V FM, U/U) must use conventional split instead.
        """
        if self._rig is None or self._hamlib is None:
            return
        _H = self._hamlib
        if not hasattr(_H, "RIG_FUNC_SATMODE"):
            return
        vfo_curr = int(_H.RIG_VFO_CURR)
        rx_vfo = self._vfo_str_to_const("VFOA")
        tx_vfo = self._vfo_str_to_const("VFOB")
        logger.info("RigDirect: exiting satmode → normal VFO-A/B split (same-band)")
        try:
            self._rig.set_func(vfo_curr, _H.RIG_FUNC_SATMODE, 0)
            self._rig.set_split_vfo(rx_vfo, 1, tx_vfo)
            self._satmode_active = False
        except Exception as exc:
            logger.warning("RigDirect: _satmode_exit failed — %s", exc)
        finally:
            self._last_dl_hz = None
            self._last_ul_hz = None
            self._last_ul_update_time = 0.0

    def _init_split(self) -> None:
        """Enable split/satmode. Called once at connect.

        Icom satmode rigs (IC-9700 etc.): rig.set_func(RIG_FUNC_SATMODE, 1)
        sends the correct CI-V write frame (fe fe a2 e0 16 59 01 fd) via
        icom_set_func().  set_conf("satmode", "1") only sets an internal Hamlib
        flag and does NOT send a CI-V command; set_split_vfo(MAIN, 1, MAIN)
        only generates a CI-V read query (16 59 fd) — both are wrong.
        Falls back to set_conf if RIG_FUNC_SATMODE is not in the binding.
        Generic rigs: conventional VFOA/VFOB split via set_split_vfo.
        """
        if self._rig is None:
            return
        try:
            if self._satmode:
                _H = self._hamlib
                if _H is not None and hasattr(_H, "RIG_FUNC_SATMODE"):
                    vfo_curr = int(_H.RIG_VFO_CURR)
                    self._rig.set_func(vfo_curr, _H.RIG_FUNC_SATMODE, 1)
                    self._satmode_active = True
                    logger.info(
                        "RigDirect: satmode ON via set_func(RIG_FUNC_SATMODE) "
                        "(CI-V 16 59 01 fd sent)"
                    )
            else:
                rx_vfo = self._vfo_str_to_const("VFOA")
                tx_vfo = self._vfo_str_to_const("VFOB")
                self._rig.set_split_vfo(rx_vfo, 1, tx_vfo)
                logger.info("RigDirect: split enabled (RX=VFOA, TX=VFOB)")
        except Exception as exc:
            logger.warning("RigDirect: _init_split failed — %s", exc)

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

# FT-991/FT-991A CAT mode codes for the MD command (e.g. MD02; = USB on VFO-A).
_FT991_MODE_MAP: dict[str, str] = {
    "LSB": "1",
    "USB": "2",
    "CW": "3",
    "FM": "4",
    "AM": "5",
    "CW-R": "7",
    "FM-N": "B",
}


class HamlibNetController(RigController):
    """
    Transceiver controller that connects to rigctld over TCP.

    Compatible with GPredict NET Control mode — works with any existing
    rigctld setup. Uses the rigctld newline-delimited text protocol.
    """

    _TIMEOUT = 10.0  # seconds — allows for slow CAT backends such as FTX-1

    def __init__(
        self,
        host: str = "localhost",
        port: int = 4532,
        radio_type: str = "full_duplex",
        direct_cat_port: str = "",
        direct_cat_baud: int = 38400,
        ctcss_method: str = "hamlib",
    ) -> None:
        """
        Args:
            host:             Host where rigctld is running
            port:             rigctld port number (default 4532)
            radio_type:       "full_duplex"=send both F and I (default) /
                              "rx_only"=F only / "tx_only"=I only
            direct_cat_port:  Serial port for direct CAT (bypasses rigctld w cmd).
                              Empty string disables direct CAT (uses rigctld).
            direct_cat_baud:  Baud rate for direct_cat_port (default 38400)
            ctcss_method:     CTCSS method key ("hamlib", "ftx1", "ft991", "custom_cat").
                              Used to select the mode-setting strategy in send_mode_only().
        """
        super().__init__()
        self._host = host
        self._port = port
        self._radio_type = radio_type
        self._direct_port = direct_cat_port
        self._direct_baud = direct_cat_baud
        self._ctcss_method = ctcss_method
        self._sock: socket.socket | None = None
        self._vfo_mode: bool = False
        self._cmd_lock = threading.Lock()  # serialise send+recv to prevent response misalignment
        self._cached_model_name: str = ""  # fetched once on connect and cached
        self._satmode: bool = False  # set in connect() after querying rig name via _
        self._last_dl_hz: float | None = None  # None = just connected; forces the first F/I send
        self._last_ul_hz: float | None = None

    # -- Connection management --

    @property
    def is_satmode(self) -> bool:
        """True when the connected rig uses satmode (e.g. IC-9700)."""
        return self._satmode

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
            # Query rig name via the _ command (after S 1 Main, so satmode is already
            # active on the rig).  _fetch_model_name() uses a raw socket operation that
            # does not go through _cmd_lock, so it is safe to call here without holding
            # any lock.  The result is used to auto-detect satmode rigs: for these rigs
            # send_mode_only() must be deferred until after connect() so that satmode is
            # active before the V Sub / M commands are issued.
            self._cached_model_name = self._fetch_model_name()
            self._satmode = self._cached_model_name in _SATMODE_RIG_NAMES
            if self._satmode:
                logger.info("RigNet: satmode rig detected (%s)", self._cached_model_name)
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

        rigctld responds to _ with a multi-line block, e.g.:
            Model name:\tIC-9700
            Mfg name:\tIcom
            ...
            RPRT 0

        We extract the value after "Model name:" so the result matches the
        strings in _SATMODE_RIG_NAMES ("IC-9700" etc.).  Falls back to
        "host:port" if the command is unsupported or times out.
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
            resp = data.decode(errors="replace")
            for line in resp.splitlines():
                line = line.strip()
                if line.lower().startswith("model name"):
                    # "Model name:\tIC-9700" → "IC-9700"
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        name = parts[1].strip()
                        logger.info("RigNet: rigctld rig model = %r", name)
                        return name
            # Fallback: return first non-RPRT line (old single-line rigctld behaviour)
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

    def _send_cat_direct(self, cmd: str) -> None:
        """Send a single CAT command directly to the serial port, bypassing rigctld.

        Used when _direct_port is configured and rigctld's w command is unreliable
        for the connected rig (e.g. FT-991 CTCSS commands).
        Silently ignores all errors (best-effort).
        """
        if not self._direct_port:
            return
        try:
            import serial  # pyserial — optional dependency

            with serial.Serial(self._direct_port, self._direct_baud, timeout=0.5) as s:
                s.write(cmd.encode())
                time.sleep(0.1)
        except Exception as exc:
            logger.warning("RigNet: direct CAT failed: %s", exc)

    def send_ctcss_cat(
        self,
        tone_hz: float,
        cat_on_template: str,
        cat_off_template: str,
    ) -> None:
        """Send a custom CTCSS CAT command via a fresh TCP connection to rigctld.

        Opens an independent socket (same pattern as send_mode_only()) so this
        works regardless of the main connection state — _send_mode_only_to_rig()
        disconnects the main socket before calling send_mode_only(), which would
        leave self._sock=None and silently discard commands sent via _cmd().

        Each ';'-separated sub-command is wrapped as 'w <part>;' and forwarded
        verbatim to the rig's serial port by rigctld.
        """
        if tone_hz > 0 and cat_on_template:
            tone_number = CTCSS_TABLE.get(tone_hz)
            if tone_number is None:
                logger.warning("RigNet.send_ctcss_cat: %.1f Hz not in CTCSS_TABLE", tone_hz)
                return
            template = cat_on_template.format(tone=tone_number)
        elif cat_off_template:
            template = cat_off_template
        else:
            return
        parts = [p.strip() for p in template.split(";") if p.strip()]
        if not parts:
            return
        logger.info(
            "RigNet.send_ctcss_cat: tone_hz=%s cmd=%r direct=%r",
            tone_hz,
            template,
            bool(self._direct_port),
        )
        if self._direct_port:
            for part in parts:
                self._send_cat_direct(f"{part};")
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self._TIMEOUT)
            sock.connect((self._host, self._port))
            # Use a short recv timeout: the w command may return "?;" (rig CAT error),
            # empty string, or RPRT 0. We only care that the bytes were sent, not the
            # rig's response, so drain the buffer without blocking on slow rigs.
            sock.settimeout(1.0)
            for part in parts:
                cmd = f"w {part};"
                logger.info("RigNet.send_ctcss_cat: sending %r", cmd)
                sock.sendall((cmd + "\n").encode())
                with contextlib.suppress(OSError):
                    sock.recv(256)
            sock.close()
        except Exception as exc:
            logger.error("RigNet.send_ctcss_cat: %s", exc)

    def send_mode_only(self, dl_mode: str, ul_mode: str) -> None:
        """Set mode on both VFOs via an independent TCP connection.

        FT-991/FT-991A path (ctcss_method == "ft991"):
          Opens a fresh independent socket; main connection is kept alive.
          MD0{code};           — set VFO-A (DL) mode via rigctld w command
          SV; MD0{code}; SV;  — swap to VFO-B, set UL mode, swap back
          Each command waits for RPRT in the response before proceeding.
          2-second per-command timeout (SV may not return RPRT on some firmwares).

        FTX-1F / generic path:
          Opens a fresh socket (main socket disconnected by the caller).
          V Sub → M {ul} 0 → V Main → M {dl} 0
          On S 1 Main split: Sub=TX (uplink), Main=RX (downlink).
        """
        if self._ctcss_method == "ft991":
            dl_code = _FT991_MODE_MAP.get(dl_mode)
            ul_code = _FT991_MODE_MAP.get(ul_mode)
            if not dl_code and not ul_code:
                return
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(self._TIMEOUT)
                sock.connect((self._host, self._port))
                sock.settimeout(2.0)  # short per-command timeout; SV may not send RPRT

                def _w(cmd: str) -> None:
                    sock.sendall(f"w {cmd}\n".encode())
                    buf = b""
                    with contextlib.suppress(OSError):
                        while b"RPRT" not in buf:
                            chunk = sock.recv(256)
                            if not chunk:
                                break
                            buf += chunk

                if dl_code:
                    _w(f"MD0{dl_code};")
                if ul_code:
                    _w("SV;")
                    _w(f"MD0{ul_code};")
                    _w("SV;")
                sock.close()
                logger.info("RigNet: FT-991 mode dl=%s ul=%s", dl_mode, ul_mode)
            except Exception as exc:
                logger.warning("RigNet: FT-991 mode send failed: %s", exc)
            return
        rigctld_ul = _SATNOGS_TO_RIGCTLD_MODE.get(ul_mode)
        rigctld_dl = _SATNOGS_TO_RIGCTLD_MODE.get(dl_mode)
        if not rigctld_ul and not rigctld_dl:
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self._TIMEOUT)
            sock.connect((self._host, self._port))

            def _send_recv(cmd: str) -> str:
                sock.sendall((cmd + "\n").encode())
                buf = b""
                with contextlib.suppress(OSError):
                    while b"RPRT" not in buf:
                        chunk = sock.recv(256)
                        if not chunk:
                            break
                        buf += chunk
                resp = buf.decode(errors="replace").strip()
                if resp and "RPRT 0" not in resp:
                    logger.warning("RigNet.send_mode_only: %r -> %r", cmd, resp)
                return resp

            if self._vfo_mode:
                # Extended rigctld protocol: VFO is specified inline — no active-VFO
                # switch needed.  This works correctly for IC-9700 satmode where the
                # V command may be rejected while satmode is active.
                if rigctld_ul:
                    _send_recv(f"\\set_mode Sub {rigctld_ul} 0")
                if rigctld_dl:
                    _send_recv(f"\\set_mode Main {rigctld_dl} 0")
            else:
                # Legacy rigctld protocol: switch active VFO then set mode.
                if rigctld_ul:
                    _send_recv("V Sub")
                    _send_recv(f"M {rigctld_ul} 0")
                if rigctld_dl:
                    _send_recv("V Main")
                    _send_recv(f"M {rigctld_dl} 0")

            sock.close()
            logger.info("RigNet: send_mode_only dl=%s ul=%s done", dl_mode, ul_mode)
        except Exception as exc:
            logger.warning("RigNet: send_mode_only failed: %s", exc)

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

    _CATCH_UP_THRESHOLD: float = 5.0  # degrees; switch to normal tracking when within this
    _CATCH_UP_TIMEOUT: float = 60.0  # seconds; resend P command if catch-up takes too long

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
        self._last_az: float | None = None  # last commanded AZ for shortest-path calc
        self._catching_up: bool = False  # True while rotator is moving to initial position
        self._catch_up_start_time: float | None = None  # monotonic time when catch-up started

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
                logger.info(
                    "Rotator: creating controller port=%s model=%s",
                    self._port,
                    self._model_id,
                )
                rot.set_conf("rot_pathname", self._port)
                rot.set_conf("serial_speed", str(self._baud_rate))
                rot.open()
                self._rot = rot
            else:
                self._rot = _MockRotator()

            with self._lock:
                self._state = RigState.CONNECTED
            self._last_az = None
            self._catching_up = False
            self._catch_up_start_time = None
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

    def _send_p(self, az: float, el: float) -> None:
        """Send the P command and discard the RPRT response to keep the socket buffer clean."""
        if self._net_mode and self._sock:
            self._sock.sendall(f"P {az:.1f} {el:.1f}\n".encode())
            with contextlib.suppress(Exception):
                self._sock.recv(256)  # discard RPRT 0
        elif self._rot is not None:
            self._rot.set_position(az, el)
        with self._lock:
            self._rotor_state.azimuth_deg = az
            self._rotor_state.elevation_deg = el
            self._rotor_state.is_moving = True

    def set_position(self, azimuth_deg: float, elevation_deg: float) -> bool:
        """Rotate to the specified azimuth and elevation.

        Four phases:
        1. First call after connect (_last_az is None): send P command to current
           satellite position and enter catch-up mode.
        2. Catch-up mode: poll the rotator position each cycle.
           - Within _CATCH_UP_THRESHOLD degrees: exit catch-up, start normal tracking.
           - Timeout (_CATCH_UP_TIMEOUT seconds): resend P command and restart timer.
           - Otherwise: return and wait for the next cycle.
        3. Normal tracking: send P command with current satellite AZ/EL each cycle.
        4. 0-degree wrap (large AZ jump): re-enter catch-up and send P immediately.
        """
        if not self.is_connected:
            return False
        try:
            el_cmd = max(0.0, min(90.0, elevation_deg))

            if self._last_az is None:
                self._send_p(azimuth_deg, el_cmd)
                self._catching_up = True
                self._catch_up_start_time = time.monotonic()
                self._last_az = azimuth_deg
                logger.info("Rotator: initial jump to az=%.1f el=%.1f", azimuth_deg, el_cmd)
                return True

            if self._catching_up:
                current = self.get_position()
                rot_az = current.azimuth_deg
                sat_az = azimuth_deg

                az_diff = abs(rot_az - sat_az)
                if az_diff > 180:
                    az_diff = 360.0 - az_diff

                if az_diff <= self._CATCH_UP_THRESHOLD:
                    self._catching_up = False
                    self._catch_up_start_time = None
                    logger.info("Rotator: caught up at rot=%.1f sat=%.1f", rot_az, sat_az)
                    # Fall through to normal tracking below
                elif time.monotonic() - (self._catch_up_start_time or 0.0) > self._CATCH_UP_TIMEOUT:
                    self._send_p(azimuth_deg, el_cmd)
                    self._catch_up_start_time = time.monotonic()
                    self._last_az = azimuth_deg
                    logger.info("Rotator: catch-up timeout, retrying az=%.1f", azimuth_deg)
                    return True
                else:
                    return True  # Still waiting for rotator to reach target

            last = self._last_az
            crossed_zero = (last > 270 and azimuth_deg < 90) or (last < 90 and azimuth_deg > 270)

            if crossed_zero:
                self._catching_up = True
                self._catch_up_start_time = time.monotonic()
                self._last_az = azimuth_deg
                self._send_p(azimuth_deg, el_cmd)
                logger.info(
                    "Rotator: 0-degree wrap %.1f->%.1f, re-entering catch-up",
                    last,
                    azimuth_deg,
                )
                return True

            self._last_az = azimuth_deg
            self._send_p(azimuth_deg, el_cmd)
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
                data = self._sock.recv(512).decode(errors="replace")
                values: list[float] = []
                for line in data.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("RPRT"):
                        with contextlib.suppress(ValueError):
                            values.append(float(line))
                if len(values) >= 2:
                    with self._lock:
                        self._rotor_state.azimuth_deg = values[0]
                        self._rotor_state.elevation_deg = values[1]
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
# SdrRigAdapter — wraps an SdrDevice as a RigController slot
# ---------------------------------------------------------------------------


class SdrRigAdapter(RigController):
    """
    Adapter that presents an SDR device as a Rig 1 / Rig 2 controller.

    The SDR does not transmit, so set_mode / set_ctcss_tone / set_dcs_code are
    no-ops.  set_frequency / set_vfo_frequencies update the SDR center frequency
    so the Doppler-correction loop drives the SDR tuning.

    is_sdr = True lets the UI distinguish SDR slots from Hamlib rigs.
    """

    is_sdr: bool = True

    def __init__(self) -> None:
        super().__init__()
        # Lazily imported to avoid loading SoapySDR at startup
        self._sdr_device: SdrDevice | None = None
        self._pipeline: SDRPipeline | None = None
        self._device_info: SdrDeviceInfo | None = None
        # Audio params applied after open()
        self._sample_rate_hz: float = 2_400_000
        self._ppm: float = 0.0
        self._gain_auto: bool = True
        self._gain_db: float = 40.0
        self._bias_tee: bool = False

    def set_device_info(self, info: SdrDeviceInfo) -> None:
        """Attach an SdrDeviceInfo before calling connect()."""
        self._device_info = info

    def set_audio_params(
        self,
        sample_rate_hz: float = 2_400_000,
        ppm: float = 0.0,
        gain_auto: bool = True,
        gain_db: float = 40.0,
        bias_tee: bool = False,
    ) -> None:
        """Store sample rate, PPM correction, gain and Bias-T settings applied on connect()."""
        self._sample_rate_hz = sample_rate_hz
        self._ppm = ppm
        self._gain_auto = gain_auto
        self._gain_db = gain_db
        self._bias_tee = bias_tee

    def attach_pipeline(self, pipeline: SDRPipeline) -> None:
        """Attach a running SDRPipeline (set after connect succeeds)."""
        self._pipeline = pipeline

    def connect(self) -> bool:
        """Open the SoapySDR device. Returns True on success."""
        if self._device_info is None:
            logger.warning("SdrRigAdapter: no device_info set")
            return False
        try:
            from sdr.device import SdrDevice

            dev = SdrDevice(self._device_info)
            logger.info(
                "SdrRigAdapter.connect: sample_rate=%.0f ppm=%g gain_auto=%s "
                "gain_db=%g bias_tee=%s",
                self._sample_rate_hz,
                self._ppm,
                self._gain_auto,
                self._gain_db,
                self._bias_tee,
            )
            if dev.open():
                # Apply stored audio settings immediately after open
                dev.set_sample_rate(self._sample_rate_hz)
                dev.set_ppm(self._ppm)
                if self._gain_auto:
                    dev.set_gain_auto()
                else:
                    dev.set_gain_db(self._gain_db)
                dev.set_bias_tee(self._bias_tee)
                self._sdr_device = dev
                with self._lock:
                    self._state = RigState.CONNECTED
                logger.info("SDR connected: %s", self._device_info.display_name)
                return True
        except Exception:
            logger.exception("SdrRigAdapter.connect failed")
        with self._lock:
            self._state = RigState.ERROR
        return False

    def disconnect(self) -> None:
        """Stop the pipeline and close the device."""
        if self._pipeline is not None:
            try:
                self._pipeline.stop()
                self._pipeline.wait(3000)
            except Exception:
                pass
            self._pipeline = None
        if self._sdr_device is not None:
            with contextlib.suppress(Exception):
                self._sdr_device.close()
            self._sdr_device = None
        with self._lock:
            self._state = RigState.DISCONNECTED

    def set_frequency(self, freq_hz: float, vfo: str = "VFOA") -> bool:
        """Retune the SDR center frequency (used by Doppler correction loop)."""
        if self._sdr_device is not None:
            return self._sdr_device.set_center_freq(freq_hz)
        return False

    def get_frequency(self, vfo: str = "VFOA") -> float:
        if self._sdr_device is not None:
            return self._sdr_device.center_freq
        return -1.0

    def set_vfo_frequencies(
        self,
        vfoa_hz: float | None,
        vfob_hz: float | None,
    ) -> bool:
        """For SDR, only the downlink (vfoa_hz) matters."""
        if vfoa_hz is not None:
            return self.set_frequency(vfoa_hz)
        return True

    def set_mode(self, mode: str, passband_hz: int = 0, vfo: str = "VFOA") -> bool:
        """SDR mode is controlled via SDR Control tab, not Hamlib."""
        return True

    def get_mode(self, vfo: str = "VFOA") -> str:
        return self._freq_state.mode

    def set_ctcss_tone(self, tone_hz: float) -> bool:
        return True  # SDR RX only — no CTCSS

    def set_dcs_code(self, code: int) -> bool:
        return True

    def set_vfo(self, vfo: str) -> bool:
        return True

    def get_rig_info(self) -> RigInfo | None:
        """Return a minimal RigInfo for the SDR device (RX-only)."""
        if self._device_info is None:
            return None
        return RigInfo(
            model_id=0,
            model_name=self._device_info.display_name,
            port="SoapySDR",
            baud_rate=0,
            state=self._state,
        )

    @property
    def sdr_device(self) -> SdrDevice | None:
        return self._sdr_device


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
