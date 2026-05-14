"""
GPredict-Improved エントリーポイント

起動順序:
    1. QApplication 生成
    2. SQLite DB 初期化
    3. TLEManager・LocationManager・SatelliteEngine・PassPredictor 生成
    4. FastAPI アプリ生成
    5. MainWindow 表示（内部で Web サーバー・スケジューラを起動）
    6. Qt イベントループ実行
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime

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
    """アプリケーションのメインエントリーポイント。"""
    app = QApplication(sys.argv)
    app.setApplicationName("GPredict-Improved")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("GPredict-Improved")

    # Natural Earth 地図データのプリフェッチ（初回のみネットワーク取得、以降はキャッシュ）
    prefetch_land_data()

    # SQLite DB 初期化
    conn = init_database()

    # コアコンポーネント生成
    tle_manager = TLEManager(conn)
    location_manager = LocationManager(conn)
    location = location_manager.load_saved()

    # 手動設定・GPSは上書きしない。未設定またはIP由来のみIPジオロケーションを実行する。
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

    # FastAPI アプリ生成
    fastapi_app = create_app(
        conn=conn,
        tle_manager=tle_manager,
        pass_predictor=pass_predictor,
        engine=engine,
        start_time=datetime.now(UTC),
        location_manager=location_manager,
    )

    # メインウィンドウ（Web サーバー・スケジューラも内部で起動）
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
