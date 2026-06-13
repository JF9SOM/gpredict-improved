"""FT4 codec — wraps ft8_lib via ctypes for encode/decode.

TX path: pack77() → genft4() → symbols_to_audio() → sounddevice + PTT
RX path: capture audio → spectrogram → ft8_lib LDPC decode

ft8_lib (kgoba/ft8_lib) must be installed as a shared library:
  Linux:   libft8.so
  macOS:   libft8.dylib
  Windows: ft8.dll

Without ft8_lib the codec is unavailable — both TX and RX are disabled.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

# ---------------------------------------------------------------------------
# FT4 physical-layer constants
# ---------------------------------------------------------------------------

SAMPLE_RATE: int = 12_000
FT4_SYMBOL_COUNT: int = 105
FT4_SAMPLES_PER_SYM: int = 576  # 12000 Hz / (500/24 baud) = exactly 576
FT4_SYMBOL_RATE: float = SAMPLE_RATE / FT4_SAMPLES_PER_SYM  # ≈ 20.833 Hz
FT4_TONE_SPACING: float = FT4_SYMBOL_RATE  # Hz between adjacent tones (= symbol rate)
FT4_TONE_COUNT: int = 4
FT4_PERIOD: float = 6.0  # period duration (s)
FT4_TX_OFFSET: float = 0.5  # TX starts 0.5 s into the period
FT4_TX_DURATION: float = FT4_SYMBOL_COUNT * FT4_SAMPLES_PER_SYM / SAMPLE_RATE  # ≈ 5.04 s

_PAYLOAD_BYTES: int = 10  # pack77 output: 77 bits padded to 10 bytes
_MSG_BUFLEN: int = 25  # unpack77 / ft8_decode output buffer
_MAX_CANDIDATES: int = 200
_LDPC_ITERATIONS: int = 20
_MIN_SYNC_SCORE: int = 10

# ---------------------------------------------------------------------------
# Decoded message dataclass
# ---------------------------------------------------------------------------


@dataclass
class Ft4Message:
    """One decoded FT4 message."""

    text: str
    freq_hz: float
    snr_db: float
    dt_sec: float  # time offset relative to period start


# ---------------------------------------------------------------------------
# ctypes structures (matching kgoba/ft8_lib ≥ v0.4 API)
# ---------------------------------------------------------------------------


class _MagArray(ctypes.Structure):
    """Log-magnitude spectrogram passed to ft8_lib decode functions."""

    _fields_ = [
        ("num_blocks", ctypes.c_int),
        ("num_bins", ctypes.c_int),
        ("block_stride", ctypes.c_int),
        ("mag", ctypes.POINTER(ctypes.c_float)),
    ]


class _Candidate(ctypes.Structure):
    """Sync candidate returned by ft8_find_sync."""

    _fields_ = [
        ("score", ctypes.c_int16),
        ("time_offset", ctypes.c_int16),
        ("freq_offset", ctypes.c_int16),
        ("time_sub", ctypes.c_uint8),
        ("freq_sub", ctypes.c_uint8),
    ]


# ---------------------------------------------------------------------------
# Library discovery
# ---------------------------------------------------------------------------


def get_user_ft8lib_dir() -> Path:
    """Return platform-specific user install directory for ft8lib."""
    from platformdirs import user_data_dir

    return Path(user_data_dir("gpredict-improved")) / "ft8lib"


def _find_ft8lib() -> ctypes.CDLL | None:
    """Try to load libft8 from user dir, system paths, and PyInstaller bundle."""
    user_dir = get_user_ft8lib_dir()
    candidates: list[str] = []

    if sys.platform == "win32":
        candidates.append(str(user_dir / "ft8.dll"))
        candidates.append("ft8.dll")
    elif sys.platform == "darwin":
        candidates.append(str(user_dir / "libft8.dylib"))
        candidates += ["libft8.dylib", "libft8.0.dylib"]
    else:
        candidates.append(str(user_dir / "libft8.so"))
        found = ctypes.util.find_library("ft8")
        if found:
            candidates.append(found)
        candidates += ["libft8.so", "libft8.so.0"]

    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        for name in ("libft8.so", "ft8.dll", "libft8.dylib"):
            candidates.append(str(meipass / name))

    for path in candidates:
        try:
            lib = ctypes.CDLL(path)
            # Smoke test: essential encode symbols must exist
            _ = lib.pack77
            _ = lib.genft4
            return lib
        except (OSError, AttributeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Bindings wrapper
# ---------------------------------------------------------------------------


class _Ft8LibBindings:
    """Thin ctypes wrapper around ft8_lib.  Separated from Ft4Codec so that
    prototype setup errors are contained."""

    def __init__(self, lib: ctypes.CDLL) -> None:
        self._lib = lib
        self._decode_available: bool = False
        self._setup_encode_prototypes()
        self._setup_decode_prototypes()

    def _setup_encode_prototypes(self) -> None:
        # int pack77(const char *msg, uint8_t *c77)
        self._lib.pack77.restype = ctypes.c_int
        self._lib.pack77.argtypes = [
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint8),
        ]
        # int unpack77(const uint8_t *c77, char *msg)
        self._lib.unpack77.restype = ctypes.c_int
        self._lib.unpack77.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_char_p,
        ]
        # void genft4(const uint8_t *payload, uint8_t *tones)
        self._lib.genft4.restype = None
        self._lib.genft4.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_uint8),
        ]

    def _setup_decode_prototypes(self) -> None:
        try:
            # int ft8_find_sync(const MagArray *power, int num_cands,
            #                   Candidate *heap, int min_score) -> int
            self._lib.ft8_find_sync.restype = ctypes.c_int
            self._lib.ft8_find_sync.argtypes = [
                ctypes.POINTER(_MagArray),
                ctypes.c_int,
                ctypes.POINTER(_Candidate),
                ctypes.c_int,
            ]
            # bool ft8_decode(const MagArray *power, const Candidate *cand,
            #                 int ldpc_iters, char *msg, uint8_t *a91, float *snr)
            self._lib.ft8_decode.restype = ctypes.c_bool
            self._lib.ft8_decode.argtypes = [
                ctypes.POINTER(_MagArray),
                ctypes.POINTER(_Candidate),
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_uint8),
                ctypes.POINTER(ctypes.c_float),
            ]
            self._decode_available = True
        except AttributeError:
            pass  # older ft8_lib without decode API — TX-only mode

    # ------------------------------------------------------------------ #
    # Encode path                                                          #
    # ------------------------------------------------------------------ #

    def pack77(self, message: str) -> bytes | None:
        """Pack text to 10-byte FT4/FT8 payload. Returns None on bad message."""
        buf = (ctypes.c_uint8 * _PAYLOAD_BYTES)()
        ret = self._lib.pack77(message.encode("ascii"), buf)
        return bytes(buf) if ret == 0 else None

    def genft4(self, payload: bytes) -> bytes:
        """Generate 105 FT4 tone values (0-3) from 10-byte payload."""
        buf_in = (ctypes.c_uint8 * _PAYLOAD_BYTES)(*payload)
        buf_out = (ctypes.c_uint8 * FT4_SYMBOL_COUNT)()
        self._lib.genft4(buf_in, buf_out)
        return bytes(buf_out)

    # ------------------------------------------------------------------ #
    # Decode path                                                          #
    # ------------------------------------------------------------------ #

    def decode_waterfall(
        self,
        mag: NDArray[np.float32],
        num_blocks: int,
        num_bins: int,
    ) -> list[Ft4Message]:
        """Run ft8_lib sync + LDPC decode on a spectrogram array.

        mag must be shape (num_blocks, num_bins), contiguous float32.
        Returns list of decoded Ft4Message objects.
        """
        if not self._decode_available:
            return []

        flat = np.ascontiguousarray(mag[:num_blocks, :num_bins], dtype=np.float32)
        flat_p = flat.ctypes.data_as(ctypes.POINTER(ctypes.c_float))

        wf = _MagArray(
            num_blocks=num_blocks,
            num_bins=num_bins,
            block_stride=num_bins,
            mag=flat_p,
        )
        candidates = (_Candidate * _MAX_CANDIDATES)()
        n_found = self._lib.ft8_find_sync(
            ctypes.byref(wf),
            _MAX_CANDIDATES,
            candidates,
            _MIN_SYNC_SCORE,
        )

        results: list[Ft4Message] = []
        msg_buf = ctypes.create_string_buffer(_MSG_BUFLEN)
        a91 = (ctypes.c_uint8 * 11)()
        snr = ctypes.c_float(0.0)

        for i in range(n_found):
            ok: bool = self._lib.ft8_decode(
                ctypes.byref(wf),
                ctypes.byref(candidates[i]),
                _LDPC_ITERATIONS,
                msg_buf,
                a91,
                ctypes.byref(snr),
            )
            if ok:
                text = msg_buf.value.decode("ascii", errors="replace").strip()
                freq = candidates[i].freq_offset * FT4_TONE_SPACING
                dt = candidates[i].time_offset * FT4_SAMPLES_PER_SYM / SAMPLE_RATE
                results.append(
                    Ft4Message(
                        text=text,
                        freq_hz=freq,
                        snr_db=float(snr.value),
                        dt_sec=dt,
                    )
                )
        return results


# ---------------------------------------------------------------------------
# Pure-Python audio helpers (no ft8_lib required)
# ---------------------------------------------------------------------------


def symbols_to_audio(
    tones: bytes,
    base_freq: float,
    sample_rate: int = SAMPLE_RATE,
) -> NDArray[np.float32]:
    """Generate phase-continuous 4-FSK audio from a FT4 tone array.

    Args:
        tones: Sequence of tone values (0-3), typically 105 bytes.
        base_freq: Frequency (Hz) of tone 0.
        sample_rate: Output audio sample rate.

    Returns:
        Float32 audio array of length len(tones) * samples_per_symbol.
    """
    spf = int(round(sample_rate / FT4_SYMBOL_RATE))
    total = len(tones) * spf
    audio = np.empty(total, dtype=np.float32)
    phase = 0.0
    pos = 0
    for tone in tones:
        freq = base_freq + tone * FT4_TONE_SPACING
        n = spf
        delta_phi = 2.0 * np.pi * freq / sample_rate
        phases = phase + delta_phi * np.arange(1, n + 1, dtype=np.float64)
        audio[pos : pos + n] = np.sin(phases).astype(np.float32)
        phase = float(phases[-1] % (2.0 * np.pi))
        pos += n
    return audio


def compute_waterfall(
    audio: NDArray[np.float32],
    sample_rate: int = SAMPLE_RATE,
) -> NDArray[np.float32]:
    """Compute log-magnitude spectrogram suitable for ft8_lib decode.

    Returns shape (num_blocks, num_bins) float32 where
    num_bins = FFT_SIZE // 2 + 1 and each block covers one FT4 symbol period.
    """
    spf = int(round(sample_rate / FT4_SYMBOL_RATE))
    num_bins = spf // 2 + 1
    n_blocks = int(np.ceil(len(audio) / spf))
    padded = np.zeros(n_blocks * spf, dtype=np.float32)
    padded[: len(audio)] = audio
    blocks = padded.reshape(n_blocks, spf)
    window = np.hanning(spf).astype(np.float32)
    fft_mag = np.abs(np.fft.rfft(blocks * window, axis=1)).astype(np.float32)
    return np.log10(np.maximum(fft_mag[:, :num_bins], 1e-10))


# ---------------------------------------------------------------------------
# Public codec class
# ---------------------------------------------------------------------------


class Ft4Codec:
    """High-level FT4 encode / decode interface.

    Audio synthesis (TX) and spectrogram computation are pure Python.
    LDPC encode/decode require ft8_lib shared library.
    """

    def __init__(self) -> None:
        _raw = _find_ft8lib()
        self._lib: _Ft8LibBindings | None = None
        if _raw is not None:
            with contextlib.suppress(AttributeError):
                self._lib = _Ft8LibBindings(_raw)

    @property
    def is_available(self) -> bool:
        """True if ft8_lib is loaded and TX encode is functional."""
        return self._lib is not None

    @property
    def decode_available(self) -> bool:
        """True if ft8_lib's decode API is accessible."""
        return self._lib is not None and self._lib._decode_available

    def encode_audio(
        self,
        message: str,
        base_freq: float = 1000.0,
        sample_rate: int = SAMPLE_RATE,
    ) -> NDArray[np.float32] | None:
        """Encode a standard FT4 message to audio.

        Returns float32 audio array, or None if ft8_lib is unavailable
        or the message string is not valid FT4 format.
        """
        if self._lib is None:
            return None
        payload = self._lib.pack77(message)
        if payload is None:
            return None
        tones = self._lib.genft4(payload)
        return symbols_to_audio(tones, base_freq, sample_rate)

    def decode_audio(
        self,
        audio: NDArray[np.float32],
        sample_rate: int = SAMPLE_RATE,
    ) -> list[Ft4Message]:
        """Decode FT4 messages from one period of recorded audio.

        Returns empty list if ft8_lib decode API is not available.
        """
        if self._lib is None or not self._lib._decode_available:
            return []
        wf = compute_waterfall(audio, sample_rate)
        num_blocks, num_bins = wf.shape
        return self._lib.decode_waterfall(wf, num_blocks, num_bins)
