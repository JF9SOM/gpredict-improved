"""
レーダーチャート（スカイビュー）ウィジェット

RadarView    — PySide6 QPainter による極座標レーダー表示
SatTrackData — 衛星の現在位置・パス軌跡データコンテナ
az_el_to_xy  — 方位角・仰角をレーダー上の (x, y) に変換するユーティリティ

レーダー座標系:
    中心 = 天頂（仰角 90°）
    外周 = 地平線（仰角 0°）
    上   = 北（方位角 0°）、時計回りで増加（東 = 90°）
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

SAT_COLORS: list[QColor] = [
    QColor("#e74c3c"),
    QColor("#3498db"),
    QColor("#2ecc71"),
    QColor("#f39c12"),
    QColor("#9b59b6"),
    QColor("#1abc9c"),
    QColor("#e67e22"),
    QColor("#34495e"),
]

_ELEVATION_RINGS: tuple[int, ...] = (0, 30, 60)
_CARDINALS: tuple[tuple[str, int], ...] = (("N", 0), ("E", 90), ("S", 180), ("W", 270))


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------


@dataclass
class SatTrackData:
    """
    レーダーに表示する衛星の位置・軌跡データ。

    Attributes:
        name:            衛星名（ラベル表示用）
        norad_cat_id:    NORAD カタログ番号
        azimuth_deg:     現在の方位角（度、北=0、東=90）
        elevation_deg:   現在の仰角（度、0=地平線、90=天頂）
        is_visible:      地平線より上か
        track:           パス軌跡 [(az_deg, el_deg), ...] AOS→LOS 順
        aos_time:        AOS 時刻（UTC）— 次パスのAOS（未可視時）または現パスAOS
        los_time:        LOS 時刻（UTC）— 現パスLOS（可視時）または次パスLOS
        next_max_el:     次パス（または現パス）の最大仰角（度）
        next_duration_s: 次パス（または現パス）の継続時間（秒）
    """

    name: str
    norad_cat_id: int
    azimuth_deg: float = 0.0
    elevation_deg: float = 0.0
    is_visible: bool = False
    track: list[tuple[float, float]] = field(default_factory=list)
    aos_time: datetime | None = None
    los_time: datetime | None = None
    next_max_el: float | None = None
    next_duration_s: float | None = None


# ---------------------------------------------------------------------------
# ユーティリティ（UI 非依存）
# ---------------------------------------------------------------------------


def az_el_to_xy(
    azimuth_deg: float,
    elevation_deg: float,
    cx: float,
    cy: float,
    radius: float,
) -> tuple[float, float]:
    """
    方位角・仰角を極座標レーダー上の (x, y) に変換する。

    Args:
        azimuth_deg:   方位角（度、北=0、東=90）
        elevation_deg: 仰角（度、0=地平線、90=天頂）
        cx, cy:        レーダー中心座標（ピクセル）
        radius:        地平線円の半径（ピクセル）

    Returns:
        (x, y) レーダー上のピクセル座標
    """
    el = max(0.0, min(90.0, elevation_deg))
    r = (90.0 - el) / 90.0 * radius
    az_rad = math.radians(azimuth_deg)
    x = cx + r * math.sin(az_rad)
    y = cy - r * math.cos(az_rad)
    return x, y


# ---------------------------------------------------------------------------
# RadarView ウィジェット
# ---------------------------------------------------------------------------


class RadarView(QWidget):
    """
    衛星の現在位置とパス軌跡を極座標レーダーで表示する PySide6 ウィジェット。

    使い方::

        radar = RadarView()
        radar.set_tracks([
            SatTrackData(
                name="ISS", norad_cat_id=25544,
                azimuth_deg=45.0, elevation_deg=34.2, is_visible=True,
                track=[(0, 0), (45, 34), (90, 20)],
            ),
        ])
        layout.addWidget(radar)

    Signals:
        sat_clicked(str): 衛星ドットのクリック時に衛星名を emit する
    """

    sat_clicked: Signal = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(200, 200)
        self._tracks: list[SatTrackData] = []
        self._dot_hit_radius: float = 10.0

    # ------------------------------------------------------------------ #
    # 公開 API
    # ------------------------------------------------------------------ #

    def set_tracks(self, tracks: list[SatTrackData]) -> None:
        """
        表示する衛星リストを設定してレーダーを再描画する。

        Args:
            tracks: SatTrackData のリスト（空でクリア）
        """
        self._tracks = tracks
        self.update()

    def clear(self) -> None:
        """すべての衛星をクリアする。"""
        self._tracks = []
        self.update()

    # ------------------------------------------------------------------ #
    # Qt イベントハンドラー
    # ------------------------------------------------------------------ #

    def sizeHint(self) -> QSize:
        return QSize(400, 400)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """衛星ドット付近のクリックで sat_clicked を emit する。"""
        cx, cy, r = self._radar_geometry()
        px = event.position().x()
        py = event.position().y()
        for track in reversed(self._tracks):
            sx, sy = az_el_to_xy(track.azimuth_deg, track.elevation_deg, cx, cy, r)
            if math.hypot(px - sx, py - sy) <= self._dot_hit_radius:
                self.sat_clicked.emit(track.name)
                return

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            painter.fillRect(self.rect(), QColor("#0d1b2a"))
            self._draw(painter)
        finally:
            painter.end()

    # ------------------------------------------------------------------ #
    # 描画ヘルパー
    # ------------------------------------------------------------------ #

    def _radar_geometry(self) -> tuple[float, float, float]:
        """(center_x, center_y, radius) を返す。下部テキスト分のマージンを確保する。"""
        w = self.width()
        h = self.height()
        margin = 70  # 次パス情報テキスト用に余裕を持たせる
        r = (min(w, h - margin) - 20) / 2.0
        cx = w / 2.0
        cy = (h - margin) / 2.0 + 10.0
        return cx, cy, max(r, 1.0)

    def _draw(self, p: QPainter) -> None:
        cx, cy, r = self._radar_geometry()
        self._draw_background(p, cx, cy, r)
        self._draw_rings(p, cx, cy, r)
        self._draw_crosshairs(p, cx, cy, r)
        self._draw_cardinals(p, cx, cy, r)

        for idx, track in enumerate(self._tracks):
            color = SAT_COLORS[idx % len(SAT_COLORS)]
            self._draw_track(p, track, color, cx, cy, r)
            self._draw_satellite(p, track, color, cx, cy, r)

        self._draw_status(p, cx, cy, r)

    def _draw_background(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1a1a2e"))
        p.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))
        p.setPen(QPen(QColor("#4a4a6a"), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))

    def _draw_rings(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        label_font = QFont()
        label_font.setPointSize(7)
        p.setFont(label_font)

        for el in _ELEVATION_RINGS:
            cr = int((90 - el) / 90.0 * r)
            pen = QPen(QColor("#2c3e50"), 1)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(int(cx) - cr, int(cy) - cr, cr * 2, cr * 2)
            p.setPen(QColor("#7f8c8d"))
            p.drawText(int(cx) + cr + 2, int(cy) + 5, f"{el}°")

    def _draw_crosshairs(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        pen = QPen(QColor("#2c3e50"), 1)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(int(cx), int(cy - r), int(cx), int(cy + r))
        p.drawLine(int(cx - r), int(cy), int(cx + r), int(cy))

    def _draw_cardinals(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        p.setFont(font)

        for label, az in _CARDINALS:
            x, y = az_el_to_xy(float(az), 0.0, cx, cy, r)
            offset = 14
            if az == 0:  # N — 上
                x -= 4.0
                y -= float(offset - 4)
            elif az == 90:  # E — 右
                x += 4.0
                y += 4.0
            elif az == 180:  # S — 下
                x -= 4.0
                y += float(offset)
            else:  # W — 左
                x -= float(offset + 2)
                y += 4.0
            color = QColor("#e74c3c") if label == "N" else QColor("#bdc3c7")
            p.setPen(color)
            p.drawText(int(x), int(y), label)

    def _draw_track(
        self,
        p: QPainter,
        track: SatTrackData,
        color: QColor,
        cx: float,
        cy: float,
        r: float,
    ) -> None:
        if len(track.track) < 2:
            return

        _TRACK_COLOR = QColor("#3498db")  # blue
        _AOS_COLOR = QColor("#2ecc71")  # green
        _LOS_COLOR = QColor("#e74c3c")  # red

        pts = [az_el_to_xy(az, el, cx, cy, r) for az, el in track.track]

        # 青い軌跡ライン
        p.setPen(QPen(_TRACK_COLOR, 2))
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            p.drawLine(int(x0), int(y0), int(x1), int(y1))

        # AOS 点（緑の塗り潰し円）
        ax, ay = pts[0]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_AOS_COLOR)
        p.drawEllipse(int(ax) - 4, int(ay) - 4, 8, 8)

        # LOS 点（赤の塗り潰し円）
        lx, ly = pts[-1]
        p.setBrush(_LOS_COLOR)
        p.drawEllipse(int(lx) - 4, int(ly) - 4, 8, 8)

        p.setBrush(Qt.BrushStyle.NoBrush)

        label_font = QFont()
        label_font.setPointSize(8)
        p.setFont(label_font)
        p.setPen(_TRACK_COLOR)

        if track.aos_time is not None:
            p.drawText(int(ax) + 6, int(ay) - 2, f"AOS {track.aos_time.strftime('%H:%M')}")

        if track.los_time is not None:
            p.drawText(int(lx) + 6, int(ly) + 10, f"LOS {track.los_time.strftime('%H:%M')}")

    def _draw_satellite(
        self,
        p: QPainter,
        track: SatTrackData,
        color: QColor,
        cx: float,
        cy: float,
        r: float,
    ) -> None:
        x, y = az_el_to_xy(track.azimuth_deg, track.elevation_deg, cx, cy, r)
        dot_r = 6

        if track.is_visible:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
        else:
            p.setPen(QPen(color, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(int(x) - dot_r, int(y) - dot_r, dot_r * 2, dot_r * 2)

        label_font = QFont()
        label_font.setPointSize(8)
        p.setFont(label_font)
        p.setPen(color)
        p.drawText(int(x) + dot_r + 2, int(y) + 4, track.name)

    def _draw_status(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        """レーダー円の下部に現在パス情報または次パス情報を表示する。"""
        font = QFont()
        font.setPointSize(12)
        p.setFont(font)

        y_base = int(cy + r + 16)
        line_h = 22

        for i, track in enumerate(self._tracks):
            if track.is_visible:
                # IN PASS: 緑色でパス中情報を表示
                los_str = ""
                if track.los_time is not None:
                    los_str = f"  LOS: {track.los_time.strftime('%H:%M')} UTC"
                text = (
                    f"IN PASS  EL: {track.elevation_deg:.1f}°"
                    f"  AZ: {track.azimuth_deg:.1f}°{los_str}"
                )
                p.setPen(QColor("#2ecc71"))
            elif track.aos_time is not None:
                # 次パス情報: "Next: MM/DD HH:MM UTC  Max X.X°  Xm Ys"
                aos_str = track.aos_time.strftime("%m/%d %H:%M") + " UTC"
                max_el_str = (
                    f"  Max {track.next_max_el:.1f}°" if track.next_max_el is not None else ""
                )
                dur_str = ""
                if track.next_duration_s is not None:
                    m = int(track.next_duration_s) // 60
                    s = int(track.next_duration_s) % 60
                    dur_str = f"  {m}m {s:02d}s"
                text = f"Next: {aos_str}{max_el_str}{dur_str}"
                p.setPen(QColor("#ffffff"))
            else:
                continue

            p.drawText(
                0,
                y_base + i * line_h,
                self.width(),
                line_h,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                text,
            )
