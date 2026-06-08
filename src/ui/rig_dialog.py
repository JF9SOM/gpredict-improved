"""
Rig settings dialog.

RigSettingsDialog — Dialog opened from Radio > Rig Settings.
Three tabs: Rig 1 / Rig 2 / SDR Settings.
Supports Hamlib direct connection and NET (rigctld) connection.

DB keys:
  'rig1_settings' — JSON dict for Rig 1 (always active)
  'rig2_settings' — JSON dict for Rig 2 (has an 'enabled' boolean field)
  'sdr_settings'  — JSON dict for SDR (device args, sample rate, gain, etc.)

Backward compatibility: if 'rig1_settings' is absent but the legacy
'rig_settings' key exists, it is migrated to 'rig1_settings' on first open.
"""

from __future__ import annotations

import contextlib
import glob
import json
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sdr.device import SdrDeviceInfo

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
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
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from i18n import _
from rig.controller import CTCSS_PRESET_TEMPLATES
from sdr import SOAPY_AVAILABLE

# ---------------------------------------------------------------------------
# Hamlib Python binding (imported lazily to avoid Qt TLS collision at startup)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Fallback model list (actual Hamlib 4.x model numbers)
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


# ---------------------------------------------------------------------------
# _RigPanel — reusable settings form for one rig
# ---------------------------------------------------------------------------


class _RigPanel(QWidget):
    """Configuration panel for a single rig.

    Used as a tab page inside RigSettingsDialog.
    Rig 1 is always active; Rig 2 has an "Enable Rig 2" checkbox that
    enables or disables the form below it.
    """

    def __init__(
        self,
        rig_index: int,
        all_models: list[tuple[int, str, str]],
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            rig_index:  1 or 2.  Rig 2 renders an enable checkbox.
            all_models: pre-loaded Hamlib model list shared between both panels.
            parent:     parent widget.
        """
        super().__init__(parent)
        self._rig_index = rig_index
        self._all_models = all_models
        self._enable_cb: QCheckBox | None = None
        self._form_widget: QWidget
        # Rig 1 only: manual Radio Type selector
        self._radio_type_combo: QComboBox | None = None
        # Rig 2 only: split-mode selector (determines radio_type for both rigs)
        self._split_mode_combo: QComboBox | None = None
        self._setup_ui()
        self._on_scan_ports()
        self._on_ctcss_method_changed()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        # Rig 2 only: enable checkbox lives above the scrollable form
        if self._rig_index == 2:
            self._enable_cb = QCheckBox(_("Enable Rig 2"))
            self._enable_cb.toggled.connect(self._on_enable_toggled)
            outer.addWidget(self._enable_cb)

        # Scroll area so the form remains accessible even in a small dialog
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        # Form container placed inside the scroll area
        self._form_widget = QWidget()
        scroll.setWidget(self._form_widget)
        form = QVBoxLayout(self._form_widget)

        # --- Connection mode ---
        mode_group = QGroupBox(_("Connection Mode"))
        mode_layout = QVBoxLayout(mode_group)
        self._radio_direct = QRadioButton(_("Direct (Hamlib built-in)"))
        self._radio_net = QRadioButton(_("NET (rigctld compatible)"))
        self._radio_direct.setChecked(True)
        self._radio_direct.toggled.connect(self._on_mode_toggled)
        mode_layout.addWidget(self._radio_direct)
        mode_layout.addWidget(self._radio_net)
        form.addWidget(mode_group)

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
        self._populate_model_combo(self._all_models)
        direct_form.addRow(_("Rig Model:"), self._model_combo)

        form.addWidget(self._direct_group)

        # --- NET connection settings ---
        self._net_group = QGroupBox(_("NET Connection Settings"))
        net_form = QFormLayout(self._net_group)
        self._host_edit = QLineEdit("localhost")
        net_form.addRow(_("Host:"), self._host_edit)
        self._net_port_spin = QSpinBox()
        self._net_port_spin.setRange(1, 65535)
        # Rig 1 defaults to rigctld port 4532; Rig 2 defaults to 4533
        self._net_port_spin.setValue(4532 if self._rig_index == 1 else 4533)
        net_form.addRow(_("Port:"), self._net_port_spin)
        form.addWidget(self._net_group)
        self._net_group.setVisible(False)

        # --- Radio Type (Rig 1) / Split Mode (Rig 2) ---
        if self._rig_index == 1:
            # Rig 1 running alone: choose full-duplex / RX-only / TX-only
            type_group = QGroupBox(_("Radio Type"))
            type_form = QFormLayout(type_group)
            self._radio_type_combo = QComboBox()
            self._radio_type_combo.addItem(
                _("Duplex — Main: Downlink (RX) / Sub: Uplink (TX)"), "full_duplex"
            )
            self._radio_type_combo.addItem(_("Simplex — Downlink (RX) only"), "rx_only")
            self._radio_type_combo.addItem(_("Simplex — Uplink (TX) only"), "tx_only")
            type_form.addRow(_("Radio Type:"), self._radio_type_combo)
            form.addWidget(type_group)
        else:
            # Rig 2 enabled: describe how the two rigs share DL/UL duties.
            # The selection automatically sets radio_type for both rigs when saving.
            split_group = QGroupBox(_("Split Mode"))
            split_form = QFormLayout(split_group)
            self._split_mode_combo = QComboBox()
            self._split_mode_combo.addItem(
                _("Rig 1: Downlink (RX only) / Rig 2: Uplink (TX only)"),
                "rig1_dl_rig2_ul",
            )
            self._split_mode_combo.addItem(
                _("Rig 1: Uplink (TX only) / Rig 2: Downlink (RX only)"),
                "rig1_ul_rig2_dl",
            )
            split_form.addRow(_("Split Mode:"), self._split_mode_combo)
            form.addWidget(split_group)

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
        form.addWidget(ctcss_group)

        # Status label (port-scan / model-search results)
        self._status_label = QLabel("")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        form.addWidget(self._status_label)
        form.addStretch()

        # Rig 2 starts disabled until the checkbox is checked
        if self._rig_index == 2:
            self._form_widget.setEnabled(False)

    # ------------------------------------------------------------------ #
    # Slot handlers
    # ------------------------------------------------------------------ #

    def _on_enable_toggled(self, checked: bool) -> None:
        """Enable or disable the entire form based on the Rig 2 checkbox."""
        self._form_widget.setEnabled(checked)

    def _on_mode_toggled(self, _checked: bool) -> None:
        is_direct = self._radio_direct.isChecked()
        self._direct_group.setVisible(is_direct)
        self._net_group.setVisible(not is_direct)

    def _on_scan_ports(self) -> None:
        """Scan serial ports and update the COM port combo box."""
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

    def _on_ctcss_method_changed(self) -> None:
        """Show/hide CAT command fields based on the selected CTCSS method."""
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

    def _on_model_search(self, text: str) -> None:
        """Filter the Hamlib model list as the user types."""
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
    # Model combo helpers
    # ------------------------------------------------------------------ #

    def _populate_model_combo(self, models: list[tuple[int, str, str]]) -> None:
        """Populate the model combo box, preserving the current selection if possible."""
        current_id: int | None = self._model_combo.currentData()
        self._model_combo.clear()
        for mid, mfg, name in models:
            label = f"{mfg} — {name} (#{mid})" if mfg else f"{name} (#{mid})"
            self._model_combo.addItem(label, mid)
        for i in range(self._model_combo.count()):
            if self._model_combo.itemData(i) == current_id:
                self._model_combo.setCurrentIndex(i)
                break

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def is_enabled(self) -> bool:
        """Return True when this rig should be activated.

        Rig 1 is always enabled.  Rig 2 is enabled only when its checkbox
        is checked.
        """
        if self._enable_cb is None:
            return True
        return self._enable_cb.isChecked()

    def load(self, s: dict[str, Any]) -> None:
        """Restore form fields from a saved settings dictionary.

        Args:
            s: dict produced by :meth:`save` (may be a legacy ``rig_settings`` dict).
        """
        # Enable checkbox (Rig 2 only)
        if self._enable_cb is not None:
            checked = bool(s.get("enabled", False))
            self._enable_cb.blockSignals(True)
            self._enable_cb.setChecked(checked)
            self._enable_cb.blockSignals(False)
            self._form_widget.setEnabled(checked)

        # Connection mode
        if s.get("mode") == "net":
            self._radio_net.setChecked(True)
        else:
            self._radio_direct.setChecked(True)

        # COM port
        port = str(s.get("port", ""))
        if port:
            idx = self._port_combo.findText(port)
            if idx >= 0:
                self._port_combo.setCurrentIndex(idx)
            else:
                self._port_combo.setEditText(port)

        # Baud rate
        baud = str(s.get("baud_rate", 9600))
        idx = self._baud_combo.findText(baud)
        if idx >= 0:
            self._baud_combo.setCurrentIndex(idx)

        # Rig model
        model_id = int(s.get("model_id", 1))
        for i in range(self._model_combo.count()):
            if self._model_combo.itemData(i) == model_id:
                self._model_combo.setCurrentIndex(i)
                break

        # NET settings
        self._host_edit.setText(str(s.get("host", "localhost")))
        self._net_port_spin.setValue(int(s.get("net_port", 4532 if self._rig_index == 1 else 4533)))

        # Radio Type (Rig 1) or Split Mode (Rig 2)
        if self._rig_index == 1 and self._radio_type_combo is not None:
            radio_type = str(s.get("radio_type", "full_duplex"))
            for i in range(self._radio_type_combo.count()):
                if self._radio_type_combo.itemData(i) == radio_type:
                    self._radio_type_combo.setCurrentIndex(i)
                    break
        elif self._rig_index == 2 and self._split_mode_combo is not None:
            split_mode = str(s.get("split_mode", "rig1_dl_rig2_ul"))
            for i in range(self._split_mode_combo.count()):
                if self._split_mode_combo.itemData(i) == split_mode:
                    self._split_mode_combo.setCurrentIndex(i)
                    break

        # CTCSS
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

    def save(self) -> dict[str, Any]:
        """Return the current form state as a settings dictionary.

        Returns:
            dict with all rig parameters.  Rig 2 dicts include an ``'enabled'``
            key set to the checkbox state.
        """
        model_id: int = self._model_combo.currentData() or 1
        s: dict[str, Any] = {
            "mode": "direct" if self._radio_direct.isChecked() else "net",
            "port": self._port_combo.currentText(),
            "baud_rate": int(self._baud_combo.currentText()),
            "model_id": model_id,
            "host": self._host_edit.text(),
            "net_port": self._net_port_spin.value(),
            "ctcss_method": self._ctcss_method_combo.currentData() or "hamlib",
            "ctcss_cat_on": self._ctcss_cat_on_edit.text(),
            "ctcss_cat_off": self._ctcss_cat_off_edit.text(),
            "direct_cat_port": self._direct_cat_port_edit.text(),
            "direct_cat_baud": int(self._direct_cat_baud_combo.currentText()),
        }
        # Rig 1: store its own radio_type (used when Rig 2 is disabled)
        if self._rig_index == 1 and self._radio_type_combo is not None:
            s["radio_type"] = self._radio_type_combo.currentData() or "full_duplex"
        # Rig 2: store split_mode; radio_type is derived by RigSettingsDialog._save_settings()
        if self._rig_index == 2 and self._split_mode_combo is not None:
            s["split_mode"] = self._split_mode_combo.currentData() or "rig1_dl_rig2_ul"
        if self._enable_cb is not None:
            s["enabled"] = self._enable_cb.isChecked()
        return s


# ---------------------------------------------------------------------------
# RigSettingsDialog
# ---------------------------------------------------------------------------


class _SdrSettingsPanel(QWidget):
    """SDR Settings tab panel.

    Allows the user to enumerate SoapySDR devices, configure the selected
    device, and assign it to Rig 1 or Rig 2.

    When SoapySDR is not installed, the panel shows an install prompt instead.
    """

    # Sample rates offered in the dropdown (Hz)
    _SAMPLE_RATES: list[tuple[str, float]] = [
        ("250 kHz", 250_000),
        ("1.0 MHz", 1_000_000),
        ("1.4 MHz", 1_400_000),
        ("1.8 MHz", 1_800_000),
        ("2.0 MHz", 2_000_000),
        ("2.4 MHz", 2_400_000),
        ("3.2 MHz", 3_200_000),
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._devices: list[SdrDeviceInfo] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        if not SOAPY_AVAILABLE:
            msg = QLabel(
                _(  # noqa: F823
                    "SoapySDR is not installed.\n"
                    "Use Help > SDR Device Installation to set up your device."
                )
            )
            msg.setWordWrap(True)
            msg.setStyleSheet("color: orange; font-weight: bold;")
            layout.addWidget(msg)
            layout.addStretch()
            return

        # -- Device selection row --
        dev_group = QGroupBox(_("SDR Device"))
        dev_form = QFormLayout(dev_group)

        dev_row = QHBoxLayout()
        self._dev_combo = QComboBox()
        self._dev_combo.setMinimumWidth(260)
        self._enum_btn = QPushButton(_("Enumerate"))
        self._enum_btn.clicked.connect(self._on_enumerate)
        dev_row.addWidget(self._dev_combo)
        dev_row.addWidget(self._enum_btn)
        dev_form.addRow(_("Device:"), dev_row)

        self._driver_label = QLabel("—")
        dev_form.addRow(_("Driver:"), self._driver_label)

        self._serial_label = QLabel("—")
        dev_form.addRow(_("Serial:"), self._serial_label)

        layout.addWidget(dev_group)

        # -- Configuration --
        cfg_group = QGroupBox(_("Configuration"))
        cfg_form = QFormLayout(cfg_group)

        self._rate_combo = QComboBox()
        for label, _hz in self._SAMPLE_RATES:
            self._rate_combo.addItem(label)
        self._rate_combo.setCurrentIndex(5)  # default 2.4 MHz
        cfg_form.addRow(_("Sample Rate:"), self._rate_combo)

        self._ppm_spin = QSpinBox()
        self._ppm_spin.setRange(-200, 200)
        self._ppm_spin.setValue(0)
        self._ppm_spin.setSuffix(" ppm")
        cfg_form.addRow(_("PPM Correction:"), self._ppm_spin)

        gain_row = QHBoxLayout()
        self._gain_auto_rb = QRadioButton(_("Auto"))
        self._gain_manual_rb = QRadioButton(_("Manual"))
        self._gain_auto_rb.setChecked(True)
        self._gain_spin = QSpinBox()
        self._gain_spin.setRange(0, 80)
        self._gain_spin.setValue(40)
        self._gain_spin.setSuffix(" dB")
        self._gain_spin.setEnabled(False)
        self._gain_auto_rb.toggled.connect(lambda on: self._gain_spin.setDisabled(on))
        gain_row.addWidget(self._gain_auto_rb)
        gain_row.addWidget(self._gain_manual_rb)
        gain_row.addWidget(self._gain_spin)
        cfg_form.addRow(_("RF Gain:"), gain_row)

        layout.addWidget(cfg_group)

        # -- Rig slot assignment --
        assign_group = QGroupBox(_("Assign as"))
        assign_layout = QHBoxLayout(assign_group)
        self._rig1_rb = QRadioButton(_("Rig 1"))
        self._rig2_rb = QRadioButton(_("Rig 2"))
        self._rig_none_rb = QRadioButton(_("Not assigned"))
        self._rig_none_rb.setChecked(True)
        assign_layout.addWidget(self._rig1_rb)
        assign_layout.addWidget(self._rig2_rb)
        assign_layout.addWidget(self._rig_none_rb)
        layout.addWidget(assign_group)

        # -- IQ save directory --
        iq_group = QGroupBox(_("IQ Recording"))
        iq_form = QFormLayout(iq_group)
        iq_row = QHBoxLayout()
        self._iq_dir_edit = QLineEdit()
        self._iq_dir_edit.setPlaceholderText(str(QWidget().fontMetrics()))  # overwritten below
        self._iq_dir_edit.setText(str(__import__("pathlib").Path.home() / "iq_recordings"))
        iq_browse_btn = QPushButton(_("Browse…"))
        iq_browse_btn.clicked.connect(self._on_browse_iq_dir)
        iq_row.addWidget(self._iq_dir_edit)
        iq_row.addWidget(iq_browse_btn)
        iq_form.addRow(_("Save directory:"), iq_row)
        layout.addWidget(iq_group)

        layout.addStretch()

        # Enumerate on first show
        self._on_enumerate()

    # ------------------------------------------------------------------ #

    def _on_enumerate(self) -> None:
        if not SOAPY_AVAILABLE:
            return
        try:
            from sdr.device import SdrDevice

            self._devices = SdrDevice.enumerate()
        except Exception:
            self._devices = []

        if not hasattr(self, "_dev_combo"):
            return

        self._dev_combo.clear()
        if not self._devices:
            self._dev_combo.addItem(_("(no devices found)"))
            self._driver_label.setText("—")
            self._serial_label.setText("—")
        else:
            for d in self._devices:
                self._dev_combo.addItem(d.display_name)
            self._on_device_selected(0)
            self._dev_combo.currentIndexChanged.connect(self._on_device_selected)

    def _on_device_selected(self, idx: int) -> None:
        if not self._devices or idx < 0 or idx >= len(self._devices):
            return
        d = self._devices[idx]
        self._driver_label.setText(d.driver or "—")
        self._serial_label.setText(d.serial or "—")

    def _on_browse_iq_dir(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path = QFileDialog.getExistingDirectory(
            self, _("Select IQ Recording Directory"), self._iq_dir_edit.text()
        )
        if path:
            self._iq_dir_edit.setText(path)

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def save(self) -> dict[str, object]:
        """Return a JSON-serialisable dict of current settings."""
        if not SOAPY_AVAILABLE or not hasattr(self, "_dev_combo"):
            return {"enabled": False}

        idx = self._dev_combo.currentIndex()
        device_args: dict[str, str] = {}
        if self._devices and 0 <= idx < len(self._devices):
            device_args = dict(self._devices[idx].args)

        rate_idx = self._rate_combo.currentIndex() if hasattr(self, "_rate_combo") else 5
        rate_hz = (
            self._SAMPLE_RATES[rate_idx][1]
            if 0 <= rate_idx < len(self._SAMPLE_RATES)
            else 2_400_000
        )

        assigned: int | None = None
        if hasattr(self, "_rig1_rb") and self._rig1_rb.isChecked():
            assigned = 1
        elif hasattr(self, "_rig2_rb") and self._rig2_rb.isChecked():
            assigned = 2

        return {
            "enabled": assigned is not None,
            "assigned_rig": assigned,
            "device_args": device_args,
            "device_label": self._dev_combo.currentText(),
            "sample_rate_hz": rate_hz,
            "ppm": self._ppm_spin.value() if hasattr(self, "_ppm_spin") else 0,
            "gain_auto": self._gain_auto_rb.isChecked() if hasattr(self, "_gain_auto_rb") else True,
            "gain_db": self._gain_spin.value() if hasattr(self, "_gain_spin") else 40,
            "iq_save_dir": self._iq_dir_edit.text() if hasattr(self, "_iq_dir_edit") else "",
        }

    def load(self, data: dict[str, object]) -> None:
        """Restore settings from a previously saved dict."""
        if not SOAPY_AVAILABLE or not hasattr(self, "_dev_combo"):
            return

        rate_hz = float(data.get("sample_rate_hz") or 2_400_000)
        for i, (_lbl, r) in enumerate(self._SAMPLE_RATES):
            if abs(r - rate_hz) < 1:
                self._rate_combo.setCurrentIndex(i)
                break

        self._ppm_spin.setValue(int(data.get("ppm") or 0))

        gain_auto = bool(data.get("gain_auto", True))
        self._gain_auto_rb.setChecked(gain_auto)
        self._gain_manual_rb.setChecked(not gain_auto)
        self._gain_spin.setValue(int(data.get("gain_db") or 40))

        assigned = data.get("assigned_rig")
        if assigned == 1:
            self._rig1_rb.setChecked(True)
        elif assigned == 2:
            self._rig2_rb.setChecked(True)
        else:
            self._rig_none_rb.setChecked(True)

        iq_dir = str(data.get("iq_save_dir", ""))
        if iq_dir:
            self._iq_dir_edit.setText(iq_dir)


class RigSettingsDialog(QDialog):
    """Radio > Rig Settings dialog.

    Three tabs — Rig 1, Rig 2, and SDR Settings — each backed by its panel.
    Hamlib models are loaded once and shared between both rig panels.

    DB keys written on OK:
        ``rig1_settings`` — Rig 1 JSON dict
        ``rig2_settings`` — Rig 2 JSON dict (includes ``"enabled": bool``)
        ``sdr_settings``  — SDR JSON dict
    """

    def __init__(self, conn: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._conn = conn
        self.setWindowTitle(_("Rig Settings"))
        self.resize(560, 620)

        # Load models once; share between both panels to avoid double Hamlib scan
        self._all_models = _load_hamlib_models()
        self._panel1 = _RigPanel(1, self._all_models)
        self._panel2 = _RigPanel(2, self._all_models)
        self._sdr_panel = _SdrSettingsPanel()

        self._setup_ui()
        self._load_settings()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._panel1, _("Rig 1"))
        tabs.addTab(self._panel2, _("Rig 2"))
        tabs.addTab(self._sdr_panel, _("SDR Settings"))
        layout.addWidget(tabs)

        # Global info label: total model count
        n = len(self._all_models)
        self._status_label = QLabel(_("{n} rig models available").format(n=n))
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
    # Settings persistence
    # ------------------------------------------------------------------ #

    def _load_settings(self) -> None:
        """Load Rig 1 and Rig 2 settings from the DB.

        Migrates the legacy ``rig_settings`` key to ``rig1_settings`` on first
        open so existing configurations are not lost.
        """
        if not hasattr(self._conn, "execute"):
            return

        # --- Rig 1: migrate legacy 'rig_settings' → 'rig1_settings' ---
        row1 = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = 'rig1_settings'"
        ).fetchone()
        if row1 is None:
            row_old = self._conn.execute(
                "SELECT value FROM app_settings WHERE key = 'rig_settings'"
            ).fetchone()
            if row_old and row_old["value"]:
                self._conn.execute(
                    "INSERT OR REPLACE INTO app_settings (key, value, updated_at) "
                    "VALUES ('rig1_settings', ?, CURRENT_TIMESTAMP)",
                    (row_old["value"],),
                )
                self._conn.commit()
                row1 = self._conn.execute(
                    "SELECT value FROM app_settings WHERE key = 'rig1_settings'"
                ).fetchone()

        if row1 and row1["value"]:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                self._panel1.load(json.loads(row1["value"]))

        # --- Rig 2 ---
        row2 = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = 'rig2_settings'"
        ).fetchone()
        if row2 and row2["value"]:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                self._panel2.load(json.loads(row2["value"]))

        # --- SDR ---
        row_sdr = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = 'sdr_settings'"
        ).fetchone()
        if row_sdr and row_sdr["value"]:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                self._sdr_panel.load(json.loads(row_sdr["value"]))

    def _save_settings(self) -> None:
        """Save Rig 1 and Rig 2 settings to the DB.

        When Rig 2 is enabled, ``radio_type`` for both rigs is derived
        automatically from Rig 2's split_mode selection so the caller
        never has to set both manually:

        * ``rig1_dl_rig2_ul`` → Rig 1 = rx_only, Rig 2 = tx_only
        * ``rig1_ul_rig2_dl`` → Rig 1 = tx_only, Rig 2 = rx_only
        """
        if not hasattr(self._conn, "execute"):
            return

        s1 = self._panel1.save()
        s2 = self._panel2.save()

        # Derive radio_type for both rigs from the split-mode combo when Rig 2 is active
        if s2.get("enabled", False):
            split_mode = str(s2.get("split_mode", "rig1_dl_rig2_ul"))
            if split_mode == "rig1_dl_rig2_ul":
                s1["radio_type"] = "rx_only"
                s2["radio_type"] = "tx_only"
            else:  # rig1_ul_rig2_dl
                s1["radio_type"] = "tx_only"
                s2["radio_type"] = "rx_only"
        # When Rig 2 is disabled, s1["radio_type"] comes from the Rig 1 panel as-is

        self._conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) "
            "VALUES ('rig1_settings', ?, CURRENT_TIMESTAMP)",
            (json.dumps(s1),),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) "
            "VALUES ('rig2_settings', ?, CURRENT_TIMESTAMP)",
            (json.dumps(s2),),
        )
        s_sdr = self._sdr_panel.save()
        self._conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) "
            "VALUES ('sdr_settings', ?, CURRENT_TIMESTAMP)",
            (json.dumps(s_sdr),),
        )
        self._conn.commit()
