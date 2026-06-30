"""Communications > CW Decoder tab.

Decodes CW (Morse code) from audio using DeepCW ONNX models
(e04/web-deep-cw-decoder).  Audio input can come from:
  - SDR pipeline (audio_ready signal) when an SDR is connected
  - Soundcard InputStream (sounddevice) for rig/external audio

No rig is required — CW decoding is receive-only.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
from collections import deque
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from comms.cw.codec import SAMPLE_RATE, CwDecoder
from comms.cw.model_info import all_models_available, is_onnxruntime_available
from i18n import _

# Rolling audio buffer: keep the last N seconds for decoding
_BUFFER_SECONDS = 4
_DECODE_INTERVAL_MS = 2_000  # decode every 2 s

# Minimum audio length (seconds) required to attempt decoding
_MIN_DECODE_SECONDS = 0.5


# ---------------------------------------------------------------------------
# Background decode worker
# ---------------------------------------------------------------------------


class _DecodeWorker(QThread):
    """Runs CwDecoder.decode() off the UI thread."""

    result_ready = Signal(str)  # decoded text (may be empty)

    def __init__(self, decoder: CwDecoder, audio: NDArray[np.float32], sample_rate: int) -> None:
        super().__init__()
        self._decoder = decoder
        self._audio = audio
        self._sample_rate = sample_rate

    def run(self) -> None:
        text = self._decoder.decode(self._audio, self._sample_rate)
        self.result_ready.emit(text)


# ---------------------------------------------------------------------------
# CW Decoder tab
# ---------------------------------------------------------------------------


class CwTab(QWidget):
    """Non-resident Communications > CW Decoder tab."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        radio_control: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._conn = conn
        self._radio_control = radio_control

        # Decoder state
        self._decoder: CwDecoder | None = None
        self._lang: str = "en"

        # Audio accumulation buffer (deque of float32 arrays, all at SAMPLE_RATE)
        self._rx_buffer: deque[NDArray[np.float32]] = deque()
        self._rx_sample_rate: int = SAMPLE_RATE  # may differ for soundcard path
        self._sdr_connected: bool = False
        self._sdr_pipeline: Any = None

        # Sounddevice
        self._audio_stream: Any = None
        self._in_device: Any = None  # set from Rig Settings Sound Card config

        # Decode worker
        self._worker: _DecodeWorker | None = None
        self._decoding: bool = False
        self._last_text: str = ""

        # State
        self._running: bool = False

        self._setup_ui()
        self._load_sound_card_device()

        # Periodic decode timer
        self._decode_timer = QTimer(self)
        self._decode_timer.setInterval(_DECODE_INTERVAL_MS)
        self._decode_timer.timeout.connect(self._trigger_decode)

        # Level meter update timer
        self._level_timer = QTimer(self)
        self._level_timer.setInterval(200)
        self._level_timer.timeout.connect(self._update_level)

        self._refresh_model_status()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Top row: model status banner (hidden when OK)
        self._banner = QLabel()
        self._banner.setWordWrap(True)
        self._banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._banner.setStyleSheet(
            "background:#c0392b; color:white; padding:6px; border-radius:4px;"
        )
        self._banner.setVisible(False)
        root.addWidget(self._banner)

        # Controls row
        ctrl_box = QGroupBox(_("CW Decoder"))
        cl = QHBoxLayout(ctrl_box)

        # Input source
        cl.addWidget(QLabel(_("Input:")))
        self._rb_sdr = QRadioButton(_("SDR"))
        self._rb_sd = QRadioButton(_("Soundcard"))
        self._rb_sdr.setChecked(True)
        cl.addWidget(self._rb_sdr)
        cl.addWidget(self._rb_sd)
        self._rb_sdr.toggled.connect(self._on_source_changed)

        cl.addSpacing(16)

        # Language
        cl.addWidget(QLabel(_("Language:")))
        self._lang_combo = QComboBox()
        self._lang_combo.addItem("EN", "en")
        self._lang_combo.addItem("JA", "ja")
        self._lang_combo.setFixedWidth(72)
        self._lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        cl.addWidget(self._lang_combo)

        cl.addStretch()

        # Start / Stop
        self._start_btn = QPushButton(_("▶ Start"))
        self._start_btn.setCheckable(True)
        self._start_btn.setFixedWidth(90)
        self._start_btn.toggled.connect(self._on_start_stop)
        cl.addWidget(self._start_btn)

        # Clear button
        self._clear_btn = QPushButton(_("Clear"))
        self._clear_btn.setFixedWidth(72)
        self._clear_btn.clicked.connect(self._on_clear)
        cl.addWidget(self._clear_btn)

        root.addWidget(ctrl_box)

        # Status / level row
        stat_row = QHBoxLayout()
        self._status_label = QLabel(_("Ready"))
        self._status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        stat_row.addWidget(self._status_label)
        stat_row.addWidget(QLabel(_("Level:")))
        self._level_label = QLabel("— dB")
        self._level_label.setFixedWidth(72)
        stat_row.addWidget(self._level_label)
        root.addLayout(stat_row)

        # Decoded text output
        self._text_edit = QPlainTextEdit()
        self._text_edit.setReadOnly(True)
        font = QFont("Monospace")
        font.setPointSize(12)
        self._text_edit.setFont(font)
        self._text_edit.setPlaceholderText(_("Decoded CW text will appear here…"))
        root.addWidget(self._text_edit)

    # ------------------------------------------------------------------ #
    # Model status
    # ------------------------------------------------------------------ #

    def _refresh_model_status(self) -> None:
        """Show a banner if onnxruntime or model files are missing."""
        if not is_onnxruntime_available():
            self._banner.setText(_("onnxruntime not installed — run: pip install onnxruntime"))
            self._banner.setVisible(True)
            self._start_btn.setEnabled(False)
            return
        if not all_models_available():
            self._banner.setText(_("CW model files not found — use Help > CW Model Installation…"))
            self._banner.setVisible(True)
            self._start_btn.setEnabled(False)
            return
        self._banner.setVisible(False)
        self._start_btn.setEnabled(True)

    # ------------------------------------------------------------------ #
    # Decoder lifecycle
    # ------------------------------------------------------------------ #

    def _ensure_decoder(self) -> bool:
        """Load the decoder for the current language if not loaded."""
        if self._decoder is not None and self._decoder.is_ready:
            return True
        self._decoder = CwDecoder(lang=self._lang)
        return self._decoder.is_ready

    # ------------------------------------------------------------------ #
    # Start / Stop
    # ------------------------------------------------------------------ #

    @Slot(bool)
    def _on_start_stop(self, checked: bool) -> None:
        if checked:
            self._start()
        else:
            self._stop()

    def _start(self) -> None:
        if not self._ensure_decoder():
            self._status_label.setText(
                _("Model not ready — install via Help > CW Model Installation…")
            )
            self._start_btn.setChecked(False)
            return

        self._running = True
        self._rx_buffer.clear()
        self._last_text = ""
        self._start_btn.setText(_("■ Stop"))

        source = "sdr" if self._rb_sdr.isChecked() else "soundcard"
        if source == "sdr":
            self._connect_sdr_audio()
            if not self._sdr_connected:
                self._status_label.setText(_("SDR not connected — connect SDR first"))
        else:
            self._start_audio_capture()

        self._decode_timer.start()
        self._level_timer.start()
        self._status_label.setText(_("Listening…"))

    def _stop(self) -> None:
        self._running = False
        self._decode_timer.stop()
        self._level_timer.stop()
        self._start_btn.setText(_("▶ Start"))
        self._disconnect_sdr_audio()
        self._stop_audio_capture()
        self._status_label.setText(_("Stopped"))
        self._level_label.setText("— dB")

    # ------------------------------------------------------------------ #
    # Source / language changes
    # ------------------------------------------------------------------ #

    @Slot()
    def _on_source_changed(self) -> None:
        if self._running:
            self._stop()
            self._start_btn.setChecked(False)

    @Slot(int)
    def _on_lang_changed(self, _index: int) -> None:
        self._lang = self._lang_combo.currentData() or "en"
        if self._decoder is not None:
            self._decoder.reload(self._lang)

    @Slot()
    def _on_clear(self) -> None:
        self._text_edit.clear()
        self._last_text = ""

    # ------------------------------------------------------------------ #
    # SDR audio connection
    # ------------------------------------------------------------------ #

    def _connect_sdr_audio(self) -> None:
        """Subscribe to the SDR pipeline's audio_ready signal."""
        if self._radio_control is None:
            return
        try:
            sdr_ctrl = getattr(self._radio_control, "_sdr_control", None)
            if sdr_ctrl is None:
                return
            pipeline = getattr(sdr_ctrl, "_pipeline", None)
            if pipeline is None:
                return
            self._sdr_pipeline = pipeline
            pipeline.audio_ready.connect(self._on_sdr_audio_chunk)
            self._sdr_connected = True
        except Exception:
            pass

    def _disconnect_sdr_audio(self) -> None:
        if self._sdr_pipeline is not None:
            with contextlib.suppress(Exception):
                self._sdr_pipeline.audio_ready.disconnect(self._on_sdr_audio_chunk)
            self._sdr_pipeline = None
        self._sdr_connected = False

    @Slot(object)
    def _on_sdr_audio_chunk(self, chunk: NDArray[np.float32]) -> None:
        if not self._running or not self._rb_sdr.isChecked():
            return
        self._rx_buffer.append(chunk.astype(np.float32))
        # Keep buffer bounded: drop oldest if > _BUFFER_SECONDS
        self._trim_buffer()

    # ------------------------------------------------------------------ #
    # Soundcard audio capture
    # ------------------------------------------------------------------ #

    def _load_sound_card_device(self) -> None:
        """Load sound card input device index from app settings."""
        try:
            from data.database import get_app_setting

            dev_index = get_app_setting(self._conn, "sound_card_in_device")
            if dev_index is not None:
                self._in_device = int(dev_index)
        except Exception:
            pass

    def _start_audio_capture(self) -> None:
        if self._audio_stream is not None:
            return
        try:
            import sounddevice as sd
        except ImportError:
            self._status_label.setText(_("sounddevice not installed — pip install sounddevice"))
            return
        if self._in_device is None:
            self._status_label.setText(
                _("Sound Card not configured — open Rig Settings > Sound Card")
            )
            return
        self._rx_buffer.clear()
        self._rx_sample_rate = 48_000
        try:
            self._audio_stream = sd.InputStream(
                samplerate=self._rx_sample_rate,
                channels=1,
                dtype="float32",
                device=self._in_device,
                callback=self._audio_callback,
            )
            self._audio_stream.start()
        except Exception as exc:
            self._status_label.setText(f"Audio open error: {exc}")
            self._audio_stream = None

    def _stop_audio_capture(self) -> None:
        if self._audio_stream is not None:
            with contextlib.suppress(Exception):
                self._audio_stream.stop()
                self._audio_stream.close()
            self._audio_stream = None

    def _audio_callback(
        self,
        indata: NDArray[np.float32],
        frames: int,
        _time: Any,
        _status: Any,
    ) -> None:
        chunk = indata[:, 0].copy()
        self._rx_buffer.append(chunk)
        self._trim_buffer()

    # ------------------------------------------------------------------ #
    # Buffer management
    # ------------------------------------------------------------------ #

    def _trim_buffer(self) -> None:
        """Discard oldest chunks to keep total duration <= _BUFFER_SECONDS."""
        sr = self._rx_sample_rate if not self._rb_sdr.isChecked() else SAMPLE_RATE
        max_samples = _BUFFER_SECONDS * sr
        total = sum(len(c) for c in self._rx_buffer)
        while total > max_samples and self._rx_buffer:
            removed = self._rx_buffer.popleft()
            total -= len(removed)

    def _get_audio_snapshot(self) -> NDArray[np.float32] | None:
        """Concatenate current buffer into a single float32 array."""
        if not self._rx_buffer:
            return None
        return np.concatenate(list(self._rx_buffer), axis=0).astype(np.float32)

    # ------------------------------------------------------------------ #
    # Periodic decode
    # ------------------------------------------------------------------ #

    @Slot()
    def _trigger_decode(self) -> None:
        if self._decoding or not self._running:
            return
        if self._decoder is None or not self._decoder.is_ready:
            return

        audio = self._get_audio_snapshot()
        if audio is None:
            return

        sr = self._rx_sample_rate if not self._rb_sdr.isChecked() else SAMPLE_RATE
        if len(audio) < _MIN_DECODE_SECONDS * sr:
            return

        self._decoding = True
        self._worker = _DecodeWorker(self._decoder, audio, sr)
        self._worker.result_ready.connect(self._on_decode_result)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    @Slot(str)
    def _on_decode_result(self, text: str) -> None:
        if not text:
            return
        # Only append text that differs from the previous result
        if text != self._last_text:
            self._last_text = text
            self._append_text(text)

    @Slot()
    def _on_worker_finished(self) -> None:
        self._decoding = False
        self._worker = None

    def _append_text(self, text: str) -> None:
        """Append decoded text to the output pane with a line break."""
        # Collapse extra spaces and strip
        cleaned = re.sub(r" {2,}", " ", text).strip()
        if cleaned:
            self._text_edit.appendPlainText(cleaned)
            # Scroll to bottom
            scrollbar = self._text_edit.verticalScrollBar()
            if scrollbar is not None:
                scrollbar.setValue(scrollbar.maximum())

    # ------------------------------------------------------------------ #
    # Level meter
    # ------------------------------------------------------------------ #

    @Slot()
    def _update_level(self) -> None:
        audio = self._get_audio_snapshot()
        if audio is None or len(audio) == 0:
            self._level_label.setText("— dB")
            return
        rms = float(np.sqrt(np.mean(audio**2)))
        db = 20.0 * np.log10(max(rms, 1e-10))
        self._level_label.setText(f"{db:.1f} dB")

    # ------------------------------------------------------------------ #
    # Public API: rig / SDR state notifications (called by MainWindow)
    # ------------------------------------------------------------------ #

    def notify_rig1_connected(self) -> None:
        """Called when Rig 1 connects (unused for CW, but kept for interface parity)."""

    def notify_rig1_disconnected(self) -> None:
        """Called when Rig 1 disconnects."""

    def notify_sdr_connected(self) -> None:
        """Called when an SDR connects — enable SDR radio button."""
        self._rb_sdr.setEnabled(True)

    def notify_sdr_disconnected(self) -> None:
        """Called when SDR disconnects — disable SDR button and stop if active."""
        self._rb_sdr.setEnabled(False)
        if self._running and self._rb_sdr.isChecked():
            self._stop()
            self._start_btn.setChecked(False)
            self._disconnect_sdr_audio()
            self._status_label.setText(_("SDR disconnected"))

    # ------------------------------------------------------------------ #
    # Cleanup
    # ------------------------------------------------------------------ #

    def closeEvent(self, event: Any) -> None:  # type: ignore[override]
        self._stop()
        super().closeEvent(event)
