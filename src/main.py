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

# Prepend custom Hamlib build path so python-hamlib is found when not system-installed.
# Has no effect if Hamlib is already on sys.path or LD_LIBRARY_PATH is set externally.
_HAMLIB_SITE = "/opt/hamlib/4.7/lib/python3.12/site-packages"
if _HAMLIB_SITE not in sys.path:
    sys.path.insert(0, _HAMLIB_SITE)
os.environ.setdefault("LD_LIBRARY_PATH", "/opt/hamlib/4.7/lib")

from PySide6.QtWidgets import QApplication

from core.engine import PassPredictor, SatelliteEngine
from core.location import LocationManager, LocationSource
from data.database import init_database
from data.tle_manager import TLEManager
from ui.main_window import MainWindow
from ui.world_map import prefetch_land_data
from web.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> int:
    """Application main entry point."""
    app = QApplication(sys.argv)
    app.setApplicationName("GPredict-Improved")
    app.setApplicationVersion("0.1.0")
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

    # Create FastAPI app
    fastapi_app = create_app(
        conn=conn,
        tle_manager=tle_manager,
        pass_predictor=pass_predictor,
        engine=engine,
        start_time=datetime.now(UTC),
        location_manager=location_manager,
    )

    # Show main window (web server and scheduler also start internally)
    window = MainWindow(
        conn=conn,
        tle_manager=tle_manager,
        engine=engine,
        pass_predictor=pass_predictor,
        location_manager=location_manager,
        fastapi_app=fastapi_app,
    )
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
