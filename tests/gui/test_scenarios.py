"""Scenario manager widget tests, offscreen (issue 8.5, §4.7).

The pane is thin by policy, so these tests only check the bindings:
the scenario list, override table, and comparison render from the
view model; dialogs forward raw text and stable keys back through the
callbacks; and the main-window flow wires it all to the app layer.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from PySide6.QtWidgets import QInputDialog, QRadioButton, QTableWidget

from glidepath.app import (
    PlanState,
    build_scenarios_view_model,
    build_shell_view_model,
    initial_plan_state,
    state_with_household,
    state_with_scenario_added,
    state_with_scenario_override,
)
from glidepath.app.scenarios import NO_SCENARIOS_MESSAGE
from glidepath.core import (
    Decision,
    EntityId,
    Fact,
    Household,
    Money,
    Person,
    SpendingPlan,
    Wrapper,
)
from glidepath.gui.scenarios import ScenariosPane, ScenariosPaneCallbacks
from glidepath.gui.widgets import MainWindow
from glidepath.regions.uk import ISA_KIND, RUK_RESIDENCY

if TYPE_CHECKING:
    from collections.abc import Callable

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)

PERSON_ID = EntityId("scen-gui-person")
RETIREMENT_KEY = f"{PERSON_ID}:target_retirement_age"
RETIREMENT_LABEL = "You — target retirement age"
SCENARIO = "Retire at 58"


def pane_callbacks(
    *,
    add_scenario: Callable[[str], str | None] = lambda _name: None,
    remove_scenario: Callable[[str], None] = lambda _name: None,
    set_override: Callable[[str, str, str], str | None] = lambda _s, _k, _v: None,
    remove_override: Callable[[str, str], None] = lambda _s, _k: None,
    select_basis: Callable[[str], None] = lambda _key: None,
    select_metric: Callable[[str], None] = lambda _key: None,
) -> ScenariosPaneCallbacks:
    """Callbacks defaulting to no-ops, overridable per test."""
    return ScenariosPaneCallbacks(
        add_scenario=add_scenario,
        remove_scenario=remove_scenario,
        set_override=set_override,
        remove_override=remove_override,
        select_basis=select_basis,
        select_metric=select_metric,
    )


def projected_state() -> PlanState:
    """One projected ISA saver, ready for scenarios."""
    isa = Wrapper(
        id=EntityId("scen-gui-isa"),
        kind=ISA_KIND,
        balance=Fact(value=Money(Decimal(25000)), as_of=AS_OF, recorded_on=RECORDED),
    )
    person = Person(
        id=PERSON_ID,
        date_of_birth=Fact(value=date(1991, 2, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        wrappers=(isa,),
    )
    plan = Household(
        persons=(person,),
        spending=SpendingPlan(
            annual_spending_real=Fact(
                value=Money(Decimal(18000)), as_of=AS_OF, recorded_on=RECORDED
            )
        ),
    )
    return state_with_household(initial_plan_state(), plan, today=TODAY)


def scenario_state(*, with_override: bool = False) -> PlanState:
    """A projected state carrying one scenario, optionally overridden."""
    state = state_with_scenario_added(projected_state(), SCENARIO, today=TODAY).state
    if with_override:
        state = state_with_scenario_override(
            state, SCENARIO, RETIREMENT_KEY, "58", today=TODAY
        ).state
    return state


def patch_text_dialog(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Make every text prompt return ``value`` as accepted."""
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *_args, **_kwargs: (value, True)),
    )


def patch_item_dialog(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Make every pick-an-item prompt return ``value`` as accepted."""
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        staticmethod(lambda *_args, **_kwargs: (value, True)),
    )


def cell_text(table: QTableWidget, row: int, column: int) -> str:
    """One populated cell's text."""
    item = table.item(row, column)
    assert item is not None
    return item.text()


class TestScenariosPane:
    """The pane binds the view model and forwards raw input."""

    def test_no_plan_shows_only_the_message(self) -> None:
        """Without a plan the screen carries just the empty-state copy."""
        pane = ScenariosPane(pane_callbacks())
        view_model = build_scenarios_view_model(initial_plan_state())
        pane.refresh(view_model)
        assert pane.message_label.text() == view_model.message
        assert not pane.message_label.isHidden()
        assert pane.comparison_tabs.isHidden()
        assert pane.scenario_list.count() == 0

    def test_scenario_and_comparison_bind_to_widgets(self) -> None:
        """The list, override table, and comparison render the model."""
        pane = ScenariosPane(pane_callbacks())
        view_model = build_scenarios_view_model(scenario_state(with_override=True))
        pane.refresh(view_model)
        assert pane.message_label.isHidden()
        assert not pane.comparison_tabs.isHidden()
        assert pane.scenario_list.count() == 1
        assert pane.selected_scenario_name() == SCENARIO
        assert pane.overrides_table.rowCount() == 1
        assert cell_text(pane.overrides_table, 0, 0) == RETIREMENT_LABEL
        assert pane.comparison_table.columnCount() == 4
        assert pane.metric_combo.count() == len(view_model.metric_options)
        assert pane.scenario_status_label.text() == view_model.scenarios[0].status

    def test_refresh_replaces_rather_than_accumulates(self) -> None:
        """A second refresh rebuilds the surfaces in place."""
        pane = ScenariosPane(pane_callbacks())
        view_model = build_scenarios_view_model(scenario_state())
        pane.refresh(view_model)
        pane.refresh(view_model)
        assert pane.scenario_list.count() == 1
        assert pane._chart_layout.count() == 1  # noqa: SLF001 - chart rebuild check

    def test_basis_radio_forwards_the_option_key(self) -> None:
        """Clicking a basis radio reports its key to the shell handler."""
        selected: list[str] = []
        pane = ScenariosPane(pane_callbacks(select_basis=selected.append))
        view_model = build_scenarios_view_model(scenario_state())
        pane.refresh(view_model)
        buttons = {button.text(): button for button in pane.findChildren(QRadioButton)}
        labels = {option.key: option.label for option in view_model.basis_options}
        assert buttons[labels["real"]].isChecked()
        buttons[labels["nominal"]].click()
        assert selected == ["nominal"]

    def test_metric_pick_forwards_the_option_key(self) -> None:
        """Choosing a metric reports its stable key, not its label."""
        selected: list[str] = []
        pane = ScenariosPane(pane_callbacks(select_metric=selected.append))
        view_model = build_scenarios_view_model(scenario_state())
        pane.refresh(view_model)
        pane.metric_combo.setCurrentIndex(3)
        pane.metric_combo.activated.emit(3)
        assert selected == [view_model.metric_options[3].key]

    def test_add_scenario_prompts_and_reports_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The name prompt forwards raw text; a rejection hits the status."""
        recorded: list[str] = []

        def rejecting(name: str) -> str:
            recorded.append(name)
            return "that name is taken"

        pane = ScenariosPane(pane_callbacks(add_scenario=rejecting))
        pane.refresh(build_scenarios_view_model(projected_state()))
        patch_text_dialog(monkeypatch, "What if")
        pane.add_scenario_button.click()
        assert recorded == ["What if"]
        assert pane.status_label.text() == "that name is taken"

    def test_add_override_forwards_scenario_key_and_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Picking a target and value forwards the stable key and raw text."""
        recorded: list[tuple[str, str, str]] = []

        def record(scenario: str, key: str, value: str) -> None:
            recorded.append((scenario, key, value))

        pane = ScenariosPane(pane_callbacks(set_override=record))
        pane.refresh(build_scenarios_view_model(scenario_state()))
        patch_item_dialog(monkeypatch, RETIREMENT_LABEL)
        patch_text_dialog(monkeypatch, "58")
        pane.add_override_button.click()
        assert recorded == [(SCENARIO, RETIREMENT_KEY, "58")]

    def test_double_click_re_prompts_the_override_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Editing a row re-opens the value prompt for its target."""
        recorded: list[tuple[str, str, str]] = []

        def record(scenario: str, key: str, value: str) -> None:
            recorded.append((scenario, key, value))

        pane = ScenariosPane(pane_callbacks(set_override=record))
        pane.refresh(build_scenarios_view_model(scenario_state(with_override=True)))
        patch_text_dialog(monkeypatch, "59")
        pane._edit_override(0, 0)  # noqa: SLF001 - the double-click slot
        assert recorded == [(SCENARIO, RETIREMENT_KEY, "59")]

    def test_remove_buttons_forward_the_selection(self) -> None:
        """Row and list selections drive the remove callbacks."""
        removed_overrides: list[tuple[str, str]] = []
        removed_scenarios: list[str] = []

        def record_override(scenario: str, key: str) -> None:
            removed_overrides.append((scenario, key))

        pane = ScenariosPane(
            pane_callbacks(
                remove_scenario=removed_scenarios.append,
                remove_override=record_override,
            )
        )
        pane.refresh(build_scenarios_view_model(scenario_state(with_override=True)))
        pane.overrides_table.setCurrentCell(0, 0)
        pane.remove_override_button.click()
        pane.remove_scenario_button.click()
        assert removed_overrides == [(SCENARIO, RETIREMENT_KEY)]
        assert removed_scenarios == [SCENARIO]


class TestMainWindowScenariosFlow:
    """Scenario edits flow through the app layer to the comparison."""

    @pytest.fixture(name="window")
    def window_fixture(self) -> MainWindow:
        """A window with a minimal projectable plan already saved."""
        window = MainWindow(build_shell_view_model())
        facts = window.facts_pane
        facts.person_form.set_value("date_of_birth", "1991-02-01")
        facts.person_form.set_value("tax_residency", str(RUK_RESIDENCY))
        facts.person_form.set_value("target_retirement_age", "60")
        facts.spending_form.set_value("annual_spending_real", "18000")
        wrapper_form = facts.wrappers.add_entry()
        wrapper_form.set_value("kind", str(ISA_KIND))
        wrapper_form.set_value("balance", "25000")
        facts.submit_button.click()
        return window

    def test_saved_facts_prompt_for_a_first_scenario(self, window: MainWindow) -> None:
        """With a plan but no scenarios the tab explains what to do."""
        pane = window.scenarios_pane
        assert pane.message_label.text() == NO_SCENARIOS_MESSAGE
        assert pane.comparison_tabs.isHidden()

    def test_adding_a_scenario_and_override_builds_the_diff(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The full flow: add, override, compare — all via the app layer."""
        pane = window.scenarios_pane
        patch_text_dialog(monkeypatch, SCENARIO)
        pane.add_scenario_button.click()
        assert pane.scenario_list.count() == 1
        assert pane.message_label.isHidden()
        patch_item_dialog(monkeypatch, RETIREMENT_LABEL)
        patch_text_dialog(monkeypatch, "58")
        pane.add_override_button.click()
        assert pane.overrides_table.rowCount() == 1
        assert cell_text(pane.overrides_table, 0, 1) == "58"
        headers = []
        for index in range(pane.comparison_table.columnCount()):
            header = pane.comparison_table.horizontalHeaderItem(index)
            assert header is not None
            headers.append(header.text())
        assert headers == ["Period", "Base plan", SCENARIO, f"{SCENARIO} (vs base)"]
        assert pane.comparison_table.rowCount() > 0

    def test_a_rejected_name_lands_on_the_status_line(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Adding the same name twice surfaces the app-layer message."""
        pane = window.scenarios_pane
        patch_text_dialog(monkeypatch, SCENARIO)
        pane.add_scenario_button.click()
        pane.add_scenario_button.click()
        assert pane.status_label.text() == "a scenario with that name already exists"

    def test_removing_the_scenario_restores_the_empty_state(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Removing the last scenario brings the prompt back."""
        pane = window.scenarios_pane
        patch_text_dialog(monkeypatch, SCENARIO)
        pane.add_scenario_button.click()
        pane.remove_scenario_button.click()
        assert pane.scenario_list.count() == 0
        assert pane.message_label.text() == NO_SCENARIOS_MESSAGE
