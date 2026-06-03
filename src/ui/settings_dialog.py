"""
Settings dialog.

SettingsDialog — Dialog opened from File > Settings.
Includes tabs for TLE source selection and world map selection.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path

import httpx
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
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
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from data.tle_manager import TLE_SOURCES
from i18n import _

# ---------------------------------------------------------------------------
# TLE source constants
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# World map constants
# ---------------------------------------------------------------------------

_GPREDICT_MAPS_BASE = "https://raw.githubusercontent.com/csete/gpredict/master/pixmaps/maps"

#: Curated list of equirectangular world maps from the GPredict repository.
KNOWN_MAPS: list[dict[str, str]] = [
    {
        "name": "NASA Topographic 1024px",
        "filename": "nasa-topo_1024.jpg",
        "description": "NASA topographic map — 1024 px JPEG (~74 KB)",
        "url": f"{_GPREDICT_MAPS_BASE}/nasa-topo_1024.jpg",
    },
    {
        "name": "NASA Topographic 800px",
        "filename": "nasa-topo_800.png",
        "description": "NASA topographic map — 800 px PNG (~280 KB)",
        "url": f"{_GPREDICT_MAPS_BASE}/nasa-topo_800.png",
    },
    {
        "name": "Earth 800px",
        "filename": "earth_800.png",
        "description": "Simple Earth map — 800 px PNG (~385 KB)",
        "url": f"{_GPREDICT_MAPS_BASE}/earth_800.png",
    },
    {
        "name": "Blue Marble — January (1024px)",
        "filename": "nasa-bmng-01_1024.jpg",
        "description": "NASA Blue Marble Natural Geography, January — 1024 px JPEG (~106 KB)",
        "url": f"{_GPREDICT_MAPS_BASE}/nasa-bmng-01_1024.jpg",
    },
    {
        "name": "Blue Marble — March (1024px)",
        "filename": "nasa-bmng-03_1024.jpg",
        "description": "NASA Blue Marble Natural Geography, March — 1024 px JPEG (~106 KB)",
        "url": f"{_GPREDICT_MAPS_BASE}/nasa-bmng-03_1024.jpg",
    },
    {
        "name": "Blue Marble — May (1024px)",
        "filename": "nasa-bmng-05_1024.jpg",
        "description": "NASA Blue Marble Natural Geography, May — 1024 px JPEG (~103 KB)",
        "url": f"{_GPREDICT_MAPS_BASE}/nasa-bmng-05_1024.jpg",
    },
    {
        "name": "Blue Marble — July (1024px)",
        "filename": "nasa-bmng-07_1024.jpg",
        "description": "NASA Blue Marble Natural Geography, July — 1024 px JPEG (~95 KB)",
        "url": f"{_GPREDICT_MAPS_BASE}/nasa-bmng-07_1024.jpg",
    },
    {
        "name": "Blue Marble — August (1024px)",
        "filename": "nasa-bmng-08_1024.jpg",
        "description": "NASA Blue Marble Natural Geography, August — 1024 px JPEG (~94 KB)",
        "url": f"{_GPREDICT_MAPS_BASE}/nasa-bmng-08_1024.jpg",
    },
]

#: Sentinel value stored in app_settings to represent the built-in polygon map.
_BUILTIN_SENTINEL = ""


def _maps_dir() -> Path:
    """Return (and create) the local directory where map images are stored."""
    from platformdirs import user_data_dir

    d = Path(user_data_dir("gpredict-improved", "gpredict-improved")) / "maps"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Download helper thread
# ---------------------------------------------------------------------------


class _DownloadThread(QThread):
    """Background thread that downloads a single URL to a local path."""

    # Use distinct names to avoid shadowing QThread.finished (no-arg signal).
    download_done: Signal = Signal(str)  # emits local file path on success
    download_error: Signal = Signal(str)  # emits error message on failure

    def __init__(self, url: str, dest: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._url = url
        self._dest = dest

    def run(self) -> None:
        """Download the file; emit download_done or download_error when done."""
        try:
            resp = httpx.get(self._url, timeout=60.0, follow_redirects=True)
            resp.raise_for_status()
            self._dest.write_bytes(resp.content)
            self.download_done.emit(str(self._dest))
        except Exception as exc:  # noqa: BLE001
            self.download_error.emit(str(exc))


# ---------------------------------------------------------------------------
# SettingsDialog
# ---------------------------------------------------------------------------


class SettingsDialog(QDialog):
    """File > Settings dialog.  Tabs: TLE Sources, World Map, Custom Groups."""

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._conn = conn
        self.setWindowTitle(_("Settings"))
        self.resize(640, 480)
        self._source_checks: dict[str, QCheckBox] = {}
        # World-map tab state
        self._map_list: QListWidget
        self._preview_label: QLabel
        self._desc_label: QLabel
        self._download_btn: QPushButton
        self._status_label: QLabel
        self._download_thread: _DownloadThread | None = None
        self._selected_map_filename: str = _BUILTIN_SENTINEL
        # Custom Groups tab state
        self._groups_list: QListWidget
        # Autotrack Lists tab state
        self._at_list_widget: QListWidget
        self._at_entry_tree: QTreeWidget
        self._at_selected_list_id: int | None = None
        # Notifications tab state
        self._notif_enabled_cb: QCheckBox
        self._notif_warn_spin: QSpinBox
        self._notif_los_cb: QCheckBox
        self._notif_los_spin: QSpinBox
        self._setup_ui()
        self._load_settings()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        tabs.addTab(self._build_tle_tab(), _("TLE Sources"))
        tabs.addTab(self._build_map_tab(), _("World Map"))
        tabs.addTab(self._build_groups_tab(), _("Custom Groups"))
        tabs.addTab(self._build_autotrack_tab(), _("Autotrack Lists"))
        tabs.addTab(self._build_notifications_tab(), _("Notifications"))

        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_settings)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_tle_tab(self) -> QWidget:
        """Build the TLE Sources tab widget."""
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.addWidget(QLabel(_("Select TLE sources to download:")))

        group = QGroupBox(_("TLE Sources"))
        group_layout = QVBoxLayout(group)

        for src in TLE_SOURCES:
            name = src["name"]
            label = _SOURCE_DISPLAY_NAMES.get(name, name)
            cb = QCheckBox(label)
            self._source_checks[name] = cb
            group_layout.addWidget(cb)

        tab_layout.addWidget(group)
        tab_layout.addStretch()
        return tab

    def _build_map_tab(self) -> QWidget:
        """Build the World Map tab widget."""
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.addWidget(
            QLabel(
                _(
                    "Select the background map for the World Map view.\n"
                    "Maps are downloaded from the GPredict repository and stored locally."
                )
            )
        )

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- Left panel: map list ---
        list_widget = QListWidget()
        list_widget.setMinimumWidth(200)
        list_widget.setMaximumWidth(260)

        # First entry: built-in polygon map (always available)
        builtin_item = QListWidgetItem(_("Built-in polygon map"))
        builtin_item.setData(Qt.ItemDataRole.UserRole, _BUILTIN_SENTINEL)
        list_widget.addItem(builtin_item)

        for info in KNOWN_MAPS:
            filename = info["filename"]
            downloaded = (_maps_dir() / filename).exists()
            suffix = _(" ✓") if downloaded else ""
            item = QListWidgetItem(info["name"] + suffix)
            item.setData(Qt.ItemDataRole.UserRole, filename)
            list_widget.addItem(item)

        list_widget.currentItemChanged.connect(self._on_map_item_changed)
        self._map_list = list_widget
        splitter.addWidget(list_widget)

        # --- Right panel: preview + info + download ---
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 4, 4, 4)

        # Thumbnail preview
        preview = QLabel()
        preview.setFixedSize(320, 160)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setStyleSheet("background: #222; border: 1px solid #555;")
        preview.setText(_("(no preview)"))
        self._preview_label = preview
        right_layout.addWidget(preview)

        # Description
        desc = QLabel()
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._desc_label = desc
        right_layout.addWidget(desc)

        # Download row
        dl_row = QHBoxLayout()
        dl_btn = QPushButton(_("Download"))
        dl_btn.setEnabled(False)
        dl_btn.clicked.connect(self._on_download_clicked)
        self._download_btn = dl_btn
        dl_row.addWidget(dl_btn)

        status_lbl = QLabel()
        self._status_label = status_lbl
        dl_row.addWidget(status_lbl)
        dl_row.addStretch()
        right_layout.addLayout(dl_row)

        right_layout.addStretch()
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        tab_layout.addWidget(splitter)
        return tab

    # ------------------------------------------------------------------ #
    # Map tab logic
    # ------------------------------------------------------------------ #

    def _on_map_item_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        """Update the right panel when the user selects a different map entry."""
        if current is None:
            return

        filename: str = current.data(Qt.ItemDataRole.UserRole)
        self._selected_map_filename = filename

        if filename == _BUILTIN_SENTINEL:
            self._preview_label.setText(_("Built-in polygon map\n(no image file)"))
            self._preview_label.setPixmap(QPixmap())
            self._desc_label.setText(
                _(
                    "The default map drawn with Natural Earth 110m land polygons.\n"
                    "No download required."
                )
            )
            self._download_btn.setEnabled(False)
            self._status_label.setText("")
            return

        # Look up metadata
        info = next((m for m in KNOWN_MAPS if m["filename"] == filename), None)
        if info is None:
            return

        self._desc_label.setText(info["description"])

        local_path = _maps_dir() / filename
        if local_path.exists():
            self._show_preview(local_path)
            self._download_btn.setEnabled(False)
            self._status_label.setText(_("Downloaded"))
        else:
            self._preview_label.setPixmap(QPixmap())
            self._preview_label.setText(_("Not downloaded"))
            self._download_btn.setEnabled(True)
            self._status_label.setText("")

    def _show_preview(self, path: Path) -> None:
        """Load and display a scaled thumbnail of the map image."""
        px = QPixmap(str(path))
        if px.isNull():
            self._preview_label.setText(_("(preview unavailable)"))
            return
        scaled = px.scaled(
            self._preview_label.width(),
            self._preview_label.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview_label.setPixmap(scaled)
        self._preview_label.setText("")

    def _on_download_clicked(self) -> None:
        """Start downloading the currently selected map in a background thread."""
        filename = self._selected_map_filename
        info = next((m for m in KNOWN_MAPS if m["filename"] == filename), None)
        if info is None:
            return

        dest = _maps_dir() / filename
        self._download_btn.setEnabled(False)
        self._status_label.setText(_("Downloading…"))

        thread = _DownloadThread(info["url"], dest, parent=self)
        thread.download_done.connect(self._on_download_finished)
        thread.download_error.connect(self._on_download_failed)
        self._download_thread = thread
        thread.start()

    def _on_download_finished(self, local_path: str) -> None:
        """Called on the main thread when a download completes successfully."""
        self._status_label.setText(_("Downloaded"))
        self._show_preview(Path(local_path))
        self._refresh_list_item_suffix(Path(local_path).name, downloaded=True)
        self._download_thread = None

    def _on_download_failed(self, error: str) -> None:
        """Called on the main thread when a download fails."""
        self._status_label.setText(_("Error: ") + error)
        self._download_btn.setEnabled(True)
        self._download_thread = None

    def _refresh_list_item_suffix(self, filename: str, *, downloaded: bool) -> None:
        """Update the ✓ suffix on the given list item."""
        for i in range(self._map_list.count()):
            item = self._map_list.item(i)
            if item is None:
                continue
            if item.data(Qt.ItemDataRole.UserRole) == filename:
                base_name = next(
                    (m["name"] for m in KNOWN_MAPS if m["filename"] == filename), filename
                )
                item.setText(base_name + (_(" ✓") if downloaded else ""))
                break

    # ------------------------------------------------------------------ #
    # Custom Groups tab
    # ------------------------------------------------------------------ #

    def _build_groups_tab(self) -> QWidget:
        """Build the Custom Groups tab for managing favorite group names and count."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(
            QLabel(
                _(
                    "Define favorite groups shown in the satellite filter.\n"
                    "Each group can be assigned to satellites via right-click."
                )
            )
        )

        self._groups_list = QListWidget()
        self._groups_list.setToolTip(_("Double-click a group name to rename it"))
        layout.addWidget(self._groups_list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton(_("Add Group"))
        add_btn.clicked.connect(self._on_add_group)
        remove_btn = QPushButton(_("Remove Last Group"))
        remove_btn.clicked.connect(self._on_remove_group)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        note = QLabel(_("Note: removing a group unassigns all satellites currently in that group."))
        note.setWordWrap(True)
        layout.addWidget(note)

        self._reload_groups_list()
        return tab

    def _reload_groups_list(self) -> None:
        """Reload the groups list widget from the DB."""
        self._groups_list.clear()
        rows = self._conn.execute(
            "SELECT id, name FROM custom_groups ORDER BY sort_order, id"
        ).fetchall()
        for row in rows:
            item = QListWidgetItem(str(row["name"]))
            item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self._groups_list.addItem(item)

    def _on_add_group(self) -> None:
        """Add a new custom group after the current last group."""
        rows = self._conn.execute("SELECT MAX(id) as mx FROM custom_groups").fetchone()
        next_id = int(rows["mx"] or 0) + 1
        rows2 = self._conn.execute("SELECT MAX(sort_order) as ms FROM custom_groups").fetchone()
        next_order = int(rows2["ms"] or 0) + 1
        default_name = f"Favorite {next_id}"
        self._conn.execute(
            "INSERT INTO custom_groups (id, name, sort_order) VALUES (?, ?, ?)",
            (next_id, default_name, next_order),
        )
        self._conn.commit()
        self._reload_groups_list()

    def _on_remove_group(self) -> None:
        """Remove the last custom group, unassigning its satellites."""
        row = self._conn.execute(
            "SELECT id, name FROM custom_groups ORDER BY sort_order DESC, id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return
        grp_id = int(row["id"])
        grp_name = str(row["name"])
        ans = QMessageBox.question(
            self,
            _("Remove Group"),
            _("Remove group '{name}'? Satellites in this group will be unassigned.").format(
                name=grp_name
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self._conn.execute("DELETE FROM custom_groups WHERE id = ?", (grp_id,))
        self._conn.execute(
            "UPDATE satellites SET favorite_group = 0, is_favorite = 0 WHERE favorite_group = ?",
            (grp_id,),
        )
        self._conn.commit()
        self._reload_groups_list()

    # ------------------------------------------------------------------ #
    # Autotrack Lists tab
    # ------------------------------------------------------------------ #

    def _build_autotrack_tab(self) -> QWidget:
        """Build the Autotrack Lists settings tab."""

        tab = QWidget()
        outer = QHBoxLayout(tab)

        # --- Left: list of Autotrack Lists ---
        left = QVBoxLayout()
        left.addWidget(QLabel(_("Autotrack Lists:")))
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
        outer.addLayout(left)

        # --- Right: entries for the selected list ---
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

        note = QLabel(
            _(
                "Each entry specifies a satellite and the transponder to use for\n"
                "Doppler correction during automatic sequential tracking."
            )
        )
        note.setWordWrap(True)
        right.addWidget(note)
        outer.addLayout(right)

        self._reload_at_lists()
        return tab

    def _reload_at_lists(self) -> None:
        """Reload the list-of-lists widget from the DB."""
        from core.autotrack import AutotrackManager  # noqa: PLC0415

        self._at_list_widget.clear()
        for lst in AutotrackManager.get_all_lists(self._conn):
            item = QListWidgetItem(str(lst["name"]))
            item.setData(0x0100, int(lst["id"]))  # Qt.ItemDataRole.UserRole
            self._at_list_widget.addItem(item)

    def _reload_at_entries(self) -> None:
        """Reload the entry tree for the currently selected list."""
        from core.autotrack import AutotrackManager  # noqa: PLC0415

        self._at_entry_tree.clear()
        if self._at_selected_list_id is None:
            return

        def _fmt_mhz(hz: int | str | None) -> str:
            if not isinstance(hz, int):
                return "—"
            return f"{hz / 1_000_000:.3f}"

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
            item.setData(0, 0x0100, entry["id"])  # entry_id in UserRole
            self._at_entry_tree.addTopLevelItem(item)

    def _on_at_list_selected(self, row: int) -> None:
        item = self._at_list_widget.item(row)
        self._at_selected_list_id = int(item.data(0x0100)) if item else None
        self._reload_at_entries()

    def _on_at_add_list(self) -> None:
        from core.autotrack import AutotrackManager  # noqa: PLC0415

        name, ok = QInputDialog.getText(self, _("New Autotrack List"), _("List name:"))
        if ok and name.strip():
            AutotrackManager.create_list(self._conn, name.strip())
            self._reload_at_lists()

    def _on_at_rename_list(self) -> None:
        from core.autotrack import AutotrackManager  # noqa: PLC0415

        item = self._at_list_widget.currentItem()
        if item is None:
            return
        list_id = int(item.data(0x0100))
        name, ok = QInputDialog.getText(self, _("Rename List"), _("New name:"), text=item.text())
        if ok and name.strip():
            AutotrackManager.rename_list(self._conn, list_id, name.strip())
            self._reload_at_lists()

    def _on_at_delete_list(self) -> None:
        from core.autotrack import AutotrackManager  # noqa: PLC0415

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
        """Open a 2-step dialog: pick satellite → pick transponder."""
        from core.autotrack import AutotrackManager  # noqa: PLC0415

        if self._at_selected_list_id is None:
            QMessageBox.information(self, _("Autotrack"), _("Please select a list first."))
            return

        # Step 1: satellite selection
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

        # Step 2: transponder selection
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
            dl = f"{int(r['downlink_low']) / 1e6:.3f}" if r["downlink_low"] else "?"  # type: ignore[index]
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
        from core.autotrack import AutotrackManager  # noqa: PLC0415

        item = self._at_entry_tree.currentItem()
        if item is None:
            return
        raw = item.data(0, 0x0100)
        if not isinstance(raw, int):
            return
        AutotrackManager.remove_entry(self._conn, raw)
        self._reload_at_entries()

    def _on_at_move_up(self) -> None:
        from core.autotrack import AutotrackManager  # noqa: PLC0415

        item = self._at_entry_tree.currentItem()
        if item is None:
            return
        raw = item.data(0, 0x0100)
        if isinstance(raw, int):
            AutotrackManager.move_entry_up(self._conn, raw)
        self._reload_at_entries()

    def _on_at_move_down(self) -> None:
        from core.autotrack import AutotrackManager  # noqa: PLC0415

        item = self._at_entry_tree.currentItem()
        if item is None:
            return
        raw = item.data(0, 0x0100)
        if isinstance(raw, int):
            AutotrackManager.move_entry_down(self._conn, raw)
        self._reload_at_entries()

    def _save_group_names(self) -> None:
        """Persist edited group names from the list widget back to the DB."""
        for i in range(self._groups_list.count()):
            item = self._groups_list.item(i)
            if item is None:
                continue
            grp_id = int(item.data(Qt.ItemDataRole.UserRole))
            new_name = item.text().strip()
            if new_name:
                self._conn.execute(
                    "UPDATE custom_groups SET name = ? WHERE id = ?", (new_name, grp_id)
                )
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # Notifications tab
    # ------------------------------------------------------------------ #

    def _build_notifications_tab(self) -> QWidget:
        """Build the Notifications settings tab."""
        from core.notifier import (  # noqa: PLC0415
            _DEFAULT_ENABLED,
            _DEFAULT_LOS_WARN_ENABLED,
            _DEFAULT_LOS_WARN_MINUTES,
            _DEFAULT_WARN_MINUTES,
        )

        tab = QWidget()
        layout = QVBoxLayout(tab)

        # --- AOS notifications ---
        aos_group = QGroupBox(_("AOS Notifications"))
        aos_form = QFormLayout(aos_group)

        self._notif_enabled_cb = QCheckBox(_("Enable AOS notifications"))
        self._notif_enabled_cb.setChecked(bool(_DEFAULT_ENABLED))
        aos_form.addRow(self._notif_enabled_cb)

        self._notif_warn_spin = QSpinBox()
        self._notif_warn_spin.setRange(1, 60)
        self._notif_warn_spin.setValue(int(_DEFAULT_WARN_MINUTES))
        self._notif_warn_spin.setSuffix(_(" min before AOS"))
        aos_form.addRow(_("Notify:"), self._notif_warn_spin)

        layout.addWidget(aos_group)

        # --- LOS notifications ---
        los_group = QGroupBox(_("LOS Notifications"))
        los_form = QFormLayout(los_group)

        self._notif_los_cb = QCheckBox(_("Enable LOS notifications"))
        self._notif_los_cb.setChecked(bool(_DEFAULT_LOS_WARN_ENABLED))
        los_form.addRow(self._notif_los_cb)

        self._notif_los_spin = QSpinBox()
        self._notif_los_spin.setRange(1, 30)
        self._notif_los_spin.setValue(int(_DEFAULT_LOS_WARN_MINUTES))
        self._notif_los_spin.setSuffix(_(" min before LOS"))
        los_form.addRow(_("Notify:"), self._notif_los_spin)

        layout.addWidget(los_group)

        note = QLabel(
            _(
                "Notifications are shown when a pass is imminent for the selected\n"
                "satellite (Target tab) or any satellite in the last Group search."
            )
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        return tab

    def _load_notification_settings(self) -> None:
        """Load notification preferences from DB into the Notifications tab widgets."""
        from core.notifier import load_notification_settings  # noqa: PLC0415

        s = load_notification_settings(self._conn)
        self._notif_enabled_cb.setChecked(bool(s["enabled"]))
        self._notif_warn_spin.setValue(int(s["warn_minutes"]))
        self._notif_los_cb.setChecked(bool(s["los_enabled"]))
        self._notif_los_spin.setValue(int(s["los_warn_minutes"]))

    def _save_notification_settings(self) -> None:
        """Persist notification preferences from the tab widgets to the DB."""
        from core.notifier import save_notification_settings  # noqa: PLC0415

        save_notification_settings(
            self._conn,
            {
                "enabled": self._notif_enabled_cb.isChecked(),
                "warn_minutes": self._notif_warn_spin.value(),
                "los_enabled": self._notif_los_cb.isChecked(),
                "los_warn_minutes": self._notif_los_spin.value(),
            },
        )

    # ------------------------------------------------------------------ #
    # Settings persistence
    # ------------------------------------------------------------------ #

    def _load_settings(self) -> None:
        """Load TLE source enablement and world map selection from the DB."""
        # TLE sources
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

        # World map selection
        map_row = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = 'world_map_file'"
        ).fetchone()
        saved_file = (map_row["value"] if map_row and map_row["value"] else "").strip()

        # Select matching item in the list (default to built-in at index 0)
        target_index = 0
        for i in range(self._map_list.count()):
            item = self._map_list.item(i)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == saved_file:
                target_index = i
                break
        self._map_list.setCurrentRow(target_index)

        # Notifications
        self._load_notification_settings()

    def _save_settings(self) -> None:
        """Persist TLE source enablement and world map selection to the DB."""
        # TLE sources
        enabled = [name for name, cb in self._source_checks.items() if cb.isChecked()]
        self._conn.execute(
            """
            INSERT OR REPLACE INTO app_settings (key, value, updated_at)
            VALUES ('tle_enabled_sources', ?, CURRENT_TIMESTAMP)
            """,
            (json.dumps(enabled),),
        )

        # World map
        self._conn.execute(
            """
            INSERT OR REPLACE INTO app_settings (key, value, updated_at)
            VALUES ('world_map_file', ?, CURRENT_TIMESTAMP)
            """,
            (self._selected_map_filename,),
        )
        self._conn.commit()

        # Custom groups: persist inline edits
        self._save_group_names()

        # Notifications
        self._save_notification_settings()

    # ------------------------------------------------------------------ #
    # Static helpers
    # ------------------------------------------------------------------ #

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

    @staticmethod
    def get_world_map_path(conn: sqlite3.Connection) -> str | None:
        """Return the absolute path to the selected world map image, or None for built-in.

        Returns None when no map has been selected, when the saved filename is
        empty (built-in), or when the file does not exist on disk.
        """
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'world_map_file'").fetchone()
        filename = (row["value"] if row and row["value"] else "").strip()
        if not filename:
            return None
        path = _maps_dir() / filename
        return str(path) if path.exists() else None
