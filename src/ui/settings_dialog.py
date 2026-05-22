"""
Settings dialog.

SettingsDialog — Dialog opened from File > Settings.
Includes a tab for TLE source selection.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from data.tle_manager import TLE_SOURCES
from i18n import _

_SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "celestrak-stations": "Space Stations (CelesTrak)",
    "celestrak-amateur": "Amateur Satellites (CelesTrak)",
    "celestrak-cubesat": "CubeSat (CelesTrak)",
    "celestrak-weather": "Weather Satellites (CelesTrak)",
    "celestrak-earth-obs": "Earth Observation (CelesTrak)",
    "celestrak-science": "Science Satellites (CelesTrak)",
}

_DEFAULT_ENABLED = {
    "celestrak-stations",
    "celestrak-amateur",
    "celestrak-cubesat",
    "celestrak-weather",
    "celestrak-earth-obs",
    "celestrak-science",
}


class SettingsDialog(QDialog):
    """File > Settings dialog. Includes a tab for TLE source selection."""

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._conn = conn
        self.setWindowTitle(_("Settings"))
        self.resize(420, 320)
        self._source_checks: dict[str, QCheckBox] = {}
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        tle_tab = QWidget()
        tle_layout = QVBoxLayout(tle_tab)
        tle_layout.addWidget(QLabel(_("Select TLE sources to download:")))

        group = QGroupBox(_("TLE Sources"))
        group_layout = QVBoxLayout(group)

        for src in TLE_SOURCES:
            name = src["name"]
            label = _SOURCE_DISPLAY_NAMES.get(name, name)
            cb = QCheckBox(label)
            self._source_checks[name] = cb
            group_layout.addWidget(cb)

        tle_layout.addWidget(group)
        tle_layout.addStretch()
        tabs.addTab(tle_tab, _("TLE Sources"))

        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_settings)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_settings(self) -> None:
        row = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = 'tle_enabled_sources'"
        ).fetchone()
        enabled: set[str] | None = None
        if row and row["value"]:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                enabled = set(json.loads(row["value"]))

        for name, cb in self._source_checks.items():
            if enabled is None:
                cb.setChecked(name in _DEFAULT_ENABLED)
            else:
                cb.setChecked(name in enabled)

    def _save_settings(self) -> None:
        enabled = [name for name, cb in self._source_checks.items() if cb.isChecked()]
        self._conn.execute(
            """
            INSERT OR REPLACE INTO app_settings (key, value, updated_at)
            VALUES ('tle_enabled_sources', ?, CURRENT_TIMESTAMP)
            """,
            (json.dumps(enabled),),
        )
        self._conn.commit()

    @staticmethod
    def get_enabled_sources(conn: sqlite3.Connection) -> list[str]:
        """Return the list of enabled TLE source names. Returns defaults when not yet saved."""
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'tle_enabled_sources'"
        ).fetchone()
        if row and row["value"]:
            try:
                return list(json.loads(row["value"]))
            except (json.JSONDecodeError, TypeError):
                pass
        return list(_DEFAULT_ENABLED)
