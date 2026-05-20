"""
トランスポンダ手動追加ダイアログ

NORAD ID・周波数・モード・CTCSSトーンを入力してDBに保存する（manual_override=True）。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from data.transmitter_manager import TransmitterManager
from i18n import _

_MODES: list[str] = ["FM", "SSB", "CW", "CW-R", "DIGITALVOICE", "BPSK", "AFSK", "Other"]
_TYPES: list[str] = ["Transmitter", "Transponder", "Transceiver", "Beacon"]
_CTCSS_TYPES: list[str] = ["", "CTCSS", "DCS"]


class TransmitterDialog(QDialog):
    """トランスポンダ手動追加ダイアログ。"""

    def __init__(
        self,
        transmitter_manager: TransmitterManager,
        norad_cat_id: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            transmitter_manager: トランスポンダ管理クラス
            norad_cat_id:        初期NORAD ID（Noneなら25544=ISSがデフォルト）
            parent:              親ウィジェット
        """
        super().__init__(parent)
        self._tm = transmitter_manager
        self.setWindowTitle(_("Add Transmitter"))
        self.setMinimumWidth(440)
        self._build_ui()
        if norad_cat_id is not None:
            self._norad_spin.setValue(norad_cat_id)

    # ------------------------------------------------------------------ #
    # UI構築
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # 衛星
        sat_group = QGroupBox(_("Satellite"))
        sat_form = QFormLayout(sat_group)
        self._norad_spin = QSpinBox()
        self._norad_spin.setRange(1, 999999)
        self._norad_spin.setValue(25544)
        sat_form.addRow(_("NORAD ID:"), self._norad_spin)
        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText(_("e.g. ISS FM downlink"))
        sat_form.addRow(_("Description:"), self._desc_edit)
        layout.addWidget(sat_group)

        # 周波数（MHz入力 → 内部で Hz に変換）
        freq_group = QGroupBox(_("Frequency"))
        freq_form = QFormLayout(freq_group)

        self._dl_spin = QDoubleSpinBox()
        self._dl_spin.setRange(0.001, 10000.0)
        self._dl_spin.setDecimals(3)
        self._dl_spin.setSuffix(" MHz")
        self._dl_spin.setValue(145.800)
        freq_form.addRow(_("Downlink (MHz):"), self._dl_spin)

        self._dl_high_spin = QDoubleSpinBox()
        self._dl_high_spin.setRange(0.0, 10000.0)
        self._dl_high_spin.setDecimals(3)
        self._dl_high_spin.setSuffix(" MHz")
        self._dl_high_spin.setSpecialValueText(_("(none)"))
        freq_form.addRow(_("Downlink High (MHz):"), self._dl_high_spin)

        self._ul_spin = QDoubleSpinBox()
        self._ul_spin.setRange(0.0, 10000.0)
        self._ul_spin.setDecimals(3)
        self._ul_spin.setSuffix(" MHz")
        self._ul_spin.setSpecialValueText(_("(none)"))
        freq_form.addRow(_("Uplink (MHz):"), self._ul_spin)

        self._ul_high_spin = QDoubleSpinBox()
        self._ul_high_spin.setRange(0.0, 10000.0)
        self._ul_high_spin.setDecimals(3)
        self._ul_high_spin.setSuffix(" MHz")
        self._ul_high_spin.setSpecialValueText(_("(none)"))
        freq_form.addRow(_("Uplink High (MHz):"), self._ul_high_spin)

        layout.addWidget(freq_group)

        # モード・タイプ
        mode_group = QGroupBox(_("Mode"))
        mode_form = QFormLayout(mode_group)

        self._type_combo = QComboBox()
        self._type_combo.addItems(_TYPES)
        mode_form.addRow(_("Type:"), self._type_combo)

        self._mode_combo = QComboBox()
        self._mode_combo.addItems(_MODES)
        mode_form.addRow(_("Mode:"), self._mode_combo)

        self._invert_check = QCheckBox(_("Inverting transponder"))
        mode_form.addRow("", self._invert_check)

        layout.addWidget(mode_group)

        # CTCSSトーン
        ctcss_group = QGroupBox(_("CTCSS / DCS Tone"))
        ctcss_form = QFormLayout(ctcss_group)

        self._ctcss_type_combo = QComboBox()
        self._ctcss_type_combo.addItems(_CTCSS_TYPES)
        ctcss_form.addRow(_("Tone type:"), self._ctcss_type_combo)

        self._ctcss_spin = QDoubleSpinBox()
        self._ctcss_spin.setRange(0.0, 9999.9)
        self._ctcss_spin.setDecimals(1)
        self._ctcss_spin.setSuffix(" Hz")
        self._ctcss_spin.setSpecialValueText(_("(none)"))
        ctcss_form.addRow(_("Tone (Hz):"), self._ctcss_spin)

        layout.addWidget(ctcss_group)

        # メモ
        notes_form = QFormLayout()
        self._notes_edit = QLineEdit()
        self._notes_edit.setPlaceholderText(_("Optional notes"))
        notes_form.addRow(_("Notes:"), self._notes_edit)
        layout.addLayout(notes_form)

        # ボタン
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ #
    # 内部ユーティリティ
    # ------------------------------------------------------------------ #

    def _mhz_to_hz(self, mhz: float) -> int | None:
        """MHz を Hz に変換する。0.0（specialValue）の場合は None を返す。"""
        if mhz <= 0.0:
            return None
        return int(round(mhz * 1_000_000))

    # ------------------------------------------------------------------ #
    # シグナルハンドラー
    # ------------------------------------------------------------------ #

    def _on_accept(self) -> None:
        """OKボタン時の処理。"""
        desc = self._desc_edit.text().strip()
        if not desc:
            QMessageBox.warning(self, _("Error"), _("Description is required."))
            return

        dl_low = self._mhz_to_hz(self._dl_spin.value())
        if dl_low is None:
            QMessageBox.warning(self, _("Error"), _("Downlink frequency is required."))
            return

        norad = self._norad_spin.value()
        dl_high = self._mhz_to_hz(self._dl_high_spin.value())
        ul_low = self._mhz_to_hz(self._ul_spin.value())
        ul_high = self._mhz_to_hz(self._ul_high_spin.value())
        mode = self._mode_combo.currentText()
        xpdr_type = self._type_combo.currentText()
        invert = self._invert_check.isChecked()

        ctcss_type_str: str | None = self._ctcss_type_combo.currentText() or None
        ctcss_tone: float | None = (
            self._ctcss_spin.value() if self._ctcss_spin.value() > 0.0 else None
        )
        notes = self._notes_edit.text().strip()

        try:
            self._tm.add_manual_transmitter(
                norad_cat_id=norad,
                description=desc,
                downlink_low=dl_low,
                mode=mode,
                uplink_low=ul_low,
                uplink_high=ul_high,
                downlink_high=dl_high,
                invert=invert,
                ctcss_tone=ctcss_tone,
                ctcss_tone_type=ctcss_type_str,
                notes=notes,
                xpdr_type=xpdr_type,
            )
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, _("Error"), str(exc))
