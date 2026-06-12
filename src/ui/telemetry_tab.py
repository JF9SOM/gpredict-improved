"""Telemetry tab widget — Communications > Telemetry.

Placeholder implementation.  Full AX.25 / AFSK demodulation pipeline is
added in a subsequent commit.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from i18n import _


class TelemetryTab(QWidget):
    """Non-resident tab opened from Communications > Telemetry."""

    def __init__(
        self,
        conn: Any,
        radio_control: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._conn = conn
        self._radio_control = radio_control
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        label = QLabel(_("Telemetry — coming soon"))
        label.setStyleSheet("color: #aaa; font-size: 14px;")
        layout.addWidget(label)
        layout.addStretch()
