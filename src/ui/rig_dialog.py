"""
Rig settings dialog.

RigSettingsDialog — Dialog opened from Radio > Rig Settings.
Supports Hamlib direct connection and NET (rigctld) connection.
Fetches all supported models from the Hamlib Python binding and
displays them with a search filter.
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
from rig.controller import CTCSS_PRESET_TEMPLATES

# ---------------------------------------------------------------------------
# Hamlib Python binding (imported only when available)
# ---------------------------------------------------------------------------

# Hamlib is imported lazily inside _load_from_hamlib_api() to avoid loading the
# shared library at startup, which collides with Qt's thread-local storage.

# ---------------------------------------------------------------------------
# Fallback model list (for environments without the Hamlib Python binding).
# Uses actual Hamlib 4.x model numbers.
# ---------------------------------------------------------------------------
_FALLBACK_MODELS: list[tuple[int, str, str]] = [
    # Hamlib internal
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
    (1051, "Yaesu", "FTX-1"),
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
    """Fetch all supported models from the Hamlib Python binding.

    Uses the riglist dict (Hamlib 3.x API). Hamlib 4.x removed riglist and
    provides no efficient API to enumerate model names without creating a Rig
    instance per model — creating 384+ Rig instances exhausts pthread keys
    (PTHREAD_KEYS_MAX=1024) and crashes Qt via QThreadStorage hash collision.

    Returns:
        List of (model_id, manufacturer, model_name). Empty on failure.
    """
    try:
        import Hamlib as _hamlib_mod  # lazy — avoids Qt TLS collision at startup
    except ModuleNotFoundError:
        return []

    if not hasattr(_hamlib_mod, "riglist"):
        return []  # Hamlib 4.x: fall back to _FALLBACK_MODELS

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
    """Return all supported Hamlib models sorted by manufacturer and model name.

    Priority:
        1. ``riglist`` dictionary from the Hamlib Python binding
        2. Hard-coded fallback list

    Returns:
        List of (model_id, manufacturer, model_name).
    """
    models = _load_from_hamlib_api()
    if not models:
        models = list(_FALLBACK_MODELS)
    return sorted(models, key=lambda x: (x[1].lower(), x[2].lower()))


def _scan_serial_ports() -> list[str]:
    """Scan for available serial ports and return them. No extra dependencies needed."""
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
            "/dev/FTX*",
            "/dev/ttyUSB*",
            "/dev/ttyACM*",
            "/dev/ttyS*",
            "/dev/cu.*",
        ]
        found: list[str] = []
        for pattern in patterns:
            found.extend(glob.glob(pattern))
        return sorted(set(found))


class RigSettingsDialog(QDialog):
    """Radio > Rig Settings dialog.

    Displays all Hamlib-supported models with a search filter.
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
    # UI construction
    # ------------------------------------------------------------------ #

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # --- Connection mode ---
        mode_group = QGroupBox(_("Connection Mode"))
        mode_layout = QVBoxLayout(mode_group)
        self._radio_direct = QRadioButton(_("Direct (Hamlib built-in)"))
        self._radio_net = QRadioButton(_("NET (rigctld compatible)"))
        self._radio_direct.setChecked(True)
        self._radio_direct.toggled.connect(self._on_mode_toggled)
        mode_layout.addWidget(self._radio_direct)
        mode_layout.addWidget(self._radio_net)
        layout.addWidget(mode_group)

        # --- Direct connection settings ---
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

        # --- NET connection settings ---
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

        # --- Radio Type ---
        type_group = QGroupBox(_("Radio Type"))
        type_form = QFormLayout(type_group)
        self._radio_type_combo = QComboBox()
        self._radio_type_combo.addItem(_("Full-duplex (F + I)"), "full_duplex")
        self._radio_type_combo.addItem(_("RX only (F only)"), "rx_only")
        self._radio_type_combo.addItem(_("TX only (I only)"), "tx_only")
        type_form.addRow(_("Radio Type:"), self._radio_type_combo)
        layout.addWidget(type_group)

        # --- CTCSS Tone Settings ---
        ctcss_group = QGroupBox(_("CTCSS Tone Settings"))
        ctcss_form = QFormLayout(ctcss_group)
        self._ctcss_method_combo = QComboBox()
        self._ctcss_method_combo.addItem(_("Hamlib standard"), "hamlib")
        self._ctcss_method_combo.addItem(_("FTX-1 (Custom CAT)"), "ftx1")
        self._ctcss_method_combo.addItem(_("FT-991 (Custom CAT)"), "ft991")
        self._ctcss_method_combo.addItem(_("Custom CAT command"), "custom_cat")
        self._ctcss_method_combo.currentIndexChanged.connect(self._on_ctcss_method_changed)
        ctcss_form.addRow(_("CTCSS Method:"), self._ctcss_method_combo)
        self._ctcss_cat_on_edit = QLineEdit()
        self._ctcss_cat_on_edit.setPlaceholderText(_("e.g. CN1{tone:03d};CT11;"))
        ctcss_form.addRow(_("CAT ON command:"), self._ctcss_cat_on_edit)
        self._ctcss_cat_off_edit = QLineEdit()
        self._ctcss_cat_off_edit.setPlaceholderText(_("e.g. CT10;"))
        ctcss_form.addRow(_("CAT OFF command:"), self._ctcss_cat_off_edit)
        self._direct_cat_port_edit = QLineEdit()
        self._direct_cat_port_edit.setPlaceholderText(
            _("e.g. /dev/ttyUSB0  (empty = use rigctld w cmd)")
        )
        ctcss_form.addRow(_("Direct CAT Port:"), self._direct_cat_port_edit)
        self._direct_cat_baud_combo = QComboBox()
        for b in ["4800", "9600", "19200", "38400", "57600", "115200"]:
            self._direct_cat_baud_combo.addItem(b)
        self._direct_cat_baud_combo.setCurrentText("38400")
        ctcss_form.addRow(_("Direct CAT Baud:"), self._direct_cat_baud_combo)
        layout.addWidget(ctcss_group)
        self._on_ctcss_method_changed()

        # --- Status ---
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
    # Model list
    # ------------------------------------------------------------------ #

    def _load_models(self) -> None:
        """Fetch the Hamlib model list and initialise the combo box."""
        self._all_models = _load_hamlib_models()
        self._populate_model_combo(self._all_models)
        n = len(self._all_models)
        self._status_label.setText(_("{n} rig models available").format(n=n))

    def _populate_model_combo(self, models: list[tuple[int, str, str]]) -> None:
        """Update the model combo box with the given list, preserving the current selection."""
        current_id: int | None = self._model_combo.currentData()
        self._model_combo.clear()
        for mid, mfg, name in models:
            label = f"{mfg} — {name} (#{mid})" if mfg else f"{name} (#{mid})"
            self._model_combo.addItem(label, mid)
        # Restore the previous selection
        for i in range(self._model_combo.count()):
            if self._model_combo.itemData(i) == current_id:
                self._model_combo.setCurrentIndex(i)
                break

    def _on_model_search(self, text: str) -> None:
        """Filter the model list in real time as the user types."""
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
            if len(filtered) == 1:
                self._model_combo.setCurrentIndex(0)
            self._status_label.setText(
                _("Showing {n} / {total} models").format(
                    n=len(filtered), total=len(self._all_models)
                )
            )

    # ------------------------------------------------------------------ #
    # Port scan / mode toggle
    # ------------------------------------------------------------------ #

    def _on_ctcss_method_changed(self) -> None:
        """Enable/disable CAT command fields based on the selected CTCSS method."""
        method = self._ctcss_method_combo.currentData()
        if method in CTCSS_PRESET_TEMPLATES:
            on_cmd, off_cmd = CTCSS_PRESET_TEMPLATES[method]
            self._ctcss_cat_on_edit.setText(on_cmd)
            self._ctcss_cat_off_edit.setText(off_cmd)
            self._ctcss_cat_on_edit.setEnabled(False)
            self._ctcss_cat_off_edit.setEnabled(False)
        elif method == "custom_cat":
            self._ctcss_cat_on_edit.setEnabled(True)
            self._ctcss_cat_off_edit.setEnabled(True)
        else:  # "hamlib"
            self._ctcss_cat_on_edit.setText("")
            self._ctcss_cat_off_edit.setText("")
            self._ctcss_cat_on_edit.setEnabled(False)
            self._ctcss_cat_off_edit.setEnabled(False)

    def _on_mode_toggled(self, _checked: bool) -> None:
        is_direct = self._radio_direct.isChecked()
        self._direct_group.setVisible(is_direct)
        self._net_group.setVisible(not is_direct)

    def _on_scan_ports(self) -> None:
        """Scan serial ports and update the combo box."""
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
    # Settings load / save
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

        radio_type = str(s.get("radio_type", "full_duplex"))
        for i in range(self._radio_type_combo.count()):
            if self._radio_type_combo.itemData(i) == radio_type:
                self._radio_type_combo.setCurrentIndex(i)
                break

        ctcss_method = str(s.get("ctcss_method", "hamlib"))
        for i in range(self._ctcss_method_combo.count()):
            if self._ctcss_method_combo.itemData(i) == ctcss_method:
                self._ctcss_method_combo.setCurrentIndex(i)
                break
        self._ctcss_cat_on_edit.setText(str(s.get("ctcss_cat_on", "")))
        self._ctcss_cat_off_edit.setText(str(s.get("ctcss_cat_off", "")))
        self._direct_cat_port_edit.setText(str(s.get("direct_cat_port", "")))
        baud_str = str(s.get("direct_cat_baud", 38400))
        idx = self._direct_cat_baud_combo.findText(baud_str)
        if idx >= 0:
            self._direct_cat_baud_combo.setCurrentIndex(idx)
        self._on_ctcss_method_changed()

    def _save_settings(self) -> None:
        model_id: int = self._model_combo.currentData() or 1
        s = {
            "mode": "direct" if self._radio_direct.isChecked() else "net",
            "port": self._port_combo.currentText(),
            "baud_rate": int(self._baud_combo.currentText()),
            "model_id": model_id,
            "host": self._host_edit.text(),
            "net_port": self._net_port_spin.value(),
            "radio_type": self._radio_type_combo.currentData() or "full_duplex",
            "ctcss_method": self._ctcss_method_combo.currentData() or "hamlib",
            "ctcss_cat_on": self._ctcss_cat_on_edit.text(),
            "ctcss_cat_off": self._ctcss_cat_off_edit.text(),
            "direct_cat_port": self._direct_cat_port_edit.text(),
            "direct_cat_baud": int(self._direct_cat_baud_combo.currentText()),
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
