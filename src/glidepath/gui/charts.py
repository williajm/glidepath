"""The projection chart widgets (§4.7, roadmap 8.4).

One sub-tab per chart, each a stacked bar chart bound to the app
layer's :class:`~glidepath.app.ChartsViewModel`; the money-basis
radio toggle forwards the selected option key back. All copy, series
labels, and the real/nominal presentation come from the app layer —
this pane only draws what the view model says (planning §4.7).
"""

from typing import TYPE_CHECKING

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSet,
    QChart,
    QChartView,
    QStackedBarSeries,
    QValueAxis,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from glidepath.app import ChartSpec, ChartsViewModel

_CATEGORY_LABEL_ANGLE = -90


class ChartsPane(QWidget):
    """The charts tab: basis toggle, empty-state message, chart sub-tabs."""

    def __init__(
        self,
        on_basis_selected: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        """Build the pane and wire the basis-toggle callback."""
        super().__init__(parent)
        self._on_basis_selected = on_basis_selected
        self._basis_buttons: dict[str, QRadioButton] = {}

        self._basis_box = QGroupBox(self)
        self._basis_layout = QHBoxLayout(self._basis_box)

        self.message_label = QLabel("", self)
        self.message_label.setWordWrap(True)

        self.chart_tabs = QTabWidget(self)

        layout = QVBoxLayout(self)
        layout.addWidget(self._basis_box)
        layout.addWidget(self.message_label)
        layout.addWidget(self.chart_tabs, 1)

    def refresh(self, view_model: ChartsViewModel) -> None:
        """Re-render the toggle, message, and charts from the view model.

        The selected sub-tab survives the rebuild, so toggling the
        basis re-presents the chart the user is looking at.
        """
        self._basis_box.setTitle(view_model.basis_heading)
        self._sync_basis_buttons(view_model)
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

    def _sync_basis_buttons(self, view_model: ChartsViewModel) -> None:
        """Create the basis radio buttons once; keep the selection bound."""
        for option in view_model.basis_options:
            button = self._basis_buttons.get(option.key)
            if button is None:
                button = QRadioButton(option.label, self._basis_box)
                button.clicked.connect(
                    lambda _checked=False, key=option.key: self._on_basis_selected(key)
                )
                self._basis_layout.addWidget(button)
                self._basis_buttons[option.key] = button
            button.setText(option.label)
            button.setChecked(option.key == view_model.selected_basis_key)

    def _chart_view(self, chart: ChartSpec, categories: tuple[str, ...]) -> QChartView:
        """One stacked bar chart bound to a chart spec."""
        series = QStackedBarSeries()
        for entry in chart.series:
            bar_set = QBarSet(entry.label)
            for value in entry.values:
                bar_set.append(float(value))
            series.append(bar_set)

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

        view = QChartView(qchart, self.chart_tabs)
        view.setRenderHint(QPainter.RenderHint.Antialiasing)
        return view


__all__ = [
    "ChartsPane",
]
