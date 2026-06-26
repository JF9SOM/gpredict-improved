"""
Hamlib version detection and user-installation path helpers.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path


def get_hamlib_version() -> str:
    """Return the version string of the currently loaded Hamlib."""
    try:
        import Hamlib

        return str(Hamlib.hamlib_version)
    except Exception:
        return "unknown"


def get_user_hamlib_dir() -> Path:
    """Return the per-user Hamlib installation directory (flat layout)."""
    try:
        from platformdirs import user_data_dir

        return Path(user_data_dir("fbsat59")) / "hamlib"
    except Exception:
        if sys.platform == "win32":
            appdata = Path.home() / "AppData" / "Roaming"
            return appdata / "fbsat59" / "hamlib"
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / "fbsat59" / "hamlib"
        return Path.home() / ".local" / "share" / "fbsat59" / "hamlib"


def get_user_hamlib_version() -> str | None:
    """Return the version stored in the user Hamlib dir, or None if not installed."""
    ver_file = get_user_hamlib_dir() / "version.txt"
    if ver_file.exists():
        try:
            return ver_file.read_text().strip()
        except Exception:
            pass
    return None


def is_user_hamlib_installed() -> bool:
    """Return True if a user-local Hamlib installation is present."""
    d = get_user_hamlib_dir()
    if not d.exists():
        return False
    # Look for Hamlib.py (present on all platforms in the flat layout)
    return (d / "Hamlib.py").exists()


# ---------------------------------------------------------------------------
# Asset naming — must match what the CI uploads to GitHub Releases
# ---------------------------------------------------------------------------
HAMLIB_GITHUB_API = "https://api.github.com/repos/Hamlib/Hamlib/releases/latest"
HAMLIB_GITHUB_RELEASES = "https://github.com/Hamlib/Hamlib/releases"

_PYVER_TAG = f"py{sys.version_info.major}{sys.version_info.minor}"


def linux_asset_name(version: str) -> str:
    """e.g. 'hamlib-linux-x86_64-py311-4.7.1.tar.gz'"""
    return f"hamlib-linux-x86_64-{_PYVER_TAG}-{version}.tar.gz"


def windows_asset_name(version: str) -> str:
    """e.g. 'hamlib-windows-x86_64-py311-4.7.1.zip' (custom CI asset, flat layout)"""
    return f"hamlib-windows-x86_64-{_PYVER_TAG}-{version}.zip"


def macos_asset_name(version: str) -> str:
    """e.g. 'hamlib-macos-arm64-py311-4.7.1.tar.gz'"""
    arch = platform.machine()  # 'arm64' on Apple Silicon, 'x86_64' on Intel
    return f"hamlib-macos-{arch}-{_PYVER_TAG}-{version}.tar.gz"
