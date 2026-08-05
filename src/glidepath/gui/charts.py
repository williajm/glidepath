"""The projection chart widgets (§4.7, roadmap 8.4, 9.13, 9.14).

One sub-tab per chart, each a stacked bar chart bound to the app
layer's :class:`~glidepath.app.ChartsViewModel`; the money-basis
radio toggle and the run-mode control forward their selected option
keys back, and the Monte Carlo run action forwards the raw seed and
path-count text. Monte Carlo percentile bands draw as line series
over the stacked bars. The "When can I retire?" card (9.14) forwards
the raw replacement-rate and success-target text — plus the Monte
Carlo panel's seed and path text, its basis under that mode — and
renders the answer, detail, and message the view model carries. All
copy, series labels, and the real/nominal presentation come from the
app layer — this pane only draws what the view model says (planning
§4.7).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSet,
    QChart,
    QChartView,
    QLineSeries,
    QStackedBarSeries,
    QValueAxis,
)
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QCursor, QImage, QPainter
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from glidepath.app import bar_tooltip

if TYPE_CHECKING:
    from collections.abc import Callable

    from glidepath.app import (
        BacktestPanelViewModel,
        ChartSeries,
        ChartSpec,
        ChartsViewModel,
        MonteCarloPanelViewModel,
        RetirementPanelViewModel,
    )

_CATEGORY_LABEL_ANGLE = -90

_REPORT_CHART_SIZE = QSize(880, 460)


@dataclass(frozen=True)
class ChartsPaneCallbacks:
    """The shell handlers a :class:`ChartsPane` forwards actions to.

    ``run_retirement`` receives the raw replacement-rate and
    success-target text plus the Monte Carlo panel's raw seed and
    path-count text (its basis under that run mode) — the shell
    parses, the pane only captures (planning §4.7). ``run_backtest``
    carries nothing — the run itself has no inputs (9.18) — and
    ``select_backtest_year`` forwards the starting-year picker's raw
    text, presentation state like the basis and mode selections.
    """

    select_basis: Callable[[str], None]
    select_mode: Callable[[str], None]
    run_monte_carlo: Callable[[str, str], None]
    run_retirement: Callable[[str, str, str, str], None]
    run_backtest: Callable[[], None]
    select_backtest_year: Callable[[str], None]


def _bar_hovered(
    entry: ChartSeries, categories: tuple[str, ...], index: int, *, hovering: bool
) -> None:
    """Show or hide one bar segment's tooltip at the pointer."""
    if hovering and 0 <= index < min(len(entry.values), len(categories)):
        QToolTip.showText(
            QCursor.pos(),
            bar_tooltip(categories[index], entry.label, entry.values[index]),
        )
    else:
        QToolTip.hideText()


def tooltip_bar_set(entry: ChartSeries, categories: tuple[str, ...]) -> QBarSet:
    """One series as a bar set with hover tooltips bound (§4.7).

    The tooltip copy comes from the app layer's exact ``Decimal``
    amounts, not the float plot coordinates.
    """
    bar_set = QBarSet(entry.label)
    for value in entry.values:
        bar_set.append(float(value))
    bar_set.hovered.connect(
        lambda status, index: _bar_hovered(entry, categories, index, hovering=status)
    )
    return bar_set


def chart_view(
    chart: ChartSpec, categories: tuple[str, ...], parent: QWidget | None = None
) -> QChartView:
    """One stacked bar chart, percentile bands overlaid, bound to a spec."""
    series = QStackedBarSeries()
    for entry in chart.series:
        series.append(tooltip_bar_set(entry, categories))

    qchart = QChart()
    qchart.addSeries(series)

    x_axis = QBarCategoryAxis()
    x_axis.append(list(categories))
    x_axis.setLabelsAngle(_CATEGORY_LABEL_ANGLE)
    qchart.addAxis(x_axis, Qt.AlignmentFlag.AlignBottom)
    series.attachAxis(x_axis)

    y_axis = QValueAxis()
    y_axis.setTitleText(chart.y_axis_label)
    y_axis.setRange(0.0, float(chart.y_axis_max))
    y_axis.applyNiceNumbers()
    qchart.addAxis(y_axis, Qt.AlignmentFlag.AlignLeft)
    series.attachAxis(y_axis)

    for band in chart.bands:
        line = QLineSeries()
        line.setName(band.label)
        for index, value in enumerate(band.values):
            line.append(float(index), float(value))
        qchart.addSeries(line)
        line.attachAxis(x_axis)
        line.attachAxis(y_axis)

    view = QChartView(qchart, parent)
    view.setRenderHint(QPainter.RenderHint.Antialiasing)
    return view


def chart_image(chart: ChartSpec, categories: tuple[str, ...]) -> QImage:
    """Rasterise one chart spec for the plan report (roadmap 9.19).

    Renders the same widget the charts tab shows, offscreen at a fixed
    report size, so the printed charts match the on-screen ones.
    """
    view = chart_view(chart, categories)
    view.resize(_REPORT_CHART_SIZE)
    image = QImage(view.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    view.render(painter)
    painter.end()
    view.deleteLater()
    return image


class ChartsPane(QWidget):
    """The charts tab: basis toggle, run-mode control, chart sub-tabs."""

    def __init__(
        self,
        callbacks: ChartsPaneCallbacks,
        parent: QWidget | None = None,
    ) -> None:
        """Build the pane and wire the shell callbacks."""
        super().__init__(parent)
        self._callbacks = callbacks
        self._basis_buttons: dict[str, QRadioButton] = {}
        self._mode_buttons: dict[str, QRadioButton] = {}

        self._basis_box = QGroupBox(self)
        self._basis_layout = QHBoxLayout(self._basis_box)

        self._monte_carlo_box = QGroupBox(self)
        monte_carlo_layout = QVBoxLayout(self._monte_carlo_box)
        self._mode_layout = QHBoxLayout()
        monte_carlo_layout.addLayout(self._mode_layout)
        controls_row = QHBoxLayout()
        self.seed_label = QLabel("", self._monte_carlo_box)
        self.seed_edit = QLineEdit(self._monte_carlo_box)
        self.paths_label = QLabel("", self._monte_carlo_box)
        self.paths_edit = QLineEdit(self._monte_carlo_box)
        self.run_button = QPushButton("", self._monte_carlo_box)
        self.run_button.clicked.connect(self._run_clicked)
        controls_row.addWidget(self.seed_label)
        controls_row.addWidget(self.seed_edit)
        controls_row.addWidget(self.paths_label)
        controls_row.addWidget(self.paths_edit)
        controls_row.addWidget(self.run_button)
        controls_row.addStretch(1)
        monte_carlo_layout.addLayout(controls_row)
        self.metrics_label = QLabel("", self._monte_carlo_box)
        self.metrics_label.setWordWrap(True)
        monte_carlo_layout.addWidget(self.metrics_label)
        self.monte_carlo_message_label = QLabel("", self._monte_carlo_box)
        self.monte_carlo_message_label.setWordWrap(True)
        monte_carlo_layout.addWidget(self.monte_carlo_message_label)

        self._build_retirement_box()
        self._build_backtest_box()
        busy_row = self._build_busy_row()

        self.allocation_label = QLabel("", self)
        self.allocation_label.setWordWrap(True)

        self.message_label = QLabel("", self)
        self.message_label.setWordWrap(True)

        self.chart_tabs = QTabWidget(self)

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(self._basis_box)
        controls.addWidget(self._monte_carlo_box, 1)
        layout.addLayout(controls)
        layout.addWidget(self._retirement_box)
        layout.addWidget(self._backtest_box)
        layout.addLayout(busy_row)
        layout.addWidget(self.allocation_label)
        layout.addWidget(self.message_label)
        layout.addWidget(self.chart_tabs, 1)

    def refresh(self, view_model: ChartsViewModel) -> None:
        """Re-render the controls, message, and charts from the view model.

        The selected sub-tab survives the rebuild, so toggling the
        basis or run mode re-presents the chart the user is looking
        at.
        """
        self._basis_box.setTitle(view_model.basis_heading)
        self._sync_basis_buttons(view_model)
        self._sync_monte_carlo(view_model.monte_carlo)
        self._sync_retirement(view_model.retirement)
        self._sync_backtest(view_model.backtest)
        self.allocation_label.setText(view_model.allocation_note)
        self.allocation_label.setVisible(bool(view_model.allocation_note))
        self.message_label.setText(view_model.message)
        self.message_label.setVisible(bool(view_model.message))
        self.chart_tabs.setVisible(bool(view_model.charts))
        selected_index = self.chart_tabs.currentIndex()
        while self.chart_tabs.count():
            widget = self.chart_tabs.widget(0)
            self.chart_tabs.removeTab(0)
            if widget is not None:
                widget.deleteLater()
        for chart in view_model.charts:
            self.chart_tabs.addTab(
                chart_view(chart, view_model.categories, self.chart_tabs), chart.title
            )
        if 0 <= selected_index < self.chart_tabs.count():
            self.chart_tabs.setCurrentIndex(selected_index)

    def _build_busy_row(self) -> QHBoxLayout:
        """Create the in-flight busy indicator's widgets (roadmap 9.16).

        A zero-range progress bar animates indeterminately until a
        range is set; both widgets start hidden until a run is launched.
        """
        self.busy_bar = QProgressBar(self)
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.setMaximumWidth(160)
        self.busy_label = QLabel("", self)
        self.busy_bar.setVisible(False)
        self.busy_label.setVisible(False)
        busy_row = QHBoxLayout()
        busy_row.addWidget(self.busy_bar)
        busy_row.addWidget(self.busy_label, 1)
        return busy_row

    def _build_retirement_box(self) -> None:
        """Create the "When can I retire?" card's widgets (9.14)."""
        self._retirement_box = QGroupBox(self)
        retirement_layout = QVBoxLayout(self._retirement_box)
        retirement_controls = QHBoxLayout()
        self.rate_label = QLabel("", self._retirement_box)
        self.rate_edit = QLineEdit(self._retirement_box)
        self.success_label = QLabel("", self._retirement_box)
        self.success_edit = QLineEdit(self._retirement_box)
        self.retirement_button = QPushButton("", self._retirement_box)
        self.retirement_button.clicked.connect(self._retirement_clicked)
        retirement_controls.addWidget(self.rate_label)
        retirement_controls.addWidget(self.rate_edit)
        retirement_controls.addWidget(self.success_label)
        retirement_controls.addWidget(self.success_edit)
        retirement_controls.addWidget(self.retirement_button)
        retirement_controls.addStretch(1)
        retirement_layout.addLayout(retirement_controls)
        self.retirement_answer_label = QLabel("", self._retirement_box)
        self.retirement_answer_label.setWordWrap(True)
        retirement_layout.addWidget(self.retirement_answer_label)
        self.retirement_detail_label = QLabel("", self._retirement_box)
        self.retirement_detail_label.setWordWrap(True)
        retirement_layout.addWidget(self.retirement_detail_label)
        self.retirement_message_label = QLabel("", self._retirement_box)
        self.retirement_message_label.setWordWrap(True)
        retirement_layout.addWidget(self.retirement_message_label)

    def _build_backtest_box(self) -> None:
        """Create the historical-backtest card's widgets (9.18)."""
        self._backtest_box = QGroupBox(self)
        backtest_layout = QVBoxLayout(self._backtest_box)
        backtest_controls = QHBoxLayout()
        self.backtest_button = QPushButton("", self._backtest_box)
        self.backtest_button.clicked.connect(self._backtest_clicked)
        backtest_controls.addWidget(self.backtest_button)
        self.backtest_year_label = QLabel("", self._backtest_box)
        self.backtest_year_edit = QLineEdit(self._backtest_box)
        self.backtest_year_edit.setMaximumWidth(80)
        self.backtest_year_edit.editingFinished.connect(self._backtest_year_changed)
        backtest_controls.addWidget(self.backtest_year_label)
        backtest_controls.addWidget(self.backtest_year_edit)
        backtest_controls.addStretch(1)
        backtest_layout.addLayout(backtest_controls)
        self.backtest_year_message_label = QLabel("", self._backtest_box)
        self.backtest_year_message_label.setWordWrap(True)
        backtest_layout.addWidget(self.backtest_year_message_label)
        self.backtest_metrics_label = QLabel("", self._backtest_box)
        self.backtest_metrics_label.setWordWrap(True)
        backtest_layout.addWidget(self.backtest_metrics_label)
        self.backtest_message_label = QLabel("", self._backtest_box)
        self.backtest_message_label.setWordWrap(True)
        backtest_layout.addWidget(self.backtest_message_label)

    def _run_clicked(self) -> None:
        """Forward the raw seed and path-count text to the shell."""
        self._callbacks.run_monte_carlo(self.seed_edit.text(), self.paths_edit.text())

    def _backtest_clicked(self) -> None:
        """Ask the shell to run the historical backtest (9.18)."""
        self._callbacks.run_backtest()

    def _backtest_year_changed(self) -> None:
        """Forward the starting-year picker's raw text to the shell."""
        self._callbacks.select_backtest_year(self.backtest_year_edit.text())

    def _retirement_clicked(self) -> None:
        """Forward the card's raw text to the shell (roadmap 9.14).

        The Monte Carlo panel's seed and path text ride along: they
        are the search's basis when the Monte Carlo mode is selected.
        """
        self._callbacks.run_retirement(
            self.rate_edit.text(),
            self.success_edit.text(),
            self.seed_edit.text(),
            self.paths_edit.text(),
        )

    def set_monte_carlo_busy(self, *, busy: bool) -> None:
        """Disable the run action while a Monte Carlo run is in flight.

        The run happens off the GUI thread (the window owns the
        worker); disabling the button prevents a second overlapping
        run from the same inputs.
        """
        self.run_button.setEnabled(not busy)

    def set_retirement_busy(self, *, busy: bool) -> None:
        """Disable the card's action while a search is in flight (9.14).

        Same rationale as :meth:`set_monte_carlo_busy`: the search
        runs off the GUI thread and must not overlap itself.
        """
        self.retirement_button.setEnabled(not busy)

    def set_backtest_busy(self, *, busy: bool) -> None:
        """Disable the card's action while a backtest is in flight (9.18).

        Same rationale as :meth:`set_monte_carlo_busy`: the windows
        run off the GUI thread and must not overlap themselves.
        """
        self.backtest_button.setEnabled(not busy)

    def show_busy(self, status: str) -> None:
        """Show the busy animation with a status line naming the run (9.16).

        Visible from run start until :meth:`clear_busy` — disabled
        buttons alone are easy to miss, and a minutes-long retirement
        search would otherwise look like a hang. ``status`` comes from
        the app layer like all copy (planning §4.7).
        """
        self.busy_label.setText(status)
        self.busy_bar.setVisible(True)
        self.busy_label.setVisible(True)

    def clear_busy(self) -> None:
        """Hide the busy animation and its status line (9.16).

        Called on completion, failure, and the staleness-discard path
        alike — a discarded run must never leave a spinner running.
        """
        self.busy_bar.setVisible(False)
        self.busy_label.setVisible(False)
        self.busy_label.setText("")

    def _sync_basis_buttons(self, view_model: ChartsViewModel) -> None:
        """Create the basis radio buttons once; keep the selection bound."""
        for option in view_model.basis_options:
            button = self._basis_buttons.get(option.key)
            if button is None:
                button = QRadioButton(option.label, self._basis_box)
                button.clicked.connect(
                    lambda _checked=False, key=option.key: self._callbacks.select_basis(
                        key
                    )
                )
                self._basis_layout.addWidget(button)
                self._basis_buttons[option.key] = button
            button.setText(option.label)
            button.setChecked(option.key == view_model.selected_basis_key)

    def _sync_monte_carlo(self, panel: MonteCarloPanelViewModel) -> None:
        """Re-render the run-mode control and Monte Carlo readout (9.13)."""
        self._monte_carlo_box.setTitle(panel.heading)
        for option in panel.mode_options:
            button = self._mode_buttons.get(option.key)
            if button is None:
                button = QRadioButton(option.label, self._monte_carlo_box)
                button.clicked.connect(
                    lambda _checked=False, key=option.key: self._callbacks.select_mode(
                        key
                    )
                )
                self._mode_layout.addWidget(button)
                self._mode_buttons[option.key] = button
            button.setText(option.label)
            button.setChecked(option.key == panel.selected_mode_key)
        self.seed_label.setText(panel.seed_label)
        self.seed_edit.setText(panel.seed_value)
        self.paths_label.setText(panel.paths_label)
        self.paths_edit.setText(panel.paths_value)
        self.run_button.setText(panel.run_label)
        metrics = "\n".join(f"{row.label}: {row.value}" for row in panel.metrics)
        self.metrics_label.setText(metrics)
        self.monte_carlo_message_label.setText(panel.message)
        for widget in (
            self.seed_label,
            self.seed_edit,
            self.paths_label,
            self.paths_edit,
            self.run_button,
        ):
            widget.setVisible(panel.controls_visible)
        self.metrics_label.setVisible(bool(metrics))
        self.monte_carlo_message_label.setVisible(bool(panel.message))

    def _sync_backtest(self, panel: BacktestPanelViewModel) -> None:
        """Re-render the historical-backtest card (roadmap 9.18)."""
        self._backtest_box.setTitle(panel.heading)
        self.backtest_button.setText(panel.run_label)
        self.backtest_year_label.setText(panel.year_label)
        self.backtest_year_edit.setText(panel.year_value)
        self.backtest_year_edit.setPlaceholderText(panel.year_placeholder)
        self.backtest_year_label.setToolTip(panel.year_tooltip)
        self.backtest_year_edit.setToolTip(panel.year_tooltip)
        self.backtest_year_message_label.setText(panel.year_message)
        self.backtest_year_message_label.setVisible(bool(panel.year_message))
        metrics = "\n".join(f"{row.label}: {row.value}" for row in panel.metrics)
        self.backtest_metrics_label.setText(metrics)
        self.backtest_metrics_label.setVisible(bool(metrics))
        self.backtest_message_label.setText(panel.message)
        self.backtest_message_label.setVisible(bool(panel.message))

    def _sync_retirement(self, panel: RetirementPanelViewModel) -> None:
        """Re-render the "When can I retire?" card (roadmap 9.14)."""
        self._retirement_box.setTitle(panel.heading)
        self.rate_label.setText(panel.rate_label)
        self.rate_edit.setText(panel.rate_value)
        self.success_label.setText(panel.success_label)
        self.success_edit.setText(panel.success_value)
        self.retirement_button.setText(panel.run_label)
        self.success_label.setVisible(panel.success_visible)
        self.success_edit.setVisible(panel.success_visible)
        self.retirement_answer_label.setText(panel.answer)
        self.retirement_answer_label.setVisible(bool(panel.answer))
        self.retirement_detail_label.setText(panel.detail)
        self.retirement_detail_label.setVisible(bool(panel.detail))
        self.retirement_message_label.setText(panel.message)
        self.retirement_message_label.setVisible(bool(panel.message))


__all__ = [
    "ChartsPane",
    "ChartsPaneCallbacks",
    "chart_image",
    "chart_view",
    "tooltip_bar_set",
]
