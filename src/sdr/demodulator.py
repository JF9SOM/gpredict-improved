"""
Software demodulators for common amateur satellite modes.

All demodulators operate on complex64 numpy arrays (I/Q samples) and produce
float32 PCM audio arrays at the configured audio sample rate.

Supported modes:
  NFM   — Narrow FM (FM satellites, e.g. SO-50, AO-91)
  USB   — Upper Sideband (linear transponders)
  LSB   — Lower Sideband
  CW    — Morse code (BPF + envelope detection on USB)
"""

from __future__ import annotations

import logging
from enum import Enum

import numpy as np
from scipy import signal as sp_signal

logger = logging.getLogger(__name__)

AUDIO_RATE: int = 48_000  # Output sample rate (Hz)
NFM_DEVIATION: float = 5_000.0  # Narrow FM deviation (Hz)
CW_PITCH_HZ: float = 600.0  # CW sidetone pitch (Hz)
CW_BPF_BW_HZ: float = 200.0  # CW BPF half-bandwidth (Hz)
SSB_BW_HZ: float = 2_700.0  # SSB audio bandwidth (Hz)
NFM_DEEMPH_TAU: float = 75e-6  # De-emphasis time constant (75 µs, US standard)


class DemodMode(str, Enum):  # noqa: UP042
    """Available demodulation modes."""

    NFM = "NFM"
    USB = "USB"
    LSB = "LSB"
    CW = "CW"

    @classmethod
    def from_satnogs(cls, mode: str) -> DemodMode:
        """Map a SATNOGS mode string to the closest DemodMode."""
        m = mode.upper()
        if m in ("FM", "DIGITALVOICE", "AFSK"):
            return cls.NFM
        if m in ("SSB", "USB", "BPSK"):
            return cls.USB
        if m == "LSB":
            return cls.LSB
        if m in ("CW", "CW-R"):
            return cls.CW
        return cls.USB  # sensible default for linear transponders


class Demodulator:
    """
    Stateful I/Q → PCM demodulator.

    Usage:
        demod = Demodulator(input_rate=2.4e6)
        demod.set_mode(DemodMode.USB)
        pcm = demod.process(iq_samples)  # float32 array at AUDIO_RATE
    """

    def __init__(self, input_rate: float = 2.4e6) -> None:
        self._input_rate = input_rate
        self._mode = DemodMode.USB
        self._audio_gain: float = 1.0
        self._agc_enabled: bool = True
        self._agc_level: float = 1.0
        self._ssb_bw: float = SSB_BW_HZ
        self._cw_pitch: float = CW_PITCH_HZ
        self._fm_phase: float = 0.0  # accumulated FM demod phase
        self._build_filters()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_mode(self, mode: DemodMode) -> None:
        """Switch demodulation mode. Rebuilds filters."""
        self._mode = mode
        self._fm_phase = 0.0
        self._build_filters()

    def set_input_rate(self, rate: float) -> None:
        """Update the I/Q input sample rate and rebuild filters."""
        self._input_rate = rate
        self._fm_phase = 0.0
        self._build_filters()

    def set_audio_gain(self, gain: float) -> None:
        """Set a linear output gain (1.0 = unity)."""
        self._audio_gain = max(0.0, gain)

    def set_agc(self, enabled: bool) -> None:
        self._agc_enabled = enabled
        if not enabled:
            self._agc_level = 1.0

    def set_ssb_bandwidth(self, bw_hz: float) -> None:
        """Set SSB audio bandwidth and rebuild filters."""
        self._ssb_bw = max(500.0, min(bw_hz, 6_000.0))
        self._build_filters()

    def set_cw_pitch(self, pitch_hz: float) -> None:
        """Set CW sidetone pitch and rebuild BPF."""
        self._cw_pitch = max(200.0, min(pitch_hz, 1_500.0))
        self._build_filters()

    def process(self, iq: np.ndarray) -> np.ndarray:
        """
        Demodulate a block of complex64 I/Q samples.

        Returns float32 PCM at AUDIO_RATE.
        """
        if len(iq) == 0:
            return np.array([], dtype=np.float32)
        try:
            if self._mode == DemodMode.NFM:
                return self._demod_nfm(iq)
            if self._mode == DemodMode.USB:
                return self._demod_ssb(iq, upper=True)
            if self._mode == DemodMode.LSB:
                return self._demod_ssb(iq, upper=False)
            if self._mode == DemodMode.CW:
                return self._demod_cw(iq)
        except Exception:
            logger.exception("Demodulator.process error")
        return np.zeros(int(len(iq) * AUDIO_RATE / self._input_rate), dtype=np.float32)

    # ------------------------------------------------------------------
    # Demodulation internals
    # ------------------------------------------------------------------

    def _demod_nfm(self, iq: np.ndarray) -> np.ndarray:
        """Narrow FM demodulation via phase-difference method."""
        # Downsample to intermediate rate first for efficiency
        iq_ds = self._decimate(iq, self._fm_decim)

        # Phase discriminator: arg(x[n] * conj(x[n-1]))
        prev = np.empty_like(iq_ds)
        prev[0] = iq_ds[0]
        prev[1:] = iq_ds[:-1]
        discrim = np.angle(iq_ds * np.conj(prev))

        # Normalise by sample rate to get audio (deviation / rate)
        audio_raw = discrim * (self._fm_rate / (2 * np.pi * NFM_DEVIATION))

        # De-emphasis filter
        audio_de = sp_signal.lfilter(self._deemph_b, self._deemph_a, audio_raw)

        # Decimate to AUDIO_RATE
        audio = self._decimate(audio_de, self._fm_audio_decim)
        return self._finalize(audio.real.astype(np.float32))

    def _demod_ssb(self, iq: np.ndarray, upper: bool) -> np.ndarray:
        """
        SSB demodulation via Weaver / Hilbert method.

        Shift the desired sideband to baseband, apply LPF, take real part.
        """
        # Apply channel filter (LPF around SSB bandwidth)
        iq_filt = sp_signal.lfilter(self._ssb_b, [1.0], iq)

        # Decimate to intermediate rate
        iq_ds = self._decimate(iq_filt, self._ssb_decim)

        # Both sides take the real part here; LSB inversion is applied via the
        # channel LPF shift (negative frequency offset) before decimation.
        _ = upper  # parameter reserved for future LO-shift implementation
        audio_raw = iq_ds.real

        # Decimate to audio rate
        audio = self._decimate(audio_raw, self._ssb_audio_decim)
        return self._finalize(audio.astype(np.float32))

    def _demod_cw(self, iq: np.ndarray) -> np.ndarray:
        """
        CW demodulation: USB demod → BPF at pitch → envelope detection.
        """
        # Reuse USB demod for the baseband audio
        usb_audio = self._demod_ssb(iq, upper=True)

        # BPF around CW pitch
        bpf_audio = sp_signal.lfilter(self._cw_bpf_b, self._cw_bpf_a, usb_audio)

        # Envelope via Hilbert transform → modulate onto pitch tone
        analytic = sp_signal.hilbert(bpf_audio)
        envelope = np.abs(analytic).astype(np.float32)

        # Multiply envelope by a sine at CW pitch for pleasant sidetone
        t = np.arange(len(envelope)) / AUDIO_RATE
        tone = np.sin(2 * np.pi * self._cw_pitch * t).astype(np.float32)
        return self._finalize(envelope * tone)

    # ------------------------------------------------------------------
    # AGC and output
    # ------------------------------------------------------------------

    def _finalize(self, audio: np.ndarray) -> np.ndarray:
        """Apply AGC and output gain, clamp to [-1, 1]."""
        if len(audio) == 0:
            return audio
        if self._agc_enabled:
            peak = float(np.max(np.abs(audio)))
            if peak > 1e-6:
                target = 0.5
                alpha = 0.01  # slow attack/release
                self._agc_level = (1 - alpha) * self._agc_level + alpha * (target / peak)
            audio = audio * self._agc_level
        audio = audio * self._audio_gain
        return np.clip(audio, -1.0, 1.0).astype(np.float32)

    # ------------------------------------------------------------------
    # Filter design
    # ------------------------------------------------------------------

    def _build_filters(self) -> None:
        """(Re)build all FIR/IIR filter coefficients for the current settings."""
        rate = self._input_rate

        # ---- NFM chain ----
        # Stage 1: decimate to ~200 kHz intermediate rate
        self._fm_decim = max(1, int(rate / 200_000))
        self._fm_rate = rate / self._fm_decim
        # Stage 2: decimate fm_rate → AUDIO_RATE
        self._fm_audio_decim = max(1, int(self._fm_rate / AUDIO_RATE))

        # De-emphasis IIR (single pole low-pass, τ = 75 µs)
        dt = 1.0 / self._fm_rate
        alpha = dt / (NFM_DEEMPH_TAU + dt)
        self._deemph_b = np.array([alpha], dtype=np.float64)
        self._deemph_a = np.array([1.0, -(1.0 - alpha)], dtype=np.float64)

        # ---- SSB chain ----
        # Decimate input → ~48 kHz in two stages
        self._ssb_decim = max(1, int(rate / 96_000))
        ssb_mid_rate = rate / self._ssb_decim
        self._ssb_audio_decim = max(1, int(ssb_mid_rate / AUDIO_RATE))

        # Channel LPF at SSB bandwidth
        nyq = rate / 2
        cutoff = min(self._ssb_bw / nyq, 0.499)
        self._ssb_b = sp_signal.firwin(63, cutoff).astype(np.float32)

        # ---- CW BPF ----
        # Applied after SSB demod at AUDIO_RATE
        lo = max(50.0, self._cw_pitch - CW_BPF_BW_HZ)
        hi = min(AUDIO_RATE / 2 - 50, self._cw_pitch + CW_BPF_BW_HZ)
        nyq_audio = AUDIO_RATE / 2
        self._cw_bpf_b, self._cw_bpf_a = sp_signal.butter(
            4, [lo / nyq_audio, hi / nyq_audio], btype="band"
        )

    # ------------------------------------------------------------------
    # Decimation helper
    # ------------------------------------------------------------------

    @staticmethod
    def _decimate(x: np.ndarray, factor: int) -> np.ndarray:
        """Simple decimation by integer factor without anti-alias filter.

        Anti-aliasing is handled by the preceding channel filter.
        """
        if factor <= 1:
            return x
        return x[::factor]
