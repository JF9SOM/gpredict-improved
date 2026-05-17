"""
手動TLE追加ダイアログ

ManualTLEDialog — Satellite > Add Manual TLE... で開くダイアログ。
NORADカタログ番号でCelesTrakから自動取得するか、
TLE 3行を手動入力してDBに追加する。
"""

from __future__ import annotations

import asyncio

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from data.tle_manager import TLEManager
from i18n import _


class ManualTLEDialog(QDialog):
    """Satellite > Add Manual TLE... ダイアログ。

    NORAD カタログ番号を入力して CelesTrak から自動取得するか、
    TLE 3 行を手動で貼り付けて DB に追加する。
    """

    def __init__(self, tle_manager: TLEManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tle_manager = tle_manager
        self._added_norad: int | None = None
        self.setWindowTitle(_("Add Manual TLE"))
        self.resize(480, 300)
        self._setup_ui()

    # ------------------------------------------------------------------ #
    # Public
    # ------------------------------------------------------------------ #

    @property
    def added_norad(self) -> int | None:
        """追加された衛星の NORAD カタログ番号。キャンセル時は None。"""
        return self._added_norad

    # ------------------------------------------------------------------ #
    # UI 構築
    # ------------------------------------------------------------------ #

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()

        # Tab 1: NORAD ID → CelesTrak 自動取得
        fetch_tab = QWidget()
        fetch_layout = QVBoxLayout(fetch_tab)
        fetch_form = QFormLayout()

        self._norad_spin = QSpinBox()
        self._norad_spin.setRange(1, 999999)
        self._norad_spin.setValue(25544)
        fetch_form.addRow(_("NORAD Cat ID:"), self._norad_spin)
        fetch_layout.addLayout(fetch_form)

        self._fetch_btn = QPushButton(_("Fetch from CelesTrak"))
        self._fetch_btn.clicked.connect(self._on_fetch)
        fetch_layout.addWidget(self._fetch_btn)

        self._fetch_status = QLabel("")
        self._fetch_status.setWordWrap(True)
        fetch_layout.addWidget(self._fetch_status)
        fetch_layout.addStretch()

        self._tabs.addTab(fetch_tab, _("Fetch by NORAD ID"))

        # Tab 2: TLE 手動貼り付け
        manual_tab = QWidget()
        manual_layout = QVBoxLayout(manual_tab)
        manual_layout.addWidget(QLabel(_("Paste TLE (3 lines: Name, Line 1, Line 2):")))

        self._tle_edit = QPlainTextEdit()
        self._tle_edit.setPlaceholderText(
            "SATELLITE NAME\n1 NNNNNC NNNNNAAA.AAAAAAAA ...\n2 NNNNN ..."
        )
        manual_layout.addWidget(self._tle_edit)

        self._tabs.addTab(manual_tab, _("Manual TLE"))

        layout.addWidget(self._tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ #
    # イベントハンドラー
    # ------------------------------------------------------------------ #

    def _on_fetch(self) -> None:
        """Fetch ボタン: CelesTrak から TLE を取得して DB に追加する。"""
        norad = self._norad_spin.value()
        self._fetch_status.setText(_("Fetching..."))
        self._fetch_btn.setEnabled(False)
        QApplication.processEvents()

        try:
            success = asyncio.run(self._tle_manager.fetch_single(norad))
            if success:
                self._fetch_status.setText(
                    _("Successfully fetched TLE for NORAD {n}").format(n=norad)
                )
                self._added_norad = norad
            else:
                self._fetch_status.setText(
                    _("Failed to fetch TLE. Check NORAD ID and network connection.")
                )
        except Exception as exc:  # noqa: BLE001
            self._fetch_status.setText(f"Error: {exc}")
        finally:
            self._fetch_btn.setEnabled(True)

    def _on_accept(self) -> None:
        """OK ボタン処理。"""
        if self._tabs.currentIndex() == 0:
            if self._added_norad is None:
                QMessageBox.warning(
                    self,
                    _("No TLE"),
                    _("Please click 'Fetch from CelesTrak' first."),
                )
                return
            self.accept()
        else:
            self._accept_manual()

    def _accept_manual(self) -> None:
        """Manual タブ: TLE テキストをパースして DB に追加する。"""
        text = self._tle_edit.toPlainText().strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) < 3:  # noqa: PLR2004
            QMessageBox.warning(
                self,
                _("Invalid TLE"),
                _("Please enter 3 lines: Name, Line 1, Line 2."),
            )
            return
        name, line1, line2 = lines[0], lines[1], lines[2]
        if not line1.startswith("1 ") or not line2.startswith("2 "):
            QMessageBox.warning(
                self,
                _("Invalid TLE"),
                _("Line 1 must start with '1 ' and Line 2 with '2 '."),
            )
            return
        try:
            norad = int(line1[2:7])
        except ValueError:
            QMessageBox.warning(
                self,
                _("Invalid TLE"),
                _("Cannot parse NORAD ID from Line 1."),
            )
            return
        if self._tle_manager.add_manual_tle(norad, name, line1, line2):
            self._added_norad = norad
            self.accept()
        else:
            QMessageBox.warning(
                self,
                _("Invalid TLE"),
                _("TLE validation failed. Please check the format."),
            )
