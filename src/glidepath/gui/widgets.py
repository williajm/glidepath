"""The shell's widgets: disclaimer, main window, and its tabs (§1, §4.7).

The main window owns the immutable app-layer session state and swaps
it through the pure transitions in :mod:`glidepath.app`; widgets only
render view models and forward raw user input back.
"""

from datetime import UTC, date, datetime

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from glidepath.app import (
    DEFAULT_CHART_BASIS,
    DEFAULT_COMPARISON_METRIC_KEY,
    AboutViewModel,
    DisclaimerViewModel,
    FactsFormData,
    ShellViewModel,
    basis_from_key,
    build_charts_view_model,
    build_inspector_view_model,
    build_scenarios_view_model,
    example_facts_form_data,
    facts_saved_message,
    format_form_errors,
    initial_plan_state,
    metric_from_key,
    parse_facts_form,
    state_with_household,
    state_with_override,
    state_with_scenario_added,
    state_with_scenario_override,
    state_without_scenario,
    state_without_scenario_override,
)
from glidepath.gui.charts import ChartsPane
from glidepath.gui.forms import FactsEntryPane
from glidepath.gui.inspector import InspectorPane
from glidepath.gui.scenarios import ScenariosPane, ScenariosPaneCallbacks


def _today() -> date:
    """The user's calendar day, as the run and form defaults use it (§4.8)."""
    return datetime.now(tz=UTC).astimezone().date()


class DisclaimerDialog(QDialog):
    """Modal first-run disclaimer; accepting is required to proceed (§1)."""

    def __init__(
        self, view_model: DisclaimerViewModel, parent: QWidget | None = None
    ) -> None:
        """Bind the disclaimer view model to the dialog."""
        super().__init__(parent)
        self.setWindowTitle(view_model.title)
        self.setModal(True)

        body = QLabel(view_model.body, self)
        body.setWordWrap(True)

        buttons = QDialogButtonBox(self)
        self.accept_button = QPushButton(view_model.accept_label, self)
        self.decline_button = QPushButton(view_model.decline_label, self)
        buttons.addButton(self.accept_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(self.decline_button, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(body)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    """The application shell: facts entry, the inspector, and Help → About."""

    def __init__(self, view_model: ShellViewModel) -> None:
        """Bind the shell view model to the window and start a session."""
        super().__init__()
        self._view_model = view_model
        self._about_view_model = view_model.about
        self._state = initial_plan_state()
        self._charts_basis = DEFAULT_CHART_BASIS
        self._comparison_basis = DEFAULT_CHART_BASIS
        self._comparison_metric = DEFAULT_COMPARISON_METRIC_KEY
        self.setWindowTitle(view_model.window_title)
        self.resize(1120, 780)

        self.facts_pane = FactsEntryPane(
            view_model.facts_form,
            self._handle_facts_submitted,
            self._handle_cleared,
        )
        self.charts_pane = ChartsPane(self._handle_charts_basis)
        self.scenarios_pane = ScenariosPane(
            ScenariosPaneCallbacks(
                add_scenario=self._handle_scenario_added,
                remove_scenario=self._handle_scenario_removed,
                set_override=self._handle_scenario_override,
                remove_override=self._handle_scenario_override_removed,
                select_basis=self._handle_comparison_basis,
                select_metric=self._handle_comparison_metric,
            )
        )
        self.inspector_pane = InspectorPane(self._handle_override)
        tabs = QTabWidget(self)
        tabs.addTab(self.facts_pane, view_model.facts_tab_label)
        tabs.addTab(self.charts_pane, view_model.charts_tab_label)
        tabs.addTab(self.scenarios_pane, view_model.scenarios_tab_label)
        tabs.addTab(self.inspector_pane, view_model.inspector_tab_label)
        self.setCentralWidget(tabs)
        self._load_example()

        # The "&" mnemonic is toolkit mechanics, not copy — the label
        # itself comes from the app layer (§4.7).
        help_menu = self.menuBar().addMenu(f"&{view_model.help_menu_label}")
        about_action = help_menu.addAction(view_model.about.title)
        about_action.triggered.connect(self.show_about)

    def _load_example(self) -> None:
        """Open with the example plan on screen and projected (§4.9).

        The example is raw form text through the normal submission
        path — guaranteed parseable by test — with the status line
        explaining it is an example, not the user's data.
        """
        self.facts_pane.set_form_data(example_facts_form_data())
        self._handle_facts_submitted(self.facts_pane.form_data())
        self.facts_pane.status_label.setText(self._view_model.facts_form.example_note)

    def _handle_cleared(self) -> str:
        """Reset the session to no plan and re-render the result panes."""
        self._state = initial_plan_state()
        self._refresh_result_panes()
        return self._view_model.facts_form.cleared_note

    def _handle_facts_submitted(self, data: FactsFormData) -> str:
        """Parse a facts submission; on success, re-project and refresh."""
        now = datetime.now(tz=UTC)
        # Provenance timestamps stay UTC; "today" is the user's calendar
        # day, which differs from the UTC date around midnight. The form
        # defaults blank as_of dates from the same local day the run
        # uses, so a defaulted balance date is never future-dated (§4.8).
        today = now.astimezone().date()
        result = parse_facts_form(
            data, recorded_on=now, today=today, previous=self._state.household
        )
        if result.household is None:
            return format_form_errors(self._view_model.facts_form, result.errors)
        self._state = state_with_household(self._state, result.household, today=today)
        self._refresh_result_panes()
        return facts_saved_message(self._state)

    def _handle_override(self, key: str, raw_value: str) -> str | None:
        """Apply an in-place assumption override; report a rejection."""
        now = datetime.now(tz=UTC)
        outcome = state_with_override(
            self._state, key, raw_value, recorded_on=now, today=now.astimezone().date()
        )
        if outcome.error is not None:
            return outcome.error
        self._state = outcome.state
        self._refresh_result_panes()
        return None

    def _handle_charts_basis(self, key: str) -> None:
        """Re-present the charts in the basis the user selected."""
        self._charts_basis = basis_from_key(key)
        self.charts_pane.refresh(
            build_charts_view_model(self._state, basis=self._charts_basis)
        )

    def _handle_scenario_added(self, name: str) -> str | None:
        """Add a scenario; report a rejection."""
        outcome = state_with_scenario_added(self._state, name, today=_today())
        if outcome.error is not None:
            return outcome.error
        self._state = outcome.state
        self._refresh_scenarios_pane()
        return None

    def _handle_scenario_removed(self, name: str) -> None:
        """Remove a scenario and re-render the comparison."""
        self._state = state_without_scenario(self._state, name, today=_today())
        self._refresh_scenarios_pane()

    def _handle_scenario_override(
        self, scenario: str, target_key: str, raw_value: str
    ) -> str | None:
        """Set one scenario override; report a rejection."""
        outcome = state_with_scenario_override(
            self._state, scenario, target_key, raw_value, today=_today()
        )
        if outcome.error is not None:
            return outcome.error
        self._state = outcome.state
        self._refresh_scenarios_pane()
        return None

    def _handle_scenario_override_removed(self, scenario: str, target_key: str) -> None:
        """Remove one scenario override and re-render the comparison."""
        self._state = state_without_scenario_override(
            self._state, scenario, target_key, today=_today()
        )
        self._refresh_scenarios_pane()

    def _handle_comparison_basis(self, key: str) -> None:
        """Re-present the comparison in the basis the user selected."""
        self._comparison_basis = basis_from_key(key)
        self._refresh_scenarios_pane()

    def _handle_comparison_metric(self, key: str) -> None:
        """Re-present the comparison on the metric the user selected."""
        self._comparison_metric = metric_from_key(key)
        self._refresh_scenarios_pane()

    def _refresh_scenarios_pane(self) -> None:
        """Re-render the scenario manager and comparison report."""
        self.scenarios_pane.refresh(
            build_scenarios_view_model(
                self._state,
                basis=self._comparison_basis,
                metric_key=self._comparison_metric,
            )
        )

    def _refresh_result_panes(self) -> None:
        """Re-render every pane that reads the session's projection."""
        self.charts_pane.refresh(
            build_charts_view_model(self._state, basis=self._charts_basis)
        )
        self._refresh_scenarios_pane()
        self.inspector_pane.refresh(build_inspector_view_model(self._state))

    def show_about(self) -> None:
        """Show the About box; it repeats the disclaimer (§1)."""
        about = self._about_view_model
        QMessageBox.about(self, about.title, about.body)


def prompt_disclaimer(view_model: DisclaimerViewModel) -> bool:
    """Run the disclaimer dialog modally; True means the user accepted."""
    dialog = DisclaimerDialog(view_model)
    return dialog.exec() == QDialog.DialogCode.Accepted


__all__ = [
    "AboutViewModel",
    "DisclaimerDialog",
    "MainWindow",
    "prompt_disclaimer",
]
