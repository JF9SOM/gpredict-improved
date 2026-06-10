"""
GPredict-Improved application entry point.

Startup sequence:
    1. Create QApplication
    2. Initialize SQLite DB
    3. Create TLEManager, LocationManager, SatelliteEngine, PassPredictor
    4. Create FastAPI app
    5. Show MainWindow (web server and scheduler start internally)
    6. Run Qt event loop
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# In a PyInstaller bundle the Python SSL CA bundle is not present on the system
# path.  Point httpx (and the stdlib ssl module) at the certifi bundle that was
# collected into the frozen archive so that HTTPS requests succeed on all
# platforms (critical for TLE/SATNOGS downloads on macOS and Windows).
if getattr(sys, "frozen", False):
    try:
        import certifi

        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except Exception:
        pass

# On the developer's Linux machine, ensure only Hamlib 4.7.1 is loaded and not
# the older system package (4.5.5).  Loading both causes a "Hash collision"
# fatal error in Hamlib's rig registry.  This block is a no-op when running
# from a PyInstaller bundle or on Windows/macOS where /opt/hamlib does not exist.
# If the user installed a newer Hamlib via the in-app updater, load it first
# so it takes priority over the bundled version on all platforms.
try:
    from platformdirs import user_data_dir as _udd

    _hamlib_user_dir = Path(_udd("gpredict-improved")) / "hamlib"
except Exception:
    _hamlib_user_dir = Path.home() / ".local" / "share" / "gpredict-improved" / "hamlib"
if _hamlib_user_dir.exists():
    _hamlib_user_str = str(_hamlib_user_dir)
    if _hamlib_user_str not in sys.path:
        sys.path.insert(0, _hamlib_user_str)
    # Windows: register the directory for DLL loading so _Hamlib.pyd finds
    # hamlib.dll and its dependencies placed in the same flat directory.
    if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(_hamlib_user_str)

if sys.platform == "linux":
    _pyver = f"{sys.version_info.major}.{sys.version_info.minor}"
    _HAMLIB_SITE = f"/opt/hamlib/4.7/lib/python{_pyver}/site-packages"
    _HAMLIB_SYS = "/usr/lib/python3/dist-packages"
    # Only apply the sys.path surgery when the custom Hamlib build is present.
    # On standard installations /usr/lib/python3/dist-packages is the only
    # Hamlib source, so removing it would break both Hamlib and SoapySDR.
    if os.path.exists(_HAMLIB_SITE):
        # Pre-import SoapySDR before stripping _HAMLIB_SYS so it stays in
        # sys.modules (SoapySDR lives in the same dist-packages directory).
        import contextlib

        with contextlib.suppress(Exception):
            import SoapySDR as _soapy_preload  # noqa: F401
        if _HAMLIB_SYS in sys.path:
            sys.path.remove(_HAMLIB_SYS)
        if _HAMLIB_SITE not in sys.path:
            sys.path.insert(0, _HAMLIB_SITE)

from PySide6.QtWidgets import QApplication

from core.engine import PassPredictor, SatelliteEngine
from core.location import LocationManager, LocationSource
from data.database import init_database
from data.tle_manager import TLEManager
from ui.main_window import MainWindow
from ui.world_map import prefetch_land_data
from web.app import create_app
from web.rig_state import RigWebState


def _setup_logging() -> None:
    """Configure logging: always write to stderr; in frozen bundles also write to a log file."""
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if getattr(sys, "frozen", False):
        # In a PyInstaller bundle stderr is discarded; write to a log file instead
        # so the user can inspect it from the macOS/Windows Console or a text editor.
        from platformdirs import user_log_dir

        log_dir = user_log_dir("GPredict-Improved", "GPredict-Improved")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "gpredict-improved.log")
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(fmt))
        handlers.append(file_handler)
        # Print log location to stderr (visible when launched from Terminal)
        print(f"[GPredict-Improved] Log file: {log_path}", file=sys.stderr)

    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


_setup_logging()
logger = logging.getLogger(__name__)


def _get_version() -> str:
    """Return the application version string.

    Tagged release  (v0.1.0)      → "0.1.0"
    Dev build after tag           → "0.1.0.dev3"  (last tag + commit count)
    Fallback (no metadata/git)    → "0.1.0"

    setuptools-scm default scheme produces ``X.(Y+1).0.devN+gHASH`` for commits
    after a tag, which is confusing because it implies the *next* version.
    Instead we reformat it as ``<last-tag>.devN`` using ``git describe``.
    """
    try:
        from importlib.metadata import version as _meta_version

        ver = _meta_version("gpredict-improved")
    except Exception:
        return "0.1.0"

    # Clean release tag — no rewriting needed
    if ".dev" not in ver and "+" not in ver:
        return ver

    # Dev build: reformat "X.Y+1.0.devN+gHASH" → "<last-tag>.devN"
    # Extract the dev count from the raw string first
    dev_count = ""
    if ".dev" in ver:
        dev_count = ver.split(".dev")[1].split("+")[0]

    try:
        import subprocess

        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            base_tag = result.stdout.strip().lstrip("v")
            return f"{base_tag}.dev{dev_count}" if dev_count else base_tag
    except Exception:
        pass

    # Fallback: strip only the local hash suffix
    return ver.split("+")[0]


APP_VERSION = _get_version()


def main() -> int:
    """Application main entry point."""
    app = QApplication(sys.argv)
    app.setApplicationName("GPredict-Improved")
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("GPredict-Improved")

    # Prefetch Natural Earth map data (downloads on first run, uses cache thereafter)
    prefetch_land_data()

    # Initialize SQLite DB
    conn = init_database()

    # Create core components
    tle_manager = TLEManager(conn)
    location_manager = LocationManager(conn)
    location = location_manager.load_saved()

    # Do not overwrite manually set or GPS location. Run IP geolocation only when unset or IP-based.
    _skip_ip = location is not None and location.source in (
        LocationSource.MANUAL,
        LocationSource.GPS,
    )
    if not _skip_ip:
        logger.info("No saved QTH (or IP-based) — trying IP geolocation...")
        try:
            ip_loc = asyncio.run(location_manager.from_ip())
            if ip_loc:
                location = ip_loc
                logger.info(
                    "IP geolocation: %.4f°N %.4f°E (%s, %s)",
                    ip_loc.latitude_deg,
                    ip_loc.longitude_deg,
                    ip_loc.city,
                    ip_loc.country,
                )
        except Exception as exc:
            logger.warning("IP geolocation failed at startup: %s", exc)

    engine: SatelliteEngine | None = None
    pass_predictor: PassPredictor | None = None

    if location is not None:
        engine = SatelliteEngine(
            tle_manager,
            location.latitude_deg,
            location.longitude_deg,
            location.elevation_m,
        )
        pass_predictor = PassPredictor(
            tle_manager,
            location.latitude_deg,
            location.longitude_deg,
            location.elevation_m,
        )
        logger.info(
            "Engine initialized at %.4f°N %.4f°E",
            location.latitude_deg,
            location.longitude_deg,
        )
    else:
        logger.info("No saved location — engine not initialized. Set QTH from menu.")

    # Shared rig/rotator state (written by Qt UI, read by FastAPI)
    rig_state = RigWebState()

    # Create FastAPI app
    fastapi_app = create_app(
        conn=conn,
        tle_manager=tle_manager,
        pass_predictor=pass_predictor,
        engine=engine,
        start_time=datetime.now(UTC),
        location_manager=location_manager,
        rig_state=rig_state,
    )

    # Show main window (web server and scheduler also start internally)
    window = MainWindow(
        conn=conn,
        tle_manager=tle_manager,
        engine=engine,
        pass_predictor=pass_predictor,
        location_manager=location_manager,
        fastapi_app=fastapi_app,
        rig_state=rig_state,
    )
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
