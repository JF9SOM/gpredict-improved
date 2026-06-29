"""METEOR / HRPT reception tab — Communications > METEOR / HRPT.

Uses SatDump as a subprocess to receive and decode LRPT imagery from
METEOR-M satellites.  While SatDump is running it holds exclusive access
to the SDR device, so the SDR Control tab is greyed out.

Lifecycle
---------
* User opens the tab via Communications > METEOR / HRPT.
* User selects a satellite / pipeline from the combo box.
* User selects the SoapySDR source string (e.g. ``rtlsdr``).
* User clicks [▶ Start]:
    - If an SDR is active, it is disconnected automatically.
    - The SDR Control tab is disabled.
    - SatDumpProcess is launched in a background QThread.
    - ImageWatcher polls the output directory for new PNGs.
* User clicks [■ Stop] (or the process ends on its own):
    - SatDump is terminated.
    - SDR Control tab is re-enabled.
* Tab × closes the tab and stops any running process.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, QStandardPaths, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from comms.meteor.image_watcher import ImageWatcher
from comms.meteor.satdump import METEOR_PIPELINES, SatDumpProcess, find_satdump
from i18n import _

_THUMB_W = 160
_THUMB_H = 100


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_output_dir() -> Path:
    pics = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    base = Path(pics) if pics else Path.home() / "Pictures"
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return base / "fbsat59_meteor" / ts


def _list_soapy_sources() -> list[str]:
    """Return a list of SoapySDR driver strings available on this system."""
    sources: list[str] = []
    try:
        import SoapySDR  # type: ignore[import-untyped]

        for dev in SoapySDR.Device.enumerate():
            driver = dev.get("driver", "")
            if driver and driver not in sources:
                sources.append(driver)
    except Exception:
        pass
    # Fallback common drivers if SoapySDR not available or no devices found
    if not sources:
        sources = ["rtlsdr", "hackrf", "airspy"]
    return sources


# ---------------------------------------------------------------------------
# Thumbnail list item
# ---------------------------------------------------------------------------


class _ThumbItem(QListWidgetItem):
    def __init__(self, image: QImage, label: str) -> None:
        super().__init__()
        self.full_image = image.copy()
        thumb = image.scaled(
            _THUMB_W,
            _THUMB_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setIcon(QIcon(QPixmap.fromImage(thumb)))
        self.setText(label)
        self.setSizeHint(QSize(_THUMB_W + 8, _THUMB_H + 28))


# ---------------------------------------------------------------------------
# Main widget
# ---------------------------------------------------------------------------


class MeteorTab(QWidget):
    """Non-resident tab opened from Communications > METEOR / HRPT."""

    # Emitted when the user changes the pipeline combo so main_window can
    # sync the satellite list and Radio Control transponder selection.
    satellite_selection_requested: Signal = Signal(int, int)  # norad, downlink_hz

    def __init__(
        self,
        sdr_control_tab: QWidget | None = None,
        sdr_widget: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._sdr_control_tab = sdr_control_tab
        self._sdr_widget = sdr_widget  # SdrControlWidget instance for disconnect
        self._process: SatDumpProcess | None = None
        self._watcher: ImageWatcher | None = None
        self._output_dir: Path | None = None
        self._suppress_sync: bool = False  # prevents feedback loop during Radio Control sync
        self._setup_ui()
        self._check_satdump()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # --- Warning banner (shown when SatDump is not found) ---
        self._banner = QLabel()
        self._banner.setWordWrap(True)
        self._banner.setStyleSheet(
            "background:#c0392b; color:white; padding:6px; border-radius:4px;"
        )
        self._banner.setVisible(False)
        root.addWidget(self._banner)

        # --- Control row ---
        ctrl_box = QGroupBox(_("Reception Control"))
        ctrl_layout = QVBoxLayout(ctrl_box)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel(_("Satellite / Pipeline:")))
        self._combo_sat = QComboBox()
        for p in METEOR_PIPELINES:
            self._combo_sat.addItem(str(p["label"]), p)
        self._combo_sat.currentIndexChanged.connect(self._on_pipeline_changed)
        row1.addWidget(self._combo_sat, 1)
        ctrl_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel(_("SDR Source:")))
        self._combo_source = QComboBox()
        for src in _list_soapy_sources():
            self._combo_source.addItem(src)
        row2.addWidget(self._combo_source, 1)

        row2.addWidget(QLabel(_("Gain (dB):")))
        self._combo_gain = QComboBox()
        for g in [20, 30, 40, 48, 50]:
            self._combo_gain.addItem(str(g), g)
        self._combo_gain.setCurrentIndex(2)  # default 40 dB
        row2.addWidget(self._combo_gain)
        ctrl_layout.addLayout(row2)

        btn_row = QHBoxLayout()
        self._btn_start = QPushButton(_("▶  Start"))
        self._btn_start.setMinimumWidth(100)
        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop = QPushButton(_("■  Stop"))
        self._btn_stop.setMinimumWidth(100)
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._on_stop)
        self._lbl_lock = QLabel(_("Lock: —"))
        self._lbl_lock.setMinimumWidth(80)
        btn_row.addWidget(self._btn_start)
        btn_row.addWidget(self._btn_stop)
        btn_row.addWidget(self._lbl_lock)
        btn_row.addStretch()
        ctrl_layout.addLayout(btn_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        ctrl_layout.addWidget(self._progress)

        self._lbl_status = QLabel(_("Ready.  Select a satellite and press Start."))
        ctrl_layout.addWidget(self._lbl_status)

        root.addWidget(ctrl_box)

        # --- Main splitter: image area + history ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Image display
        image_widget = QWidget()
        image_layout = QVBoxLayout(image_widget)
        image_layout.setContentsMargins(0, 0, 0, 0)
        self._image_label = QLabel(_("No image received yet."))
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(400, 240)
        self._image_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._image_label.setStyleSheet("border: 1px solid #555; background: #111;")
        image_layout.addWidget(self._image_label, 1)

        open_row = QHBoxLayout()
        self._btn_open_folder = QPushButton(_("📁 Open Output Folder"))
        self._btn_open_folder.clicked.connect(self._on_open_folder)
        self._btn_clear = QPushButton(_("🗑 Clear History"))
        self._btn_clear.clicked.connect(self._on_clear_history)
        open_row.addWidget(self._btn_open_folder)
        open_row.addWidget(self._btn_clear)
        open_row.addStretch()
        image_layout.addLayout(open_row)
        splitter.addWidget(image_widget)

        # History thumbnails
        history_widget = QWidget()
        hl = QVBoxLayout(history_widget)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(QLabel(_("Received Images:")))
        self._history_list = QListWidget()
        self._history_list.setIconSize(QSize(_THUMB_W, _THUMB_H))
        self._history_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._history_list.currentItemChanged.connect(self._on_history_selection)
        hl.addWidget(self._history_list)
        splitter.addWidget(history_widget)

        splitter.setSizes([600, 200])
        root.addWidget(splitter, 1)

        # --- Log output ---
        log_box = QGroupBox(_("SatDump Log"))
        ll = QVBoxLayout(log_box)
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(500)
        self._log_view.setFixedHeight(100)
        self._log_view.setStyleSheet("font-family: monospace; font-size: 10px;")
        ll.addWidget(self._log_view)
        root.addWidget(log_box)

    # ------------------------------------------------------------------
    # SatDump availability check
    # ------------------------------------------------------------------

    def _check_satdump(self) -> None:
        if find_satdump() is None:
            self._banner.setText(
                _(
                    "⚠  SatDump is not installed.  "
                    "Go to Help > SatDump… for installation instructions."
                )
            )
            self._banner.setVisible(True)
            self._btn_start.setEnabled(False)
        else:
            self._banner.setVisible(False)
            self._btn_start.setEnabled(True)

    # ------------------------------------------------------------------
    # Pipeline combo → Radio Control sync
    # ------------------------------------------------------------------

    def _on_pipeline_changed(self, index: int) -> None:
        """Emit satellite_selection_requested so main_window can sync Radio Control."""
        if self._suppress_sync:
            return
        p = self._combo_sat.itemData(index)
        if p:
            self.satellite_selection_requested.emit(int(p["norad"]), int(p["xpdr_freq"]))

    def select_pipeline_by_norad_and_freq(self, norad: int, downlink_hz: int) -> None:
        """Select the combo entry matching *norad* and closest *downlink_hz*.

        Called by main_window when Radio Control selects a METEOR transponder so
        this tab mirrors the selection without triggering a feedback loop.
        """
        best_idx = -1
        best_diff = float("inf")
        for i in range(self._combo_sat.count()):
            p = self._combo_sat.itemData(i)
            if p and int(p["norad"]) == norad:
                diff = abs(int(p["xpdr_freq"]) - downlink_hz)
                if diff < best_diff:
                    best_diff = diff
                    best_idx = i
        if best_idx >= 0 and best_idx != self._combo_sat.currentIndex():
            self._suppress_sync = True
            self._combo_sat.setCurrentIndex(best_idx)
            self._suppress_sync = False

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        # Disconnect SDR if active
        self._disconnect_sdr()

        pipeline_data: dict[str, Any] = self._combo_sat.currentData()
        source = self._combo_source.currentText().strip() or "rtlsdr"
        gain = int(self._combo_gain.currentData())

        self._output_dir = _default_output_dir()

        self._process = SatDumpProcess(
            pipeline=str(pipeline_data["pipeline"]),
            source=source,
            frequency=int(pipeline_data["frequency"]),
            samplerate=int(pipeline_data["samplerate"]),
            output_dir=self._output_dir,
            gain=gain,
            parent=self,
        )
        self._process.log_line.connect(self._on_log_line)
        self._process.progress.connect(self._on_progress)
        self._process.lock_status.connect(self._on_lock_status)
        self._process.finished_ok.connect(self._on_finished_ok)
        self._process.finished_err.connect(self._on_finished_err)
        self._process.start()

        # Start image watcher
        self._watcher = ImageWatcher(self._output_dir, parent=self)
        self._watcher.new_image.connect(self._on_new_image)
        self._watcher.start()

        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._combo_sat.setEnabled(False)
        self._combo_source.setEnabled(False)
        self._combo_gain.setEnabled(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._lbl_status.setText(_("Receiving…"))
        self._lbl_lock.setText(_("Lock: —"))

    def _on_stop(self) -> None:
        if self._process is not None:
            self._process.stop()
        if self._watcher is not None:
            self._watcher.stop()
        self._lbl_status.setText(_("Stopping…"))
        self._btn_stop.setEnabled(False)

    def _disconnect_sdr(self) -> None:
        """Disconnect the SDR and grey out the SDR Control tab."""
        if self._sdr_widget is not None:
            try:
                # Call disconnect if the SDR is active
                if hasattr(self._sdr_widget, "disconnect_sdr"):
                    self._sdr_widget.disconnect_sdr()
                elif hasattr(self._sdr_widget, "_on_disconnect"):
                    self._sdr_widget._on_disconnect()
            except Exception:
                pass
        if self._sdr_control_tab is not None:
            self._sdr_control_tab.setEnabled(False)

    def _reenable_sdr_tab(self) -> None:
        if self._sdr_control_tab is not None:
            self._sdr_control_tab.setEnabled(True)

    # ------------------------------------------------------------------
    # Process signal handlers
    # ------------------------------------------------------------------

    def _on_log_line(self, line: str) -> None:
        self._log_view.appendPlainText(line)

    def _on_progress(self, pct: int) -> None:
        self._progress.setValue(pct)

    def _on_lock_status(self, locked: bool) -> None:
        if locked:
            self._lbl_lock.setText("<b style='color:#2ecc71'>Lock: ✓</b>")
        else:
            self._lbl_lock.setText("<b style='color:#e74c3c'>Lock: ✗</b>")
        self._lbl_lock.setTextFormat(Qt.TextFormat.RichText)

    def _on_finished_ok(self) -> None:
        self._lbl_status.setText(_("Reception finished."))
        self._progress.setVisible(False)
        self._reset_controls()
        self._reenable_sdr_tab()

    def _on_finished_err(self, msg: str) -> None:
        self._lbl_status.setText(_("Error: ") + msg)
        self._log_view.appendPlainText(_("[ERROR] ") + msg)
        self._progress.setVisible(False)
        self._reset_controls()
        self._reenable_sdr_tab()

    def _reset_controls(self) -> None:
        self._btn_start.setEnabled(find_satdump() is not None)
        self._btn_stop.setEnabled(False)
        self._combo_sat.setEnabled(True)
        self._combo_source.setEnabled(True)
        self._combo_gain.setEnabled(True)
        self._lbl_lock.setText(_("Lock: —"))

    # ------------------------------------------------------------------
    # Image display
    # ------------------------------------------------------------------

    def _on_new_image(self, path: object) -> None:
        from pathlib import Path as _Path

        p = _Path(str(path))
        image = QImage(str(p))
        if image.isNull():
            return

        # Show in main area (scaled to fit)
        self._show_image(image)

        # Add thumbnail to history
        label = p.name
        item = _ThumbItem(image, label)
        self._history_list.addItem(item)
        self._history_list.setCurrentItem(item)

        self._lbl_status.setText(_("Image received: ") + label)

    def _show_image(self, image: QImage) -> None:
        w = self._image_label.width()
        h = self._image_label.height()
        pixmap = QPixmap.fromImage(image).scaled(
            w,
            h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(pixmap)

    def _on_history_selection(self, current: QListWidgetItem | None, _: Any) -> None:
        if current is None or not isinstance(current, _ThumbItem):
            return
        self._show_image(current.full_image)

    # ------------------------------------------------------------------
    # Misc slots
    # ------------------------------------------------------------------

    def _on_open_folder(self) -> None:
        folder = self._output_dir or _default_output_dir().parent.parent
        if not folder.exists():
            folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(folder))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    def _on_clear_history(self) -> None:
        self._history_list.clear()
        self._image_label.clear()
        self._image_label.setText(_("No image received yet."))

    # ------------------------------------------------------------------
    # Cleanup on tab close
    # ------------------------------------------------------------------

    def closeEvent(self, event: Any) -> None:  # type: ignore[override]
        self._on_stop()
        self._reenable_sdr_tab()
        super().closeEvent(event)
