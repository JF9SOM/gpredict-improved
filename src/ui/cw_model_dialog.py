"""Help > CW Model Installation… dialog.

Downloads DeepCW ONNX model files directly from e04's GitHub Pages
and installs them to the user data directory.

Models (e04/web-deep-cw-decoder):
  model_en.onnx   — English CW decoder
  model_ja.onnx   — Japanese CW decoder
  detect_cw.onnx  — CW signal frequency detector
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from comms.cw.model_info import (
    MODEL_FILES,
    MODEL_URLS,
    find_model,
    get_user_cw_model_dir,
    is_onnxruntime_available,
)
from i18n import _

# ---------------------------------------------------------------------------
# Background worker: download models
# ---------------------------------------------------------------------------


class _DownloadWorker(QThread):
    """Downloads all CW model files from e04's GitHub Pages."""

    progress = Signal(int)  # 0-100
    status = Signal(str)
    finished_ok = Signal(str)
    finished_err = Signal(str)

    # Models to download: (key, filename, url)
    _MODELS = [
        ("en", MODEL_FILES["en"], MODEL_URLS["en"]),
        ("ja", MODEL_FILES["ja"], MODEL_URLS["ja"]),
        ("detect", MODEL_FILES["detect"], MODEL_URLS["detect"]),
    ]

    def run(self) -> None:
        import urllib.request

        dest_dir = get_user_cw_model_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)

        n = len(self._MODELS)
        for i, (_key, filename, url) in enumerate(self._MODELS):
            self.status.emit(_("Downloading {name}…").format(name=filename))
            dest = dest_dir / filename
            base_progress = i * 100 // n

            def _hook(
                block: int, block_size: int, total: int, _base: int = base_progress, _n: int = n
            ) -> None:
                if total > 0:
                    file_pct = min(block * block_size * 100 // total, 100)
                    self.progress.emit(_base + file_pct // _n)

            try:
                urllib.request.urlretrieve(url, dest, reporthook=_hook)
            except Exception as exc:
                self.finished_err.emit(f"{filename}: {exc}")
                return

        self.progress.emit(100)
        self.finished_ok.emit(str(dest_dir))


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class CwModelDialog(QDialog):
    """Help > CW Model Installation… dialog."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("CW Model Installation"))
        self.setMinimumWidth(520)
        self._worker: _DownloadWorker | None = None
        self._setup_ui()
        self._refresh_status()

    # ------------------------------------------------------------------ #

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)

        # Status group
        status_box = QGroupBox(_("Current Status"))
        sl = QVBoxLayout(status_box)
        self._lbl_ort = QLabel()
        self._lbl_ort.setWordWrap(True)
        self._lbl_models = QLabel()
        self._lbl_models.setWordWrap(True)
        sl.addWidget(self._lbl_ort)
        sl.addWidget(self._lbl_models)
        root.addWidget(status_box)

        # About group
        about_box = QGroupBox(_("About DeepCW Models"))
        al = QVBoxLayout(about_box)
        about_lbl = QLabel(
            _(
                "The DeepCW models are provided by "
                "<a href='https://github.com/e04/web-deep-cw-decoder'>e04/web-deep-cw-decoder</a>.<br><br>"
                "They use a CRNN + CTC architecture trained to decode CW (Morse code) "
                "from audio spectrograms with near-zero error at −4 dB S/N.<br><br>"
                "<b>Three files are downloaded (~few MB each):</b><br>"
                "  • model_en.onnx — English decoder<br>"
                "  • model_ja.onnx — Japanese (Katakana) decoder<br>"
                "  • detect_cw.onnx — CW frequency auto-detector<br><br>"
                "<b>Runtime:</b> <tt>pip install onnxruntime</tt> is required separately."
            )
        )
        about_lbl.setOpenExternalLinks(True)
        about_lbl.setWordWrap(True)
        al.addWidget(about_lbl)
        root.addWidget(about_box)

        # Download group
        dl_box = QGroupBox(_("Install Models"))
        dl = QVBoxLayout(dl_box)
        dl.addWidget(
            QLabel(
                _(
                    "Downloads model files from e04.github.io and installs them\n"
                    "to your user data directory."
                )
            )
        )
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._lbl_dl = QLabel()
        self._lbl_dl.setVisible(False)
        dl.addWidget(self._progress)
        dl.addWidget(self._lbl_dl)
        btn_row = QHBoxLayout()
        self._btn_dl = QPushButton(_("Download && Install Models"))
        self._btn_dl.clicked.connect(self._on_download)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_dl)
        dl.addLayout(btn_row)
        root.addWidget(dl_box)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    def _refresh_status(self) -> None:
        # onnxruntime
        if is_onnxruntime_available():
            self._lbl_ort.setText(
                "<b style='color:#27ae60'>&#x2714; onnxruntime " + _("available") + "</b>"
            )
        else:
            self._lbl_ort.setText(
                "<b style='color:#e74c3c'>&#x2718; onnxruntime "
                + _("not installed")
                + "</b>"
                + "  —  <tt>pip install onnxruntime</tt>"
            )

        # Model files
        lines: list[str] = []
        for name, filename in MODEL_FILES.items():
            path = find_model(name)
            if path:
                size_kb = path.stat().st_size // 1024
                lines.append(f"&#x2714; <b>{filename}</b> ({size_kb} KB)")
            else:
                lines.append(f"&#x2718; <b>{filename}</b> — {_('not found')}")
        self._lbl_models.setText("<br>".join(lines))

    # ------------------------------------------------------------------ #

    def _on_download(self) -> None:
        self._btn_dl.setEnabled(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._lbl_dl.setVisible(True)
        self._lbl_dl.setText(_("Starting…"))

        self._worker = _DownloadWorker(self)
        self._worker.progress.connect(self._progress.setValue)
        self._worker.status.connect(self._lbl_dl.setText)
        self._worker.finished_ok.connect(self._on_ok)
        self._worker.finished_err.connect(self._on_err)
        self._worker.start()

    def _on_ok(self, path: str) -> None:
        self._progress.setValue(100)
        self._lbl_dl.setText(_("Installed to: ") + path)
        self._btn_dl.setEnabled(True)
        self._refresh_status()

    def _on_err(self, msg: str) -> None:
        self._lbl_dl.setText(_("Error: ") + msg)
        self._btn_dl.setEnabled(True)
        self._progress.setVisible(False)
