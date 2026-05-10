"""
パス予測グラフィカル表示ウィジェット

PassChartView  — PySide6 + QtCharts による仰角 vs 時刻チャート
pass_quality() — 最大仰角から品質ランクを返す共用ユーティリティ
elevation_points() — AOS/TCA/LOS からサイン近似の仰角点列を生成する

品質ランクと色:
    excellent (>=60°): 緑 #2ecc71
    good      (>=30°): 青 #3498db
    fair      (>=10°): 黄 #f1c40f
    low       (< 10°): グレー #95a5a6
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from PySide6.QtCharts import (
    QChart,
    QChartView,
    QDateTimeAxis,
    QLineSeries,
    QSplineSeries,
    QValueAxis,
)
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

if TYPE_CHECKING:
    from core.engine import PassInfo

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

QUALITY_COLORS: dict[str, QColor] = {
    "excellent": QColor("#2ecc71"),
    "good": QColor("#3498db"),
    "fair": QColor("#f1c40f"),
    "low": QColor("#95a5a6"),
}

_ELEVATION_SAMPLE_POINTS = 20


# ---------------------------------------------------------------------------
# ユーティリティ（UI に依存しない純粋関数）
# ---------------------------------------------------------------------------


def pass_quality(max_elevation_deg: float) -> str:
    """
    最大仰角からパスの品質ランク文字列を返す。

    Args:
        max_elevation_deg: パスの最大仰角（度）

    Returns:
        "excellent" (>=60) / "good" (>=30) / "fair" (>=10) / "low" (<10)
    """
    if max_elevation_deg >= 60.0:
        return "excellent"
    if max_elevation_deg >= 30.0:
        return "good"
    if max_elevation_deg >= 10.0:
        return "fair"
    return "low"


def elevation_points(
    aos: datetime,
    tca: datetime,
    los: datetime,
    max_elevation_deg: float,
    n_points: int = _ELEVATION_SAMPLE_POINTS,
) -> list[tuple[float, float]]:
    """
    AOS・TCA・LOS と最大仰角からサイン近似の仰角点列を生成する。

    AOS→TCA 区間は sin(π·t/2)、TCA→LOS 区間は cos(π·t/2) で近似する。
    x 値は Unix タイムスタンプ [ms]、y 値は仰角 [度]。

    Args:
        aos: 衛星可視開始時刻 (UTC)
        tca: 最大仰角時刻 (UTC)
        los: 衛星可視終了時刻 (UTC)
        max_elevation_deg: 最大仰角（度）
        n_points: AOS→TCA・TCA→LOS それぞれのサンプル数

    Returns:
        (timestamp_ms, elevation_deg) のリスト
    """
    if aos.tzinfo is None:
        aos = aos.replace(tzinfo=UTC)
    if tca.tzinfo is None:
        tca = tca.replace(tzinfo=UTC)
    if los.tzinfo is None:
        los = los.replace(tzinfo=UTC)

    aos_ms = aos.timestamp() * 1000.0
    tca_ms = tca.timestamp() * 1000.0
    los_ms = los.timestamp() * 1000.0
    points: list[tuple[float, float]] = []

    # AOS → TCA（sin カーブ上昇）
    for i in range(n_points):
        t = i / n_points
        el = max_elevation_deg * math.sin(math.pi * t / 2.0)
        ms = aos_ms + t * (tca_ms - aos_ms)
        points.append((ms, el))

    # TCA（頂点）
    points.append((tca_ms, max_elevation_deg))

    # TCA → LOS（cos カーブ下降）
    for i in range(1, n_points + 1):
        t = i / n_points
        el = max_elevation_deg * math.cos(math.pi * t / 2.0)
        ms = tca_ms + t * (los_ms - tca_ms)
        points.append((ms, el))

    return points


# ---------------------------------------------------------------------------
# PassChartView ウィジェット
# ---------------------------------------------------------------------------


class PassChartView(QChartView):
    """
    衛星パスの仰角 vs 時刻チャートを表示する PySide6 ウィジェット。

    使い方::

        chart = PassChartView()
        chart.set_passes(passes, sat_name="ISS (ZARYA)")
        layout.addWidget(chart)

    Signals:
        pass_clicked(PassInfo): パスの曲線クリック時に発火する
    """

    pass_clicked: Signal = Signal(object)  # emit(PassInfo)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._chart = QChart()
        self._chart.setAnimationOptions(QChart.AnimationOption.SeriesAnimations)
        self._chart.legend().setVisible(True)
        self._chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.setChart(self._chart)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._passes: list[PassInfo] = []
        self._series_to_pass: dict[QSplineSeries, PassInfo] = {}

    # ------------------------------------------------------------------ #
    # 公開 API
    # ------------------------------------------------------------------ #

    def set_passes(self, passes: list[PassInfo], sat_name: str = "") -> None:
        """
        表示するパスリストを設定してチャートを再描画する。

        Args:
            passes:   PassInfo のリスト（空リストでチャートをクリア）
            sat_name: チャートタイトルに使う衛星名
        """
        self._passes = passes
        self._series_to_pass = {}
        self._rebuild(sat_name)

    def clear(self) -> None:
        """チャートをクリアする。"""
        self._passes = []
        self._series_to_pass = {}
        self._chart.removeAllSeries()
        for axis in self._chart.axes():
            self._chart.removeAxis(axis)
        self._chart.setTitle("")

    # ------------------------------------------------------------------ #
    # チャート構築
    # ------------------------------------------------------------------ #

    def _rebuild(self, sat_name: str) -> None:
        """パスリストからチャートを構築する。"""
        self._chart.removeAllSeries()
        for axis in self._chart.axes():
            self._chart.removeAxis(axis)

        if not self._passes:
            self._chart.setTitle(sat_name or "パス予測（データなし）")
            return

        title = f"{sat_name} パス予測" if sat_name else "パス予測"
        self._chart.setTitle(title)

        # 軸を先に追加してから series を attach する
        dt_axis = self._make_time_axis()
        el_axis = self._make_elevation_axis()
        self._chart.addAxis(dt_axis, Qt.AlignmentFlag.AlignBottom)
        self._chart.addAxis(el_axis, Qt.AlignmentFlag.AlignLeft)

        for p in self._passes:
            series = self._build_pass_series(p)
            self._chart.addSeries(series)
            series.attachAxis(dt_axis)
            series.attachAxis(el_axis)
            self._series_to_pass[series] = p
            series.clicked.connect(self._on_series_clicked)

        # 現在時刻ライン
        now = datetime.now(UTC)
        all_aos = min(p.aos for p in self._passes)
        all_los = max(p.los for p in self._passes)
        if all_aos <= now <= all_los:
            now_series = self._build_now_line(now)
            self._chart.addSeries(now_series)
            now_series.attachAxis(dt_axis)
            now_series.attachAxis(el_axis)

    def _make_time_axis(self) -> QDateTimeAxis:
        axis = QDateTimeAxis()
        axis.setFormat("HH:mm")
        axis.setTitleText("時刻 (UTC)")
        axis.setTickCount(7)
        return axis

    def _make_elevation_axis(self) -> QValueAxis:
        axis = QValueAxis()
        axis.setRange(0.0, 90.0)
        axis.setTitleText("仰角 (度)")
        axis.setTickCount(10)
        axis.setLabelFormat("%.0f°")
        return axis

    def _build_pass_series(self, p: PassInfo) -> QSplineSeries:
        """1 パス分の仰角曲線 QSplineSeries を生成する。"""
        quality = pass_quality(p.max_elevation_deg)
        color = QUALITY_COLORS[quality]

        series = QSplineSeries()
        label = f"max {p.max_elevation_deg:.0f}° ({quality})"
        series.setName(label)

        pen = QPen(color)
        pen.setWidth(2)
        series.setPen(pen)

        pts = elevation_points(p.aos, p.tca, p.los, p.max_elevation_deg)
        for ms, el in pts:
            series.append(QPointF(ms, el))

        return series

    def _build_now_line(self, now: datetime) -> QLineSeries:
        """現在時刻を示す赤い縦線の QLineSeries を生成する。"""
        now_ms = now.timestamp() * 1000.0
        series = QLineSeries()
        series.setName("現在時刻")
        pen = QPen(QColor("#e74c3c"))
        pen.setWidth(2)
        pen.setStyle(Qt.PenStyle.DashLine)
        series.setPen(pen)
        series.append(QPointF(now_ms, 0.0))
        series.append(QPointF(now_ms, 90.0))
        return series

    # ------------------------------------------------------------------ #
    # シグナルハンドラー
    # ------------------------------------------------------------------ #

    def _on_series_clicked(self, point: QPointF) -> None:
        """クリックされた series からパス情報を特定して pass_clicked を emit する。"""
        sender = self.sender()
        if isinstance(sender, QSplineSeries) and sender in self._series_to_pass:
            self.pass_clicked.emit(self._series_to_pass[sender])
