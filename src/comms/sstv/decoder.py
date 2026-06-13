"""SSTV decoder — pure Python / numpy / scipy implementation.

Supports Robot36 and PD120 modes (most common for amateur satellite SSTV).
Accepts audio chunks via push_samples() and emits Qt signals as lines and
complete images arrive.

Algorithm overview
------------------
1. Buffer incoming audio until we have enough for sync detection.
2. Compute instantaneous frequency via Hilbert transform (scipy).
3. Locate 1200 Hz sync pulses to find line boundaries.
4. For each line, sample the Y (luminance) and chroma scans at the
   pixel clock positions and map frequency → intensity (1500–2300 Hz).
5. Emit line_received() for progressive display and image_complete()
   when all lines have arrived.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

try:
    from scipy import signal as _sp

    _SCIPY = True
except ImportError:
    _sp = None  # type: ignore
    _SCIPY = False

# ---------------------------------------------------------------------------
# Robot36 timing constants (seconds)
# ---------------------------------------------------------------------------
_R36_SYNC_MS: float = 9.0  # 1200 Hz sync pulse
_R36_PORCH_MS: float = 3.0  # 1500 Hz porch after sync
_R36_Y_MS: float = 88.0  # luminance scan per line
_R36_SEP_MS: float = 4.5  # separator between Y and chroma
_R36_CPORCH_MS: float = 1.5  # chroma porch (1900 Hz)
_R36_C_MS: float = 44.0  # chroma scan per line
_R36_LINE_MS: float = 150.0  # total line period
_R36_LINES: int = 240
_R36_PIXELS: int = 320

# PD120 timing constants (seconds)
_PD120_SYNC_MS: float = 20.0
_PD120_PORCH_MS: float = 2.08
_PD120_Y_MS: float = 121.6  # Y scan
_PD120_C_MS: float = 60.8  # each of Cb, Cr
_PD120_LINE_MS: float = 508.48
_PD120_LINES: int = 496
_PD120_PIXELS: int = 640

# Frequency mapping (Hz)
_FREQ_BLACK: float = 1500.0
_FREQ_WHITE: float = 2300.0
_FREQ_SYNC: float = 1200.0
_FREQ_RANGE: float = _FREQ_WHITE - _FREQ_BLACK  # 800 Hz

# Minimum audio buffer before starting scan (4 seconds at 44100)
_MIN_BUFFER_SAMPLES: int = 44100 * 4


def _freq_to_pixel(freq: np.ndarray) -> np.ndarray:
    """Map instantaneous frequency array to 0–255 pixel values."""
    p = (freq - _FREQ_BLACK) / _FREQ_RANGE * 255.0
    return np.clip(p, 0, 255).astype(np.uint8)


def _inst_freq(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Return instantaneous frequency (Hz) for each sample."""
    if _SCIPY:
        analytic = _sp.hilbert(samples.astype(np.float32))
    else:
        # Fallback: simple phase differencing on raw samples
        # Less accurate but avoids hard scipy dependency
        n = len(samples)
        analytic = np.zeros(n, dtype=np.complex64)
        analytic.real = samples.astype(np.float32)
        analytic.imag = np.concatenate([[0], np.cumsum(samples[:-1].astype(np.float32))])
    phase = np.unwrap(np.angle(analytic))
    freq = np.diff(phase) / (2.0 * np.pi) * sample_rate
    # Pad to original length
    return np.concatenate([freq, [freq[-1]]]).astype(np.float32)


def _find_sync_positions(freq: np.ndarray, sample_rate: int) -> list[int]:
    """Return sample indices where Robot36 / PD120 sync pulses start.

    A sync pulse is a run of samples near 1200 Hz lasting at least 8 ms.
    """
    min_run = int(sample_rate * 0.008)
    near_sync = np.abs(freq - _FREQ_SYNC) < 150.0
    positions: list[int] = []
    i = 0
    n = len(near_sync)
    while i < n:
        if near_sync[i]:
            j = i
            while j < n and near_sync[j]:
                j += 1
            if j - i >= min_run:
                positions.append(i)
            i = j
        else:
            i += 1
    return positions


def _detect_mode(sync_positions: list[int], sample_rate: int) -> str:
    """Guess SSTV mode from line period between consecutive syncs."""
    if len(sync_positions) < 2:
        return "Robot36"
    periods_ms = [
        (sync_positions[k + 1] - sync_positions[k]) / sample_rate * 1000.0
        for k in range(min(5, len(sync_positions) - 1))
    ]
    avg_ms = float(np.median(periods_ms))
    if abs(avg_ms - _PD120_LINE_MS) < 50.0:
        return "PD120"
    return "Robot36"


def _sample_range(
    freq: np.ndarray, start: int, duration_ms: float, sample_rate: int, pixels: int
) -> np.ndarray:
    """Sample *pixels* evenly-spaced values from freq[start:start+duration]."""
    n = int(duration_ms / 1000.0 * sample_rate)
    end = min(start + n, len(freq))
    segment = freq[start:end]
    if len(segment) == 0:
        return np.zeros(pixels, dtype=np.uint8)
    # Resample to pixel count
    indices = np.linspace(0, len(segment) - 1, pixels).astype(int)
    return _freq_to_pixel(segment[indices])


# ---------------------------------------------------------------------------
# Robot36 image reconstruction
# ---------------------------------------------------------------------------


def _decode_robot36(
    freq: np.ndarray, sync_pos: int, sample_rate: int, image: np.ndarray, line: int
) -> None:
    """Decode one Robot36 line pair into *image* (H×W×3 uint8, YCbCr→RGB)."""
    sr = sample_rate
    ms = lambda t: int(t / 1000.0 * sr)  # noqa: E731

    y_start = sync_pos + ms(_R36_SYNC_MS) + ms(_R36_PORCH_MS)
    c_start = y_start + ms(_R36_Y_MS) + ms(_R36_SEP_MS) + ms(_R36_CPORCH_MS)

    y_vals = _sample_range(freq, y_start, _R36_Y_MS, sr, _R36_PIXELS)
    c_vals = _sample_range(freq, c_start, _R36_C_MS, sr, _R36_PIXELS)

    # Robot36 uses Ry / By alternating lines; approximate as Cb for even, Cr for odd
    y = y_vals.astype(np.float32)
    c = c_vals.astype(np.float32) - 128.0

    if line < _R36_LINES:
        if line % 2 == 0:
            # even: Ry = 1.402 * (Cr - 128), By stored next line
            r = np.clip(y + 1.402 * c, 0, 255).astype(np.uint8)
            g = np.clip(y - 0.714 * c, 0, 255).astype(np.uint8)
            b = np.clip(y, 0, 255).astype(np.uint8)
        else:
            r = np.clip(y, 0, 255).astype(np.uint8)
            g = np.clip(y - 0.344 * c, 0, 255).astype(np.uint8)
            b = np.clip(y + 1.772 * c, 0, 255).astype(np.uint8)
        image[line, :, 0] = r
        image[line, :, 1] = g
        image[line, :, 2] = b


# ---------------------------------------------------------------------------
# PD120 image reconstruction
# ---------------------------------------------------------------------------


def _decode_pd120(
    freq: np.ndarray, sync_pos: int, sample_rate: int, image: np.ndarray, line: int
) -> None:
    """Decode one PD120 line into *image* (H×W×3 uint8)."""
    sr = sample_rate
    ms = lambda t: int(t / 1000.0 * sr)  # noqa: E731

    y1_start = sync_pos + ms(_PD120_SYNC_MS) + ms(_PD120_PORCH_MS)
    cb_start = y1_start + ms(_PD120_Y_MS)
    cr_start = cb_start + ms(_PD120_C_MS)
    y2_start = cr_start + ms(_PD120_C_MS)

    y1 = _sample_range(freq, y1_start, _PD120_Y_MS, sr, _PD120_PIXELS).astype(np.float32)
    cb = _sample_range(freq, cb_start, _PD120_C_MS, sr, _PD120_PIXELS).astype(np.float32) - 128.0
    cr = _sample_range(freq, cr_start, _PD120_C_MS, sr, _PD120_PIXELS).astype(np.float32) - 128.0
    y2 = _sample_range(freq, y2_start, _PD120_Y_MS, sr, _PD120_PIXELS).astype(np.float32)

    for row_offset, y_row in enumerate([y1, y2]):
        row = line * 2 + row_offset
        if row >= _PD120_LINES:
            break
        r = np.clip(y_row + 1.402 * cr, 0, 255).astype(np.uint8)
        g = np.clip(y_row - 0.344 * cb - 0.714 * cr, 0, 255).astype(np.uint8)
        b = np.clip(y_row + 1.772 * cb, 0, 255).astype(np.uint8)
        image[row, :, 0] = r
        image[row, :, 1] = g
        image[row, :, 2] = b


# ---------------------------------------------------------------------------
# Public decoder class
# ---------------------------------------------------------------------------


class SstvDecoder(QObject):
    """Buffer audio chunks and decode SSTV frames.

    Signals
    -------
    line_received(line_number, QImage)
        Emitted after each decoded scan line for progressive display.
    image_complete(QImage, str)
        Emitted when a full image has been decoded.  Second arg is mode name.
    mode_detected(str)
        Emitted once the SSTV mode has been identified.
    status_changed(str)
        Short human-readable status string.
    """

    line_received: Signal = Signal(int, object)
    image_complete: Signal = Signal(object, str)
    mode_detected: Signal = Signal(str)
    status_changed: Signal = Signal(str)

    def __init__(self, sample_rate: int = 44100, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sample_rate = sample_rate
        self._buffer: list[np.ndarray] = []
        self._total_samples: int = 0
        self._lock = threading.Lock()
        self._active = False

    def start(self) -> None:
        """Begin accepting audio chunks."""
        with self._lock:
            self._buffer.clear()
            self._total_samples = 0
            self._active = True
        self.status_changed.emit("Listening for SSTV signal…")

    def stop(self) -> None:
        """Stop decoding and discard buffered audio."""
        with self._lock:
            self._active = False
            self._buffer.clear()
            self._total_samples = 0
        self.status_changed.emit("Stopped")

    def push_samples(self, samples: np.ndarray) -> None:
        """Receive audio samples (float32 mono, any chunk size).

        Call from any thread.  Processing is done inline here; for large
        buffers the caller should invoke this from a worker thread.
        """
        with self._lock:
            if not self._active:
                return
            mono = samples.astype(np.float32)
            if mono.ndim == 2:
                mono = mono.mean(axis=1)
            self._buffer.append(mono)
            self._total_samples += len(mono)
            if self._total_samples < _MIN_BUFFER_SAMPLES:
                return
            combined = np.concatenate(self._buffer)
            self._buffer.clear()
            self._total_samples = 0

        self._process(combined)

    # ------------------------------------------------------------------ #
    # Internal processing
    # ------------------------------------------------------------------ #

    def _process(self, audio: np.ndarray) -> None:
        """Compute instantaneous frequency and decode all complete lines."""
        freq = _inst_freq(audio, self._sample_rate)
        syncs = _find_sync_positions(freq, self._sample_rate)
        if not syncs:
            # No sync found — put audio back for next chunk
            with self._lock:
                if self._active:
                    self._buffer.insert(0, audio[-self._sample_rate * 2 :])
                    self._total_samples += self._sample_rate * 2
            return

        mode = _detect_mode(syncs, self._sample_rate)
        self.mode_detected.emit(mode)

        if mode == "Robot36":
            h, w = _R36_LINES, _R36_PIXELS
            line_ms = _R36_LINE_MS
            decode_fn: Any = _decode_robot36
        else:
            h, w = _PD120_LINES, _PD120_PIXELS
            line_ms = _PD120_LINE_MS
            decode_fn = _decode_pd120

        image = np.zeros((h, w, 3), dtype=np.uint8)
        line_samples = int(line_ms / 1000.0 * self._sample_rate)
        decoded_lines = 0

        for idx, sync_pos in enumerate(syncs):
            line = idx
            if line >= h:
                break
            # Check we have enough samples for this line
            if sync_pos + line_samples > len(freq):
                break
            decode_fn(freq, sync_pos, self._sample_rate, image, line)
            decoded_lines += 1

            # Emit progressive line update
            qimg = self._ndarray_to_qimage(image, w, h)
            self.line_received.emit(line, qimg)
            self.status_changed.emit(f"{mode}: line {decoded_lines}/{h}")

        if decoded_lines > 0:
            qimg = self._ndarray_to_qimage(image, w, h)
            self.image_complete.emit(qimg, mode)
            self.status_changed.emit(f"{mode}: received {decoded_lines} lines")

    @staticmethod
    def _ndarray_to_qimage(arr: np.ndarray, w: int, h: int) -> QImage:
        """Convert H×W×3 uint8 numpy array to QImage (RGB888)."""
        contiguous = np.ascontiguousarray(arr)
        qimg = QImage(
            contiguous.data,
            w,
            h,
            w * 3,
            QImage.Format.Format_RGB888,
        )
        return qimg.copy()  # copy so numpy buffer can be freed
