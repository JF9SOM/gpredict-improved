"""
Autotrack / Record dialog.

Combines Autotrack list management, enable/disable control,
and SDR recording (Audio + IQ) settings in one place.
Opened from the "Autotrack/Record" menu in the main menu bar.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from i18n import _

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_AUTOTRACK_HELP = (
    "Autotrack — Sequential Satellite Tracking\n"
    "\n"
    "Autotrack automatically connects the rig and rotator at AOS,\n"
    "switches Doppler correction between satellites in priority order,\n"
    "and disconnects at LOS.\n"
    "\n"
    "Satellite switching rules:\n"
    "\n"
    "1. Current satellite is above Min El → keep tracking.\n"
    "\n"
    "2. Current satellite drops below Min El:\n"
    "   a. Another satellite is already visible\n"
    "      → switch immediately (list order as tiebreak).\n"
    "   b. No satellite is visible yet\n"
    "      → switch to the one with the earliest AOS\n"
    "        (list order as tiebreak on equal AOS).\n"
    "\n"
    "3. Overlapping passes: never interrupt a pass in progress.\n"
    "   Wait for the current satellite's LOS before switching.\n"
    "\n"
    "Prerequisites:\n"
    "  1. Create an Autotrack list and add satellites below.\n"
    "  2. Run a pass search in Upcoming Passes > Group tab.\n"
    "  3. Select the list and check 'Enable Autotrack'.\n"
    "\n"
    "Recording (SDR):\n"
    "  When an SDR is connected, Audio and/or IQ recording\n"
    "  starts automatically at AOS and stops at LOS."
)


class AutotrackRecordDialog(QDialog):
    """
    Non-modal dialog for Autotrack list management and recording settings.

    Signals
    -------
    autotrack_toggled(bool)
        Emitted when the Enable Autotrack checkbox changes.
    autotrack_list_changed(object)
        Emitted when the selected list changes (int list_id or None).
    audio_record_changed(bool)
        Emitted when the Audio Record checkbox changes.
    iq_record_changed(bool)
        Emitted when the IQ Record checkbox changes.
    lists_modified()
        Emitted when lists/entries are added, removed, or reordered
        (callers should reload their list combos).
    """

    autotrack_toggled: Signal = Signal(bool)
    autotrack_list_changed: Signal = Signal(object)
    audio_record_changed: Signal = Signal(bool)
    iq_record_changed: Signal = Signal(bool)
    lists_modified: Signal = Signal()

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Autotrack / Record"))
        self.setMinimumSize(760, 520)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self._conn = conn
        self._at_selected_list_id: int | None = None
        self._setup_ui()
        self._reload_at_lists()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def populate_list_combo(self, lists: list[dict]) -> None:
        """Refresh the list selector combo from external data."""
        self._at_sel_combo.blockSignals(True)
        self._at_sel_combo.clear()
        for lst in lists:
            self._at_sel_combo.addItem(str(lst["name"]), userData=lst["id"])
        self._at_sel_combo.setEnabled(bool(lists))
        self._at_enable_cb.setEnabled(bool(lists))
        if not lists:
            self._at_enable_cb.setChecked(False)
        self._at_sel_combo.blockSignals(False)

    def set_autotrack_enabled(self, enabled: bool) -> None:
        """Programmatically set the Enable Autotrack checkbox."""
        self._at_enable_cb.blockSignals(True)
        self._at_enable_cb.setChecked(enabled)
        self._at_enable_cb.blockSignals(False)

    def set_autotrack_status(self, text: str, ok: bool = True) -> None:
        """Update the status label."""
        self._at_status_label.setText(text)
        color = "#2ecc71" if ok else "#e74c3c"
        self._at_status_label.setStyleSheet(f"color: {color};")

    def is_audio_record_enabled(self) -> bool:
        return bool(self._audio_rec_cb.isChecked())

    def is_iq_record_enabled(self) -> bool:
        return bool(self._iq_rec_cb.isChecked())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setSpacing(10)

        # Help text
        help_label = QLabel(_AUTOTRACK_HELP)
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: gray; font-size: 11px;")
        outer.addWidget(help_label)

        # ── Autotrack control row ─────────────────────────────────────
        ctrl_group = QGroupBox(_("Autotrack Control"))
        ctrl_form = QFormLayout(ctrl_group)
        ctrl_form.setSpacing(6)

        self._at_sel_combo = QComboBox()
        self._at_sel_combo.setEnabled(False)
        self._at_sel_combo.currentIndexChanged.connect(self._on_sel_combo_changed)
        ctrl_form.addRow(_("List:"), self._at_sel_combo)

        self._at_enable_cb = QCheckBox(_("Enable Autotrack"))
        self._at_enable_cb.setEnabled(False)
        self._at_enable_cb.toggled.connect(self._on_enable_toggled)
        ctrl_form.addRow(self._at_enable_cb)

        self._at_status_label = QLabel("—")
        self._at_status_label.setWordWrap(True)
        ctrl_form.addRow(_("Status:"), self._at_status_label)

        outer.addWidget(ctrl_group)

        # ── Record section ────────────────────────────────────────────
        rec_group = QGroupBox(_("Recording (SDR)  — starts at AOS, stops at LOS"))
        rec_layout = QHBoxLayout(rec_group)
        self._audio_rec_cb = QCheckBox(_("Audio Record (MP3)"))
        self._audio_rec_cb.toggled.connect(self.audio_record_changed.emit)
        self._iq_rec_cb = QCheckBox(_("IQ Record"))
        self._iq_rec_cb.toggled.connect(self.iq_record_changed.emit)
        rec_layout.addWidget(self._audio_rec_cb)
        rec_layout.addWidget(self._iq_rec_cb)
        rec_layout.addStretch()
        outer.addWidget(rec_group)

        # ── List management ───────────────────────────────────────────
        mgmt_group = QGroupBox(_("Autotrack Lists"))
        mgmt_layout = QHBoxLayout(mgmt_group)

        # Left: list of lists
        left = QVBoxLayout()
        left.addWidget(QLabel(_("Lists:")))
        self._at_list_widget = QListWidget()
        self._at_list_widget.setMaximumWidth(180)
        self._at_list_widget.currentRowChanged.connect(self._on_at_list_selected)
        left.addWidget(self._at_list_widget)

        list_btn_row = QHBoxLayout()
        add_list_btn = QPushButton(_("Add"))
        add_list_btn.clicked.connect(self._on_at_add_list)
        rename_list_btn = QPushButton(_("Rename"))
        rename_list_btn.clicked.connect(self._on_at_rename_list)
        del_list_btn = QPushButton(_("Delete"))
        del_list_btn.clicked.connect(self._on_at_delete_list)
        list_btn_row.addWidget(add_list_btn)
        list_btn_row.addWidget(rename_list_btn)
        list_btn_row.addWidget(del_list_btn)
        left.addLayout(list_btn_row)
        mgmt_layout.addLayout(left)

        # Right: entries
        right = QVBoxLayout()
        right.addWidget(QLabel(_("Entries (satellite + transponder):")))
        self._at_entry_tree = QTreeWidget()
        self._at_entry_tree.setHeaderLabels(
            [_("Satellite"), _("Transponder"), _("DL (MHz)"), _("UL (MHz)"), _("Mode")]
        )
        self._at_entry_tree.setColumnWidth(0, 140)
        self._at_entry_tree.setColumnWidth(1, 180)
        self._at_entry_tree.setColumnWidth(2, 80)
        self._at_entry_tree.setColumnWidth(3, 80)
        right.addWidget(self._at_entry_tree)

        entry_btn_row = QHBoxLayout()
        add_entry_btn = QPushButton(_("Add Satellite…"))
        add_entry_btn.clicked.connect(self._on_at_add_entry)
        remove_entry_btn = QPushButton(_("Remove"))
        remove_entry_btn.clicked.connect(self._on_at_remove_entry)
        up_btn = QPushButton(_("▲"))
        up_btn.setFixedWidth(30)
        up_btn.clicked.connect(self._on_at_move_up)
        down_btn = QPushButton(_("▼"))
        down_btn.setFixedWidth(30)
        down_btn.clicked.connect(self._on_at_move_down)
        entry_btn_row.addWidget(add_entry_btn)
        entry_btn_row.addWidget(remove_entry_btn)
        entry_btn_row.addStretch()
        entry_btn_row.addWidget(up_btn)
        entry_btn_row.addWidget(down_btn)
        right.addLayout(entry_btn_row)
        mgmt_layout.addLayout(right)

        outer.addWidget(mgmt_group)

        # Close button
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.hide)
        outer.addWidget(btn_box)

    # ------------------------------------------------------------------
    # Slots — Autotrack control
    # ------------------------------------------------------------------

    def _on_sel_combo_changed(self, _idx: int) -> None:
        list_id = self._at_sel_combo.currentData()
        has_list = list_id is not None
        self._at_enable_cb.setEnabled(has_list)
        if not has_list:
            self._at_enable_cb.setChecked(False)
        self.autotrack_list_changed.emit(list_id)

    def _on_enable_toggled(self, checked: bool) -> None:
        self.autotrack_toggled.emit(checked)

    # ------------------------------------------------------------------
    # Slots — List management
    # ------------------------------------------------------------------

    def _reload_at_lists(self) -> None:
        from core.autotrack import AutotrackManager

        lists = AutotrackManager.get_all_lists(self._conn)
        self._at_list_widget.clear()
        for lst in lists:
            item = QListWidgetItem(str(lst["name"]))
            item.setData(0x0100, int(lst["id"]))
            self._at_list_widget.addItem(item)
        self.populate_list_combo(lists)
        self.lists_modified.emit()

    def _reload_at_entries(self) -> None:
        from core.autotrack import AutotrackManager

        self._at_entry_tree.clear()
        if self._at_selected_list_id is None:
            return

        def _fmt_mhz(hz: int | str | None) -> str:
            return f"{int(hz) / 1_000_000:.3f}" if isinstance(hz, int) else "—"

        for entry in AutotrackManager.get_entries(self._conn, self._at_selected_list_id):
            item = QTreeWidgetItem(
                [
                    str(entry.get("sat_name") or entry["norad_cat_id"]),
                    str(entry.get("xpdr_desc") or entry["xpdr_uuid"]),
                    _fmt_mhz(entry.get("downlink_low")),
                    _fmt_mhz(entry.get("uplink_low")),
                    str(entry.get("mode") or ""),
                ]
            )
            item.setData(0, 0x0100, entry["id"])
            self._at_entry_tree.addTopLevelItem(item)

    def _on_at_list_selected(self, row: int) -> None:
        item = self._at_list_widget.item(row)
        self._at_selected_list_id = int(item.data(0x0100)) if item else None
        self._reload_at_entries()

    def _on_at_add_list(self) -> None:
        from core.autotrack import AutotrackManager

        name, ok = QInputDialog.getText(self, _("New Autotrack List"), _("List name:"))
        if ok and name.strip():
            AutotrackManager.create_list(self._conn, name.strip())
            self._reload_at_lists()

    def _on_at_rename_list(self) -> None:
        from core.autotrack import AutotrackManager

        item = self._at_list_widget.currentItem()
        if item is None:
            return
        list_id = int(item.data(0x0100))
        name, ok = QInputDialog.getText(self, _("Rename List"), _("New name:"), text=item.text())
        if ok and name.strip():
            AutotrackManager.rename_list(self._conn, list_id, name.strip())
            self._reload_at_lists()

    def _on_at_delete_list(self) -> None:
        from core.autotrack import AutotrackManager

        item = self._at_list_widget.currentItem()
        if item is None:
            return
        list_id = int(item.data(0x0100))
        ans = QMessageBox.question(
            self,
            _("Delete List"),
            _("Delete list '{name}' and all its entries?").format(name=item.text()),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            AutotrackManager.delete_list(self._conn, list_id)
            self._at_selected_list_id = None
            self._reload_at_lists()
            self._at_entry_tree.clear()

    def _on_at_add_entry(self) -> None:
        from core.autotrack import AutotrackManager

        if self._at_selected_list_id is None:
            QMessageBox.information(self, _("Autotrack"), _("Please select a list first."))
            return

        rows = self._conn.execute(
            "SELECT norad_cat_id, name FROM satellites WHERE is_hidden = 0 ORDER BY name"
        ).fetchall()
        if not rows:
            QMessageBox.information(self, _("Autotrack"), _("No satellites available."))
            return
        sat_names = [f"{r['name']} ({r['norad_cat_id']})" for r in rows]
        sat_name, ok = QInputDialog.getItem(
            self, _("Add Satellite"), _("Select satellite:"), sat_names, 0, False
        )
        if not ok:
            return
        idx = sat_names.index(sat_name)
        norad = int(rows[idx]["norad_cat_id"])

        xpdr_rows = self._conn.execute(
            "SELECT uuid, description, downlink_low, uplink_low, mode"
            " FROM transmitters WHERE norad_cat_id = ? AND alive = 1"
            " ORDER BY downlink_low",
            (norad,),
        ).fetchall()
        if not xpdr_rows:
            QMessageBox.information(
                self, _("Autotrack"), _("No transponders found for this satellite.")
            )
            return

        def _xpdr_label(r: object) -> str:
            dl = (
                f"{int(r['downlink_low']) / 1e6:.3f}"  # type: ignore[index]
                if r["downlink_low"]  # type: ignore[index]
                else "?"
            )
            return f"{r['description']}  DL:{dl} MHz  {r['mode'] or ''}"  # type: ignore[index]

        xpdr_labels = [_xpdr_label(r) for r in xpdr_rows]
        xpdr_label, ok = QInputDialog.getItem(
            self, _("Select Transponder"), _("Transponder:"), xpdr_labels, 0, False
        )
        if not ok:
            return
        xpdr_idx = xpdr_labels.index(xpdr_label)
        xpdr_uuid = str(xpdr_rows[xpdr_idx]["uuid"])
        AutotrackManager.add_entry(self._conn, self._at_selected_list_id, norad, xpdr_uuid)
        self._reload_at_entries()

    def _on_at_remove_entry(self) -> None:
        from core.autotrack import AutotrackManager

        item = self._at_entry_tree.currentItem()
        if item is None:
            return
        raw = item.data(0, 0x0100)
        if not isinstance(raw, int):
            return
        AutotrackManager.remove_entry(self._conn, raw)
        self._reload_at_entries()

    def _on_at_move_up(self) -> None:
        from core.autotrack import AutotrackManager

        item = self._at_entry_tree.currentItem()
        if item is None:
            return
        raw = item.data(0, 0x0100)
        if isinstance(raw, int):
            AutotrackManager.move_entry_up(self._conn, raw)
        self._reload_at_entries()

    def _on_at_move_down(self) -> None:
        from core.autotrack import AutotrackManager

        item = self._at_entry_tree.currentItem()
        if item is None:
            return
        raw = item.data(0, 0x0100)
        if isinstance(raw, int):
            AutotrackManager.move_entry_down(self._conn, raw)
        self._reload_at_entries()
