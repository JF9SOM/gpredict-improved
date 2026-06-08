"""
SDR Device Installation dialog.

Opened from Help > SDR Device Installation.

Detects connected SDR USB devices and installed SoapySDR drivers.
Provides OS-specific installation guidance and, where possible, launches
automatic installation with a single button press.

OS support:
  Linux  — pkexec apt-get install (fully automatic)
  Windows — downloads PothosSDR installer + Zadig (semi-automatic)
  macOS  — brew install (automatic if Homebrew present)
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
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
from sdr import SOAPY_AVAILABLE
from sdr.device import SdrDevice, SdrDeviceInfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known SoapySDR apt/brew/pip package names per driver
# ---------------------------------------------------------------------------
_APT_PACKAGES: dict[str, list[str]] = {
    "SoapyRTLSDR": ["python3-soapysdr", "soapysdr-module-rtlsdr", "rtl-sdr"],
    "SoapyHackRF": ["python3-soapysdr", "soapysdr-module-hackrf", "hackrf"],
    "SoapyAirspy": ["python3-soapysdr", "soapysdr-module-airspy"],
    "SoapySDRPlay": ["python3-soapysdr", "soapysdr-module-sdrplay3"],
}
_BREW_PACKAGES: dict[str, list[str]] = {
    "SoapyRTLSDR": ["soapysdr", "soapyrtlsdr"],
    "SoapyHackRF": ["soapysdr", "soapyhackrf"],
    "SoapyAirspy": ["soapysdr", "soapyairspy"],
    "SoapySDRPlay": ["soapysdr", "soapysdrplay"],
}

# Windows download URLs (GitHub releases)
_POTHOS_URL = "https://github.com/pothosware/PothosCore/releases/latest"
_ZADIG_URL = "https://zadig.akeo.ie/downloads/zadig_2.9.exe"


class _InstallWorker(QThread):
    """Background thread that runs an installation command."""

    progress = Signal(str)
    finished = Signal(bool, str)  # success, message

    def __init__(self, cmd: list[str]) -> None:
        super().__init__()
        self._cmd = cmd

    def run(self) -> None:
        try:
            proc = subprocess.Popen(
                self._cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                self.progress.emit(line.rstrip())
            proc.wait()
            success = proc.returncode == 0
            msg = _("Installation complete.") if success else _("Installation failed (see log).")
            self.finished.emit(success, msg)
        except Exception as exc:
            self.finished.emit(False, str(exc))


class SdrInstallDialog(QDialog):
    """
    SDR Device Installation dialog.

    Shows:
      1. Connected USB devices (with driver status)
      2. Installed SoapySDR module status
      3. OS-appropriate install button / guidance
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("SDR Device Installation"))
        self.resize(620, 560)
        self._worker: _InstallWorker | None = None
        self._setup_ui()
        self._refresh()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # -- Connected devices --
        dev_grp = QGroupBox(_("Connected USB Devices"))
        self._dev_layout = QVBoxLayout(dev_grp)
        self._dev_placeholder = QLabel(_("Scanning…"))
        self._dev_layout.addWidget(self._dev_placeholder)
        layout.addWidget(dev_grp)

        # -- Installation status --
        status_grp = QGroupBox(_("Driver Status"))
        self._status_layout = QVBoxLayout(status_grp)
        self._soapy_status = QLabel()
        self._status_layout.addWidget(self._soapy_status)
        layout.addWidget(status_grp)

        # -- Action area --
        action_grp = QGroupBox(_("Installation"))
        action_v = QVBoxLayout(action_grp)

        self._action_label = QLabel()
        self._action_label.setWordWrap(True)
        action_v.addWidget(self._action_label)

        self._install_btn = QPushButton(_("Install Selected Packages"))
        self._install_btn.clicked.connect(self._on_install)
        action_v.addWidget(self._install_btn)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # indeterminate
        self._progress_bar.setVisible(False)
        action_v.addWidget(self._progress_bar)

        layout.addWidget(action_grp)

        # -- Log output --
        log_grp = QGroupBox(_("Log"))
        log_v = QVBoxLayout(log_grp)
        self._log = QTextBrowser()
        self._log.setMaximumHeight(120)
        log_v.addWidget(self._log)
        layout.addWidget(log_grp)

        # -- Rescan + close --
        btn_row = QHBoxLayout()
        self._rescan_btn = QPushButton(_("🔍 Rescan Devices"))
        self._rescan_btn.clicked.connect(self._refresh)
        btn_row.addWidget(self._rescan_btn)
        btn_row.addStretch()
        close_btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn.rejected.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Scan and populate
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """Scan devices and update the UI."""
        # Clear device list
        while self._dev_layout.count():
            item = self._dev_layout.takeAt(0)
            if item:
                w = item.widget()
                if w:
                    w.deleteLater()

        # 1. Try SoapySDR enumerate first (preferred — gives full info)
        soapy_devices: list[SdrDeviceInfo] = []
        if SOAPY_AVAILABLE:
            soapy_devices = SdrDevice.enumerate()

        # 2. Fallback to USB scan when SoapySDR absent or no devices found
        # enumerate_usb() tries pyusb first, then Linux sysfs — no guard needed
        usb_devices: list[SdrDeviceInfo] = SdrDevice.enumerate_usb()

        all_devices = soapy_devices or usb_devices

        if not all_devices:
            self._dev_layout.addWidget(QLabel(_("No SDR devices detected.")))
        else:
            for dev in all_devices:
                row = QHBoxLayout()
                if dev.driver is not None:
                    icon = "✅"
                    status = _("Ready")
                    color = "#2ecc71"
                else:
                    icon = "❓"
                    status = _("Driver not installed")
                    color = "#e67e22"
                name_lbl = QLabel(f"{icon}  {dev.display_name}")
                status_lbl = QLabel(status)
                status_lbl.setStyleSheet(f"color: {color}; font-weight: bold;")
                row.addWidget(name_lbl)
                row.addStretch()
                row.addWidget(status_lbl)
                w = QWidget()
                w.setLayout(row)
                self._dev_layout.addWidget(w)

        # SoapySDR overall status
        if SOAPY_AVAILABLE:
            self._soapy_status.setText("✅  " + _("SoapySDR is installed and ready."))
            self._soapy_status.setStyleSheet("color: #2ecc71;")
        else:
            self._soapy_status.setText("❌  " + _("SoapySDR is NOT installed."))
            self._soapy_status.setStyleSheet("color: #e74c3c;")

        # Build install instructions and button visibility
        self._build_install_section(all_devices)

    def _build_install_section(self, devices: list[SdrDeviceInfo]) -> None:
        """Populate the install action area based on OS and detected devices."""
        os_name = platform.system()

        # Collect needed packages
        needed_modules = {d.soapy_module for d in devices if d.driver is None and d.soapy_module}
        if SOAPY_AVAILABLE and not needed_modules:
            self._action_label.setText(
                "✅  " + _("All detected devices have drivers installed. No action needed.")
            )
            self._install_btn.setVisible(False)
            return

        self._install_btn.setVisible(True)

        if os_name == "Linux":
            pkgs: list[str] = []
            if not SOAPY_AVAILABLE:
                pkgs += ["python3-soapysdr"]
            for mod in needed_modules:
                pkgs += _APT_PACKAGES.get(mod, [])
            unique_pkgs = list(dict.fromkeys(pkgs))  # deduplicate preserving order
            self._pending_cmd = ["pkexec", "apt-get", "install", "-y"] + unique_pkgs
            self._action_label.setText(
                _("The following packages will be installed:\n") + "  " + "  ".join(unique_pkgs)
            )
            self._install_btn.setText(_("Install via apt-get (requires password)"))

        elif os_name == "Darwin":
            pkgs = []
            if not SOAPY_AVAILABLE:
                pkgs += ["soapysdr"]
            for mod in needed_modules:
                pkgs += _BREW_PACKAGES.get(mod, [])
            unique_pkgs = list(dict.fromkeys(pkgs))
            if shutil.which("brew"):
                self._pending_cmd = ["brew", "install"] + unique_pkgs
                self._action_label.setText(
                    _("The following Homebrew packages will be installed:\n")
                    + "  "
                    + "  ".join(unique_pkgs)
                )
                self._install_btn.setText(_("Install via Homebrew"))
            else:
                self._pending_cmd = []
                self._action_label.setText(
                    _(
                        "Homebrew is not installed.\n"
                        "Install Homebrew first by running the following in Terminal:\n\n"
                        '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
                    )
                )
                self._install_btn.setVisible(False)

        elif os_name == "Windows":
            self._pending_cmd = []
            needs_rtl = any("RTLSDR" in m for m in needed_modules)
            self._action_label.setText(
                _(
                    "Windows installation steps:\n"
                    "1. Download and run the PothosSDR installer"
                    " (installs SoapySDR + all drivers).\n"
                    "2. For RTL-SDR only: run Zadig and apply the WinUSB driver.\n\n"
                    "Click the buttons below to download each tool."
                )
            )
            self._install_btn.setVisible(False)
            # Add download buttons dynamically
            self._add_windows_buttons(needs_rtl)
        else:
            self._pending_cmd = []
            self._action_label.setText(
                _(
                    "Automatic installation is not supported on this OS.\n"
                    "Please install SoapySDR and the appropriate driver module manually."
                )
            )
            self._install_btn.setVisible(False)

    def _add_windows_buttons(self, needs_zadig: bool) -> None:
        """Add PothosSDR and (optionally) Zadig download buttons."""
        pothos_btn = QPushButton(_("Download PothosSDR Installer"))
        pothos_btn.clicked.connect(lambda: self._download_and_run(_POTHOS_URL))
        self._status_layout.addWidget(pothos_btn)
        if needs_zadig:
            zadig_btn = QPushButton(_("Download Zadig (RTL-SDR driver)"))
            zadig_btn.clicked.connect(lambda: self._download_and_run(_ZADIG_URL))
            self._status_layout.addWidget(zadig_btn)

    # ------------------------------------------------------------------
    # Installation
    # ------------------------------------------------------------------

    def _on_install(self) -> None:
        cmd = getattr(self, "_pending_cmd", [])
        if not cmd:
            return
        self._install_btn.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._log.clear()
        self._worker = _InstallWorker(cmd)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    @Slot(str)
    def _on_progress(self, line: str) -> None:
        self._log.append(line)

    @Slot(bool, str)
    def _on_finished(self, success: bool, msg: str) -> None:
        self._progress_bar.setVisible(False)
        self._install_btn.setEnabled(True)
        self._log.append(f"\n{'✅' if success else '❌'}  {msg}")
        if success:
            self._refresh()
            self._log.append(
                _("\n⚠️  Restart GPredict-Improved to activate the installed drivers.")
            )

    def _download_and_run(self, url: str) -> None:
        """Download a file and run it (Windows helper)."""
        import tempfile
        import urllib.request

        try:
            fname = url.split("/")[-1]
            dest = __import__("pathlib").Path(tempfile.gettempdir()) / fname
            self._log.append(f"Downloading {url}…")
            urllib.request.urlretrieve(url, dest)
            self._log.append(f"Launching {dest}…")
            subprocess.Popen([str(dest)], shell=True)
        except Exception as exc:
            self._log.append(f"Error: {exc}")
