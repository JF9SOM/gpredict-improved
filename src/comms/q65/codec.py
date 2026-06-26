"""Q65 codec — wraps libq65 (built from WSJT-X source) via ctypes.

RX path: capture 60-second audio block → q65_decode() → decoded messages

libq65 must be installed as a shared library:
  Linux:   libq65.so
  macOS:   libq65.dylib
  Windows: q65.dll

Install via Help > Q65 (libq65) Installation… or by running the
build-q65lib.yml workflow and downloading the result.

Without libq65 the codec is unavailable — decoding is disabled.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

# ---------------------------------------------------------------------------
# Q65 physical-layer constants
# ---------------------------------------------------------------------------

SAMPLE_RATE: int = 12_000

# Period durations in seconds (A=60s is the EME standard on 144 MHz+)
Q65_PERIODS: dict[str, int] = {
    "Q65-60A": 60,
    "Q65-60B": 60,
    "Q65-30B": 30,
    "Q65-30C": 30,
    "Q65-15C": 15,
    "Q65-15D": 15,
    "Q65-15E": 15,
}

# Submode letter → index used by libq65
Q65_SUBMODE: dict[str, int] = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}

# Maximum decoded messages returned per period
_MAX_MESSAGES: int = 20

# Output buffer length per message (Q65 messages are ≤ 22 chars + null)
_MSG_BUFLEN: int = 37


# ---------------------------------------------------------------------------
# Decoded message dataclass
# ---------------------------------------------------------------------------


@dataclass
class Q65Message:
    """One decoded Q65 message."""

    text: str
    freq_hz: float
    snr_db: float
    dt_sec: float


# ---------------------------------------------------------------------------
# libq65 loader
# ---------------------------------------------------------------------------

_USER_DIR_ENVVAR = "FBSAT59_Q65LIB_DIR"


def _find_libq65() -> Path | None:
    """Search for libq65 shared library in priority order.

    1. User-installed via Help > Q65 Installation
    2. Bundled inside PyInstaller _MEIPASS
    3. System path (development convenience)
    """
    import platformdirs

    candidates: list[Path] = []

    # 1. User-installed directory
    user_dir = Path(platformdirs.user_data_dir("fbsat59")) / "q65lib"
    if sys.platform == "win32":
        candidates.append(user_dir / "q65.dll")
    elif sys.platform == "darwin":
        candidates.append(user_dir / "libq65.dylib")
    else:
        candidates.append(user_dir / "libq65.so")

    # 2. PyInstaller bundle (_MEIPASS)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        mp = Path(meipass)
        for name in ("q65.dll", "libq65.dylib", "libq65.so"):
            candidates.append(mp / name)

    # 3. Development: repo-local q65lib-bundle/
    try:
        import importlib.util as _ilu

        spec = _ilu.find_spec("comms.q65.codec")
        if spec and spec.origin:
            repo_root = Path(spec.origin).parent.parent.parent.parent
            bundle = repo_root / "q65lib-bundle"
            for name in ("q65.dll", "libq65.dylib", "libq65.so"):
                candidates.append(bundle / name)
    except Exception:
        pass

    for p in candidates:
        if p.exists():
            return p
    return None


def _load_libq65() -> ctypes.CDLL | None:
    """Load libq65 and set up function signatures. Returns None if not found."""
    path = _find_libq65()
    if path is None:
        return None
    try:
        lib = ctypes.CDLL(str(path))
        # q65_decode(samples, n_samples, submode, nfa, nfb, nfqso, nperiod,
        #            messages_buf, snr_buf, dt_buf, freq_buf, max_messages)
        # Returns: number of decoded messages
        lib.q65_decode.restype = ctypes.c_int
        lib.q65_decode.argtypes = [
            ctypes.POINTER(ctypes.c_float),  # samples
            ctypes.c_int,  # n_samples
            ctypes.c_int,  # submode (0=A..4=E)
            ctypes.c_int,  # nfa (Hz low)
            ctypes.c_int,  # nfb (Hz high)
            ctypes.c_int,  # nfqso (0 = search all)
            ctypes.c_int,  # nperiod (seconds)
            ctypes.c_char_p,  # messages output buffer
            ctypes.POINTER(ctypes.c_float),  # snr output
            ctypes.POINTER(ctypes.c_float),  # dt output
            ctypes.POINTER(ctypes.c_int),  # freq output (Hz)
            ctypes.c_int,  # max_messages
        ]
        # q65_lib_version() → const char* version string
        with __import__("contextlib").suppress(AttributeError):
            lib.q65_lib_version.restype = ctypes.c_char_p
            lib.q65_lib_version.argtypes = []
        return lib
    except OSError:
        return None


_lib: ctypes.CDLL | None = _load_libq65()


def is_available() -> bool:
    """Return True if libq65 is loaded and decoding is possible."""
    return _lib is not None


def lib_version() -> str:
    """Return libq65 version string, or empty string if not loaded."""
    if _lib is None:
        return ""
    try:
        ver = _lib.q65_lib_version()
        return ver.decode() if ver else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Q65Codec
# ---------------------------------------------------------------------------


class Q65Codec:
    """Stateless Q65 decoder backed by libq65.

    Args:
        submode: One of 'A', 'B', 'C', 'D', 'E'.  Default 'A' (EME standard).
        nfa: Low frequency bound for search (Hz).
        nfb: High frequency bound for search (Hz).
        nfqso: Partner frequency in Hz; 0 means search the full nfa–nfb range.
    """

    def __init__(
        self,
        submode: str = "A",
        nfa: int = 200,
        nfb: int = 3000,
        nfqso: int = 0,
    ) -> None:
        self.submode = submode.upper()
        self.nfa = nfa
        self.nfb = nfb
        self.nfqso = nfqso

    def decode(self, samples: NDArray[np.float32], period_seconds: int = 60) -> list[Q65Message]:
        """Decode one complete Q65 audio period.

        Args:
            samples: Float32 audio at SAMPLE_RATE Hz.
                     Length should be period_seconds * SAMPLE_RATE.
            period_seconds: T/R period length (15, 30, or 60).

        Returns:
            List of decoded Q65Message objects (empty if none decoded or
            library unavailable).
        """
        if _lib is None:
            return []

        n = len(samples)
        buf = samples.astype(np.float32, copy=False)
        c_buf = buf.ctypes.data_as(ctypes.POINTER(ctypes.c_float))

        msg_buf = ctypes.create_string_buffer(_MSG_BUFLEN * _MAX_MESSAGES)
        snr_arr = (ctypes.c_float * _MAX_MESSAGES)()
        dt_arr = (ctypes.c_float * _MAX_MESSAGES)()
        freq_arr = (ctypes.c_int * _MAX_MESSAGES)()

        try:
            n_decoded = _lib.q65_decode(
                c_buf,
                ctypes.c_int(n),
                ctypes.c_int(Q65_SUBMODE.get(self.submode, 0)),
                ctypes.c_int(self.nfa),
                ctypes.c_int(self.nfb),
                ctypes.c_int(self.nfqso),
                ctypes.c_int(period_seconds),
                msg_buf,
                snr_arr,
                dt_arr,
                freq_arr,
                ctypes.c_int(_MAX_MESSAGES),
            )
        except Exception:
            return []

        results: list[Q65Message] = []
        for i in range(max(0, n_decoded)):
            offset = i * _MSG_BUFLEN
            raw = msg_buf.raw[offset : offset + _MSG_BUFLEN]
            text = raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()
            if text:
                results.append(
                    Q65Message(
                        text=text,
                        freq_hz=float(freq_arr[i]),
                        snr_db=float(snr_arr[i]),
                        dt_sec=float(dt_arr[i]),
                    )
                )
        return results
