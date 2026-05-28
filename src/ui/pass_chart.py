"""
Graphical pass prediction display widget

PassChartView  — elevation vs time chart using PySide6 + QtCharts (with time range selector)
pass_quality() — shared utility that returns a quality rank from a maximum elevation
elevation_points() — generates a sine-approximated elevation point sequence from AOS/TCA/LOS

Quality ranks and colours:
    excellent (>=60°): green  #2ecc71
    good      (>=30°): blue   #3498db
    fair      (>=10°): yellow #f1c40f
    low       (< 10°): grey   #95a5a6
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
from PySide6.QtCore import QDateTime, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QGraphicsLineItem,
    QGraphicsTextItem,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from core.engine import PassInfo

# ---------------------------------------------------------------------------
# Constants
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
# Utilities (pure functions with no UI dependency)
# ---------------------------------------------------------------------------


def pass_quality(max_elevation_deg: float) -> str:
    """
    Return the pass quality rank string from a maximum elevation.

    Args:
        max_elevation_deg: maximum elevation of the pass (degrees)

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
    Generate a sine-approximated elevation point sequence from AOS, TCA, LOS, and max elevation.

    The AOS->TCA segment uses sin(pi*t/2) and the TCA->LOS segment uses cos(pi*t/2).
    x values are Unix timestamps [ms]; y values are elevation [degrees].

    Args:
        aos: satellite rise time (UTC)
        tca: time of maximum elevation (UTC)
        los: satellite set time (UTC)
        max_elevation_deg: maximum elevation (degrees)
        n_points: number of samples for each of the AOS->TCA and TCA->LOS segments

    Returns:
        list of (timestamp_ms, elevation_deg) tuples
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

    # AOS -> TCA (sin curve rising)
    for i in range(n_points):
        t = i / n_points
        el = max_elevation_deg * math.sin(math.pi * t / 2.0)
        ms = aos_ms + t * (tca_ms - aos_ms)
        points.append((ms, el))

    # TCA (peak)
    points.append((tca_ms, max_elevation_deg))

    # TCA -> LOS (cos curve descending)
    for i in range(1, n_points + 1):
        t = i / n_points
        el = max_elevation_deg * math.cos(math.pi * t / 2.0)
        ms = tca_ms + t * (los_ms - tca_ms)
        points.append((ms, el))

    return points


# ---------------------------------------------------------------------------
# PassChartView widget
# ---------------------------------------------------------------------------


class PassChartView(QWidget):
    """
    PySide6 widget that displays a satellite pass elevation vs time chart.

    Has a time-range dropdown at the top and draws only passes within the selected range.
    Peak elevation labels are placed directly in the scene as QGraphicsTextItems.

    Usage::

        chart = PassChartView()
        chart.set_passes(passes, sat_name="ISS (ZARYA)")
        layout.addWidget(chart)

    Signals:
        pass_clicked(PassInfo): emitted when a pass curve is clicked
        range_changed(float):   emits the selected number of hours when the time range changes
    """

    pass_clicked: Signal = Signal(object)  # emit(PassInfo)
    range_changed: Signal = Signal(float)  # emit(hours)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._passes: list[PassInfo] = []
        self._sat_name: str = ""
        self._series_to_pass: dict[QSplineSeries, PassInfo] = {}
        self._overlay: list[tuple[QSplineSeries, float, QColor]] = []
        self._peak_label_items: list[QGraphicsTextItem] = []
        self._rot_az: float | None = None
        self._rot_el: float | None = None
        self._rot_marker_lines: list[QGraphicsLineItem] = []
        self._setup_ui()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Time range dropdown
        header = QWidget()
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(6, 2, 6, 2)
        h_layout.addWidget(QLabel("Range:"))
        self._range_combo = QComboBox()
        for label, _ in _RANGE_OPTIONS:
            self._range_combo.addItem(label)
        self._range_combo.setCurrentIndex(len(_RANGE_OPTIONS) - 1)  # default to "Next 24 hours"
        self._range_combo.currentIndexChanged.connect(self._on_range_changed)
        h_layout.addWidget(self._range_combo)
        h_layout.addStretch()
        layout.addWidget(header)

        # Chart view (animation disabled so series render immediately)
        self._chart = QChart()
        self._chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        self._chart.legend().setVisible(True)
        self._chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)

        self._chart_view = QChartView(self._chart)
        self._chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._chart_view.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        layout.addWidget(self._chart_view)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def set_passes(self, passes: list[PassInfo], sat_name: str = "") -> None:
        """
        Set the pass list to display and redraw the chart.

        Args:
            passes:   list of PassInfo objects (up to 24 hours recommended)
            sat_name: satellite name used in the chart title
        """
        self._passes = passes
        self._sat_name = sat_name
        self._rebuild()

    def clear(self) -> None:
        """Clear the chart."""
        self._passes = []
        self._sat_name = ""
        self._series_to_pass = {}
        self._overlay = []
        self._clear_peak_labels()
        self._clear_rotator_marker()
        self._chart.removeAllSeries()
        for axis in self._chart.axes():
            self._chart.removeAxis(axis)
        self._chart.setTitle("")

    def set_rotator_position(self, az: float | None, el: float | None) -> None:
        """Update the rotator current-position marker on the chart.

        Args:
            az: rotator azimuth in degrees, or None to hide the marker
            el: rotator elevation in degrees, or None to hide the marker
        """
        self._rot_az = az
        self._rot_el = el
        self._update_rotator_marker()

    # ------------------------------------------------------------------ #
    # Chart construction
    # ------------------------------------------------------------------ #

    def _on_range_changed(self, _idx: int) -> None:
        hours = self._selected_hours()
        self.range_changed.emit(hours)
        self._rebuild()

    def _selected_hours(self) -> float:
        idx = self._range_combo.currentIndex()
        return _RANGE_OPTIONS[idx][1]

    def _rebuild(self) -> None:
        """Rebuild the chart using passes within the selected time range."""
        # Remove existing overlays from the scene before deleting series
        self._clear_peak_labels()
        self._clear_rotator_marker()
        self._overlay = []
        self._chart.removeAllSeries()
        for axis in self._chart.axes():
            self._chart.removeAxis(axis)
        self._series_to_pass = {}

        hours = self._selected_hours()
        now = datetime.now(UTC)
        cutoff = now + timedelta(hours=hours)
        filtered = [p for p in self._passes if p.los >= now and p.aos <= cutoff]

        if not filtered:
            self._chart.setTitle("No passes in range")
            return

        title = f"{self._sat_name} Pass Prediction" if self._sat_name else "Pass Prediction"
        self._chart.setTitle(title)

        dt_axis = self._make_time_axis()
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

        # Current time line
        t_end = max(p.los for p in filtered)
        if now <= t_end:
            now_series = self._build_now_line(now)
            self._chart.addSeries(now_series)
            now_series.attachAxis(dt_axis)
            now_series.attachAxis(el_axis)

        # Set the range after all series have been added (attachAxis overwrites auto-range)
        dt_axis.setRange(
            QDateTime.fromMSecsSinceEpoch(int(now.timestamp() * 1000)),
            QDateTime.fromMSecsSinceEpoch(int(cutoff.timestamp() * 1000)),
        )
        el_axis.setRange(0.0, 90.0)

        self._overlay = overlay

        # Place labels and rotator marker 150 ms after Qt Charts finishes layout calculation
        # (mapToPosition() returns incorrect coordinates before layout is finalised)
        QTimer.singleShot(150, self._add_peak_labels)
        QTimer.singleShot(150, self._update_rotator_marker)

    def _clear_peak_labels(self) -> None:
        """Remove all peak-label QGraphicsTextItems from the scene."""
        scene = self._chart.scene()
        if scene is not None:
            for item in self._peak_label_items:
                scene.removeItem(item)
        self._peak_label_items.clear()

    def _clear_rotator_marker(self) -> None:
        """Remove rotator marker lines from the scene."""
        scene = self._chart.scene()
        if scene is not None:
            for item in self._rot_marker_lines:
                scene.removeItem(item)
        self._rot_marker_lines.clear()

    def _update_rotator_marker(self) -> None:
        """Place or hide the rotator × marker at (now, rotator_el) in the chart."""
        self._clear_rotator_marker()

        if self._rot_az is None or self._rot_el is None:
            return

        if not self.isVisible():
            return

        if not self._chart.axes():
            return

        plot_area = self._chart.plotArea()
        if plot_area.width() <= 1 or plot_area.height() <= 1:
            return

        now_ms = datetime.now(UTC).timestamp() * 1000.0
        el_clamped = max(0.0, min(90.0, self._rot_el))
        try:
            chart_pt = self._chart.mapToPosition(QPointF(now_ms, el_clamped))
            if not plot_area.contains(chart_pt):
                return
            scene_pos = self._chart.mapToScene(chart_pt)
        except Exception:  # noqa: BLE001
            return

        sz = 6.0
        pen = QPen(QColor("#FF8C00"))
        pen.setWidth(2)
        tooltip = f"Rotator: AZ={self._rot_az:.1f}° EL={self._rot_el:.1f}°"

        line1 = QGraphicsLineItem(
            scene_pos.x() - sz, scene_pos.y() - sz,
            scene_pos.x() + sz, scene_pos.y() + sz,
        )
        line1.setPen(pen)
        line1.setToolTip(tooltip)

        line2 = QGraphicsLineItem(
            scene_pos.x() - sz, scene_pos.y() + sz,
            scene_pos.x() + sz, scene_pos.y() - sz,
        )
        line2.setPen(pen)
        line2.setToolTip(tooltip)

        scene = self._chart.scene()
        if scene is None:
            return
        scene.addItem(line1)
        scene.addItem(line2)
        self._rot_marker_lines = [line1, line2]

    def showEvent(self, event: object) -> None:  # noqa: ANN001
        """(Re)place labels and rotator marker when the widget becomes visible (e.g. on tab switch).

        The chart's drawing mapping is not yet finalised immediately after showEvent,
        so these are called after a short delay.
        """
        super().showEvent(event)  # type: ignore[arg-type]
        if self._overlay:
            QTimer.singleShot(50, self._add_peak_labels)
        if self._rot_az is not None:
            QTimer.singleShot(50, self._update_rotator_marker)

    def _add_peak_labels(self, retry: int = 0) -> None:
        """Place elevation labels at each pass peak as QGraphicsTextItems in the scene.

        Root cause and mitigation:
          - Inactive QTabWidget tabs have isVisible()=False.
            In that state plotArea() returns a plausible size, but the chart's
            internal data-to-pixel mapping is uninitialised, so mapToPosition()
            returns values near (0,0) and all labels cluster in the top-left corner.
          - Check isVisible() and do nothing if hidden (showEvent will trigger later).
          - mapToPosition() returns chart-local coordinates, so convert to scene
            coordinates with mapToScene() before calling setPos().
          - If plotArea is very small, retry up to 5 times at 150 ms intervals.
        """
        self._clear_peak_labels()

        # Do not place labels on a hidden chart (showEvent will trigger them)
        if not self.isVisible():
            return

        plot_area = self._chart.plotArea()
        if plot_area.width() <= 1 or plot_area.height() <= 1:
            if retry < 5:
                QTimer.singleShot(150, lambda: self._add_peak_labels(retry + 1))
            return

        scene = self._chart.scene()
        if scene is None:
            return

        label_font = QFont()
        label_font.setPointSize(8)
        label_font.setBold(True)

        for series, max_el, color in self._overlay:
            # Find the data point with the highest elevation
            best: QPointF | None = None
            for i in range(series.count()):
                pt = series.at(i)
                if best is None or pt.y() > best.y():
                    best = QPointF(pt.x(), pt.y())
            if best is None:
                continue

            try:
                # mapToPosition() returns chart-local coordinates
                chart_pt = self._chart.mapToPosition(best, series)
                if not plot_area.contains(chart_pt):
                    continue

                # Convert chart-local coordinates to scene coordinates before placing
                scene_pos = self._chart.mapToScene(chart_pt)

                lbl = f"{max_el:.0f}°"
                item = QGraphicsTextItem(lbl)
                item.setDefaultTextColor(color)
                item.setFont(label_font)
                bw = item.boundingRect().width()
                item.setPos(scene_pos.x() - bw / 2.0, scene_pos.y() - 18.0)
                scene.addItem(item)
                self._peak_label_items.append(item)
            except Exception:  # noqa: BLE001
                pass

    def _make_time_axis(self) -> QDateTimeAxis:
        axis = QDateTimeAxis()
        axis.setFormat("HH:mm")
        axis.setTitleText("Time (UTC)")
        axis.setTickCount(7)
        return axis

    def _make_elevation_axis(self) -> QValueAxis:
        axis = QValueAxis()
        axis.setRange(0.0, 90.0)
        axis.setTitleText("Elevation (°)")
        axis.setTickCount(10)
        axis.setLabelFormat("%d")
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
        series.setName("Now")
        pen = QPen(QColor("#e74c3c"))
        pen.setWidth(2)
        pen.setStyle(Qt.PenStyle.DashLine)
        series.setPen(pen)
        series.append(QPointF(now_ms, 0.0))
        series.append(QPointF(now_ms, 90.0))
        return series

    # ------------------------------------------------------------------ #
    # Signal handlers
    # ------------------------------------------------------------------ #

    def _on_series_clicked(self, point: QPointF) -> None:
        sender = self.sender()
        if isinstance(sender, QSplineSeries) and sender in self._series_to_pass:
            self.pass_clicked.emit(self._series_to_pass[sender])
