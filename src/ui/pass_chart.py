"""
パス予測グラフィカル表示ウィジェット

PassChartView  — PySide6 + QtCharts による仰角 vs 時刻チャート（時間範囲選択付き）
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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from PySide6.QtCharts import (
    QChart,
    QChartView,
    QDateTimeAxis,
    QLineSeries,
    QSplineSeries,
    QValueAxis,
)
from PySide6.QtCore import QDateTime, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

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

_RANGE_OPTIONS: tuple[tuple[str, float], ...] = (
    ("Next 4 hours", 4.0),
    ("Next 8 hours", 8.0),
    ("Next 12 hours", 12.0),
    ("Next 24 hours", 24.0),
)


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
# 内部 QChartView：山頂ラベルを重ね描きする
# ---------------------------------------------------------------------------


class _ElevationChartView(QChartView):
    """各パス山頂に最大仰角テキストを重ね描きする内部ウィジェット。"""

    def __init__(self, chart: QChart, parent: QWidget | None = None) -> None:
        super().__init__(chart, parent)
        self._overlay: list[tuple[QSplineSeries, float, QColor]] = []

    def set_overlay_labels(
        self,
        labels: list[tuple[QSplineSeries, float, QColor]],
    ) -> None:
        """山頂ラベルリストを設定する。(series, max_el_deg, color) のタプルリスト。"""
        self._overlay = labels
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: ANN001
        super().paintEvent(event)  # type: ignore[arg-type]
        if not self._overlay:
            return

        chart = self.chart()
        painter = QPainter(self.viewport())
        try:
            font = QFont()
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)
            fm = painter.fontMetrics()

            for series, max_el, color in self._overlay:
                # 仰角が最大のデータ点を探す
                best: QPointF | None = None
                for i in range(series.count()):
                    pt = series.at(i)
                    if best is None or pt.y() > best.y():
                        best = QPointF(pt.x(), pt.y())
                if best is None:
                    continue
                try:
                    scene_pt = chart.mapToPosition(best, series)
                    view_pt = self.mapFromScene(scene_pt)
                    lbl = f"{max_el:.0f}°"
                    w = fm.horizontalAdvance(lbl)
                    painter.setPen(color)
                    painter.drawText(int(view_pt.x()) - w // 2, int(view_pt.y()) - 4, lbl)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            painter.end()


# ---------------------------------------------------------------------------
# PassChartView ウィジェット
# ---------------------------------------------------------------------------


class PassChartView(QWidget):
    """
    衛星パスの仰角 vs 時刻チャートを表示する PySide6 ウィジェット。

    上部に時間範囲プルダウンを持ち、選択範囲内のパスのみ描画する。
    各パス山頂には最大仰角ラベルを表示する。

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
        self._passes: list[PassInfo] = []
        self._sat_name: str = ""
        self._series_to_pass: dict[QSplineSeries, PassInfo] = {}
        self._setup_ui()

    # ------------------------------------------------------------------ #
    # UI 構築
    # ------------------------------------------------------------------ #

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # 時間範囲プルダウン
        header = QWidget()
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(6, 2, 6, 2)
        h_layout.addWidget(QLabel("表示範囲:"))
        self._range_combo = QComboBox()
        for label, _ in _RANGE_OPTIONS:
            self._range_combo.addItem(label)
        self._range_combo.setCurrentIndex(len(_RANGE_OPTIONS) - 1)  # "Next 24 hours" をデフォルト
        self._range_combo.currentIndexChanged.connect(self._on_range_changed)
        h_layout.addWidget(self._range_combo)
        h_layout.addStretch()
        layout.addWidget(header)

        # チャートビュー
        self._chart = QChart()
        self._chart.setAnimationOptions(QChart.AnimationOption.SeriesAnimations)
        self._chart.legend().setVisible(True)
        self._chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)

        self._chart_view = _ElevationChartView(self._chart)
        self._chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._chart_view.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        layout.addWidget(self._chart_view)

    # ------------------------------------------------------------------ #
    # 公開 API
    # ------------------------------------------------------------------ #

    def set_passes(self, passes: list[PassInfo], sat_name: str = "") -> None:
        """
        表示するパスリストを設定してチャートを再描画する。

        Args:
            passes:   PassInfo のリスト（最大24時間分を推奨）
            sat_name: チャートタイトルに使う衛星名
        """
        self._passes = passes
        self._sat_name = sat_name
        self._rebuild()

    def clear(self) -> None:
        """チャートをクリアする。"""
        self._passes = []
        self._sat_name = ""
        self._series_to_pass = {}
        self._chart.removeAllSeries()
        for axis in self._chart.axes():
            self._chart.removeAxis(axis)
        self._chart.setTitle("")
        self._chart_view.set_overlay_labels([])

    # ------------------------------------------------------------------ #
    # チャート構築
    # ------------------------------------------------------------------ #

    def _on_range_changed(self, _idx: int) -> None:
        self._rebuild()

    def _selected_hours(self) -> float:
        idx = self._range_combo.currentIndex()
        return _RANGE_OPTIONS[idx][1]

    def _rebuild(self) -> None:
        """選択された時間範囲のパスでチャートを再構築する。"""
        self._chart.removeAllSeries()
        for axis in self._chart.axes():
            self._chart.removeAxis(axis)
        self._series_to_pass = {}
        self._chart_view.set_overlay_labels([])

        hours = self._selected_hours()
        now = datetime.now(UTC)
        cutoff = now + timedelta(hours=hours)
        filtered = [p for p in self._passes if p.los >= now and p.aos <= cutoff]

        if not filtered:
            self._chart.setTitle(self._sat_name or "パス予測（データなし）")
            return

        title = f"{self._sat_name} パス予測" if self._sat_name else "パス予測"
        self._chart.setTitle(title)

        # 全パスの範囲をカバーする時間軸（明示設定しないと1本分しか表示されない）
        t_start = min(p.aos for p in filtered)
        t_end = max(p.los for p in filtered)
        dt_axis = self._make_time_axis(t_start, t_end)
        el_axis = self._make_elevation_axis()
        self._chart.addAxis(dt_axis, Qt.AlignmentFlag.AlignBottom)
        self._chart.addAxis(el_axis, Qt.AlignmentFlag.AlignLeft)

        overlay: list[tuple[QSplineSeries, float, QColor]] = []
        for p in filtered:
            series = self._build_pass_series(p)
            self._chart.addSeries(series)
            series.attachAxis(dt_axis)
            series.attachAxis(el_axis)
            self._series_to_pass[series] = p
            series.clicked.connect(self._on_series_clicked)

            quality = pass_quality(p.max_elevation_deg)
            overlay.append((series, p.max_elevation_deg, QUALITY_COLORS[quality]))

        # 現在時刻ライン
        if now <= t_end:
            now_series = self._build_now_line(now)
            self._chart.addSeries(now_series)
            now_series.attachAxis(dt_axis)
            now_series.attachAxis(el_axis)

        self._chart_view.set_overlay_labels(overlay)

    def _make_time_axis(self, t_start: datetime, t_end: datetime) -> QDateTimeAxis:
        axis = QDateTimeAxis()
        axis.setFormat("HH:mm")
        axis.setTitleText("時刻 (UTC)")
        axis.setTickCount(7)
        axis.setRange(
            QDateTime.fromMSecsSinceEpoch(int(t_start.timestamp() * 1000)),
            QDateTime.fromMSecsSinceEpoch(int(t_end.timestamp() * 1000)),
        )
        return axis

    def _make_elevation_axis(self) -> QValueAxis:
        axis = QValueAxis()
        axis.setRange(0.0, 90.0)
        axis.setTitleText("仰角 (度)")
        axis.setTickCount(10)
        axis.setLabelFormat("%.0f°")
        return axis

    def _build_pass_series(self, p: PassInfo) -> QSplineSeries:
        quality = pass_quality(p.max_elevation_deg)
        color = QUALITY_COLORS[quality]

        series = QSplineSeries()
        series.setName(f"max {p.max_elevation_deg:.0f}° ({quality})")

        pen = QPen(color)
        pen.setWidth(2)
        series.setPen(pen)

        pts = elevation_points(p.aos, p.tca, p.los, p.max_elevation_deg)
        for ms, el in pts:
            series.append(QPointF(ms, el))

        return series

    def _build_now_line(self, now: datetime) -> QLineSeries:
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
        sender = self.sender()
        if isinstance(sender, QSplineSeries) and sender in self._series_to_pass:
            self.pass_clicked.emit(self._series_to_pass[sender])
