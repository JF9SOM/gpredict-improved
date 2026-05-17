"""
パス予測パネル (タブ構成)

PassPanel          — Upcoming Passes パネル（Target / Group の 2 タブ）
_GroupSearchWorker — Group 検索バックグラウンドスレッド
GroupPassResult    — Group 検索結果 1 件
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from PySide6.QtCore import QDate, QDateTime, Qt, QThread, QTime, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateTimeEdit,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.engine import PassInfo, PassPredictor
from i18n import _
from ui.pass_chart import QUALITY_COLORS, pass_quality


def _dt_to_qdatetime(dt: datetime) -> QDateTime:
    """Python datetime (UTC) を QDateTime (UTC spec) に変換する。"""
    return QDateTime(
        QDate(dt.year, dt.month, dt.day),
        QTime(dt.hour, dt.minute, dt.second),
        Qt.TimeSpec.UTC,
    )


def _qdatetime_to_dt(qdt: QDateTime) -> datetime:
    """QDateTime を Python datetime (UTC) に変換する。"""
    utc = qdt.toUTC()
    d = utc.date()
    t = utc.time()
    return datetime(d.year(), d.month(), d.day(), t.hour(), t.minute(), t.second(), tzinfo=UTC)


@dataclass
class GroupPassResult:
    """Group タブの検索結果 1 件。"""

    norad_cat_id: int
    sat_name: str
    pass_info: PassInfo


_CacheKey = tuple[datetime, datetime, float, tuple[int, ...]]


class _GroupSearchWorker(QThread):
    """グループ衛星パス検索をバックグラウンドで実行するワーカー。"""

    progress: Signal = Signal(int)
    finished_results: Signal = Signal(object)

    def __init__(
        self,
        predictor: PassPredictor,
        sat_list: list[tuple[int, str]],
        start: datetime,
        end: datetime,
        min_el: float,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._predictor = predictor
        self._sat_list = sat_list
        self._start = start
        self._end = end
        self._min_el = min_el
        self._cancelled = False

    def cancel(self) -> None:
        """キャンセルフラグを立てる（次のイテレーションで停止）。"""
        self._cancelled = True

    def run(self) -> None:
        results: list[GroupPassResult] = []
        total = len(self._sat_list)
        for i, (norad, name) in enumerate(self._sat_list):
            if self._cancelled:
                break
            try:
                passes = self._predictor.get_passes(norad, self._start, self._end, self._min_el)
            except Exception:  # noqa: BLE001
                passes = []
            for p in passes:
                results.append(GroupPassResult(norad_cat_id=norad, sat_name=name, pass_info=p))
            pct = int((i + 1) / total * 100) if total > 0 else 100
            self.progress.emit(pct)
        if not self._cancelled:
            results.sort(key=lambda r: r.pass_info.aos)
        self.finished_results.emit(results)


def _make_dt_edit() -> QDateTimeEdit:
    """カレンダーポップアップ付き UTC 表示の QDateTimeEdit を返す。"""
    edit = QDateTimeEdit()
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("yyyy-MM-dd HH:mm")
    return edit


class PassPanel(QWidget):
    """
    Upcoming Passes パネル（2 タブ構成）。

    Tab 1 "Target" — 選択衛星のパス一覧（日時範囲・クイックボタン・Search）
    Tab 2 "Group"  — フィルター済み全衛星のパス一覧（バックグラウンド・CSV出力）
    """

    pass_selected: Signal = Signal(object)  # PassInfo
    target_search_requested: Signal = Signal(object, object)  # (start: datetime, end: datetime)
    highlight_satellite: Signal = Signal(int)  # norad_cat_id

    _PAGE_SIZE: int = 50
    _TARGET_COLS: tuple[str, ...] = (
        "AOS (UTC)", "Max El", "Duration", "AZ In", "AZ Out", "Quality"
    )
    _GROUP_COLS: tuple[str, ...] = (
        "Satellite", "AOS (UTC)", "Max El", "Duration", "AZ In", "Quality"
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._passes: list[PassInfo] = []
        self._predictor: PassPredictor | None = None
        self._sat_list: list[tuple[int, str]] = []
        self._group_results: list[GroupPassResult] = []
        self._group_page: int = 0
        self._worker: _GroupSearchWorker | None = None
        self._cache_key: _CacheKey | None = None
        self._cache_results: list[GroupPassResult] = []
        self._pending_cache_key: _CacheKey | None = None
        self._setup_ui()

    # ------------------------------------------------------------------ #
    # UI 構築
    # ------------------------------------------------------------------ #

    def _setup_ui(self) -> None:
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 0)
        layout.setSpacing(0)
        self._tabs = QTabWidget()
        title = QLabel("  Upcoming Passes  ")
        title.setStyleSheet("font-weight: bold; color: black;")
        self._tabs.setCornerWidget(title, Qt.Corner.TopRightCorner)
        self._tabs.addTab(self._build_target_tab(), _("Target"))
        self._tabs.addTab(self._build_group_tab(), _("Group"))
        layout.addWidget(self._tabs)

    def _build_target_tab(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(2, 2, 2, 2)
        vbox.setSpacing(2)

        # 日時範囲行
        dr = QHBoxLayout()
        dr.addWidget(QLabel(_("From:")))
        self._target_from = _make_dt_edit()
        dr.addWidget(self._target_from)
        dr.addWidget(QLabel(_("To:")))
        self._target_to = _make_dt_edit()
        dr.addWidget(self._target_to)
        dr.addWidget(QLabel("(UTC)"))
        dr.addStretch()
        vbox.addLayout(dr)

        # クイックボタン + Search 行
        qr = QHBoxLayout()
        for label, hours in (("+ 6h", 6), ("+24h", 24), ("+ 3d", 72), ("+ 7d", 168), ("+30d", 720)):
            btn = QPushButton(label)
            btn.setFixedWidth(46)
            btn.clicked.connect(lambda _c=False, h=hours: self._on_target_quick(h))
            qr.addWidget(btn)
        qr.addStretch()
        search_btn = QPushButton(_("Search"))
        search_btn.clicked.connect(self._on_target_search)
        qr.addWidget(search_btn)
        vbox.addLayout(qr)

        # テーブル
        self._target_table = QTableWidget(0, len(self._TARGET_COLS))
        self._target_table.setHorizontalHeaderLabels(list(self._TARGET_COLS))
        self._target_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._target_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._target_table.horizontalHeader().setStretchLastSection(True)
        self._target_table.verticalHeader().setVisible(False)
        self._target_table.itemSelectionChanged.connect(self._on_target_selection_changed)
        vbox.addWidget(self._target_table)

        self._reset_target_datetimes()
        return w

    def _build_group_tab(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(2, 2, 2, 2)
        vbox.setSpacing(2)

        # 日時範囲 + Min El 行
        dr = QHBoxLayout()
        dr.addWidget(QLabel(_("From:")))
        self._group_from = _make_dt_edit()
        dr.addWidget(self._group_from)
        dr.addWidget(QLabel(_("To:")))
        self._group_to = _make_dt_edit()
        dr.addWidget(self._group_to)
        dr.addWidget(QLabel("(UTC)"))
        dr.addWidget(QLabel(_("Min El:")))
        self._group_min_el = QSpinBox()
        self._group_min_el.setRange(0, 90)
        self._group_min_el.setValue(5)
        self._group_min_el.setSuffix("°")
        self._group_min_el.setFixedWidth(60)
        dr.addWidget(self._group_min_el)
        dr.addStretch()
        vbox.addLayout(dr)

        # クイックボタン + Search + Cancel 行
        qr = QHBoxLayout()
        for label, hours in (("+ 6h", 6), ("+24h", 24), ("+ 3d", 72), ("+ 7d", 168), ("+30d", 720)):
            btn = QPushButton(label)
            btn.setFixedWidth(46)
            btn.clicked.connect(lambda _c=False, h=hours: self._on_group_quick(h))
            qr.addWidget(btn)
        qr.addStretch()
        self._group_search_btn = QPushButton(_("Search"))
        self._group_search_btn.clicked.connect(self._on_group_search)
        qr.addWidget(self._group_search_btn)
        self._group_cancel_btn = QPushButton(_("Cancel"))
        self._group_cancel_btn.clicked.connect(self._on_group_cancel)
        self._group_cancel_btn.setEnabled(False)
        qr.addWidget(self._group_cancel_btn)
        vbox.addLayout(qr)

        # プログレスバー
        self._group_progress = QProgressBar()
        self._group_progress.setRange(0, 100)
        self._group_progress.setVisible(False)
        vbox.addWidget(self._group_progress)

        # テーブル
        self._group_table = QTableWidget(0, len(self._GROUP_COLS))
        self._group_table.setHorizontalHeaderLabels(list(self._GROUP_COLS))
        self._group_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._group_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._group_table.horizontalHeader().setStretchLastSection(True)
        self._group_table.verticalHeader().setVisible(False)
        self._group_table.cellClicked.connect(self._on_group_cell_clicked)
        self._group_table.itemSelectionChanged.connect(self._on_group_selection_changed)
        vbox.addWidget(self._group_table)

        # ページネーション + CSV エクスポート行
        pr = QHBoxLayout()
        self._prev_btn = QPushButton("← " + _("Prev"))
        self._prev_btn.clicked.connect(self._on_prev_page)
        self._page_label = QLabel("Page 1")
        self._next_btn = QPushButton(_("Next") + " →")
        self._next_btn.clicked.connect(self._on_next_page)
        pr.addWidget(self._prev_btn)
        pr.addWidget(self._page_label)
        pr.addWidget(self._next_btn)
        pr.addStretch()
        self._export_btn = QPushButton(_("Export CSV"))
        self._export_btn.clicked.connect(self._on_export_csv)
        pr.addWidget(self._export_btn)
        vbox.addLayout(pr)

        self._reset_group_datetimes()
        return w

    # ------------------------------------------------------------------ #
    # ヘルパー
    # ------------------------------------------------------------------ #

    def _reset_target_datetimes(self) -> None:
        now = datetime.now(UTC)
        self._target_from.setDateTime(_dt_to_qdatetime(now))
        self._target_to.setDateTime(_dt_to_qdatetime(now + timedelta(hours=24)))

    def _reset_group_datetimes(self) -> None:
        now = datetime.now(UTC)
        self._group_from.setDateTime(_dt_to_qdatetime(now))
        self._group_to.setDateTime(_dt_to_qdatetime(now + timedelta(hours=24)))

    def _populate_target_table(self, passes: list[PassInfo]) -> None:
        self._target_table.setRowCount(0)
        for p in passes:
            row = self._target_table.rowCount()
            self._target_table.insertRow(row)
            self._target_table.setItem(row, 0, QTableWidgetItem(p.aos.strftime("%m/%d %H:%M")))
            self._target_table.setItem(row, 1, QTableWidgetItem(f"{p.max_elevation_deg:.1f}°"))
            mins, secs = divmod(int(p.duration_s), 60)
            self._target_table.setItem(row, 2, QTableWidgetItem(f"{mins}m {secs:02d}s"))
            self._target_table.setItem(row, 3, QTableWidgetItem(f"{p.aos_azimuth_deg:.0f}°"))
            self._target_table.setItem(row, 4, QTableWidgetItem(f"{p.los_azimuth_deg:.0f}°"))
            quality = pass_quality(p.max_elevation_deg)
            q_item = QTableWidgetItem(quality)
            q_item.setForeground(QUALITY_COLORS[quality])
            self._target_table.setItem(row, 5, q_item)

    def _refresh_group_page(self) -> None:
        self._group_table.setRowCount(0)
        start = self._group_page * self._PAGE_SIZE
        end = start + self._PAGE_SIZE
        for r in self._group_results[start:end]:
            row = self._group_table.rowCount()
            self._group_table.insertRow(row)
            sat_item = QTableWidgetItem(r.sat_name)
            sat_item.setData(Qt.ItemDataRole.UserRole, r.norad_cat_id)
            self._group_table.setItem(row, 0, sat_item)
            p = r.pass_info
            self._group_table.setItem(row, 1, QTableWidgetItem(p.aos.strftime("%m/%d %H:%M")))
            self._group_table.setItem(row, 2, QTableWidgetItem(f"{p.max_elevation_deg:.1f}°"))
            mins, secs = divmod(int(p.duration_s), 60)
            self._group_table.setItem(row, 3, QTableWidgetItem(f"{mins}m {secs:02d}s"))
            self._group_table.setItem(row, 4, QTableWidgetItem(f"{p.aos_azimuth_deg:.0f}°"))
            quality = pass_quality(p.max_elevation_deg)
            q_item = QTableWidgetItem(quality)
            q_item.setForeground(QUALITY_COLORS[quality])
            self._group_table.setItem(row, 5, q_item)
        total_pages = max(1, (len(self._group_results) + self._PAGE_SIZE - 1) // self._PAGE_SIZE)
        self._page_label.setText(
            f"Page {self._group_page + 1}/{total_pages}  ({len(self._group_results)} passes)"
        )
        self._prev_btn.setEnabled(self._group_page > 0)
        self._next_btn.setEnabled(end < len(self._group_results))

    # ------------------------------------------------------------------ #
    # コールバック — Target タブ
    # ------------------------------------------------------------------ #

    def _on_target_quick(self, hours: int) -> None:
        start = _qdatetime_to_dt(self._target_from.dateTime())
        self._target_to.setDateTime(_dt_to_qdatetime(start + timedelta(hours=hours)))

    def _on_target_search(self) -> None:
        start = _qdatetime_to_dt(self._target_from.dateTime())
        end = _qdatetime_to_dt(self._target_to.dateTime())
        self.target_search_requested.emit(start, end)

    def _on_target_selection_changed(self) -> None:
        selected = self._target_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        if 0 <= row < len(self._passes):
            self.pass_selected.emit(self._passes[row])

    # ------------------------------------------------------------------ #
    # コールバック — Group タブ
    # ------------------------------------------------------------------ #

    def _on_group_quick(self, hours: int) -> None:
        start = _qdatetime_to_dt(self._group_from.dateTime())
        self._group_to.setDateTime(_dt_to_qdatetime(start + timedelta(hours=hours)))

    def _on_group_search(self) -> None:
        if self._predictor is None or not self._sat_list:
            QMessageBox.information(
                self, _("Group Search"), _("No satellites or predictor available.")
            )
            return
        start = _qdatetime_to_dt(self._group_from.dateTime())
        end = _qdatetime_to_dt(self._group_to.dateTime())
        min_el = float(self._group_min_el.value())
        norads: tuple[int, ...] = tuple(n for n, _ in self._sat_list)
        key: _CacheKey = (start, end, min_el, norads)
        if key == self._cache_key:
            self._group_results = self._cache_results
            self._group_page = 0
            self._refresh_group_page()
            return
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait()
        self._group_results = []
        self._group_page = 0
        self._group_table.setRowCount(0)
        self._group_progress.setValue(0)
        self._group_progress.setVisible(True)
        self._group_search_btn.setEnabled(False)
        self._group_cancel_btn.setEnabled(True)
        self._pending_cache_key = key
        self._worker = _GroupSearchWorker(
            self._predictor, self._sat_list, start, end, min_el, self
        )
        self._worker.progress.connect(self._on_group_progress)
        self._worker.finished_results.connect(self._on_group_results)
        self._worker.start()

    def _on_group_cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
        self._group_progress.setVisible(False)
        self._group_search_btn.setEnabled(True)
        self._group_cancel_btn.setEnabled(False)

    def _on_group_progress(self, pct: int) -> None:
        self._group_progress.setValue(pct)

    def _on_group_results(self, results: object) -> None:
        result_list: list[GroupPassResult] = results  # type: ignore[assignment]
        self._group_results = result_list
        if self._pending_cache_key is not None:
            self._cache_key = self._pending_cache_key
            self._cache_results = result_list
        self._group_page = 0
        self._group_progress.setVisible(False)
        self._group_search_btn.setEnabled(True)
        self._group_cancel_btn.setEnabled(False)
        self._refresh_group_page()

    def _on_group_cell_clicked(self, row: int, col: int) -> None:
        if col == 0:
            item = self._group_table.item(row, 0)
            if item is not None:
                norad = item.data(Qt.ItemDataRole.UserRole)
                if norad is not None:
                    self.highlight_satellite.emit(int(norad))

    def _on_group_selection_changed(self) -> None:
        selected = self._group_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        idx = self._group_page * self._PAGE_SIZE + row
        if 0 <= idx < len(self._group_results):
            self.pass_selected.emit(self._group_results[idx].pass_info)

    def _on_prev_page(self) -> None:
        if self._group_page > 0:
            self._group_page -= 1
            self._refresh_group_page()

    def _on_next_page(self) -> None:
        if (self._group_page + 1) * self._PAGE_SIZE < len(self._group_results):
            self._group_page += 1
            self._refresh_group_page()

    def _on_export_csv(self) -> None:
        if not self._group_results:
            QMessageBox.information(self, _("Export CSV"), _("No results to export."))
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, _("Export CSV"), "", "CSV Files (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(
                    ["Satellite", "NORAD", "AOS (UTC)", "Max El (deg)",
                     "Duration", "AZ In (deg)", "AZ Out (deg)", "Quality"]
                )
                for r in self._group_results:
                    p = r.pass_info
                    mins, secs = divmod(int(p.duration_s), 60)
                    writer.writerow([
                        r.sat_name,
                        r.norad_cat_id,
                        p.aos.strftime("%Y-%m-%d %H:%M:%S"),
                        f"{p.max_elevation_deg:.1f}",
                        f"{mins}m {secs:02d}s",
                        f"{p.aos_azimuth_deg:.0f}",
                        f"{p.los_azimuth_deg:.0f}",
                        pass_quality(p.max_elevation_deg),
                    ])
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, _("Export Error"), str(exc))

    # ------------------------------------------------------------------ #
    # 公開 API
    # ------------------------------------------------------------------ #

    def set_passes(self, passes: list[PassInfo]) -> None:
        """Target タブのパス一覧を設定する（外部から直接指定する場合）。"""
        self._passes = passes
        self._populate_target_table(passes)

    def clear(self) -> None:
        """Target タブのパス一覧をクリアする。"""
        self._passes = []
        self._target_table.setRowCount(0)

    def set_pass_predictor(self, predictor: PassPredictor | None) -> None:
        """Group タブの検索に使用するパス予測器を設定する。"""
        self._predictor = predictor

    def set_satellites(self, sat_list: list[tuple[int, str]]) -> None:
        """Group タブの検索対象衛星リストを設定する。フィルター変更時に呼ぶ。"""
        self._sat_list = sat_list
        # 衛星リストが変わったのでキャッシュを無効化する
        self._cache_key = None
        self._cache_results = []

    # mypy が ANN メソッドを検出するため Any を明示的に使用する箇所
    @staticmethod
    def _noop(*_args: Any) -> None:  # noqa: ANN401
        pass
