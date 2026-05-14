"""
QTH手動設定ダイアログ

緯度・経度・標高の直接入力、Maidenheadグリッドロケーター入力、コールサイン入力をサポート。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.location import LocationManager, grid_to_latlon
from i18n import _


class QTHDialog(QDialog):
    """QTH（自局位置）設定ダイアログ。"""

    def __init__(
        self,
        location_manager: LocationManager,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            location_manager: 位置情報マネージャー
            parent:           親ウィジェット
        """
        super().__init__(parent)
        self._location_manager = location_manager
        self.setWindowTitle(_("Set QTH"))
        self.setMinimumWidth(400)
        self._build_ui()
        self._load_current()

    # ------------------------------------------------------------------ #
    # UI構築
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # タブ: 座標直接入力 / グリッドロケーター
        self._tabs = QTabWidget()

        # Tab 0: 座標直接入力
        coord_tab = QWidget()
        coord_form = QFormLayout(coord_tab)

        self._lat_spin = QDoubleSpinBox()
        self._lat_spin.setRange(-90.0, 90.0)
        self._lat_spin.setDecimals(6)
        self._lat_spin.setSuffix("°")
        coord_form.addRow(_("Latitude (°N):"), self._lat_spin)

        self._lon_spin = QDoubleSpinBox()
        self._lon_spin.setRange(-180.0, 180.0)
        self._lon_spin.setDecimals(6)
        self._lon_spin.setSuffix("°")
        coord_form.addRow(_("Longitude (°E):"), self._lon_spin)

        self._elev_spin = QDoubleSpinBox()
        self._elev_spin.setRange(-100.0, 9000.0)
        self._elev_spin.setDecimals(1)
        self._elev_spin.setSuffix(" m")
        coord_form.addRow(_("Elevation (m):"), self._elev_spin)

        self._tabs.addTab(coord_tab, _("Coordinates"))

        # Tab 1: グリッドロケーター入力
        grid_tab = QWidget()
        grid_form = QFormLayout(grid_tab)

        self._grid_edit = QLineEdit()
        self._grid_edit.setPlaceholderText("PM86 / PM86ih")
        self._grid_edit.setMaxLength(6)
        grid_form.addRow(_("Grid Locator:"), self._grid_edit)

        self._grid_elev_spin = QDoubleSpinBox()
        self._grid_elev_spin.setRange(-100.0, 9000.0)
        self._grid_elev_spin.setDecimals(1)
        self._grid_elev_spin.setSuffix(" m")
        grid_form.addRow(_("Elevation (m):"), self._grid_elev_spin)

        self._grid_preview = QLabel("")
        grid_form.addRow(_("Decoded:"), self._grid_preview)
        self._grid_edit.textChanged.connect(self._on_grid_changed)

        self._tabs.addTab(grid_tab, _("Grid Locator"))

        layout.addWidget(self._tabs)

        # コールサイン
        call_group = QGroupBox(_("Station"))
        call_form = QFormLayout(call_group)
        self._call_edit = QLineEdit()
        self._call_edit.setPlaceholderText("JF9SOM")
        self._call_edit.setMaxLength(20)
        call_form.addRow(_("Callsign:"), self._call_edit)
        layout.addWidget(call_group)

        # ボタン
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_current(self) -> None:
        """現在の設定を入力欄にロードする。"""
        loc = self._location_manager.current
        if loc:
            self._lat_spin.setValue(loc.latitude_deg)
            self._lon_spin.setValue(loc.longitude_deg)
            self._elev_spin.setValue(loc.elevation_m)
            self._grid_elev_spin.setValue(loc.elevation_m)

        callsign = self._location_manager.get_callsign()
        if callsign:
            self._call_edit.setText(callsign)

    # ------------------------------------------------------------------ #
    # シグナルハンドラー
    # ------------------------------------------------------------------ #

    def _on_grid_changed(self, text: str) -> None:
        """グリッドロケーター入力時にデコードプレビューを更新する。"""
        g = text.strip()
        if len(g) in (4, 6):
            try:
                lat, lon = grid_to_latlon(g)
                ns = "N" if lat >= 0 else "S"
                ew = "E" if lon >= 0 else "W"
                self._grid_preview.setText(f"{abs(lat):.4f}°{ns}  {abs(lon):.4f}°{ew}")
            except ValueError:
                self._grid_preview.setText(_("Invalid grid locator"))
        else:
            self._grid_preview.setText("")

    def _on_accept(self) -> None:
        """OKボタン時の処理。"""
        tab = self._tabs.currentIndex()
        try:
            if tab == 0:
                self._location_manager.from_manual(
                    self._lat_spin.value(),
                    self._lon_spin.value(),
                    self._elev_spin.value(),
                )
            else:
                grid = self._grid_edit.text().strip()
                if not grid:
                    QMessageBox.warning(self, _("Error"), _("Please enter a grid locator."))
                    return
                self._location_manager.from_grid(grid, self._grid_elev_spin.value())

            callsign = self._call_edit.text().strip().upper()
            self._location_manager.save_callsign(callsign)
            self.accept()
        except ValueError as exc:
            QMessageBox.warning(self, _("Error"), str(exc))
