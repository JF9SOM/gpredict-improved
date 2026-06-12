"""
Software demodulators for common amateur satellite modes.

All demodulators operate on complex64 numpy arrays (I/Q samples) and produce
float32 PCM audio arrays at the configured audio sample rate.

Supported modes:
  NFM   — Narrow FM (FM satellites, e.g. SO-50, AO-91)
  USB   — Upper Sideband (linear transponders)
  LSB   — Lower Sideband
  CW    — Morse code (direct decimation + BPF + envelope detection)
"""

from __future__ import annotations

import logging
from enum import Enum

import numpy as np

try:
    from scipy import signal as sp_signal

    _SCIPY_AVAILABLE: bool = True
except ImportError:
    sp_signal = None  # type: ignore[assignment]
    _SCIPY_AVAILABLE = False

logger = logging.getLogger(__name__)

AUDIO_RATE: int = 48_000  # Output sample rate (Hz)
NFM_DEVIATION: float = 5_000.0  # Narrow FM deviation (Hz)
CW_PITCH_HZ: float = 600.0  # CW pitch reference (Hz) — kept for future use
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
        if not _SCIPY_AVAILABLE:
            raise ImportError(
                "scipy is required for SDR demodulation. "
                "Install it with: pip install 'gpredict-improved[sdr]'"
            )
        self._input_rate = input_rate
        self._mode = DemodMode.USB
        self._audio_gain: float = 1.0
        self._agc_enabled: bool = True
        self._agc_level: float = 1.0
        self._ssb_bw: float = SSB_BW_HZ
        self._cw_pitch: float = CW_PITCH_HZ
        self._fm_phase: float = 0.0  # accumulated FM demod phase
        # DC blocking IIR state (applied to I and Q separately before NFM,
        # and to real PCM output before SSB/CW to remove SDR DC offset hum)
        self._dc_zi_i: np.ndarray = np.zeros(1)
        self._dc_zi_q: np.ndarray = np.zeros(1)
        self._build_filters()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_mode(self, mode: DemodMode) -> None:
        """Switch demodulation mode. Rebuilds filters."""
        self._mode = mode
        self._fm_phase = 0.0
        self._dc_zi_i = np.zeros(1)
        self._dc_zi_q = np.zeros(1)
        self._build_filters()

    def set_input_rate(self, rate: float) -> None:
        """Update the I/Q input sample rate and rebuild filters."""
        self._input_rate = rate
        self._fm_phase = 0.0
        self._dc_zi_i = np.zeros(1)
        self._dc_zi_q = np.zeros(1)
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

    def _remove_dc(self, iq: np.ndarray) -> np.ndarray:
        """Remove DC offset from I and Q channels using a high-pass IIR filter.

        The HackRF (and most SDRs) produce a significant DC spike at the center
        frequency. Without this step, the DC component appears as a low-frequency
        hum in the audio output.
        """
        i_dc_raw, self._dc_zi_i = sp_signal.lfilter(
            self._dc_b, self._dc_a, iq.real.astype(np.float32), zi=self._dc_zi_i
        )
        q_dc_raw, self._dc_zi_q = sp_signal.lfilter(
            self._dc_b, self._dc_a, iq.imag.astype(np.float32), zi=self._dc_zi_q
        )
        i_dc = np.asarray(i_dc_raw, dtype=np.float32)
        q_dc = np.asarray(q_dc_raw, dtype=np.float32)
        return (i_dc + 1j * q_dc).astype(np.complex64)

    def _demod_nfm(self, iq: np.ndarray) -> np.ndarray:
        """Narrow FM demodulation via phase-difference method."""
        # Remove DC offset from the SDR first
        iq = self._remove_dc(iq)

        # Apply IF bandpass filter to limit bandwidth to ±(deviation + audio_bw)
        iq_if = sp_signal.lfilter(self._nfm_if_b, [1.0], iq)

        # Downsample to intermediate rate
        iq_ds = self._decimate(iq_if, self._fm_decim)

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
        SSB demodulation via complex mixing (Weaver / BFO injection) method.

        For USB: mix the I/Q signal by e^(-j*2π*f_bfo*t) to shift the upper
        sideband audio to baseband, apply real LPF, decimate, take real part.
        For LSB: conjugate the I/Q first to mirror the spectrum, then same.

        This correctly removes the DC spike and isolates one sideband.
        """
        # Remove DC offset (HackRF DC spike → 50 Hz hum without this)
        iq = self._remove_dc(iq)

        # Mirror spectrum for LSB (converts LSB → USB processing)
        if not upper:
            iq = np.conj(iq)

        # BFO injection: shift signal so the SSB audio sits at baseband.
        # We inject at SSB_BW/2 so the centre of the voice band lands at DC.
        # This means 300–2700 Hz voice → -1200 to +1200 Hz after mixing.
        bfo_hz = self._ssb_bw / 2.0
        n = len(iq)
        t = np.arange(n, dtype=np.float32) / self._input_rate
        mix = np.exp(-1j * 2.0 * np.pi * bfo_hz * t).astype(np.complex64)
        iq_mixed = iq * mix

        # Decimate to intermediate rate
        iq_ds = self._decimate(iq_mixed, self._ssb_decim)

        # Apply real LPF at SSB_BW to the real (I) channel
        audio_raw = sp_signal.lfilter(self._ssb_audio_b, [1.0], iq_ds.real)

        # Decimate to audio rate
        audio = self._decimate(audio_raw, self._ssb_audio_decim)
        return self._finalize(audio.astype(np.float32))

    def _demod_cw(self, iq: np.ndarray) -> np.ndarray:
        """
        CW demodulation: direct decimation → wide BPF → output.

        SDR-based CW reception does NOT need envelope detection or sidetone
        synthesis.  The CW carrier sits at some audio-frequency offset from
        the SDR centre frequency.  After decimation and taking the real part,
        that carrier is already an audible tone (turns on/off with the key).
        Envelope detection of a bandpass-filtered noise floor produces a
        *constant* non-zero amplitude → AGC cranks it up → permanent hum,
        which is exactly what we want to avoid.

        We apply a moderately wide bandpass (300–3000 Hz) so the user has
        freedom to tune the satellite frequency without needing to hit an
        exact CW pitch offset.
        """
        # Remove DC offset (HackRF DC spike)
        iq = self._remove_dc(iq)

        # Decimate directly to AUDIO_RATE in two stages
        iq_ds = self._decimate(iq, self._cw_decim1)
        iq_ds = self._decimate(iq_ds, self._cw_decim2)

        # Real part: CW tone appears at its natural carrier-offset frequency
        audio_raw = iq_ds.real.astype(np.float32)

        # Wide BPF (300–3000 Hz) — SOS format for numerical stability
        audio = sp_signal.sosfilt(self._cw_bpf_sos, audio_raw).astype(np.float32)
        return self._finalize(audio)

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

        # ---- DC blocking high-pass IIR (applied before all modes) ----
        # Single-pole HPF at 30 Hz to remove DC offset without affecting audio.
        # α = 1 - 2π * f_c / f_s  (first-order IIR high-pass)
        alpha_dc = 1.0 - (2.0 * np.pi * 30.0 / rate)
        alpha_dc = float(np.clip(alpha_dc, 0.0, 0.9999))
        self._dc_b = np.array([1.0, -1.0], dtype=np.float64)
        self._dc_a = np.array([1.0, -alpha_dc], dtype=np.float64)

        # ---- NFM chain ----
        # Stage 1: decimate to ~200 kHz intermediate rate
        self._fm_decim = max(1, int(rate / 200_000))
        self._fm_rate = rate / self._fm_decim
        # Stage 2: decimate fm_rate → AUDIO_RATE
        self._fm_audio_decim = max(1, int(self._fm_rate / AUDIO_RATE))

        # IF bandpass for NFM: pass ±(deviation + audio_bw) around centre.
        # Limits interference from strong out-of-band signals before decimation.
        nfm_if_bw = (NFM_DEVIATION + 4_000.0) / (rate / 2.0)
        nfm_if_bw = float(np.clip(nfm_if_bw, 0.001, 0.499))
        self._nfm_if_b = sp_signal.firwin(63, nfm_if_bw).astype(np.float32)

        # De-emphasis IIR (single pole low-pass, τ = 75 µs)
        dt = 1.0 / self._fm_rate
        alpha = dt / (NFM_DEEMPH_TAU + dt)
        self._deemph_b = np.array([alpha], dtype=np.float64)
        self._deemph_a = np.array([1.0, -(1.0 - alpha)], dtype=np.float64)

        # ---- SSB chain ----
        # Stage 1: decimate input to ~96 kHz
        self._ssb_decim = max(1, int(rate / 96_000))
        ssb_mid_rate = rate / self._ssb_decim
        # Stage 2: decimate to AUDIO_RATE
        self._ssb_audio_decim = max(1, int(ssb_mid_rate / AUDIO_RATE))

        # Real LPF applied at ssb_mid_rate after BFO mixing.
        # Passes ±SSB_BW/2 (the mixed voice band) and rejects the image.
        nyq_mid = ssb_mid_rate / 2.0
        cutoff_mid = float(np.clip(self._ssb_bw / nyq_mid, 0.001, 0.499))
        self._ssb_audio_b = sp_signal.firwin(63, cutoff_mid).astype(np.float32)

        # ---- CW chain ----
        # CW uses a direct decimation path (bypasses SSB BFO injection).
        # Two-stage decimation: input_rate → ~96 kHz → AUDIO_RATE
        self._cw_decim1 = max(1, int(rate / 96_000))
        cw_mid_rate = rate / self._cw_decim1
        self._cw_decim2 = max(1, int(cw_mid_rate / AUDIO_RATE))

        # CW BPF applied at AUDIO_RATE.
        # Wide passband (300–3000 Hz): the CW tone sits at its natural carrier
        # offset, so the user can tune freely without hitting a fixed pitch.
        # SOS format is used — b,a Butterworth at even moderate bandwidths
        # can be numerically ill-conditioned at higher filter orders.
        nyq_audio = AUDIO_RATE / 2
        self._cw_bpf_sos = sp_signal.butter(
            4, [300.0 / nyq_audio, 3000.0 / nyq_audio], btype="band", output="sos"
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
