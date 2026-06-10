"""
App Update dialog — Help > Check for Updates…

Checks GitHub Releases for a newer version of GPredict-Improved and
offers a one-click download + install per platform:

  Linux   — downloads GPredict-Improved-x86_64.AppImage, replaces the
             currently running AppImage, then prompts restart.
  Windows — downloads GPredict-Improved-Setup.exe and launches it
             (NSIS handles overwrite + restart).
  macOS   — downloads GPredict-Improved.dmg, mounts it, copies the
             .app bundle over the existing one, unmounts, prompts restart.
"""

from __future__ import annotations

import json
import logging
import platform
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from i18n import _

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com/repos/JF9SOM/gpredict-improved/releases/latest"
GITHUB_RELEASES = "https://github.com/JF9SOM/gpredict-improved/releases"


def _current_version() -> str:
    """Return the running application version string."""
    return QApplication.applicationVersion() or "0.0.0"


def _asset_name() -> str:
    """Return the expected release asset filename for the current platform."""
    os_name = platform.system()
    if os_name == "Linux":
        return "GPredict-Improved-x86_64.AppImage"
    if os_name == "Windows":
        return "GPredict-Improved-Setup.exe"
    if os_name == "Darwin":
        return "GPredict-Improved.dmg"
    return ""


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------


class _CheckWorker(QThread):
    """Fetches latest release info from GitHub Releases API."""

    result = Signal(str, str)  # latest_version, download_url
    error = Signal(str)

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                GITHUB_API,
                headers={"User-Agent": "gpredict-improved/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data: dict[str, object] = json.loads(resp.read())

            tag: str = str(data.get("tag_name", "")).lstrip("v")
            raw_assets = data.get("assets")
            assets: list[dict[str, object]] = (
                [a for a in raw_assets if isinstance(a, dict)]
                if isinstance(raw_assets, list)
                else []
            )

            target = _asset_name()
            url = ""
            for asset in assets:
                if str(asset.get("name", "")) == target:
                    url = str(asset.get("browser_download_url", ""))
                    break

            self.result.emit(tag, url)
        except Exception as exc:
            self.error.emit(str(exc))


class _DownloadWorker(QThread):
    """Downloads the release asset and triggers installation."""

    progress = Signal(str)
    finished = Signal(bool, str)  # success, message

    def __init__(self, url: str, version: str) -> None:
        super().__init__()
        self._url = url
        self._version = version

    def run(self) -> None:
        try:
            fname = self._url.split("/")[-1]
            tmp_dir = Path(tempfile.mkdtemp(prefix="gpredict-update-"))
            tmp_path = tmp_dir / fname

            self.progress.emit(f"Downloading {fname}…")
            urllib.request.urlretrieve(self._url, tmp_path, reporthook=self._reporthook)

            self.progress.emit("Preparing installation…")
            os_name = platform.system()

            if os_name == "Windows":
                self._install_windows(tmp_path)
            elif os_name == "Linux":
                self._install_linux(tmp_path)
            elif os_name == "Darwin":
                self._install_macos(tmp_path)
            else:
                raise RuntimeError(f"Unsupported platform: {os_name}")

        except Exception as exc:
            logger.exception("App update failed")
            self.finished.emit(False, str(exc))

    def _reporthook(self, count: int, block_size: int, total_size: int) -> None:
        if total_size > 0:
            pct = min(100, int(count * block_size * 100 / total_size))
            self.progress.emit(f"Downloading… {pct}%")

    def _install_windows(self, setup_exe: Path) -> None:
        """Launch the NSIS installer with UAC elevation via ShellExecute."""
        # Use ShellExecuteW so Windows shows the UAC elevation prompt.
        # subprocess.Popen cannot trigger UAC and would be denied when
        # the installer tries to write to Program Files.
        # Do NOT pass /S (silent) — the UAC dialog and NSIS UI must be visible.
        import ctypes

        ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, "runas", str(setup_exe), None, None, 1
        )
        self.finished.emit(
            True,
            _(
                "Installer launched. GPredict-Improved will close now "
                "so the installer can complete the update."
            ),
        )

    def _install_linux(self, appimage: Path) -> None:
        """Replace the running AppImage with the downloaded one."""
        current = Path(sys.executable)
        # In a frozen bundle sys.executable points to the AppImage
        if not getattr(sys, "frozen", False):
            raise RuntimeError(
                "Auto-update is only supported in the AppImage bundle. "
                "Please replace the AppImage manually."
            )
        appimage.chmod(0o755)
        # Atomic replace: copy to a temp name beside the target, then rename
        tmp_target = current.with_suffix(".new")
        import shutil

        shutil.copy2(appimage, tmp_target)
        tmp_target.rename(current)
        self.finished.emit(
            True,
            _("AppImage updated to {ver}. Please restart GPredict-Improved.").format(
                ver=self._version
            ),
        )

    def _install_macos(self, dmg: Path) -> None:
        """Mount DMG, copy .app, unmount."""
        mount_point = Path(tempfile.mkdtemp(prefix="gpredict-dmg-"))
        self.progress.emit("Mounting DMG…")
        subprocess.run(
            ["hdiutil", "attach", str(dmg), "-mountpoint", str(mount_point), "-nobrowse"],
            check=True,
        )
        try:
            apps = list(mount_point.glob("*.app"))
            if not apps:
                raise RuntimeError("No .app bundle found in DMG.")
            src_app = apps[0]
            # Install to /Applications if running from there, else beside current
            dest_app = Path("/Applications") / src_app.name
            self.progress.emit(f"Installing {src_app.name}…")
            import shutil

            if dest_app.exists():
                shutil.rmtree(dest_app)
            shutil.copytree(src_app, dest_app)
        finally:
            subprocess.run(["hdiutil", "detach", str(mount_point)], check=False)
        self.finished.emit(
            True,
            _("{app} updated to {ver}. Please restart GPredict-Improved.").format(
                app=src_app.name, ver=self._version
            ),
        )


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class AppUpdateDialog(QDialog):
    """
    Help > Check for Updates… dialog.

    Shows the current version, checks GitHub for the latest release,
    and offers a one-click download + install.
    """

    #: Emitted when the Windows installer has been launched and the app
    #: should quit to let NSIS complete the update.
    quit_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Check for Updates"))
        self.resize(540, 420)
        self._check_worker: _CheckWorker | None = None
        self._download_worker: _DownloadWorker | None = None
        self._latest_version: str = ""
        self._download_url: str = ""
        self._setup_ui()
        self._refresh_current()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Current version
        cur_grp = QGroupBox(_("Current Version"))
        cur_v = QVBoxLayout(cur_grp)
        self._cur_label = QLabel()
        cur_v.addWidget(self._cur_label)
        layout.addWidget(cur_grp)

        # Latest release
        latest_grp = QGroupBox(_("Latest Release"))
        latest_v = QVBoxLayout(latest_grp)
        self._latest_label = QLabel(_("Not checked yet."))
        row = QHBoxLayout()
        row.addWidget(self._latest_label)
        row.addStretch()
        self._check_btn = QPushButton(_("Check Now"))
        self._check_btn.clicked.connect(self._on_check)
        row.addWidget(self._check_btn)
        latest_v.addLayout(row)
        layout.addWidget(latest_grp)

        # Action
        action_grp = QGroupBox(_("Update"))
        action_v = QVBoxLayout(action_grp)
        self._action_label = QLabel()
        self._action_label.setWordWrap(True)
        action_v.addWidget(self._action_label)
        self._install_btn = QPushButton(_("Download & Install"))
        self._install_btn.setVisible(False)
        self._install_btn.clicked.connect(self._on_install)
        action_v.addWidget(self._install_btn)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        action_v.addWidget(self._progress)
        layout.addWidget(action_grp)

        # Log
        log_grp = QGroupBox(_("Log"))
        log_v = QVBoxLayout(log_grp)
        self._log = QTextBrowser()
        self._log.setMaximumHeight(100)
        log_v.addWidget(self._log)
        layout.addWidget(log_grp)

        # Close button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(self.reject)
        btn_row.addWidget(close_box)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_current(self) -> None:
        ver = _current_version()
        self._cur_label.setText(
            _("Version: <b>{ver}</b>  —  Platform: {os}").format(ver=ver, os=platform.system())
        )
        self._action_label.setText(
            _(
                "Click 'Check Now' to look for a newer release on GitHub.\n"
                "The update will be downloaded and installed automatically."
            )
        )

    # ------------------------------------------------------------------
    # Check
    # ------------------------------------------------------------------

    def _on_check(self) -> None:
        self._check_btn.setEnabled(False)
        self._latest_label.setText(_("Checking…"))
        worker = _CheckWorker()
        worker.result.connect(self._on_check_result)
        worker.error.connect(self._on_check_error)
        worker.start()
        self._check_worker = worker

    @Slot(str, str)
    def _on_check_result(self, version: str, url: str) -> None:
        self._check_btn.setEnabled(True)
        self._latest_version = version
        self._download_url = url

        current = _current_version()
        self._latest_label.setText(_("Latest: <b>{ver}</b>").format(ver=version))

        if version == current:
            self._log.append(_("Already up to date ({ver}).").format(ver=current))
            self._install_btn.setVisible(False)
            return

        if not url:
            self._log.append(
                _(
                    "Release asset not found for this platform.\n"
                    "Please download manually from {url}"
                ).format(url=GITHUB_RELEASES)
            )
            self._install_btn.setVisible(False)
            return

        self._install_btn.setText(_("Download & Install v{ver}").format(ver=version))
        self._install_btn.setVisible(True)

    @Slot(str)
    def _on_check_error(self, msg: str) -> None:
        self._check_btn.setEnabled(True)
        self._latest_label.setText(_("Check failed."))
        self._log.append(_("Error: {msg}").format(msg=msg))

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    def _on_install(self) -> None:
        self._install_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._log.clear()

        worker = _DownloadWorker(self._download_url, self._latest_version)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.start()
        self._download_worker = worker

    @Slot(str)
    def _on_progress(self, line: str) -> None:
        self._log.append(line)

    @Slot(bool, str)
    def _on_finished(self, success: bool, msg: str) -> None:
        self._progress.setVisible(False)
        icon = "✅" if success else "❌"
        self._log.append(f"\n{icon}  {msg}")

        if success and platform.system() == "Windows":
            # NSIS installer is running; quit the app so it can overwrite files
            self._log.append(_("Closing application for installer…"))
            self.quit_requested.emit()
