"""Scenario manager + comparison view model tests (issue 8.5, §4.3, §4.7).

The transitions are pure and message-bearing: adding, removing, and
overriding scenarios re-runs the comparison through the real UK
region; rejections come back as messages, never exceptions. The view
model renders scenarios as their override lists (the deltas *are* the
diff), flags orphans, and presents the per-period comparison as
pre-formatted table cells and grouped chart series.
"""

from dataclasses import fields
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.app import (
    DEFAULT_COMPARISON_METRIC_KEY,
    PlanState,
    build_scenarios_view_model,
    initial_plan_state,
    metric_from_key,
    scenario_target_options,
    state_with_household,
    state_with_scenario_added,
    state_with_scenario_override,
    state_with_scenarios,
    state_without_scenario,
    state_without_scenario_override,
)
from glidepath.app.scenarios import (
    NO_PLAN_MESSAGE,
    NO_SCENARIOS_MESSAGE,
    NO_VALID_SCENARIOS_MESSAGE,
    ORPHANED_OVERRIDE_STATUS,
    SCENARIO_RUN_FAILED_PREFIX,
    VALID_STATUS,
    _delta_text,
    _edit_text,
    _metric_text,
    _parsed_decision_value,
)
from glidepath.core import (
    BASE_RUN_NAME,
    AnnuityType,
    AssumptionKey,
    AssumptionTarget,
    Decision,
    EntityId,
    Fact,
    Household,
    Money,
    PeriodMetrics,
    Person,
    PlannedOutflow,
    ReportBasis,
    ScenarioPeriodEntry,
    SpendingPlan,
    TaxResidencyId,
    Wrapper,
)
from glidepath.regions.uk import ISA_KIND, RUK_RESIDENCY, SCOTLAND_RESIDENCY

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)

PERSON_ID = EntityId("scen-person")
OUTFLOW_ID = EntityId("scen-outflow")

RETIREMENT_KEY = f"{PERSON_ID}:target_retirement_age"
OUTFLOW_KEY = f"{OUTFLOW_ID}:amount_real"
CPI_KEY = AssumptionKey.INFLATION_CPI.value

SCENARIO = "Retire at 58"


def money_fact(amount: str) -> Fact[Money]:
    """A user-stated monetary fact."""
    return Fact(value=Money(Decimal(amount)), as_of=AS_OF, recorded_on=RECORDED)


def household(
    tax_residency: TaxResidencyId = RUK_RESIDENCY, *, with_outflow: bool = True
) -> Household:
    """An ISA saver retiring at 60, with an optional planned outflow."""
    isa = Wrapper(id=EntityId("scen-isa"), kind=ISA_KIND, balance=money_fact("25000"))
    person = Person(
        id=PERSON_ID,
        date_of_birth=Fact(value=date(1991, 2, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=tax_residency,
        employment_income=money_fact("42000"),
        wrappers=(isa,),
    )
    outflows: tuple[PlannedOutflow, ...] = ()
    if with_outflow:
        outflows = (
            PlannedOutflow(
                id=OUTFLOW_ID,
                label="New roof",
                amount_real=Decision(value=Money(Decimal(10000)), recorded_on=RECORDED),
                at_age_of=(PERSON_ID, 62),
            ),
        )
    return Household(
        persons=(person,),
        spending=SpendingPlan(annual_spending_real=money_fact("18000")),
        planned_outflows=outflows,
    )


@pytest.fixture(scope="module", name="projected")
def projected_fixture() -> PlanState:
    """One projected session state, shared across the module."""
    return state_with_household(initial_plan_state(), household(), today=TODAY)


@pytest.fixture(scope="module", name="with_scenario")
def with_scenario_fixture(projected: PlanState) -> PlanState:
    """The projected state carrying one empty scenario."""
    outcome = state_with_scenario_added(projected, SCENARIO, today=TODAY)
    assert outcome.error is None
    return outcome.state


class TestMetricFromKey:
    """Metric keys are validated at the shell boundary."""

    def test_known_key_round_trips(self) -> None:
        """A valid key comes back unchanged."""
        assert metric_from_key("tax_due") == "tax_due"

    def test_unknown_key_is_rejected(self) -> None:
        """An unknown key raises rather than mis-rendering."""
        with pytest.raises(ValueError, match="unknown comparison metric"):
            metric_from_key("closing_ballance")

    def test_metric_keys_match_the_comparison_metrics(self) -> None:
        """Every ``PeriodMetrics`` field is offered, and nothing else."""
        view_model = build_scenarios_view_model(initial_plan_state())
        offered = {option.key for option in view_model.metric_options}
        assert offered == {field.name for field in fields(PeriodMetrics)}


class TestScenarioAddRemove:
    """Scenarios are added and removed by name, with validation."""

    def test_add_requires_a_household(self) -> None:
        """Scenarios need a base plan to diff against."""
        outcome = state_with_scenario_added(initial_plan_state(), "x", today=TODAY)
        assert outcome.error is not None
        assert outcome.state.scenarios == ()

    def test_add_rejects_a_blank_name(self, projected: PlanState) -> None:
        """A whitespace-only name is rejected with a message."""
        outcome = state_with_scenario_added(projected, "   ", today=TODAY)
        assert outcome.error == "enter a scenario name"

    def test_add_rejects_the_reserved_base_name(self, projected: PlanState) -> None:
        """The base run's name cannot be taken by a scenario."""
        outcome = state_with_scenario_added(projected, BASE_RUN_NAME, today=TODAY)
        assert outcome.error is not None
        assert "reserved" in outcome.error

    def test_add_rejects_a_duplicate_name(self, with_scenario: PlanState) -> None:
        """Scenario names stay unique."""
        outcome = state_with_scenario_added(with_scenario, SCENARIO, today=TODAY)
        assert outcome.error == "a scenario with that name already exists"

    def test_add_runs_the_comparison(self, with_scenario: PlanState) -> None:
        """Adding a scenario produces named runs, base first."""
        assert [name for name, _ in with_scenario.scenario_runs or ()] == [
            BASE_RUN_NAME,
            SCENARIO,
        ]
        assert with_scenario.scenario_run_error is None

    def test_remove_unknown_name_is_a_no_op(self, with_scenario: PlanState) -> None:
        """Removing a name that does not exist changes nothing."""
        assert state_without_scenario(with_scenario, "ghost", today=TODAY) is (
            with_scenario
        )

    def test_remove_drops_the_scenario_and_runs(self, with_scenario: PlanState) -> None:
        """Removing the only scenario leaves nothing to compare."""
        state = state_without_scenario(with_scenario, SCENARIO, today=TODAY)
        assert state.scenarios == ()
        assert state.scenario_runs is None

    def test_base_run_failure_is_a_message(self) -> None:
        """A failing base run folds into ``scenario_run_error`` (§4.7)."""
        scottish = state_with_household(
            initial_plan_state(), household(SCOTLAND_RESIDENCY), today=TODAY
        )
        outcome = state_with_scenario_added(scottish, SCENARIO, today=TODAY)
        assert outcome.error is None
        assert outcome.state.scenario_runs is None
        assert outcome.state.scenario_run_error is not None
        message = build_scenarios_view_model(outcome.state).message
        assert message.startswith(SCENARIO_RUN_FAILED_PREFIX)

    def test_state_with_scenarios_without_household_holds_no_runs(self) -> None:
        """The generic transition tolerates a plan-less state."""
        state = state_with_scenarios(initial_plan_state(), (), today=TODAY)
        assert state.scenario_runs is None
        assert state.scenario_run_error is None


class TestScenarioOverrides:
    """Overrides are set and removed through stable target keys."""

    def test_unknown_scenario_is_rejected(self, projected: PlanState) -> None:
        """An override on a scenario that does not exist reports why."""
        outcome = state_with_scenario_override(
            projected, "ghost", RETIREMENT_KEY, "58", today=TODAY
        )
        assert outcome.error == "unknown scenario"

    def test_decision_override_parses_and_reruns(
        self, with_scenario: PlanState
    ) -> None:
        """An int decision override lands typed and re-runs the diffs."""
        outcome = state_with_scenario_override(
            with_scenario, SCENARIO, RETIREMENT_KEY, "58", today=TODAY
        )
        assert outcome.error is None
        override = outcome.state.scenarios[0].overrides[0]
        assert override.value == 58
        assert outcome.state.scenario_runs is not None

    def test_money_decision_override_parses(self, with_scenario: PlanState) -> None:
        """A Money decision override parses from plain number text."""
        outcome = state_with_scenario_override(
            with_scenario, SCENARIO, OUTFLOW_KEY, "25000", today=TODAY
        )
        assert outcome.error is None
        assert outcome.state.scenarios[0].overrides[0].value == Money(Decimal(25000))

    def test_assumption_override_parses(self, with_scenario: PlanState) -> None:
        """An assumption override targets the key with a typed value."""
        outcome = state_with_scenario_override(
            with_scenario, SCENARIO, CPI_KEY, "0.03", today=TODAY
        )
        assert outcome.error is None
        override = outcome.state.scenarios[0].overrides[0]
        assert override.target == AssumptionTarget(key=AssumptionKey.INFLATION_CPI)
        assert override.value == Decimal("0.03")

    def test_unparseable_value_leaves_the_state_untouched(
        self, with_scenario: PlanState
    ) -> None:
        """A value that does not parse is rejected with a message."""
        outcome = state_with_scenario_override(
            with_scenario, SCENARIO, RETIREMENT_KEY, "soon", today=TODAY
        )
        assert outcome.error == "enter a whole number"
        assert outcome.state is with_scenario

    def test_entity_invariants_reject_at_edit_time(
        self, with_scenario: PlanState
    ) -> None:
        """A well-typed but invalid value is refused before it is stored."""
        outcome = state_with_scenario_override(
            with_scenario, SCENARIO, OUTFLOW_KEY, "-5000", today=TODAY
        )
        assert outcome.error is not None
        assert "non-negative" in outcome.error
        assert outcome.state is with_scenario

    def test_unknown_target_key_is_rejected(self, with_scenario: PlanState) -> None:
        """A key naming no assumption is rejected."""
        outcome = state_with_scenario_override(
            with_scenario, SCENARIO, "no.such.assumption", "1", today=TODAY
        )
        assert outcome.error == "unknown override target"

    def test_unaddressable_decision_key_is_rejected(
        self, with_scenario: PlanState
    ) -> None:
        """A decision key for a missing entity points at removal."""
        outcome = state_with_scenario_override(
            with_scenario, SCENARIO, "ghost:target_retirement_age", "58", today=TODAY
        )
        assert outcome.error is not None
        assert "no longer in the plan" in outcome.error

    def test_setting_an_existing_target_replaces_in_place(
        self, with_scenario: PlanState
    ) -> None:
        """Re-setting a target keeps one override, in its position."""
        first = state_with_scenario_override(
            with_scenario, SCENARIO, RETIREMENT_KEY, "58", today=TODAY
        ).state
        second = state_with_scenario_override(
            first, SCENARIO, CPI_KEY, "0.03", today=TODAY
        ).state
        replaced = state_with_scenario_override(
            second, SCENARIO, RETIREMENT_KEY, "57", today=TODAY
        ).state
        overrides = replaced.scenarios[0].overrides
        assert [override.value for override in overrides] == [57, Decimal("0.03")]

    def test_blank_value_removes_the_override(self, with_scenario: PlanState) -> None:
        """Blank is the symmetric of "blank restores the default"."""
        set_state = state_with_scenario_override(
            with_scenario, SCENARIO, RETIREMENT_KEY, "58", today=TODAY
        ).state
        outcome = state_with_scenario_override(
            set_state, SCENARIO, RETIREMENT_KEY, "  ", today=TODAY
        )
        assert outcome.error is None
        assert outcome.state.scenarios[0].overrides == ()

    def test_removing_an_absent_override_is_a_no_op(
        self, with_scenario: PlanState
    ) -> None:
        """Removing a target with no override changes nothing."""
        assert (
            state_without_scenario_override(
                with_scenario, SCENARIO, RETIREMENT_KEY, today=TODAY
            )
            is with_scenario
        )

    def test_removing_from_an_unknown_scenario_is_a_no_op(
        self, with_scenario: PlanState
    ) -> None:
        """An unknown scenario name changes nothing."""
        assert (
            state_without_scenario_override(
                with_scenario, "ghost", RETIREMENT_KEY, today=TODAY
            )
            is with_scenario
        )

    def test_orphaned_scenarios_are_excluded_not_fatal(
        self, with_scenario: PlanState
    ) -> None:
        """Base-plan edits that orphan a scenario never break the state."""
        overridden = state_with_scenario_override(
            with_scenario, SCENARIO, OUTFLOW_KEY, "25000", today=TODAY
        ).state
        reshaped = state_with_household(
            overridden, household(with_outflow=False), today=TODAY
        )
        assert reshaped.scenarios == overridden.scenarios
        assert reshaped.scenario_runs is None
        assert reshaped.scenario_run_error is None


class TestTargetOptions:
    """The target picker offers exactly the §4.3 whitelist."""

    def test_no_household_offers_nothing(self) -> None:
        """Without a plan there is nothing to target."""
        assert scenario_target_options(initial_plan_state()) == ()

    def test_decisions_come_first_with_friendly_labels(
        self, projected: PlanState
    ) -> None:
        """Decision targets lead, labelled for humans, ids never shown."""
        options = scenario_target_options(projected)
        keys = [option.key for option in options]
        assert keys[0] == OUTFLOW_KEY
        assert RETIREMENT_KEY in keys
        by_key = {option.key: option for option in options}
        assert by_key[RETIREMENT_KEY].label == "You — target retirement age"
        assert by_key[OUTFLOW_KEY].label == "New roof — amount real"
        assert by_key[RETIREMENT_KEY].edit_text == "60"

    def test_assumption_options_follow_with_edit_text(
        self, projected: PlanState
    ) -> None:
        """Every catalogue assumption is offered; tables edit as text."""
        by_key = {option.key: option for option in scenario_target_options(projected)}
        assert by_key[CPI_KEY].structured is False
        shape = by_key[AssumptionKey.GLIDEPATH_DEFAULT_SHAPE.value]
        assert shape.structured is True
        assert "=" in shape.edit_text


class TestScenariosViewModel:
    """The screen renders the manager and the comparison report."""

    def test_no_plan_carries_the_empty_state_message(self) -> None:
        """Without a household the screen only explains itself."""
        view_model = build_scenarios_view_model(initial_plan_state())
        assert view_model.message == NO_PLAN_MESSAGE
        assert view_model.scenarios == ()
        assert view_model.chart is None

    def test_no_scenarios_prompts_for_one(self, projected: PlanState) -> None:
        """With a plan but no scenarios the screen says what to do."""
        view_model = build_scenarios_view_model(projected)
        assert view_model.message == NO_SCENARIOS_MESSAGE

    def test_valid_scenario_renders_ready_with_comparison(
        self, with_scenario: PlanState
    ) -> None:
        """A valid scenario compares against the base, per period."""
        view_model = build_scenarios_view_model(with_scenario)
        assert view_model.message == ""
        item = view_model.scenarios[0]
        assert item.name == SCENARIO
        assert item.status == VALID_STATUS
        assert item.valid is True
        assert view_model.comparison_columns == (
            "Period",
            "Base plan",
            SCENARIO,
            f"{SCENARIO} (vs base)",
        )
        assert view_model.comparison_rows
        assert all(
            len(row) == len(view_model.comparison_columns)
            for row in view_model.comparison_rows
        )

    def test_chart_holds_one_series_per_run(self, with_scenario: PlanState) -> None:
        """The grouped chart puts the runs side by side."""
        view_model = build_scenarios_view_model(with_scenario)
        chart = view_model.chart
        assert chart is not None
        assert [series.label for series in chart.series] == ["Base plan", SCENARIO]
        assert len(view_model.chart_categories) == len(view_model.comparison_rows)
        assert all(
            len(series.values) == len(view_model.chart_categories)
            for series in chart.series
        )

    def test_override_renders_as_a_diff_row(self, with_scenario: PlanState) -> None:
        """The override list *is* the scenario's diff (§4.3)."""
        state = state_with_scenario_override(
            with_scenario, SCENARIO, RETIREMENT_KEY, "58", today=TODAY
        ).state
        row = build_scenarios_view_model(state).scenarios[0].overrides[0]
        assert row.target_label == "You — target retirement age"
        assert row.value == "58"
        assert row.target_key == RETIREMENT_KEY
        assert row.edit_text == "58"
        assert row.orphaned is False
        assert row.status == ""

    def test_an_early_retirement_diff_shows_signed_deltas(
        self, with_scenario: PlanState
    ) -> None:
        """Delta cells carry signed money, not raw numbers."""
        state = state_with_scenario_override(
            with_scenario, SCENARIO, RETIREMENT_KEY, "58", today=TODAY
        ).state
        view_model = build_scenarios_view_model(state)
        deltas = {row[3] for row in view_model.comparison_rows}
        assert any(cell.lstrip("+-").startswith("£") for cell in deltas)

    def test_orphaned_override_flags_the_scenario(
        self, with_scenario: PlanState
    ) -> None:
        """Orphans flag the scenario invalid without breaking the screen."""
        overridden = state_with_scenario_override(
            with_scenario, SCENARIO, OUTFLOW_KEY, "25000", today=TODAY
        ).state
        reshaped = state_with_household(
            overridden, household(with_outflow=False), today=TODAY
        )
        view_model = build_scenarios_view_model(reshaped)
        item = view_model.scenarios[0]
        assert item.valid is False
        assert "1 orphaned override" in item.status
        row = item.overrides[0]
        assert row.orphaned is True
        assert row.status == ORPHANED_OVERRIDE_STATUS
        assert row.target_label == "Amount real"
        assert view_model.message == NO_VALID_SCENARIOS_MESSAGE
        assert view_model.chart is None

    def test_basis_and_metric_selection_re_present(
        self, with_scenario: PlanState
    ) -> None:
        """The nominal basis and chosen metric flow into the labels."""
        view_model = build_scenarios_view_model(
            with_scenario, basis=ReportBasis.NOMINAL, metric_key="tax_due"
        )
        assert view_model.selected_basis_key == "nominal"
        assert view_model.selected_metric_key == "tax_due"
        chart = view_model.chart
        assert chart is not None
        assert chart.title == "Tax due"
        assert "nominal" in chart.y_axis_label

    def test_default_selection_is_real_closing_balance(
        self, with_scenario: PlanState
    ) -> None:
        """Real terms and closing balance are the §5.2-consistent default."""
        view_model = build_scenarios_view_model(with_scenario)
        assert view_model.selected_basis_key == "real"
        assert view_model.selected_metric_key == DEFAULT_COMPARISON_METRIC_KEY

    def test_unknown_metric_key_is_rejected(self, projected: PlanState) -> None:
        """The builder validates the shell's stored metric key."""
        with pytest.raises(ValueError, match="unknown comparison metric"):
            build_scenarios_view_model(projected, metric_key="nope")


class TestValueParsingHelpers:
    """Decision values parse by the base value's shape."""

    def test_money_parses_from_plain_text(self) -> None:
        """Money comes back as Money."""
        parsed = _parsed_decision_value(Money(Decimal(1)), "2500.50")
        assert parsed == Money(Decimal("2500.50"))

    def test_decimal_parses_and_rejects_nonsense(self) -> None:
        """Decimals parse; non-numbers report the expected shape."""
        base = Decimal(1)
        assert _parsed_decision_value(base, "0.5") == Decimal("0.5")
        with pytest.raises(ValueError, match="plain number"):
            _parsed_decision_value(base, "half")

    def test_int_rejects_fractions(self) -> None:
        """An int target refuses fractional text."""
        with pytest.raises(ValueError, match="whole number"):
            _parsed_decision_value(60, "58.5")

    def test_enum_parses_by_display_text_or_name(self) -> None:
        """Enum choices accept the display form, case-insensitively."""
        assert _parsed_decision_value(AnnuityType.LEVEL, "inflation linked") is (
            AnnuityType.INFLATION_LINKED
        )
        assert _parsed_decision_value(AnnuityType.LEVEL, "level") is AnnuityType.LEVEL

    def test_enum_rejection_lists_the_choices(self) -> None:
        """A wrong choice names the valid ones."""
        with pytest.raises(ValueError, match="choose one of"):
            _parsed_decision_value(AnnuityType.LEVEL, "gilt")

    def test_unsupported_shape_is_rejected(self) -> None:
        """A shape outside the whitelist cannot be overridden."""
        with pytest.raises(ValueError, match="cannot override"):
            _parsed_decision_value("a string", "text")

    def test_edit_text_round_trips_each_shape(self) -> None:
        """Prompt prefills parse back to the value they came from."""
        assert _edit_text(Money(Decimal(25000))) == "25000.00"
        assert _edit_text(60) == "60"
        assert _edit_text(AnnuityType.LEVEL) == "Level"
        assert _edit_text({"equity_start": Decimal("0.8")}) == "equity_start = 0.8"


class TestCellFormatting:
    """Comparison cells format money, dashes, and blanks precisely."""

    @staticmethod
    def metrics_of(amount: str) -> PeriodMetrics:
        """Metrics with every field at the same amount."""
        money = Money(Decimal(amount))
        return PeriodMetrics(**{field.name: money for field in fields(PeriodMetrics)})

    def entry_of(self, delta: PeriodMetrics | None) -> ScenarioPeriodEntry:
        """A scenario entry with a fixed metric amount and given delta."""
        return ScenarioPeriodEntry(
            run_name="s",
            metrics=self.metrics_of("100"),
            year_fraction=Decimal(1),
            delta_vs_base=delta,
        )

    def test_missing_entry_renders_blank(self) -> None:
        """A run that never modelled the period contributes no figures."""
        assert _metric_text(None, "tax_due") == ""
        assert _delta_text(None, "tax_due") == ""

    def test_meaningless_delta_renders_a_dash(self) -> None:
        """No delta (base missing or unequal intervals) shows a dash."""
        assert _delta_text(self.entry_of(None), "tax_due") == "—"

    def test_deltas_are_signed_money(self) -> None:
        """Positive deltas gain a plus; negatives keep the minus."""
        assert _metric_text(self.entry_of(None), "tax_due") == "£100.00"
        assert _delta_text(self.entry_of(self.metrics_of("5")), "tax_due") == "+£5.00"
        assert _delta_text(self.entry_of(self.metrics_of("-5")), "tax_due") == (
            "-£5.00"
        )
