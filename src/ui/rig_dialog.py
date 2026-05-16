"""
リグ設定ダイアログ

RigSettingsDialog — Radio > Rig Settings で開くダイアログ。
Hamlib 直接接続 / NET (rigctld) 接続を選択できる。
COM ポートの自動スキャン機能付き。
"""

from __future__ import annotations

import glob
import json
import sys
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from i18n import _

# (model_id, display_name) の主要アマチュア衛星対応機リスト
_RIG_MODELS: list[tuple[int, str]] = [
    (3081, "IC-9700"),
    (3070, "IC-9100"),
    (3085, "IC-705"),
    (428, "FT-991A"),
    (1038, "TS-2000"),
    (1220, "FT-817ND"),
    (1, "Dummy / Test"),
]


def _scan_serial_ports() -> list[str]:
    """利用可能なシリアルポートをスキャンして返す。追加依存なし。"""
    if sys.platform.startswith("win"):
        try:
            import winreg  # type: ignore[import]

            ports: list[str] = []
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DEVICEMAP\SERIALCOMM",
            )
            i = 0
            while True:
                try:
                    _, port, _ = winreg.EnumValue(key, i)
                    ports.append(str(port))
                    i += 1
                except OSError:
                    break
            return sorted(ports)
        except OSError:
            return []
    else:
        patterns = [
            "/dev/ttyUSB*",
            "/dev/ttyACM*",
            "/dev/ttyS[0-9]*",
            "/dev/cu.*",
        ]
        found: list[str] = []
        for pattern in patterns:
            found.extend(sorted(glob.glob(pattern)))
        return found


class RigSettingsDialog(QDialog):
    """Radio > Rig Settings ダイアログ。接続モードと COM ポートを設定する。"""

    def __init__(self, conn: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._conn = conn
        self.setWindowTitle(_("Rig Settings"))
        self.resize(480, 400)
        self._setup_ui()
        self._load_settings()
        self._on_scan_ports()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # --- 接続モード ---
        mode_group = QGroupBox(_("Connection Mode"))
        mode_layout = QVBoxLayout(mode_group)
        self._radio_direct = QRadioButton(_("Direct (Hamlib built-in)"))
        self._radio_net = QRadioButton(_("NET (rigctld compatible)"))
        self._radio_direct.setChecked(True)
        self._radio_direct.toggled.connect(self._on_mode_toggled)
        mode_layout.addWidget(self._radio_direct)
        mode_layout.addWidget(self._radio_net)
        layout.addWidget(mode_group)

        # --- Direct 設定 ---
        self._direct_group = QGroupBox(_("Direct Connection Settings"))
        direct_form = QFormLayout(self._direct_group)

        port_row = QWidget()
        port_layout = QHBoxLayout(port_row)
        port_layout.setContentsMargins(0, 0, 0, 0)
        self._port_combo = QComboBox()
        self._port_combo.setEditable(True)
        self._port_combo.setMinimumWidth(160)
        self._scan_btn = QPushButton(_("Scan"))
        self._scan_btn.setMaximumWidth(80)
        self._scan_btn.clicked.connect(self._on_scan_ports)
        port_layout.addWidget(self._port_combo)
        port_layout.addWidget(self._scan_btn)
        direct_form.addRow(_("COM Port:"), port_row)

        self._baud_combo = QComboBox()
        for b in ["4800", "9600", "19200", "38400", "57600", "115200"]:
            self._baud_combo.addItem(b)
        self._baud_combo.setCurrentText("9600")
        direct_form.addRow(_("Baud Rate:"), self._baud_combo)

        self._model_combo = QComboBox()
        for mid, mname in _RIG_MODELS:
            self._model_combo.addItem(f"{mid} — {mname}", mid)
        direct_form.addRow(_("Rig Model:"), self._model_combo)

        layout.addWidget(self._direct_group)

        # --- NET 設定 ---
        self._net_group = QGroupBox(_("NET Connection Settings"))
        net_form = QFormLayout(self._net_group)
        self._host_edit = QLineEdit("localhost")
        net_form.addRow(_("Host:"), self._host_edit)
        self._net_port_spin = QSpinBox()
        self._net_port_spin.setRange(1, 65535)
        self._net_port_spin.setValue(4532)
        net_form.addRow(_("Port:"), self._net_port_spin)
        layout.addWidget(self._net_group)
        self._net_group.setVisible(False)

        # --- ステータス ---
        self._status_label = QLabel("")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_settings)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_mode_toggled(self, _checked: bool) -> None:
        """接続モード切り替えで表示グループを切り替える。"""
        is_direct = self._radio_direct.isChecked()
        self._direct_group.setVisible(is_direct)
        self._net_group.setVisible(not is_direct)

    def _on_scan_ports(self) -> None:
        """シリアルポートをスキャンしてコンボボックスを更新する。"""
        current = self._port_combo.currentText()
        ports = _scan_serial_ports()
        self._port_combo.clear()
        if ports:
            self._port_combo.addItems(ports)
            self._status_label.setText(_("{n} port(s) found").format(n=len(ports)))
        else:
            self._status_label.setText(_("No serial ports found"))
        if current:
            idx = self._port_combo.findText(current)
            if idx >= 0:
                self._port_combo.setCurrentIndex(idx)
            else:
                self._port_combo.setEditText(current)

    def _load_settings(self) -> None:
        if not hasattr(self._conn, "execute"):
            return
        row = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = 'rig_settings'"
        ).fetchone()
        if not row or not row["value"]:
            return
        try:
            s: dict[str, Any] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return

        if s.get("mode") == "net":
            self._radio_net.setChecked(True)

        port = str(s.get("port", ""))
        if port:
            idx = self._port_combo.findText(port)
            if idx >= 0:
                self._port_combo.setCurrentIndex(idx)
            else:
                self._port_combo.setEditText(port)

        baud = str(s.get("baud_rate", 9600))
        idx = self._baud_combo.findText(baud)
        if idx >= 0:
            self._baud_combo.setCurrentIndex(idx)

        model_id = int(s.get("model_id", 1))
        for i in range(self._model_combo.count()):
            if self._model_combo.itemData(i) == model_id:
                self._model_combo.setCurrentIndex(i)
                break

        self._host_edit.setText(str(s.get("host", "localhost")))
        self._net_port_spin.setValue(int(s.get("net_port", 4532)))

    def _save_settings(self) -> None:
        model_id: int = self._model_combo.currentData() or 1
        s = {
            "mode": "direct" if self._radio_direct.isChecked() else "net",
            "port": self._port_combo.currentText(),
            "baud_rate": int(self._baud_combo.currentText()),
            "model_id": model_id,
            "host": self._host_edit.text(),
            "net_port": self._net_port_spin.value(),
        }
        if hasattr(self._conn, "execute"):
            self._conn.execute(
                """
                INSERT OR REPLACE INTO app_settings (key, value, updated_at)
                VALUES ('rig_settings', ?, CURRENT_TIMESTAMP)
                """,
                (json.dumps(s),),
            )
            self._conn.commit()
