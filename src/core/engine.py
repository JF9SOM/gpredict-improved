"""
衛星追尾コアエンジン

SatelliteEngine  — Skyfieldを使ったリアルタイム仰角・方位角・距離・速度計算
PassPredictor    — 指定期間のAOS/TCA/LOS予測
DopplerCalculator — ドップラー補正（反転トランスポンダ対応）

Qt UIとFastAPI WebSocketの両方から呼ばれるため、スレッドセーフに設計する。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
from skyfield.api import EarthSatellite, Time, load, wgs84

if TYPE_CHECKING:
    from data.tle_manager import TLEManager

# 光速 km/s
_C_KM_S: float = 299_792.458


# ---------------------------------------------------------------------------
# データクラス（計算結果の型）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """ある瞬間の衛星観測値"""

    norad_cat_id: int
    timestamp: datetime  # UTC
    elevation_deg: float  # 仰角 (度)
    azimuth_deg: float  # 方位角 (度、北=0、東=90)
    range_km: float  # 距離 (km)
    range_rate_km_s: float  # 視線方向速度 (km/s、正=離遠、負=接近)
    is_above_horizon: bool  # 地平線より上か


@dataclass(frozen=True)
class PassInfo:
    """1回の衛星パス情報"""

    norad_cat_id: int
    aos: datetime  # Acquisition of Signal (UTC)
    tca: datetime  # Time of Closest Approach (UTC)
    los: datetime  # Loss of Signal (UTC)
    max_elevation_deg: float  # TCA時の最大仰角 (度)
    aos_azimuth_deg: float  # AOS時の方位角
    los_azimuth_deg: float  # LOS時の方位角
    duration_s: float  # パス継続時間 (秒)


@dataclass(frozen=True)
class DopplerCorrection:
    """ドップラー補正結果"""

    downlink_hz: float  # 補正後ダウンリンク周波数 (Hz)
    uplink_hz: float | None  # 補正後アップリンク周波数 (Hz)、受信専用ならNone
    downlink_shift_hz: float  # ドップラーシフト量 (Hz)
    uplink_shift_hz: float | None


# ---------------------------------------------------------------------------
# SatelliteEngine
# ---------------------------------------------------------------------------


class SatelliteEngine:
    """
    Skyfieldラッパー。地上局座標を基準に衛星の観測値をリアルタイム計算する。

    EarthSatelliteオブジェクトはLRUキャッシュで保持し、スレッドセーフに管理する。
    計算メソッドはすべて読み取り専用なのでGILの範囲内で安全に並行実行できるが、
    キャッシュへの書き込みは明示的なロックで保護する。
    """

    def __init__(
        self,
        tle_manager: TLEManager,
        latitude_deg: float,
        longitude_deg: float,
        elevation_m: float = 0.0,
    ) -> None:
        """
        Args:
            tle_manager: TLEデータソース
            latitude_deg: 地上局緯度 (度、北緯正)
            longitude_deg: 地上局経度 (度、東経正)
            elevation_m: 地上局標高 (m)
        """
        self._tle_manager = tle_manager
        self._ts = load.timescale()
        self._ground_station = wgs84.latlon(latitude_deg, longitude_deg, elevation_m)

        # norad_cat_id → EarthSatellite のキャッシュ（ロック保護）
        self._sat_cache: dict[int, EarthSatellite] = {}
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 公開API
    # ------------------------------------------------------------------ #

    def observe(
        self,
        norad_cat_id: int,
        at: datetime | None = None,
    ) -> Observation | None:
        """
        指定衛星の現在（またはat時刻）の観測値を返す。

        Args:
            norad_cat_id: NORAD衛星番号
            at: 計算基準時刻（UTC）。Noneなら現在時刻。

        Returns:
            Observation。TLEが存在しない場合はNone。
        """
        sat = self._get_satellite(norad_cat_id)
        if sat is None:
            return None

        t = self._to_skyfield_time(at)
        topo = (sat - self._ground_station).at(t)
        alt, az, dist = topo.altaz()

        range_rate = self._calc_range_rate(topo)

        return Observation(
            norad_cat_id=norad_cat_id,
            timestamp=t.utc_datetime(),
            elevation_deg=float(alt.degrees),
            azimuth_deg=float(az.degrees),
            range_km=float(dist.km),
            range_rate_km_s=float(range_rate),
            is_above_horizon=float(alt.degrees) > 0.0,
        )

    def observe_multi(
        self,
        norad_cat_ids: list[int],
        at: datetime | None = None,
    ) -> dict[int, Observation]:
        """複数衛星の観測値を一括取得する。存在しないIDはスキップ。"""
        result: dict[int, Observation] = {}
        for norad in norad_cat_ids:
            obs = self.observe(norad, at)
            if obs is not None:
                result[norad] = obs
        return result

    def subpoint(
        self,
        norad_cat_id: int,
        at: datetime | None = None,
    ) -> tuple[float, float] | None:
        """
        衛星の直下点（緯度・経度）を返す。

        Args:
            norad_cat_id: NORAD 衛星番号
            at: 計算基準時刻（UTC）。None なら現在時刻。

        Returns:
            (latitude_deg, longitude_deg)。TLE が存在しない場合は None。
        """
        sat = self._get_satellite(norad_cat_id)
        if sat is None:
            return None
        t = self._to_skyfield_time(at)
        geocentric = sat.at(t)
        sp = wgs84.subpoint_of(geocentric)
        return float(sp.latitude.degrees), float(sp.longitude.degrees)

    def subpoints(
        self,
        norad_cat_ids: list[int],
        at: datetime | None = None,
    ) -> dict[int, tuple[float, float]]:
        """複数衛星の直下点を一括取得する。TLE が存在しない衛星はスキップ。"""
        result: dict[int, tuple[float, float]] = {}
        for norad in norad_cat_ids:
            sp = self.subpoint(norad, at)
            if sp is not None:
                result[norad] = sp
        return result

    def invalidate_cache(self, norad_cat_id: int | None = None) -> None:
        """TLE更新後にキャッシュをクリアする。Noneで全件クリア。"""
        with self._cache_lock:
            if norad_cat_id is None:
                self._sat_cache.clear()
            else:
                self._sat_cache.pop(norad_cat_id, None)

    def update_observer(
        self,
        latitude_deg: float,
        longitude_deg: float,
        elevation_m: float = 0.0,
    ) -> None:
        """観測地点を更新する（QTH変更時に呼ぶ）。"""
        with self._cache_lock:
            self._ground_station = wgs84.latlon(latitude_deg, longitude_deg, elevation_m)

    # ------------------------------------------------------------------ #
    # 内部ユーティリティ
    # ------------------------------------------------------------------ #

    def _get_satellite(self, norad_cat_id: int) -> EarthSatellite | None:
        with self._cache_lock:
            if norad_cat_id in self._sat_cache:
                return self._sat_cache[norad_cat_id]

        sat = self._tle_manager.get_earth_satellite(norad_cat_id)
        if sat is None:
            return None

        with self._cache_lock:
            self._sat_cache[norad_cat_id] = sat
        return sat

    def _to_skyfield_time(self, dt: datetime | None) -> Time:
        if dt is None:
            return self._ts.now()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return self._ts.from_datetime(dt)

    @staticmethod
    def _calc_range_rate(topo: Any) -> float:
        """視線方向速度 (km/s) を計算する。正値=離遠、負値=接近。"""
        pos = topo.position.km
        vel = topo.velocity.km_per_s
        range_km = float(np.linalg.norm(pos))
        if range_km < 1e-9:
            return 0.0
        return float(np.dot(pos, vel) / range_km)


# ---------------------------------------------------------------------------
# PassPredictor
# ---------------------------------------------------------------------------


class PassPredictor:
    """
    指定期間内のAOS/TCA/LOSを予測するクラス。

    Skyfieldの find_events() を使い、イベントをパス単位にグループ化する。
    スレッドセーフ（内部状態を変更するメソッドなし）。
    """

    def __init__(
        self,
        tle_manager: TLEManager,
        latitude_deg: float,
        longitude_deg: float,
        elevation_m: float = 0.0,
    ) -> None:
        self._tle_manager = tle_manager
        self._ts = load.timescale()
        self._ground_station = wgs84.latlon(latitude_deg, longitude_deg, elevation_m)
        self._engine = SatelliteEngine(tle_manager, latitude_deg, longitude_deg, elevation_m)

    def update_observer(
        self,
        latitude_deg: float,
        longitude_deg: float,
        elevation_m: float = 0.0,
    ) -> None:
        """観測地点を更新する（QTH変更時に呼ぶ）。"""
        self._ground_station = wgs84.latlon(latitude_deg, longitude_deg, elevation_m)
        self._engine.update_observer(latitude_deg, longitude_deg, elevation_m)

    def get_passes(
        self,
        norad_cat_id: int,
        start: datetime,
        end: datetime,
        min_elevation_deg: float = 5.0,
    ) -> list[PassInfo]:
        """
        指定期間内のパス一覧を返す。

        Args:
            norad_cat_id: NORAD衛星番号
            start: 検索開始時刻 (UTC)
            end: 検索終了時刻 (UTC)
            min_elevation_deg: この仰角以上のパスのみ返す (デフォルト5度)

        Returns:
            PassInfoのリスト（AOS昇順）。TLEが存在しない場合は空リスト。
        """
        sat = self._engine._get_satellite(norad_cat_id)
        if sat is None:
            return []

        t0 = self._to_skyfield_time(start)
        t1 = self._to_skyfield_time(end)

        try:
            times, events = sat.find_events(
                self._ground_station,
                t0,
                t1,
                altitude_degrees=min_elevation_deg,
            )
        except Exception:
            return []

        return self._group_events(norad_cat_id, sat, times, events)

    # ------------------------------------------------------------------ #
    # 内部処理
    # ------------------------------------------------------------------ #

    def _group_events(
        self,
        norad_cat_id: int,
        sat: EarthSatellite,
        times: object,
        events: object,
    ) -> list[PassInfo]:
        """AOS(0)/TCA(1)/LOS(2) のイベント列をパス単位に纏める。"""
        passes: list[PassInfo] = []
        # Skyfield は AOS→TCA→LOS の順に並んでいることが保証されている
        # ただし先頭がTCA/LOSになる場合（検索開始時点で既に可視）もある
        pending: dict[str, object] = {}

        times_list = list(times)  # type: ignore[call-overload]
        events_list = list(events)  # type: ignore[call-overload]

        for t, ev in zip(times_list, events_list, strict=False):
            if ev == 0:  # AOS
                pending = {"aos": t}
            elif ev == 1 and "aos" in pending:  # TCA
                pending["tca"] = t
            elif ev == 2 and "tca" in pending:  # LOS — 1パス完成
                pending["los"] = t
                info = self._build_pass_info(norad_cat_id, sat, pending)
                if info is not None:
                    passes.append(info)
                pending = {}

        return passes

    def _build_pass_info(
        self,
        norad_cat_id: int,
        sat: EarthSatellite,
        ev: dict[str, Any],
    ) -> PassInfo | None:
        try:
            aos_t = ev["aos"]
            tca_t = ev["tca"]
            los_t = ev["los"]

            topo_aos = (sat - self._ground_station).at(aos_t)
            topo_tca = (sat - self._ground_station).at(tca_t)
            topo_los = (sat - self._ground_station).at(los_t)

            alt_tca, _, _ = topo_tca.altaz()
            _, az_aos, _ = topo_aos.altaz()
            _, az_los, _ = topo_los.altaz()

            aos_dt: datetime = aos_t.utc_datetime()
            tca_dt: datetime = tca_t.utc_datetime()
            los_dt: datetime = los_t.utc_datetime()

            return PassInfo(
                norad_cat_id=norad_cat_id,
                aos=aos_dt,
                tca=tca_dt,
                los=los_dt,
                max_elevation_deg=float(alt_tca.degrees),
                aos_azimuth_deg=float(az_aos.degrees),
                los_azimuth_deg=float(az_los.degrees),
                duration_s=(los_dt - aos_dt).total_seconds(),
            )
        except Exception:
            return None

    def _to_skyfield_time(self, dt: datetime) -> Time:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return self._ts.from_datetime(dt)


# ---------------------------------------------------------------------------
# DopplerCalculator
# ---------------------------------------------------------------------------


class DopplerCalculator:
    """
    ドップラーシフト計算クラス。

    衛星の視線方向速度 (range_rate_km_s) から周波数補正量を算出する。
    反転トランスポンダ（invert=True）ではアップリンクの補正方向が逆になる。

    物理モデル:
        f_received = f_nominal * (1 - range_rate / c)
        shift_hz   = -f_nominal * range_rate / c
        (range_rate > 0 = 離遠 → 受信周波数は名目より低い = shift < 0)
    """

    @staticmethod
    def shift_hz(nominal_hz: float, range_rate_km_s: float) -> float:
        """
        ドップラーシフト量を返す (Hz)。

        正値 = 受信周波数が名目より高い（接近時）
        負値 = 受信周波数が名目より低い（離遠時）
        """
        return -nominal_hz * range_rate_km_s / _C_KM_S

    @staticmethod
    def correct_downlink(
        downlink_hz: float,
        range_rate_km_s: float,
    ) -> tuple[float, float]:
        """
        ダウンリンク周波数を補正する。

        Returns:
            (補正後周波数 Hz, シフト量 Hz)
        """
        shift = DopplerCalculator.shift_hz(downlink_hz, range_rate_km_s)
        return downlink_hz + shift, shift

    @staticmethod
    def correct_uplink(
        uplink_hz: float,
        range_rate_km_s: float,
        *,
        invert: bool = False,
    ) -> tuple[float, float]:
        """
        アップリンク周波数を補正する。

        反転トランスポンダ (invert=True) では、トランスポンダがパスバンドを
        反転するため、アップリンクのドップラー補正方向がダウンリンクと逆になる。

        Args:
            uplink_hz: 公称アップリンク周波数 (Hz)
            range_rate_km_s: 視線速度 (km/s)
            invert: 反転トランスポンダか否か

        Returns:
            (補正後周波数 Hz, シフト量 Hz)
        """
        shift = DopplerCalculator.shift_hz(uplink_hz, range_rate_km_s)
        if invert:
            # 反転トランスポンダ: アップリンクはダウンリンクと逆方向に補正
            shift = -shift
        return uplink_hz + shift, shift

    @classmethod
    def correct_transponder(
        cls,
        downlink_hz: float,
        uplink_hz: float | None,
        range_rate_km_s: float,
        *,
        invert: bool = False,
    ) -> DopplerCorrection:
        """
        トランスポンダのダウンリンク・アップリンク両周波数を同時に補正する。

        Args:
            downlink_hz: 公称ダウンリンク周波数 (Hz)
            uplink_hz: 公称アップリンク周波数 (Hz)。受信専用ならNone。
            range_rate_km_s: 視線速度 (km/s)
            invert: 反転トランスポンダか否か

        Returns:
            DopplerCorrection
        """
        dl_corrected, dl_shift = cls.correct_downlink(downlink_hz, range_rate_km_s)

        ul_corrected: float | None = None
        ul_shift: float | None = None
        if uplink_hz is not None:
            ul_corrected, ul_shift = cls.correct_uplink(uplink_hz, range_rate_km_s, invert=invert)

        return DopplerCorrection(
            downlink_hz=dl_corrected,
            uplink_hz=ul_corrected,
            downlink_shift_hz=dl_shift,
            uplink_shift_hz=ul_shift,
        )
