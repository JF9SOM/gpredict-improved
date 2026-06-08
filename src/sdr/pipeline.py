"""
SDR I/Q pipeline — QThread pub/sub hub.

SDRPipeline runs in a dedicated QThread and continuously reads I/Q samples
from an SdrDevice.  It distributes the samples to:

  - FFT computation → spectrum_ready Signal  (≈10 fps)
  - Demodulator → audio_ready Signal         (each block)
  - IQRecorder                               (each block)
  - Future plugin hooks via subscribe()

The pipeline is designed so that plugin authors never need to touch this file.
New consumers simply call subscribe(callback) to receive each numpy block.

Signals emitted on the Qt main thread (via QMetaObject / queued connection):
  spectrum_ready(list)   — [(freq_hz, power_dbfs), …] for spectrum display
  audio_ready(ndarray)   — float32 PCM block at AUDIO_RATE
  status_changed(str)    — human-readable status message
  error_occurred(str)    — error message
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

from sdr.demodulator import AUDIO_RATE, DemodMode, Demodulator
from sdr.device import SdrDevice
from sdr.recorder import IQRecorder

logger = logging.getLogger(__name__)

# Number of samples per pipeline block
_BLOCK_SIZE: int = 16_384

# FFT update interval (seconds)
_FFT_INTERVAL: float = 0.1  # 10 fps

# FFT resolution
_FFT_SIZE: int = 1024


class SDRPipeline(QThread):
    """
    I/Q acquisition and distribution thread.

    Instantiate with an open SdrDevice, then call start().
    Stop by calling stop() followed by wait().
    """

    spectrum_ready: Signal = Signal(list)  # [(freq_hz, power_dbfs), …]
    audio_ready: Signal = Signal(object)  # np.ndarray float32 PCM
    status_changed: Signal = Signal(str)
    error_occurred: Signal = Signal(str)

    def __init__(self, device: SdrDevice, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._device = device
        self._demodulator = Demodulator(input_rate=device.sample_rate)
        self._recorder = IQRecorder()
        self._stop_flag = threading.Event()

        # Subscriber callbacks (called from pipeline thread — must be thread-safe)
        self._subscribers: list[Callable[[np.ndarray], None]] = []
        self._subscribers_lock = threading.Lock()

        # Audio output
        self._audio_enabled: bool = False
        self._sounddevice_stream: Any = None

        # FFT timing
        self._last_fft_time: float = 0.0

    # ------------------------------------------------------------------
    # Public API (safe to call from any thread)
    # ------------------------------------------------------------------

    def subscribe(self, callback: Callable[[np.ndarray], None]) -> None:
        """Register a callback to receive each I/Q block (complex64 numpy array)."""
        with self._subscribers_lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[np.ndarray], None]) -> None:
        with self._subscribers_lock:
            self._subscribers = [c for c in self._subscribers if c is not callback]

    def stop(self) -> None:
        """Signal the thread to stop."""
        self._stop_flag.set()

    # -- Demodulator control --

    def set_demod_mode(self, mode: DemodMode) -> None:
        self._demodulator.set_mode(mode)

    def set_audio_gain(self, gain: float) -> None:
        self._demodulator.set_audio_gain(gain)

    def set_agc(self, enabled: bool) -> None:
        self._demodulator.set_agc(enabled)

    def set_audio_enabled(self, enabled: bool) -> None:
        self._audio_enabled = enabled
        if not enabled:
            self._close_audio_stream()

    # -- Recorder control --

    @property
    def recorder(self) -> IQRecorder:
        return self._recorder

    # ------------------------------------------------------------------
    # QThread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Main loop: read samples, distribute to consumers."""
        logger.info("SDRPipeline started (rate=%.0f Hz)", self._device.sample_rate)
        self.status_changed.emit("SDR streaming")

        if not self._device.start_stream():
            self.error_occurred.emit("Failed to start SDR stream")
            return

        self._stop_flag.clear()
        self._demodulator.set_input_rate(self._device.sample_rate)

        while not self._stop_flag.is_set():
            iq = self._device.read_samples(_BLOCK_SIZE)
            if iq is None or len(iq) == 0:
                # Timeout or error — brief sleep to avoid spin-loop
                time.sleep(0.005)
                continue

            # Distribute to plugin subscribers
            with self._subscribers_lock:
                subs = list(self._subscribers)
            for cb in subs:
                try:
                    cb(iq)
                except Exception:
                    logger.exception("SDR subscriber callback error")

            # IQ recorder
            self._recorder.put_samples(iq)

            # Demodulate → audio
            if self._audio_enabled:
                try:
                    pcm = self._demodulator.process(iq)
                    if len(pcm) > 0:
                        self.audio_ready.emit(pcm)
                        self._play_audio(pcm)
                except Exception:
                    logger.exception("Demodulator error")

            # FFT → spectrum
            now = time.monotonic()
            if now - self._last_fft_time >= _FFT_INTERVAL:
                self._last_fft_time = now
                try:
                    spectrum = self._compute_fft(iq)
                    self.spectrum_ready.emit(spectrum)
                except Exception:
                    logger.exception("FFT error")

        self._device.stop_stream()
        self._close_audio_stream()
        logger.info("SDRPipeline stopped")
        self.status_changed.emit("SDR stopped")

    # ------------------------------------------------------------------
    # FFT
    # ------------------------------------------------------------------

    def _compute_fft(self, iq: np.ndarray) -> list[tuple[float, float]]:
        """Compute power spectrum.  Returns [(freq_hz, power_dbfs), …]."""
        n = min(_FFT_SIZE, len(iq))
        window = np.blackman(n).astype(np.float32)
        block = iq[:n] * window
        fft = np.fft.fftshift(np.fft.fft(block, n=_FFT_SIZE))
        power_db = 20.0 * np.log10(np.abs(fft) / n + 1e-12)
        cf = self._device.center_freq
        sr = self._device.sample_rate
        freqs = cf + np.fft.fftshift(np.fft.fftfreq(_FFT_SIZE, d=1.0 / sr))
        return list(zip(freqs.tolist(), power_db.tolist(), strict=False))

    # ------------------------------------------------------------------
    # Audio output (sounddevice)
    # ------------------------------------------------------------------

    def _play_audio(self, pcm: np.ndarray) -> None:
        """Write PCM to sounddevice output stream, opening it on first call."""
        try:
            import sounddevice as sd

            if self._sounddevice_stream is None:
                self._sounddevice_stream = sd.OutputStream(
                    samplerate=AUDIO_RATE,
                    channels=1,
                    dtype="float32",
                    blocksize=len(pcm),
                )
                self._sounddevice_stream.start()
            self._sounddevice_stream.write(pcm)
        except Exception:
            logger.exception("Audio output error")
            self._sounddevice_stream = None

    def _close_audio_stream(self) -> None:
        if self._sounddevice_stream is not None:
            try:
                self._sounddevice_stream.stop()
                self._sounddevice_stream.close()
            except Exception:
                pass
            self._sounddevice_stream = None
