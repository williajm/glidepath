"""The projection chart widgets (§4.7, roadmap 8.4, 9.13).

One sub-tab per chart, each a stacked bar chart bound to the app
layer's :class:`~glidepath.app.ChartsViewModel`; the money-basis
radio toggle and the run-mode control forward their selected option
keys back, and the Monte Carlo run action forwards the raw seed and
path-count text. Monte Carlo percentile bands draw as line series
over the stacked bars. All copy, series labels, and the real/nominal
presentation come from the app layer — this pane only draws what the
view model says (planning §4.7).
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
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QPainter
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
        ChartSeries,
        ChartSpec,
        ChartsViewModel,
        MonteCarloPanelViewModel,
    )

_CATEGORY_LABEL_ANGLE = -90


@dataclass(frozen=True)
class ChartsPaneCallbacks:
    """The shell handlers a :class:`ChartsPane` forwards actions to."""

    select_basis: Callable[[str], None]
    select_mode: Callable[[str], None]
    run_monte_carlo: Callable[[str, str], None]


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

        self.message_label = QLabel("", self)
        self.message_label.setWordWrap(True)

        self.chart_tabs = QTabWidget(self)

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(self._basis_box)
        controls.addWidget(self._monte_carlo_box, 1)
        layout.addLayout(controls)
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
                self._chart_view(chart, view_model.categories), chart.title
            )
        if 0 <= selected_index < self.chart_tabs.count():
            self.chart_tabs.setCurrentIndex(selected_index)

    def _run_clicked(self) -> None:
        """Forward the raw seed and path-count text to the shell."""
        self._callbacks.run_monte_carlo(self.seed_edit.text(), self.paths_edit.text())

    def set_monte_carlo_busy(self, *, busy: bool) -> None:
        """Disable the run action while a Monte Carlo run is in flight.

        The run happens off the GUI thread (the window owns the
        worker); disabling the button prevents a second overlapping
        run from the same inputs.
        """
        self.run_button.setEnabled(not busy)

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

    def _chart_view(self, chart: ChartSpec, categories: tuple[str, ...]) -> QChartView:
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

        view = QChartView(qchart, self.chart_tabs)
        view.setRenderHint(QPainter.RenderHint.Antialiasing)
        return view


__all__ = [
    "ChartsPane",
    "ChartsPaneCallbacks",
    "tooltip_bar_set",
]
