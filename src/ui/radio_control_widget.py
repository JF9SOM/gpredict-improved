"""
Radio Control widget.

RadioControlWidget — Rig and rotator control panel for the selected satellite.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from i18n import _
from rig.controller import RigController, RigState, RotatorController, RotatorState


class RadioControlWidget(QWidget):
    """
    Rig and rotator control panel.

    Displays Doppler-corrected frequency, mode, rotator position, and
    rig/rotator connection status for the selected satellite, with connect/disconnect buttons.
    """

    transmitter_changed: Signal = Signal(object)
    cycle_changed: Signal = Signal(int)  # ms
    tune_requested: Signal = Signal()
    lock_changed: Signal = Signal(bool)
    rig_connected: Signal = Signal()
    ctcss_send_requested: Signal = Signal(float)
    ctcss_activate_requested: Signal = Signal()  # activation-tone button pressed

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rig: RigController | None = None
        self._rotator: RotatorController | None = None
        self._transmitters: list[dict[str, Any]] = []
        self._current_ctcss_hz: float | None = None
        self._ctcss_activation_hz: float | None = None
        self._setup_ui()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Satellite info + transponder selection
        sat_group = QGroupBox(_("Satellite"))
        sat_form = QFormLayout(sat_group)
        sat_form.setContentsMargins(4, 4, 4, 4)
        self._sat_name_label = QLabel("—")
        self._norad_label = QLabel("—")
        self._xpdr_combo = QComboBox()
        self._xpdr_combo.setEnabled(False)
        self._xpdr_combo.currentIndexChanged.connect(self._on_xpdr_changed)
        sat_form.addRow(_("Name:"), self._sat_name_label)
        sat_form.addRow(_("NORAD:"), self._norad_label)
        sat_form.addRow(_("Transponder:"), self._xpdr_combo)

        # T / L button row
        tl_row = QHBoxLayout()
        self._tune_btn = QPushButton(_("T"))
        self._tune_btn.setToolTip(_("Tune: reset downlink/uplink to center of transponder band"))
        self._tune_btn.clicked.connect(self.tune_requested.emit)
        self._lock_btn = QPushButton(_("L"))
        self._lock_btn.setToolTip(_("Lock: link uplink to downlink (inverting transponder aware)"))
        self._lock_btn.setCheckable(True)
        self._lock_btn.toggled.connect(self.lock_changed.emit)
        tl_row.addWidget(self._tune_btn)
        tl_row.addWidget(self._lock_btn)
        tl_row.addStretch()
        sat_form.addRow("", tl_row)

        layout.addWidget(sat_group)

        # Frequency / Doppler
        freq_group = QGroupBox(_("Frequency"))
        freq_form = QFormLayout(freq_group)
        freq_form.setContentsMargins(4, 4, 4, 4)
        self._downlink_label = QLabel("—")
        self._downlink_doppler_label = QLabel("—")
        self._uplink_label = QLabel("—")
        self._uplink_doppler_label = QLabel("—")
        self._mode_label = QLabel("—")
        self._ctcss_label = QLabel("—")

        # CTCSS row: label + Send + Activate buttons.
        # Note: FTX-1F does not support L CTCSS_TONE via Hamlib (RPRT -11),
        # so these buttons may be no-ops on that radio.
        ctcss_row = QWidget()
        ctcss_row_layout = QHBoxLayout(ctcss_row)
        ctcss_row_layout.setContentsMargins(0, 0, 0, 0)
        ctcss_row_layout.setSpacing(4)
        self._ctcss_send_btn = QPushButton("—")
        self._ctcss_send_btn.setToolTip(_("Send current CTCSS tone to rig"))
        self._ctcss_send_btn.clicked.connect(self._on_ctcss_send)
        self._ctcss_send_btn.setVisible(False)
        self._ctcss_activate_btn = QPushButton(_("74.4 Hz (Activation)"))
        self._ctcss_activate_btn.setToolTip(_("SO-50: activate 10-minute timer with 74.4 Hz tone"))
        self._ctcss_activate_btn.clicked.connect(self._on_ctcss_activate)
        self._ctcss_activate_btn.setVisible(False)
        ctcss_row_layout.addWidget(self._ctcss_label)
        ctcss_row_layout.addWidget(self._ctcss_send_btn)
        ctcss_row_layout.addWidget(self._ctcss_activate_btn)
        ctcss_row_layout.addStretch()

        freq_form.addRow(_("Downlink:"), self._downlink_label)
        freq_form.addRow(_("  Doppler:"), self._downlink_doppler_label)
        freq_form.addRow(_("Uplink:"), self._uplink_label)
        freq_form.addRow(_("  Doppler:"), self._uplink_doppler_label)
        freq_form.addRow(_("Mode:"), self._mode_label)
        freq_form.addRow(_("CTCSS:"), ctcss_row)
        layout.addWidget(freq_group)

        # Rotator position
        rot_group = QGroupBox(_("Rotator"))
        rot_form = QFormLayout(rot_group)
        rot_form.setContentsMargins(4, 4, 4, 4)
        self._rot_az_label = QLabel("—")
        self._rot_el_label = QLabel("—")
        rot_form.addRow(_("AZ:"), self._rot_az_label)
        rot_form.addRow(_("EL:"), self._rot_el_label)
        layout.addWidget(rot_group)

        # Connection status
        status_group = QGroupBox(_("Status"))
        status_form = QFormLayout(status_group)
        status_form.setContentsMargins(4, 4, 4, 4)
        self._rig_status_label = QLabel(_("Not configured"))
        self._rot_status_label = QLabel(_("Not configured"))
        status_form.addRow(_("Rig:"), self._rig_status_label)
        status_form.addRow(_("Rotator:"), self._rot_status_label)

        cycle_row = QHBoxLayout()
        self._cycle_spin = QSpinBox()
        self._cycle_spin.setRange(10, 10000)
        self._cycle_spin.setSingleStep(10)
        self._cycle_spin.setValue(1000)
        self._cycle_spin.valueChanged.connect(lambda v: self.cycle_changed.emit(v))
        cycle_row.addWidget(self._cycle_spin)
        cycle_row.addWidget(QLabel(_("msec")))
        cycle_row.addStretch()
        status_form.addRow(_("Cycle:"), cycle_row)

        layout.addWidget(status_group)

        # Button row
        btn_row = QHBoxLayout()
        self._connect_rig_btn = QPushButton(_("Connect Rig"))
        self._connect_rig_btn.clicked.connect(self._on_connect_rig)
        self._connect_rot_btn = QPushButton(_("Connect Rotator"))
        self._connect_rot_btn.clicked.connect(self._on_connect_rotator)
        btn_row.addWidget(self._connect_rig_btn)
        btn_row.addWidget(self._connect_rot_btn)
        layout.addLayout(btn_row)

        layout.addStretch()

        self._update_rig_status()
        self._update_rot_status()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def set_satellite(self, norad: int, name: str) -> None:
        """Set the selected satellite."""
        self._sat_name_label.setText(name)
        self._norad_label.setText(str(norad))

    def clear_satellite(self) -> None:
        """Clear satellite info and frequency display."""
        self._sat_name_label.setText("—")
        self._norad_label.setText("—")
        self._xpdr_combo.blockSignals(True)
        self._xpdr_combo.clear()
        self._xpdr_combo.setEnabled(False)
        self._transmitters = []
        self._xpdr_combo.blockSignals(False)
        self._clear_frequency()
        self.update_ctcss(None, None)

    def set_transmitters(
        self,
        transmitters: list[dict[str, Any]],
        default_index: int = 0,
    ) -> None:
        """Populate the transponder combo box and apply the default selection.

        The first item is the default (caller is expected to sort by priority beforehand).
        Emits transmitter_changed with the default selection.
        """
        self._transmitters = transmitters
        self._xpdr_combo.blockSignals(True)
        self._xpdr_combo.clear()
        for xpdr in transmitters:
            self._xpdr_combo.addItem(self._xpdr_label(xpdr))
        self._xpdr_combo.setEnabled(len(transmitters) > 0)
        if transmitters:
            idx = max(0, min(default_index, len(transmitters) - 1))
            self._xpdr_combo.setCurrentIndex(idx)
        self._xpdr_combo.blockSignals(False)
        selected = transmitters[default_index] if transmitters else None
        self.transmitter_changed.emit(selected)

    def update_doppler(
        self,
        downlink_nominal_hz: float | None,
        downlink_corrected_hz: float | None,
        downlink_shift_hz: float | None,
        uplink_nominal_hz: float | None,
        uplink_corrected_hz: float | None,
        uplink_shift_hz: float | None,
        mode: str | None = None,
        ctcss_hz: float | None = None,
    ) -> None:
        """Update the Doppler-corrected frequency and status label.

        ctcss_hz: value shown in the CTCSS status label (may be an override set by
                  a button press; the Send button tone is managed by update_ctcss()).
        """
        if downlink_corrected_hz is not None:
            self._downlink_label.setText(f"{downlink_corrected_hz / 1e6:.6f} MHz")
            if downlink_shift_hz is not None:
                sign = "+" if downlink_shift_hz >= 0 else ""
                self._downlink_doppler_label.setText(f"{sign}{downlink_shift_hz:.0f} Hz")
            else:
                self._downlink_doppler_label.setText("—")
        else:
            self._downlink_label.setText("—")
            self._downlink_doppler_label.setText("—")

        if uplink_corrected_hz is not None:
            self._uplink_label.setText(f"{uplink_corrected_hz / 1e6:.6f} MHz")
            if uplink_shift_hz is not None:
                sign = "+" if uplink_shift_hz >= 0 else ""
                self._uplink_doppler_label.setText(f"{sign}{uplink_shift_hz:.0f} Hz")
            else:
                self._uplink_doppler_label.setText("—")
        else:
            self._uplink_label.setText("—")
            self._uplink_doppler_label.setText("—")

        self._mode_label.setText(mode if mode else "—")
        display_hz = ctcss_hz if (ctcss_hz and ctcss_hz > 0) else None
        self._ctcss_label.setText(f"{display_hz:.1f} Hz" if display_hz else "—")

    def update_rotator(self, state: RotatorState | None) -> None:
        """Update the current rotator position."""
        if state is None:
            self._rot_az_label.setText("—")
            self._rot_el_label.setText("—")
        else:
            self._rot_az_label.setText(f"{state.azimuth_deg:.1f}°")
            self._rot_el_label.setText(f"{state.elevation_deg:.1f}°")

    def set_rig(self, rig: RigController | None) -> None:
        """Set the rig controller."""
        self._rig = rig
        self._update_rig_status()

    def set_rotator(self, rotator: RotatorController | None) -> None:
        """Set the rotator controller."""
        self._rotator = rotator
        self._update_rot_status()

    def set_cycle(self, ms: int) -> None:
        """Set the Cycle spin box value externally without emitting a signal."""
        self._cycle_spin.blockSignals(True)
        self._cycle_spin.setValue(max(10, min(10000, ms)))
        self._cycle_spin.blockSignals(False)

    def refresh_status(self) -> None:
        """Update the connection status display (called by timer)."""
        self._update_rig_status()
        self._update_rot_status()
        if self._rotator is not None and self._rotator.is_connected:
            self.update_rotator(self._rotator.get_position())

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _clear_frequency(self) -> None:
        for label in (
            self._downlink_label,
            self._downlink_doppler_label,
            self._uplink_label,
            self._uplink_doppler_label,
            self._mode_label,
            self._ctcss_label,
        ):
            label.setText("—")

    @staticmethod
    def _xpdr_label(xpdr: dict[str, Any]) -> str:
        """Generate a display label for the transponder combo box."""
        dl = xpdr.get("downlink_low")
        dl_str = f"{dl / 1e6:.3f} MHz" if dl else "—"
        xtype = xpdr.get("type", "")
        desc = xpdr.get("description", "?")
        return f"{desc}  [{dl_str}  {xtype}]"

    def update_ctcss(self, tone_hz: float | None, activation_hz: float | None) -> None:
        """Update CTCSS button state from the current transponder and satellite DB info."""
        self._current_ctcss_hz = tone_hz if (tone_hz and tone_hz > 0) else None
        self._ctcss_activation_hz = activation_hz if (activation_hz and activation_hz > 0) else None
        self._update_ctcss_buttons()

    def _update_ctcss_buttons(self) -> None:
        """Show/hide and label CTCSS buttons based on current tone and activation tone."""
        has_tone = self._current_ctcss_hz is not None
        self._ctcss_send_btn.setVisible(has_tone)
        if has_tone:
            self._ctcss_send_btn.setText(f"{self._current_ctcss_hz:.1f} Hz")
        has_activation = self._ctcss_activation_hz is not None
        self._ctcss_activate_btn.setVisible(has_activation)
        if has_activation:
            self._ctcss_activate_btn.setText(f"{self._ctcss_activation_hz:.1f} Hz (Activation)")

    def _on_xpdr_changed(self, index: int) -> None:
        if 0 <= index < len(self._transmitters):
            self.transmitter_changed.emit(self._transmitters[index])

    def _update_rig_status(self) -> None:
        if self._rig is None:
            self._rig_status_label.setText(_("Not configured"))
            self._rig_status_label.setStyleSheet("color: gray;")
            self._connect_rig_btn.setEnabled(False)
            return
        self._connect_rig_btn.setEnabled(True)
        state = self._rig.state
        if state == RigState.CONNECTED:
            info = self._rig.get_rig_info()
            name = info.model_name if info else "—"
            self._rig_status_label.setText(f"{_('Connected')}: {name}")
            self._rig_status_label.setStyleSheet("color: green;")
            self._connect_rig_btn.setText(_("Disconnect Rig"))
        elif state == RigState.CONNECTING:
            self._rig_status_label.setText(_("Connecting..."))
            self._rig_status_label.setStyleSheet("color: orange;")
            self._connect_rig_btn.setText(_("Disconnect Rig"))
        elif state == RigState.ERROR:
            self._rig_status_label.setText(_("Error"))
            self._rig_status_label.setStyleSheet("color: red;")
            self._connect_rig_btn.setText(_("Retry"))
        else:
            self._rig_status_label.setText(_("Disconnected"))
            self._rig_status_label.setStyleSheet("color: gray;")
            self._connect_rig_btn.setText(_("Connect Rig"))

    def _update_rot_status(self) -> None:
        if self._rotator is None:
            self._rot_status_label.setText(_("Not configured"))
            self._rot_status_label.setStyleSheet("color: gray;")
            self._connect_rot_btn.setEnabled(False)
            return
        self._connect_rot_btn.setEnabled(True)
        if self._rotator.is_connected:
            self._rot_status_label.setText(_("Connected"))
            self._rot_status_label.setStyleSheet("color: green;")
            self._connect_rot_btn.setText(_("Disconnect Rotator"))
        else:
            self._rot_status_label.setText(_("Disconnected"))
            self._rot_status_label.setStyleSheet("color: gray;")
            self._connect_rot_btn.setText(_("Connect Rotator"))

    def _on_connect_rig(self) -> None:
        if self._rig is None:
            return
        if self._rig.is_connected:
            self._rig.disconnect()
        else:
            self._rig.connect()
            if self._rig.is_connected:
                self.rig_connected.emit()
        self._update_rig_status()

    def _on_ctcss_send(self) -> None:
        if self._current_ctcss_hz is not None:
            self._ctcss_label.setText(f"{self._current_ctcss_hz:.1f} Hz")
            self.ctcss_send_requested.emit(self._current_ctcss_hz)

    def _on_ctcss_activate(self) -> None:
        """Transmit the satellite's activation tone (e.g. SO-50: 74.4 Hz)."""
        if self._ctcss_activation_hz is not None:
            self._ctcss_label.setText(f"{self._ctcss_activation_hz:.1f} Hz")
        self.ctcss_activate_requested.emit()

    def _on_connect_rotator(self) -> None:
        if self._rotator is None:
            return
        if self._rotator.is_connected:
            self._rotator.disconnect()
        else:
            self._rotator.connect()
        self._update_rot_status()
