"""
SDR Control tab widget.

SdrControlWidget — Active only when an SDR device is connected (Rig 1 or Rig 2).
Contains three panels:
  - Spectrum display  (real-time FFT via QtCharts QLineSeries)
  - Demodulator       (mode / filter / volume / AGC / start-stop audio)
  - IQ Recorder       (bandwidth / record / stop / elapsed time)

The widget is notified of transponder changes from RadioControlWidget so it
can auto-select the correct demodulation mode.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from i18n import _
from sdr import SOAPY_AVAILABLE

if SOAPY_AVAILABLE:
    from sdr.demodulator import DemodMode

logger = logging.getLogger(__name__)

# Spectrum chart Y-axis range (dBFS)
_SPECTRUM_YMIN: float = -90.0
_SPECTRUM_YMAX: float = 0.0

# IQ recording bandwidths offered in the dropdown
_REC_BANDWIDTHS: list[tuple[str, int]] = [
    ("50 kHz", 50_000),
    ("100 kHz", 100_000),
    ("250 kHz", 250_000),
    ("500 kHz", 500_000),
    ("1 MHz", 1_000_000),
]


class SdrControlWidget(QWidget):
    """
    SDR Control panel.

    Call set_pipeline(pipeline) when an SDR connects.
    Call set_pipeline(None) when it disconnects.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pipeline: Any = None  # SDRPipeline | None
        self._recording = False
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1_000)
        self._status_timer.timeout.connect(self._update_rec_status)
        self._setup_ui()
        self.setEnabled(False)  # disabled until SDR connects

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_pipeline(self, pipeline: Any) -> None:  # SDRPipeline | None
        """Attach or detach the active SDRPipeline."""
        # Detach old pipeline
        if self._pipeline is not None:
            try:
                self._pipeline.spectrum_ready.disconnect(self._on_spectrum)
                self._pipeline.status_changed.disconnect(self._on_status)
            except Exception:
                pass

        self._pipeline = pipeline
        self.setEnabled(pipeline is not None)

        if pipeline is not None:
            pipeline.spectrum_ready.connect(self._on_spectrum)
            pipeline.status_changed.connect(self._on_status)
            self._status_label.setText(_("SDR Connected"))
        else:
            self._status_label.setText(_("SDR Disconnected"))
            self._stop_audio()
            self._stop_recording()

    def set_transponder_mode(self, satnogs_mode: str) -> None:
        """Auto-select demodulator mode from a SATNOGS transponder mode string."""
        if not SOAPY_AVAILABLE:
            return
        mode = DemodMode.from_satnogs(satnogs_mode)
        mode_map = {
            DemodMode.NFM: 0,
            DemodMode.USB: 1,
            DemodMode.LSB: 2,
            DemodMode.CW: 3,
        }
        idx = mode_map.get(mode, 1)
        self._mode_combo.setCurrentIndex(idx)

    def set_iq_save_dir(self, path: str) -> None:
        """Update the IQ recording save directory (from SDR settings)."""
        self._iq_save_dir = Path(path) if path else Path.home() / "iq_recordings"

    def set_satellite_info(self, norad: int, name: str) -> None:
        """Store satellite info used to name IQ recordings."""
        self._sat_norad = norad
        self._sat_name = name

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(8)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        # Status bar
        self._status_label = QLabel(_("SDR Disconnected"))
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet("color: gray; font-weight: bold;")
        layout.addWidget(self._status_label)

        # Spectrum
        layout.addWidget(self._build_spectrum_panel())
        # Demodulator
        layout.addWidget(self._build_demod_panel())
        # IQ recorder
        layout.addWidget(self._build_recorder_panel())
        layout.addStretch()

        # Private state
        self._iq_save_dir = Path.home() / "iq_recordings"
        self._sat_norad = 0
        self._sat_name = "unknown"

    def _build_spectrum_panel(self) -> QGroupBox:
        grp = QGroupBox(_("Spectrum"))
        v = QVBoxLayout(grp)

        # QtCharts spectrum display
        self._spectrum_series = QLineSeries()
        pen = QPen(QColor("#00dcff"))
        pen.setWidth(1)
        self._spectrum_series.setPen(pen)

        self._spectrum_chart = QChart()
        self._spectrum_chart.addSeries(self._spectrum_series)
        self._spectrum_chart.setBackgroundBrush(QColor("#1a1a2e"))
        self._spectrum_chart.legend().hide()
        self._spectrum_chart.setMargins(
            __import__("PySide6.QtCore", fromlist=["QMargins"]).QMargins(4, 4, 4, 4)
        )

        # Axes
        self._freq_axis = QValueAxis()
        self._freq_axis.setTitleText(_("Frequency (MHz)"))
        self._freq_axis.setLabelFormat("%.3f")
        self._freq_axis.setLabelsColor(QColor("#cccccc"))
        self._freq_axis.setGridLineColor(QColor("#333355"))

        self._pwr_axis = QValueAxis()
        self._pwr_axis.setTitleText(_("Power (dBFS)"))
        self._pwr_axis.setRange(_SPECTRUM_YMIN, _SPECTRUM_YMAX)
        self._pwr_axis.setLabelsColor(QColor("#cccccc"))
        self._pwr_axis.setGridLineColor(QColor("#333355"))

        self._spectrum_chart.addAxis(self._freq_axis, Qt.AlignmentFlag.AlignBottom)
        self._spectrum_chart.addAxis(self._pwr_axis, Qt.AlignmentFlag.AlignLeft)
        self._spectrum_series.attachAxis(self._freq_axis)
        self._spectrum_series.attachAxis(self._pwr_axis)

        chart_view = QChartView(self._spectrum_chart)
        chart_view.setMinimumHeight(160)
        chart_view.setRenderHint(
            __import__("PySide6.QtGui", fromlist=["QPainter"]).QPainter.RenderHint.Antialiasing,
            False,
        )
        v.addWidget(chart_view)
        return grp

    def _build_demod_panel(self) -> QGroupBox:
        grp = QGroupBox(_("Demodulator"))
        form = QVBoxLayout(grp)

        # Mode row
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel(_("Mode:")))
        self._mode_combo = QComboBox()
        for m in ("NFM", "USB", "LSB", "CW"):
            self._mode_combo.addItem(m)
        self._mode_combo.setCurrentIndex(1)  # USB default
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self._mode_combo)
        mode_row.addStretch()
        form.addLayout(mode_row)

        # Volume slider
        vol_row = QHBoxLayout()
        vol_row.addWidget(QLabel(_("Volume:")))
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(70)
        self._vol_slider.valueChanged.connect(self._on_volume_changed)
        self._vol_label = QLabel("70%")
        vol_row.addWidget(self._vol_slider)
        vol_row.addWidget(self._vol_label)
        form.addLayout(vol_row)

        # AGC checkbox
        agc_row = QHBoxLayout()
        self._agc_rb_on = QRadioButton(_("AGC On"))
        self._agc_rb_on.setChecked(True)
        self._agc_rb_off = QRadioButton(_("AGC Off"))
        self._agc_rb_on.toggled.connect(self._on_agc_changed)
        agc_row.addWidget(self._agc_rb_on)
        agc_row.addWidget(self._agc_rb_off)
        agc_row.addStretch()
        form.addLayout(agc_row)

        # Audio buttons
        btn_row = QHBoxLayout()
        self._start_audio_btn = QPushButton(_("▶ Start Audio"))
        self._stop_audio_btn = QPushButton(_("■ Stop Audio"))
        self._stop_audio_btn.setEnabled(False)
        self._start_audio_btn.clicked.connect(self._start_audio)
        self._stop_audio_btn.clicked.connect(self._stop_audio)
        btn_row.addWidget(self._start_audio_btn)
        btn_row.addWidget(self._stop_audio_btn)
        btn_row.addStretch()
        form.addLayout(btn_row)
        return grp

    def _build_recorder_panel(self) -> QGroupBox:
        grp = QGroupBox(_("IQ Recorder"))  # noqa: F823
        v = QVBoxLayout(grp)

        bw_row = QHBoxLayout()
        bw_row.addWidget(QLabel(_("Record BW:")))
        self._rec_bw_combo = QComboBox()
        for label, _bw in _REC_BANDWIDTHS:
            self._rec_bw_combo.addItem(label)
        self._rec_bw_combo.setCurrentIndex(2)  # 250 kHz default
        bw_row.addWidget(self._rec_bw_combo)
        bw_row.addStretch()
        v.addLayout(bw_row)

        file_row = QHBoxLayout()
        file_row.addWidget(QLabel(_("File:")))
        self._rec_file_label = QLabel("—")
        self._rec_file_label.setStyleSheet("color: gray; font-size: 10px;")
        file_row.addWidget(self._rec_file_label)
        v.addLayout(file_row)

        ctrl_row = QHBoxLayout()
        self._rec_btn = QPushButton(_("● REC"))
        self._rec_btn.setStyleSheet("color: red; font-weight: bold;")
        self._stop_rec_btn = QPushButton(_("■ STOP"))
        self._stop_rec_btn.setEnabled(False)
        self._rec_status_label = QLabel("00:00:00  0 MB")
        self._rec_btn.clicked.connect(self._start_recording)
        self._stop_rec_btn.clicked.connect(self._stop_recording)
        ctrl_row.addWidget(self._rec_btn)
        ctrl_row.addWidget(self._stop_rec_btn)
        ctrl_row.addWidget(self._rec_status_label)
        ctrl_row.addStretch()
        v.addLayout(ctrl_row)
        return grp

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    @Slot(list)
    def _on_spectrum(self, points: list[tuple[float, float]]) -> None:
        """Update the spectrum chart with new FFT data."""
        if not points:
            return
        self._spectrum_series.clear()
        # Convert Hz to MHz for the axis
        pts = [(f / 1e6, p) for f, p in points]
        # Use replace() for efficiency when series already has data
        from PySide6.QtCore import QPointF

        self._spectrum_series.replace([QPointF(f, p) for f, p in pts])
        freqs = [f for f, _ in pts]
        if freqs:
            self._freq_axis.setRange(min(freqs), max(freqs))

    @Slot(str)
    def _on_status(self, msg: str) -> None:
        self._status_label.setText(msg)
        self._status_label.setStyleSheet(
            "color: #00dcff; font-weight: bold;"
            if "stream" in msg.lower()
            else "color: gray; font-weight: bold;"
        )

    def _on_mode_changed(self, idx: int) -> None:
        if self._pipeline is None or not SOAPY_AVAILABLE:
            return
        modes = [DemodMode.NFM, DemodMode.USB, DemodMode.LSB, DemodMode.CW]
        if 0 <= idx < len(modes):
            self._pipeline.set_demod_mode(modes[idx])

    def _on_volume_changed(self, value: int) -> None:
        self._vol_label.setText(f"{value}%")
        if self._pipeline is not None:
            self._pipeline.set_audio_gain(value / 100.0)

    def _on_agc_changed(self, on: bool) -> None:
        if self._pipeline is not None:
            self._pipeline.set_agc(on)

    def _start_audio(self) -> None:
        if self._pipeline is None:
            return
        self._pipeline.set_audio_enabled(True)
        self._start_audio_btn.setEnabled(False)
        self._stop_audio_btn.setEnabled(True)

    def _stop_audio(self) -> None:
        if self._pipeline is not None:
            self._pipeline.set_audio_enabled(False)
        self._start_audio_btn.setEnabled(True)
        self._stop_audio_btn.setEnabled(False)

    def _start_recording(self) -> None:
        if self._pipeline is None or self._recording:
            return
        idx = self._rec_bw_combo.currentIndex()
        bw_hz = _REC_BANDWIDTHS[idx][1] if 0 <= idx < len(_REC_BANDWIDTHS) else 250_000

        # Adjust device sample rate for recording bandwidth
        if hasattr(self._pipeline, "_device") and self._pipeline._device is not None:
            self._pipeline._device.set_sample_rate(float(bw_hz))

        file_path = self._pipeline.recorder.start(
            sample_rate=bw_hz,
            norad=self._sat_norad,
            sat_name=self._sat_name,
        )
        self._recording = True
        self._rec_file_label.setText(file_path.name)
        self._rec_btn.setEnabled(False)
        self._stop_rec_btn.setEnabled(True)
        self._status_timer.start()

    def _stop_recording(self) -> None:
        if not self._recording:
            return
        self._recording = False
        self._status_timer.stop()
        if self._pipeline is not None:
            self._pipeline.recorder.stop()
        self._rec_btn.setEnabled(True)
        self._stop_rec_btn.setEnabled(False)
        self._rec_status_label.setText("00:00:00  0 MB")

    def _update_rec_status(self) -> None:
        if self._pipeline is None:
            return
        rec = self._pipeline.recorder
        elapsed = int(rec.elapsed_seconds)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        mb = rec.bytes_written / 1e6
        self._rec_status_label.setText(f"{h:02d}:{m:02d}:{s:02d}  {mb:.1f} MB")
