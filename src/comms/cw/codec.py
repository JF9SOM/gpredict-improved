"""CW decoder inference: spectrogram → ONNX → CTC decode.

Reproduces the signal processing pipeline from e04/web-deep-cw-decoder
(MIT-licensed preprocessing logic; ONNX model weights © e04).

Key parameters (must match the trained model):
  SAMPLE_RATE     = 9 600 Hz
  FFT_LENGTH      = 768
  HOP_LENGTH      = 192
  BIN_RESOLUTION  = 12.5 Hz
  CROPPED_BINS    = 65  (bins 32–96, covering 400–1200 Hz)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from comms.cw.model_info import find_model

if TYPE_CHECKING:
    import onnxruntime as ort

# ---------------------------------------------------------------------------
# Constants (mirror src/const.ts)
# ---------------------------------------------------------------------------

SAMPLE_RATE = 9_600
FFT_LENGTH = 768
HOP_LENGTH = 192
BIN_RESOLUTION = SAMPLE_RATE / FFT_LENGTH  # 12.5 Hz

_DECODABLE_MIN_HZ = 400.0
_DECODABLE_MAX_HZ = 1_200.0
_START_BIN = round(_DECODABLE_MIN_HZ / BIN_RESOLUTION)  # 32
_END_BIN = round(_DECODABLE_MAX_HZ / BIN_RESOLUTION) + 1  # 97
CROPPED_BINS = _END_BIN - _START_BIN  # 65

# Vocabularies and blank indices (mirror src/const.ts)
_EN_VOCAB: list[str] = list(",./0123456789?ABCDEFGHIJKLMNOPQRSTUVWXYZ ") + ["<blank>"]
_EN_BLANK = 41

_JA_VOCAB: list[str] = [
    "0",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "?",
    "、",
    "」",
    "゛",
    "゜",
    "ア",
    "イ",
    "ウ",
    "エ",
    "オ",
    "カ",
    "キ",
    "ク",
    "ケ",
    "コ",
    "サ",
    "シ",
    "ス",
    "セ",
    "ソ",
    "タ",
    "チ",
    "ツ",
    "テ",
    "ト",
    "ナ",
    "ニ",
    "ヌ",
    "ネ",
    "ノ",
    "ハ",
    "ヒ",
    "フ",
    "ヘ",
    "ホ",
    "マ",
    "ミ",
    "ム",
    "メ",
    "モ",
    "ヤ",
    "ユ",
    "ヨ",
    "ラ",
    "リ",
    "ル",
    "レ",
    "ロ",
    "ワ",
    "ヰ",
    "ヱ",
    "ヲ",
    "ン",
    "ー",
    "（",
    "）",
    " ",
    "<blank>",
]
_JA_BLANK = 67

# Hann window matching e04's formula: 0.5*(1 - cos(2π*i/N))
_HANN_WINDOW: NDArray[np.float32] = (
    0.5 * (1.0 - np.cos(2.0 * np.pi * np.arange(FFT_LENGTH) / FFT_LENGTH))
).astype(np.float32)


# ---------------------------------------------------------------------------
# Spectrogram helpers
# ---------------------------------------------------------------------------


def _resample(audio: NDArray[np.float32], src_rate: int) -> NDArray[np.float32]:
    """Resample *audio* from *src_rate* to SAMPLE_RATE (9600 Hz)."""
    if src_rate == SAMPLE_RATE:
        return audio
    try:
        from math import gcd

        from scipy.signal import resample_poly

        g = gcd(SAMPLE_RATE, src_rate)
        up, down = SAMPLE_RATE // g, src_rate // g
        return resample_poly(audio, up, down).astype(np.float32)
    except ImportError:
        # Fallback: linear interpolation
        n_out = int(len(audio) * SAMPLE_RATE / src_rate)
        indices = np.linspace(0, len(audio) - 1, n_out)
        return np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)


def _compute_spectrogram(audio: NDArray[np.float32]) -> NDArray[np.float32] | None:
    """Compute the cropped, normalised magnitude spectrogram.

    Returns float32 array of shape (1, 1, time_steps, CROPPED_BINS),
    or None if the audio is too short.
    """
    n = len(audio)
    n_frames = (n - FFT_LENGTH) // HOP_LENGTH + 1
    if n_frames <= 0:
        return None

    # Build frame matrix using stride tricks (zero-copy view)
    frames = np.lib.stride_tricks.as_strided(
        audio,
        shape=(n_frames, FFT_LENGTH),
        strides=(audio.strides[0] * HOP_LENGTH, audio.strides[0]),
    )
    windowed = (frames * _HANN_WINDOW).astype(np.float32)

    # Real FFT → magnitude, shape (n_frames, FFT_LENGTH//2+1)
    spectra = np.abs(np.fft.rfft(windowed, n=FFT_LENGTH)).astype(np.float32)

    # Crop to [400 Hz, 1200 Hz) → 65 bins
    cropped = spectra[:, _START_BIN:_END_BIN]  # (n_frames, 65)

    # Per-sample CMVN (whole spectrogram treated as one flat sample)
    flat = cropped.ravel()
    mean = float(flat.mean())
    std = max(float(flat.std()), 1e-5)
    flat = (flat - mean) / std

    return flat.reshape(1, 1, n_frames, CROPPED_BINS).astype(np.float32)


# ---------------------------------------------------------------------------
# CTC greedy decoder
# ---------------------------------------------------------------------------


def _ctc_decode(logits: NDArray[np.float32], vocab: list[str], blank: int) -> str:
    """Greedy CTC decode of shape (1, time_steps, num_classes) → text.

    Blank frames emit a display space; repeated non-blank labels collapse.
    Matches decodeCtcForDisplay() in textDecoder.ts.
    """
    pred = logits[0]  # (time_steps, num_classes)
    indices = pred.argmax(axis=-1)  # (time_steps,)

    chars: list[str] = []
    prev: int | None = None
    for idx in indices.tolist():
        if idx == blank:
            chars.append(" ")
            prev = None
        elif idx == prev:
            chars.append(" ")
        else:
            prev = idx
            ch = vocab[idx] if idx < len(vocab) else ""
            chars.append(ch)

    # Collapse runs of multiple spaces to single space and strip edges
    text = "".join(chars)
    import re

    text = re.sub(r" {2,}", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# CwDecoder
# ---------------------------------------------------------------------------


class CwDecoder:
    """Wraps the DeepCW ONNX model for inference.

    Usage::

        decoder = CwDecoder(lang="en")
        text = decoder.decode(audio_float32, sample_rate=48000)
    """

    def __init__(self, lang: str = "en") -> None:
        self._lang = lang
        self._session: ort.InferenceSession | None = None
        self._vocab, self._blank = self._get_vocab(lang)
        self._load()

    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_vocab(lang: str) -> tuple[list[str], int]:
        if lang == "ja":
            return _JA_VOCAB, _JA_BLANK
        return _EN_VOCAB, _EN_BLANK

    def _load(self) -> None:
        model_name = "ja" if self._lang == "ja" else "en"
        model_path = find_model(model_name)
        if model_path is None:
            return
        try:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.log_severity_level = 3  # suppress verbose ORT logs
            self._session = ort.InferenceSession(
                str(model_path),
                sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
        except Exception:
            self._session = None

    @property
    def is_ready(self) -> bool:
        """Return True if the model is loaded and ready."""
        return self._session is not None

    def decode(self, audio: NDArray[np.float32], sample_rate: int) -> str:
        """Decode *audio* (float32, mono) and return the CW text.

        Returns an empty string if the model is not loaded or the audio
        is too short.
        """
        if self._session is None:
            return ""

        resampled = _resample(audio, sample_rate)
        spec = _compute_spectrogram(resampled)
        if spec is None:
            return ""

        input_name = self._session.get_inputs()[0].name
        outputs = self._session.run(None, {input_name: spec})
        logits: NDArray[np.float32] = outputs[0]  # (1, time_steps, num_classes)
        return _ctc_decode(logits, self._vocab, self._blank)

    def reload(self, lang: str | None = None) -> None:
        """Reload the model, optionally switching language."""
        if lang is not None:
            self._lang = lang
            self._vocab, self._blank = self._get_vocab(lang)
        self._session = None
        self._load()


# ---------------------------------------------------------------------------
# CwDetector
# ---------------------------------------------------------------------------


class CwDetector:
    """Uses the cw_detect ONNX model to find CW signal frequencies.

    Returns a list of (frequency_hz, snr_db) tuples.
    """

    _MIN_HZ = 100.0
    _MAX_HZ = 2_000.0

    def __init__(self) -> None:
        self._session: ort.InferenceSession | None = None
        self._load()

    def _load(self) -> None:
        model_path = find_model("detect")
        if model_path is None:
            return
        try:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.log_severity_level = 3
            self._session = ort.InferenceSession(
                str(model_path),
                sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
        except Exception:
            self._session = None

    @property
    def is_ready(self) -> bool:
        return self._session is not None

    def detect(self, audio: NDArray[np.float32], sample_rate: int) -> list[tuple[float, float]]:
        """Return list of (frequency_hz, snr_db) for detected CW signals."""
        if self._session is None:
            return []

        resampled = _resample(audio, sample_rate)
        tensor, frequencies = self._build_bin_sequences(resampled)
        if tensor is None or frequencies is None:
            return []

        input_name = self._session.get_inputs()[0].name
        outputs = self._session.run(None, {input_name: tensor})
        snr_values: NDArray[np.float32] = outputs[0].ravel()

        results = [
            (float(freq), float(snr))
            for freq, snr in zip(frequencies, snr_values, strict=False)
            if snr >= -10.0
        ]
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:5]

    @staticmethod
    def _build_bin_sequences(
        audio: NDArray[np.float32],
    ) -> tuple[NDArray[np.float32] | None, list[float] | None]:
        """Build the bin-sequence tensor for the cw_detect model.

        Returns (tensor of shape (batch, 1, time_steps), frequency_list).
        Matches audioToBinSequenceTensor() in spectrogramUtils.ts.
        """
        n = len(audio)
        n_frames = (n - FFT_LENGTH) // HOP_LENGTH + 1
        if n_frames <= 0:
            return None, None

        total_bins = FFT_LENGTH // 2 + 1
        min_bin = max(0, int(CwDetector._MIN_HZ / BIN_RESOLUTION))
        max_bin = min(total_bins - 1, int(np.ceil(CwDetector._MAX_HZ / BIN_RESOLUTION)))
        batch_size = max_bin - min_bin + 1

        frames = np.lib.stride_tricks.as_strided(
            audio,
            shape=(n_frames, FFT_LENGTH),
            strides=(audio.strides[0] * HOP_LENGTH, audio.strides[0]),
        )
        windowed = (frames * _HANN_WINDOW).astype(np.float32)
        spectra = np.abs(np.fft.rfft(windowed, n=FFT_LENGTH)).astype(np.float32)
        # spectra: (n_frames, total_bins)

        cropped = spectra[:, min_bin : max_bin + 1].T  # (batch_size, n_frames)
        log_mag = np.log1p(cropped)

        # Per-sequence CMVN
        mean = log_mag.mean(axis=1, keepdims=True)
        std = np.maximum(log_mag.std(axis=1, keepdims=True), 1e-4)
        normalized = (log_mag - mean) / std  # (batch_size, n_frames)

        tensor = normalized[:, np.newaxis, :]  # (batch_size, 1, n_frames)
        frequencies = [(min_bin + i) * BIN_RESOLUTION for i in range(batch_size)]
        return tensor.astype(np.float32), frequencies
