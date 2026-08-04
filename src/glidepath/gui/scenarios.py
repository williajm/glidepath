"""The scenario manager widgets (§4.7, roadmap 8.5).

Scenario list, per-scenario override table (a scenario's overrides
*are* its diff, planning §4.3), and the comparison report as a table
and grouped bar chart. Thin by policy: every heading, prompt, status
line, and pre-formatted cell comes from the app layer's
:class:`~glidepath.app.ScenariosViewModel`; this pane binds them to
widgets and forwards raw user input back through its callbacks.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QChart,
    QChartView,
    QValueAxis,
)
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from glidepath.gui.charts import tooltip_bar_set
from glidepath.gui.tableview import fill_table, read_only_table

if TYPE_CHECKING:
    from collections.abc import Callable

    from glidepath.app import ChartSpec, OverrideRow, ScenarioItem, ScenariosViewModel

_CATEGORY_LABEL_ANGLE = -90


@dataclass(frozen=True)
class ScenariosPaneCallbacks:
    """The shell handlers a :class:`ScenariosPane` forwards actions to.

    Handlers returning ``str | None`` report a rejection message for
    the status line; the rest cannot fail.
    """

    add_scenario: Callable[[str], str | None]
    remove_scenario: Callable[[str], None]
    set_override: Callable[[str, str, str], str | None]
    remove_override: Callable[[str, str], None]
    select_basis: Callable[[str], None]
    select_metric: Callable[[str], None]


class ScenariosPane(QWidget):
    """The scenarios tab: manager, override diff list, and comparison."""

    def __init__(
        self, callbacks: ScenariosPaneCallbacks, parent: QWidget | None = None
    ) -> None:
        """Build the manager and comparison surfaces and wire callbacks."""
        super().__init__(parent)
        self._callbacks = callbacks
        self._view_model: ScenariosViewModel | None = None
        self._basis_buttons: dict[str, QRadioButton] = {}
        self._override_rows: tuple[OverrideRow, ...] = ()

        manager_row = QHBoxLayout()
        manager_row.addWidget(self._built_scenarios_box(), 1)
        manager_row.addWidget(self._built_overrides_box(), 2)

        self.message_label = QLabel("", self)
        self.message_label.setWordWrap(True)
        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addLayout(manager_row, 1)
        layout.addWidget(self._built_comparison_box(), 1)
        layout.addWidget(self.message_label)
        layout.addWidget(self.status_label)

    def _built_scenarios_box(self) -> QGroupBox:
        """The scenario list with its add/remove buttons."""
        self._scenarios_box = QGroupBox(self)
        self.scenario_list = QListWidget(self._scenarios_box)
        self.scenario_list.currentTextChanged.connect(self._render_selected_scenario)
        self.add_scenario_button = QPushButton("", self._scenarios_box)
        self.add_scenario_button.clicked.connect(self._add_scenario)
        self.remove_scenario_button = QPushButton("", self._scenarios_box)
        self.remove_scenario_button.clicked.connect(self._remove_scenario)
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_scenario_button)
        buttons.addWidget(self.remove_scenario_button)
        layout = QVBoxLayout(self._scenarios_box)
        layout.addWidget(self.scenario_list)
        layout.addLayout(buttons)
        return self._scenarios_box

    def _built_overrides_box(self) -> QGroupBox:
        """The selected scenario's override table and its buttons."""
        self._overrides_box = QGroupBox(self)
        self.scenario_status_label = QLabel("", self._overrides_box)
        self.scenario_status_label.setWordWrap(True)
        self.overrides_table = read_only_table(self._overrides_box)
        self.overrides_table.cellDoubleClicked.connect(self._edit_override)
        self.add_override_button = QPushButton("", self._overrides_box)
        self.add_override_button.clicked.connect(self._add_override)
        self.remove_override_button = QPushButton("", self._overrides_box)
        self.remove_override_button.clicked.connect(self._remove_override)
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_override_button)
        buttons.addWidget(self.remove_override_button)
        layout = QVBoxLayout(self._overrides_box)
        layout.addWidget(self.scenario_status_label)
        layout.addWidget(self.overrides_table)
        layout.addLayout(buttons)
        return self._overrides_box

    def _built_comparison_box(self) -> QGroupBox:
        """The comparison report: basis toggle, metric picker, table, chart."""
        self._comparison_box = QGroupBox(self)
        self._basis_box = QGroupBox(self._comparison_box)
        self._basis_layout = QHBoxLayout(self._basis_box)
        self.metric_label = QLabel("", self._comparison_box)
        self.metric_combo = QComboBox(self._comparison_box)
        self.metric_combo.activated.connect(self._metric_selected)
        controls = QHBoxLayout()
        controls.addWidget(self._basis_box)
        controls.addWidget(self.metric_label)
        controls.addWidget(self.metric_combo, 1)
        self.comparison_tabs = QTabWidget(self._comparison_box)
        self.comparison_table = read_only_table(self.comparison_tabs)
        self._chart_container = QWidget(self.comparison_tabs)
        self._chart_layout = QVBoxLayout(self._chart_container)
        self.comparison_tabs.addTab(self.comparison_table, "")
        self.comparison_tabs.addTab(self._chart_container, "")
        layout = QVBoxLayout(self._comparison_box)
        layout.addLayout(controls)
        layout.addWidget(self.comparison_tabs, 1)
        return self._comparison_box

    def refresh(self, view_model: ScenariosViewModel) -> None:
        """Re-render the manager and comparison from a fresh view model.

        The selected scenario and comparison sub-tab survive the
        rebuild, so edits keep the user where they were.
        """
        self._view_model = view_model
        self._refresh_manager(view_model)
        self._refresh_comparison(view_model)
        self.message_label.setText(view_model.message)
        self.message_label.setVisible(bool(view_model.message))

    def selected_scenario_name(self) -> str:
        """The name of the scenario selected in the list, or blank."""
        item = self.scenario_list.currentItem()
        return item.text() if item is not None else ""

    def _refresh_manager(self, view_model: ScenariosViewModel) -> None:
        """Re-render the scenario list and the selected override table."""
        self._scenarios_box.setTitle(view_model.scenarios_heading)
        self.add_scenario_button.setText(view_model.add_scenario_label)
        self.remove_scenario_button.setText(view_model.remove_scenario_label)
        self._overrides_box.setTitle(view_model.overrides_heading)
        self.add_override_button.setText(view_model.add_override_label)
        self.remove_override_button.setText(view_model.remove_override_label)
        selected = self.selected_scenario_name()
        with QSignalBlocker(self.scenario_list):
            self.scenario_list.clear()
            self.scenario_list.addItems([item.name for item in view_model.scenarios])
            names = [item.name for item in view_model.scenarios]
            if names:
                row = names.index(selected) if selected in names else 0
                self.scenario_list.setCurrentRow(row)
        self._render_selected_scenario()

    def _selected_item(self) -> ScenarioItem | None:
        """The view-model entry behind the selected scenario, if any."""
        if self._view_model is None:
            return None
        name = self.selected_scenario_name()
        for item in self._view_model.scenarios:
            if item.name == name:
                return item
        return None

    def _render_selected_scenario(self, _text: str = "") -> None:
        """Fill the override table for the scenario selected in the list."""
        if self._view_model is None:
            return
        item = self._selected_item()
        self._override_rows = item.overrides if item is not None else ()
        self.scenario_status_label.setText(item.status if item is not None else "")
        fill_table(
            self.overrides_table,
            self._view_model.overrides_columns,
            [
                (row.target_label, row.value, row.note, row.status)
                for row in self._override_rows
            ],
        )

    def _refresh_comparison(self, view_model: ScenariosViewModel) -> None:
        """Re-render the basis toggle, metric picker, table, and chart."""
        self._comparison_box.setTitle(view_model.comparison_heading)
        self._basis_box.setTitle(view_model.basis_heading)
        self._sync_basis_buttons(view_model)
        self._sync_metric_combo(view_model)
        self.comparison_tabs.setTabText(0, view_model.comparison_table_label)
        self.comparison_tabs.setTabText(1, view_model.comparison_chart_label)
        fill_table(
            self.comparison_table,
            view_model.comparison_columns,
            list(view_model.comparison_rows),
        )
        while self._chart_layout.count():
            entry = self._chart_layout.takeAt(0)
            widget = entry.widget() if entry is not None else None
            if widget is not None:
                widget.deleteLater()
        if view_model.chart is not None:
            self._chart_layout.addWidget(
                self._chart_view(view_model.chart, view_model.chart_categories)
            )
        self.comparison_tabs.setVisible(view_model.chart is not None)

    def _sync_basis_buttons(self, view_model: ScenariosViewModel) -> None:
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

    def _sync_metric_combo(self, view_model: ScenariosViewModel) -> None:
        """Rebuild the metric picker; keep the selection bound."""
        self.metric_label.setText(view_model.metric_heading)
        with QSignalBlocker(self.metric_combo):
            self.metric_combo.clear()
            for option in view_model.metric_options:
                self.metric_combo.addItem(option.label, userData=option.key)
            keys = [option.key for option in view_model.metric_options]
            if view_model.selected_metric_key in keys:
                self.metric_combo.setCurrentIndex(
                    keys.index(view_model.selected_metric_key)
                )

    def _metric_selected(self, index: int) -> None:
        """Forward a user-chosen metric's key to the shell."""
        key = self.metric_combo.itemData(index)
        if key is not None:
            self._callbacks.select_metric(str(key))

    def _add_scenario(self) -> None:
        """Prompt for a scenario name and forward it to the shell."""
        if self._view_model is None:  # pragma: no cover - buttons imply a model
            return
        text, accepted = QInputDialog.getText(
            self, self._view_model.name_prompt_title, self._view_model.name_prompt
        )
        if not accepted:
            return
        error = self._callbacks.add_scenario(text)
        self.status_label.setText(error or "")

    def _remove_scenario(self) -> None:
        """Remove the selected scenario."""
        name = self.selected_scenario_name()
        if name:
            self._callbacks.remove_scenario(name)
            self.status_label.setText("")

    def _add_override(self) -> None:
        """Pick a target from the whitelist, prompt for a value, forward."""
        view_model = self._view_model
        scenario = self.selected_scenario_name()
        if view_model is None or not scenario or not view_model.target_options:
            return
        labels = [option.label for option in view_model.target_options]
        label, accepted = QInputDialog.getItem(
            self,
            view_model.target_prompt_title,
            view_model.target_prompt,
            labels,
            0,
            editable=False,
        )
        if not accepted:
            return
        option = view_model.target_options[labels.index(label)]
        text, accepted = self._value_prompt(
            option.edit_text, structured=option.structured, choices=option.choices
        )
        if not accepted:
            return
        error = self._callbacks.set_override(scenario, option.key, text)
        self.status_label.setText(error or "")

    def _edit_override(self, row_index: int, _column: int) -> None:
        """Re-prompt the value of a double-clicked override row."""
        scenario = self.selected_scenario_name()
        if not scenario or row_index >= len(self._override_rows):
            return
        row = self._override_rows[row_index]
        text, accepted = self._value_prompt(
            row.edit_text, structured=row.structured, choices=row.choices
        )
        if not accepted:
            return
        error = self._callbacks.set_override(scenario, row.target_key, text)
        self.status_label.setText(error or "")

    def _remove_override(self) -> None:
        """Remove the override behind the selected table row."""
        scenario = self.selected_scenario_name()
        row_index = self.overrides_table.currentRow()
        if not scenario or not 0 <= row_index < len(self._override_rows):
            return
        row = self._override_rows[row_index]
        self._callbacks.remove_override(scenario, row.target_key)
        self.status_label.setText("")

    def _value_prompt(
        self, edit_text: str, *, structured: bool, choices: tuple[str, ...]
    ) -> tuple[str, bool]:
        """The value dialog a target's shape calls for; text and accepted."""
        view_model = self._view_model
        if view_model is None:  # pragma: no cover - prompts imply a model
            return "", False
        if choices:
            current = choices.index(edit_text) if edit_text in choices else 0
            return QInputDialog.getItem(
                self,
                view_model.value_prompt_title,
                view_model.value_prompt,
                list(choices),
                current,
                editable=False,
            )
        if structured:
            return QInputDialog.getMultiLineText(
                self,
                view_model.value_prompt_title,
                view_model.table_value_prompt,
                edit_text,
            )
        return QInputDialog.getText(
            self,
            view_model.value_prompt_title,
            view_model.value_prompt,
            text=edit_text,
        )

    def _chart_view(self, chart: ChartSpec, categories: tuple[str, ...]) -> QChartView:
        """One grouped bar chart bound to a chart spec — runs side by side."""
        series = QBarSeries()
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

        view = QChartView(qchart, self._chart_container)
        view.setRenderHint(QPainter.RenderHint.Antialiasing)
        return view


__all__ = [
    "ScenariosPane",
    "ScenariosPaneCallbacks",
]
