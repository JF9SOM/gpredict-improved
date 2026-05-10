"""
レーダービューモジュールのテスト

- az_el_to_xy()  — 座標変換ユーティリティ
- SatTrackData   — データクラス
- RadarView      — Qt ウィジェット（qtbot 使用）
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ui.radar_view import SAT_COLORS, SatTrackData, az_el_to_xy

# ---------------------------------------------------------------------------
# az_el_to_xy() のテスト
# ---------------------------------------------------------------------------


class TestAzElToXY:
    """az_el_to_xy() の単体テスト。cx=200, cy=200, r=100 を基準に検証する。"""

    CX, CY, R = 200.0, 200.0, 100.0

    def test_zenith_is_at_center(self) -> None:
        """仰角 90° は中心（天頂）を返す。"""
        x, y = az_el_to_xy(0.0, 90.0, self.CX, self.CY, self.R)
        assert abs(x - self.CX) < 1e-9
        assert abs(y - self.CY) < 1e-9

    def test_north_horizon_is_above_center(self) -> None:
        """方位角 0°（北）仰角 0° は中心の真上。"""
        x, y = az_el_to_xy(0.0, 0.0, self.CX, self.CY, self.R)
        assert abs(x - self.CX) < 1e-9        # X は中心と同じ
        assert abs(y - (self.CY - self.R)) < 1e-9  # Y は中心より上（R 分）

    def test_east_horizon_is_right_of_center(self) -> None:
        """方位角 90°（東）仰角 0° は中心の真右。"""
        x, y = az_el_to_xy(90.0, 0.0, self.CX, self.CY, self.R)
        assert abs(x - (self.CX + self.R)) < 1e-9
        assert abs(y - self.CY) < 1e-9

    def test_south_horizon_is_below_center(self) -> None:
        """方位角 180°（南）仰角 0° は中心の真下。"""
        x, y = az_el_to_xy(180.0, 0.0, self.CX, self.CY, self.R)
        assert abs(x - self.CX) < 1e-9
        assert abs(y - (self.CY + self.R)) < 1e-9

    def test_west_horizon_is_left_of_center(self) -> None:
        """方位角 270°（西）仰角 0° は中心の真左。"""
        x, y = az_el_to_xy(270.0, 0.0, self.CX, self.CY, self.R)
        assert abs(x - (self.CX - self.R)) < 1e-9
        assert abs(y - self.CY) < 1e-9

    def test_30_degree_elevation_ring_radius(self) -> None:
        """仰角 30° のドットは半径の (90-30)/90 = 2/3 ≈ 66.67 に位置する。"""
        x, y = az_el_to_xy(0.0, 30.0, self.CX, self.CY, self.R)
        expected_r = (90.0 - 30.0) / 90.0 * self.R  # 66.6̄
        dist = math.hypot(x - self.CX, y - self.CY)
        assert abs(dist - expected_r) < 1e-9

    def test_60_degree_elevation_ring_radius(self) -> None:
        """仰角 60° のドットは半径の (90-60)/90 = 1/3 に位置する。"""
        x, y = az_el_to_xy(0.0, 60.0, self.CX, self.CY, self.R)
        expected_r = 100.0 / 3.0
        dist = math.hypot(x - self.CX, y - self.CY)
        assert abs(dist - expected_r) < 1e-9

    def test_negative_elevation_clamped_to_zero(self) -> None:
        """仰角が負の場合は 0° として扱う（地平線上）。"""
        x0, y0 = az_el_to_xy(0.0, 0.0, self.CX, self.CY, self.R)
        xn, yn = az_el_to_xy(0.0, -10.0, self.CX, self.CY, self.R)
        assert abs(x0 - xn) < 1e-9
        assert abs(y0 - yn) < 1e-9

    def test_elevation_over_90_clamped(self) -> None:
        """仰角が 90° を超える場合は 90° として扱う（天頂）。"""
        x90, y90 = az_el_to_xy(0.0, 90.0, self.CX, self.CY, self.R)
        x100, y100 = az_el_to_xy(0.0, 100.0, self.CX, self.CY, self.R)
        assert abs(x90 - x100) < 1e-9
        assert abs(y90 - y100) < 1e-9

    def test_returns_tuple_of_two_floats(self) -> None:
        result = az_el_to_xy(45.0, 45.0, self.CX, self.CY, self.R)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(v, float) for v in result)

    def test_45_degree_azimuth(self) -> None:
        """方位角 45° は x と y の変化量が等しい（sin45=cos45）。"""
        x, y = az_el_to_xy(45.0, 0.0, self.CX, self.CY, self.R)
        dx = x - self.CX
        dy = self.CY - y  # y 軸反転
        assert abs(abs(dx) - abs(dy)) < 1e-9

    def test_360_same_as_0(self) -> None:
        """方位角 360° は 0°（北）と等価。"""
        x0, y0 = az_el_to_xy(0.0, 30.0, self.CX, self.CY, self.R)
        x360, y360 = az_el_to_xy(360.0, 30.0, self.CX, self.CY, self.R)
        assert abs(x0 - x360) < 1e-9
        assert abs(y0 - y360) < 1e-9

    def test_symmetry_east_west(self) -> None:
        """東（90°）と西（270°）は x 座標が中心から等距離・反対側。"""
        xe, _ = az_el_to_xy(90.0, 30.0, self.CX, self.CY, self.R)
        xw, _ = az_el_to_xy(270.0, 30.0, self.CX, self.CY, self.R)
        assert abs((xe - self.CX) + (xw - self.CX)) < 1e-9


# ---------------------------------------------------------------------------
# SatTrackData のテスト
# ---------------------------------------------------------------------------


class TestSatTrackData:
    def test_default_values(self) -> None:
        track = SatTrackData(name="ISS", norad_cat_id=25544)
        assert track.azimuth_deg == 0.0
        assert track.elevation_deg == 0.0
        assert track.is_visible is False
        assert track.track == []
        assert track.aos_time is None
        assert track.los_time is None

    def test_custom_values(self) -> None:
        aos = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
        los = aos + timedelta(minutes=10)
        track = SatTrackData(
            name="AO-91",
            norad_cat_id=43017,
            azimuth_deg=45.0,
            elevation_deg=34.2,
            is_visible=True,
            track=[(0.0, 0.0), (45.0, 34.2), (90.0, 0.0)],
            aos_time=aos,
            los_time=los,
        )
        assert track.name == "AO-91"
        assert track.elevation_deg == pytest.approx(34.2)
        assert len(track.track) == 3

    def test_track_is_independent_per_instance(self) -> None:
        """各インスタンスのトラックリストは独立していること。"""
        t1 = SatTrackData(name="A", norad_cat_id=1)
        t2 = SatTrackData(name="B", norad_cat_id=2)
        t1.track.append((1.0, 2.0))
        assert t2.track == []


# ---------------------------------------------------------------------------
# SAT_COLORS 定数のテスト
# ---------------------------------------------------------------------------


class TestSatColors:
    def test_at_least_4_colors(self) -> None:
        assert len(SAT_COLORS) >= 4

    def test_all_are_qcolor(self) -> None:
        from PySide6.QtGui import QColor

        for c in SAT_COLORS:
            assert isinstance(c, QColor)

    def test_colors_are_valid(self) -> None:
        for c in SAT_COLORS:
            assert c.isValid()


# ---------------------------------------------------------------------------
# RadarView ウィジェットのテスト（Qt 必要）
# ---------------------------------------------------------------------------


def _make_track(
    name: str = "ISS",
    az: float = 45.0,
    el: float = 34.2,
    visible: bool = True,
) -> SatTrackData:
    """テスト用 SatTrackData を生成する。"""
    aos = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
    los = aos + timedelta(minutes=10)
    return SatTrackData(
        name=name,
        norad_cat_id=25544,
        azimuth_deg=az,
        elevation_deg=el,
        is_visible=visible,
        track=[(0.0, 0.0), (az, el), (90.0, 0.0)],
        aos_time=aos,
        los_time=los,
    )


class TestRadarView:
    def test_import(self) -> None:
        """RadarView がインポートできることを確認する。"""
        from ui.radar_view import RadarView

        assert RadarView is not None

    def test_create_widget(self, qtbot: Any) -> None:
        """ウィジェットを生成できることを確認する。"""
        from ui.radar_view import RadarView

        widget = RadarView()
        qtbot.addWidget(widget)
        assert widget is not None

    def test_set_empty_tracks(self, qtbot: Any) -> None:
        """空リストを設定してもクラッシュしないことを確認する。"""
        from ui.radar_view import RadarView

        widget = RadarView()
        qtbot.addWidget(widget)
        widget.set_tracks([])

    def test_set_single_track(self, qtbot: Any) -> None:
        """1 件の衛星を設定できることを確認する。"""
        from ui.radar_view import RadarView

        widget = RadarView()
        qtbot.addWidget(widget)
        widget.set_tracks([_make_track()])

    def test_set_multiple_tracks(self, qtbot: Any) -> None:
        """複数衛星を色分けして設定できることを確認する。"""
        from ui.radar_view import RadarView

        widget = RadarView()
        qtbot.addWidget(widget)
        tracks = [_make_track(f"SAT{i}", az=float(i * 45), el=float(i * 10)) for i in range(4)]
        widget.set_tracks(tracks)

    def test_clear(self, qtbot: Any) -> None:
        """clear() でウィジェット内の衛星がリセットされることを確認する。"""
        from ui.radar_view import RadarView

        widget = RadarView()
        qtbot.addWidget(widget)
        widget.set_tracks([_make_track()])
        widget.clear()
        assert widget._tracks == []

    def test_sat_clicked_signal_exists(self, qtbot: Any) -> None:
        """sat_clicked シグナルが存在することを確認する。"""
        from ui.radar_view import RadarView

        widget = RadarView()
        qtbot.addWidget(widget)
        assert hasattr(widget, "sat_clicked")

    def test_invisible_satellite(self, qtbot: Any) -> None:
        """地平線以下の衛星（is_visible=False）も設定できることを確認する。"""
        from ui.radar_view import RadarView

        widget = RadarView()
        qtbot.addWidget(widget)
        widget.set_tracks([_make_track(el=-5.0, visible=False)])

    def test_no_track_data(self, qtbot: Any) -> None:
        """track が空のデータでもクラッシュしないことを確認する。"""
        from ui.radar_view import RadarView

        widget = RadarView()
        qtbot.addWidget(widget)
        track = SatTrackData(name="ISS", norad_cat_id=25544, is_visible=True)
        widget.set_tracks([track])

    def test_set_then_clear_then_set(self, qtbot: Any) -> None:
        """set → clear → set の繰り返しが安全なことを確認する。"""
        from ui.radar_view import RadarView

        widget = RadarView()
        qtbot.addWidget(widget)
        widget.set_tracks([_make_track()])
        widget.clear()
        widget.set_tracks([_make_track("AO-91", az=90.0, el=20.0)])

    def test_size_hint(self, qtbot: Any) -> None:
        """sizeHint が正の値を返すことを確認する。"""
        from ui.radar_view import RadarView

        widget = RadarView()
        qtbot.addWidget(widget)
        hint = widget.sizeHint()
        assert hint.width() > 0
        assert hint.height() > 0

    def test_minimum_size(self, qtbot: Any) -> None:
        """最小サイズが設定されていることを確認する。"""
        from ui.radar_view import RadarView

        widget = RadarView()
        qtbot.addWidget(widget)
        assert widget.minimumWidth() >= 200
        assert widget.minimumHeight() >= 200
