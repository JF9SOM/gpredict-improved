"""
リグ設定ダイアログ

RigSettingsDialog — Radio > Rig Settings で開くダイアログ。
Hamlib 直接接続 / NET (rigctld) 接続を選択できる。
Hamlib Python バインディングから全機種を取得し、検索フィルター付きで表示する。
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

# ---------------------------------------------------------------------------
# Hamlib Python バインディング（利用可能な場合のみ）
# ---------------------------------------------------------------------------

try:
    import Hamlib as _hamlib_mod

    _HAMLIB_OK: bool = True
except ModuleNotFoundError:
    _hamlib_mod = None
    _HAMLIB_OK = False

# ---------------------------------------------------------------------------
# フォールバックモデルリスト（Hamlib Python バインディングが使えない環境用）
# Hamlib 4.x の実際のモデル番号を使用
# ---------------------------------------------------------------------------
_FALLBACK_MODELS: list[tuple[int, str, str]] = [
    # Hamlib 内部
    (1, "Hamlib", "Dummy"),
    (2, "Hamlib", "NET rigctl"),
    (4, "FLRig", "FLRig"),
    # Yaesu
    (1001, "Yaesu", "FT-847"),
    (1003, "Yaesu", "FT-100"),
    (1010, "Yaesu", "FT-736R"),
    (1015, "Yaesu", "FT-1000MP"),
    (1020, "Yaesu", "FT-817"),
    (1021, "Yaesu", "FT-817ND"),
    (1022, "Yaesu", "FT-857"),
    (1023, "Yaesu", "FT-897"),
    (1024, "Yaesu", "FT-100D"),
    (1027, "Yaesu", "FT-450"),
    (1028, "Yaesu", "FT-950"),
    (1029, "Yaesu", "FT-2000"),
    (1030, "Yaesu", "FT-DX9000D"),
    (1035, "Yaesu", "FT-991"),
    (1036, "Yaesu", "FT-991A"),
    (1037, "Yaesu", "FT-5000"),
    (1040, "Yaesu", "FT-450D"),
    (1043, "Yaesu", "FTDX-3000"),
    (1044, "Yaesu", "FTDX-5000"),
    (1045, "Yaesu", "FTDX-1200"),
    (1046, "Yaesu", "FT-818ND"),
    (1047, "Yaesu", "FTDX-10"),
    (1048, "Yaesu", "FTDX-101MP"),
    (1049, "Yaesu", "FTDX-101D"),
    # Kenwood
    (2001, "Kenwood", "TS-50S"),
    (2002, "Kenwood", "TS-440S"),
    (2003, "Kenwood", "TS-450S"),
    (2004, "Kenwood", "TS-570D"),
    (2005, "Kenwood", "TS-690S"),
    (2006, "Kenwood", "TS-711A"),
    (2007, "Kenwood", "TS-790E"),
    (2009, "Kenwood", "TS-850S"),
    (2010, "Kenwood", "TS-870S"),
    (2011, "Kenwood", "TS-940S"),
    (2012, "Kenwood", "TS-950SDX"),
    (2014, "Kenwood", "TS-2000"),
    (2015, "Kenwood", "TM-D700"),
    (2016, "Kenwood", "TS-590S"),
    (2017, "Kenwood", "TS-590SG"),
    (2020, "Kenwood", "TM-V7"),
    (2021, "Kenwood", "TM-D710"),
    (2022, "Kenwood", "TS-990S"),
    (2024, "Kenwood", "TS-480"),
    (2025, "Kenwood", "TS-570S"),
    (2026, "Kenwood", "TH-D74"),
    (2027, "Kenwood", "TM-D710G"),
    (2041, "Kenwood", "TS-890S"),
    # Elecraft
    (2029, "Elecraft", "K3"),
    (2045, "Elecraft", "KX3"),
    (2046, "Elecraft", "K3S"),
    (2047, "Elecraft", "KX2"),
    # Icom
    (3001, "Icom", "IC-706"),
    (3002, "Icom", "IC-706MkII"),
    (3003, "Icom", "IC-706MkIIG"),
    (3004, "Icom", "IC-718"),
    (3005, "Icom", "IC-728"),
    (3006, "Icom", "IC-729"),
    (3007, "Icom", "IC-735"),
    (3008, "Icom", "IC-736"),
    (3009, "Icom", "IC-737"),
    (3010, "Icom", "IC-738"),
    (3011, "Icom", "IC-746"),
    (3012, "Icom", "IC-756"),
    (3013, "Icom", "IC-756Pro"),
    (3014, "Icom", "IC-756ProII"),
    (3015, "Icom", "IC-756ProIII"),
    (3016, "Icom", "IC-765"),
    (3017, "Icom", "IC-775"),
    (3018, "Icom", "IC-781"),
    (3019, "Icom", "IC-820H"),
    (3020, "Icom", "IC-7000"),
    (3021, "Icom", "IC-703"),
    (3022, "Icom", "IC-7100"),
    (3023, "Icom", "IC-746Pro"),
    (3024, "Icom", "IC-7200"),
    (3025, "Icom", "IC-7300"),
    (3026, "Icom", "IC-7410"),
    (3027, "Icom", "IC-7600"),
    (3028, "Icom", "IC-7700"),
    (3029, "Icom", "IC-7800"),
    (3030, "Icom", "IC-7850"),
    (3031, "Icom", "IC-7851"),
    (3032, "Icom", "IC-910H"),
    (3068, "Icom", "IC-9100"),
    (3081, "Icom", "IC-9700"),
    (3085, "Icom", "IC-705"),
    (3090, "Icom", "IC-7610"),
    # Alinco
    (4001, "Alinco", "DX-77"),
    (4006, "Alinco", "DR-135T"),
    (4008, "Alinco", "DJ-X11"),
    # TenTec
    (6001, "TenTec", "Century 21"),
    (6003, "TenTec", "Scout"),
    (6014, "TenTec", "Orion"),
    (6021, "TenTec", "Jupiter"),
    # FlexRadio
    (16503, "FlexRadio", "FLEX-6600"),
    (16506, "FlexRadio", "FLEX-6400"),
    (16507, "FlexRadio", "FLEX-6400M"),
    # SDR
    (3000801, "HPSDR", "Apache Labs ANAN-7000DLE MKII"),
]


def _load_from_hamlib_api() -> list[tuple[int, str, str]]:
    """``Hamlib.riglist`` 辞書から全サポートモデルを取得する。

    ``Hamlib.riglist`` は ``{model_id: RigCaps}`` 形式の辞書で、
    各値の ``.mfg_name`` / ``.model_name`` 属性からメーカー・機種名を取得できる。

    Returns:
        (model_id, manufacturer, model_name) のリスト。取得失敗時は空リスト。
    """
    if not _HAMLIB_OK or _hamlib_mod is None:
        return []
    models: list[tuple[int, str, str]] = []
    try:
        for model_id, info in _hamlib_mod.riglist.items():
            name = str(getattr(info, "model_name", "") or "").strip()
            mfg = str(getattr(info, "mfg_name", "") or "").strip()
            if name:
                models.append((int(model_id), mfg, name))
    except (AttributeError, TypeError):
        pass
    return models


def _load_hamlib_models() -> list[tuple[int, str, str]]:
    """全 Hamlib サポートモデルを取得してメーカー・機種名順でソートして返す。

    取得優先順位:
        1. Hamlib Python バインディングの ``riglist`` 辞書
        2. ハードコードされたフォールバックリスト

    Returns:
        (model_id, manufacturer, model_name) のリスト。
    """
    models = _load_from_hamlib_api()
    if not models:
        models = list(_FALLBACK_MODELS)
    return sorted(models, key=lambda x: (x[1].lower(), x[2].lower()))


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
    """Radio > Rig Settings ダイアログ。

    Hamlib がサポートする全機種を検索フィルター付きで表示する。
    """

    def __init__(self, conn: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._conn = conn
        self._all_models: list[tuple[int, str, str]] = []
        self.setWindowTitle(_("Rig Settings"))
        self.resize(520, 480)
        self._setup_ui()
        self._load_models()
        self._load_settings()
        self._on_scan_ports()

    # ------------------------------------------------------------------ #
    # UI 構築
    # ------------------------------------------------------------------ #

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

        self._model_search = QLineEdit()
        self._model_search.setPlaceholderText(_("Search by manufacturer or model name..."))
        self._model_search.textChanged.connect(self._on_model_search)
        direct_form.addRow(_("Search:"), self._model_search)

        self._model_combo = QComboBox()
        self._model_combo.setMinimumWidth(280)
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

    # ------------------------------------------------------------------ #
    # モデルリスト
    # ------------------------------------------------------------------ #

    def _load_models(self) -> None:
        """Hamlib モデルリストを取得してコンボボックスを初期化する。"""
        self._all_models = _load_hamlib_models()
        self._populate_model_combo(self._all_models)
        n = len(self._all_models)
        self._status_label.setText(_("{n} rig models available").format(n=n))

    def _populate_model_combo(self, models: list[tuple[int, str, str]]) -> None:
        """モデルコンボボックスを指定リストで更新する。現在の選択を可能な限り維持する。"""
        current_id: int | None = self._model_combo.currentData()
        self._model_combo.clear()
        for mid, mfg, name in models:
            label = f"{mfg} — {name} (#{mid})" if mfg else f"{name} (#{mid})"
            self._model_combo.addItem(label, mid)
        # 前回の選択を復元
        for i in range(self._model_combo.count()):
            if self._model_combo.itemData(i) == current_id:
                self._model_combo.setCurrentIndex(i)
                break

    def _on_model_search(self, text: str) -> None:
        """検索テキストに応じてモデルリストをリアルタイムフィルタリングする。"""
        query = text.lower().strip()
        if not query:
            self._populate_model_combo(self._all_models)
            self._status_label.setText(
                _("{n} rig models available").format(n=len(self._all_models))
            )
        else:
            filtered = [
                (mid, mfg, name)
                for mid, mfg, name in self._all_models
                if query in mfg.lower() or query in name.lower() or query in str(mid)
            ]
            self._populate_model_combo(filtered)
            self._status_label.setText(
                _("Showing {n} / {total} models").format(
                    n=len(filtered), total=len(self._all_models)
                )
            )

    # ------------------------------------------------------------------ #
    # ポートスキャン / モード切り替え
    # ------------------------------------------------------------------ #

    def _on_mode_toggled(self, _checked: bool) -> None:
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

    # ------------------------------------------------------------------ #
    # 設定読み書き
    # ------------------------------------------------------------------ #

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
