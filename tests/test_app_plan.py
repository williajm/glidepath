"""Plan session state tests (issues 8.2/8.3, §4.7).

The controller transitions are pure: capturing a household re-runs
the projection through the real UK region; overriding an assumption
re-stamps provenance (§1) and re-projects; failures become messages,
never shell-visible exceptions.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from factories import money_fact
from glidepath.app import (
    OVERRIDE_SOURCE,
    PlanState,
    facts_saved_message,
    initial_plan_state,
    plan_run_config,
    state_marked_saved,
    state_with_household,
    state_with_override,
    state_with_scenarios,
)
from glidepath.app.tables import table_edit_text
from glidepath.core import (
    AssumptionKey,
    Decision,
    EntityId,
    Fact,
    FixedPercentWithdrawalStrategy,
    FixedRealWithdrawalStrategy,
    GuardrailsWithdrawalStrategy,
    Household,
    Person,
    Provenance,
    Rate,
    RunMode,
    Scenario,
    SpendingPlan,
    TaxResidencyId,
    WithdrawalRule,
    WithdrawalRuleKind,
    Wrapper,
)
from glidepath.regions.uk import ISA_KIND, RUK_RESIDENCY, SCOTLAND_RESIDENCY

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)


def household(tax_residency: TaxResidencyId = RUK_RESIDENCY) -> Household:
    """A small projectable household: one ISA saver retiring at 60."""
    isa = Wrapper(
        id=EntityId("plan-isa"),
        kind=ISA_KIND,
        balance=money_fact("25000"),
    )
    person = Person(
        id=EntityId("plan-person"),
        date_of_birth=Fact(value=date(1991, 2, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=tax_residency,
        employment_income=money_fact("42000"),
        wrappers=(isa,),
    )
    return Household(
        persons=(person,),
        spending=SpendingPlan(annual_spending_real=money_fact("18000")),
    )


@pytest.fixture(scope="module", name="projected")
def projected_fixture() -> PlanState:
    """One projected session state, shared across the module."""
    return state_with_household(initial_plan_state(), household(), today=TODAY)


class TestInitialState:
    """A fresh session carries the shipped defaults and no plan."""

    def test_initial_state_has_defaults_and_no_plan(self) -> None:
        """The shipped assumption catalogue is loaded; nothing else is."""
        state = initial_plan_state()
        assert AssumptionKey.INFLATION_CPI in state.assumptions
        assert state.household is None
        assert state.result is None
        assert state.run_error is None


class TestStateWithHousehold:
    """Capturing a household runs the projection."""

    def test_projection_runs_and_records_provenance(self, projected: PlanState) -> None:
        """A projectable household produces a result with provenance."""
        assert projected.run_error is None
        assert projected.result is not None
        assert projected.result.provenance.facts
        assert projected.result.provenance.decisions
        assert projected.result.provenance.assumptions

    def test_scottish_household_projects(self) -> None:
        """A Scottish taxpayer projects under the Scottish schedule (9.1)."""
        state = state_with_household(
            initial_plan_state(), household(SCOTLAND_RESIDENCY), today=TODAY
        )
        assert state.run_error is None
        assert state.result is not None

    def test_run_failure_becomes_a_message(self) -> None:
        """A residency the UK region cannot assess fails loudly but safely."""
        state = state_with_household(
            initial_plan_state(), household(TaxResidencyId("uk.mars")), today=TODAY
        )
        assert state.result is None
        assert state.run_error is not None
        assert "residency" in state.run_error

    def test_saved_message_reports_the_outcome(self, projected: PlanState) -> None:
        """The status line distinguishes success from a failed run."""
        assert "projection run" in facts_saved_message(projected)
        failed = state_with_household(
            initial_plan_state(), household(TaxResidencyId("uk.mars")), today=TODAY
        )
        assert "failed" in facts_saved_message(failed)


class TestPlanRunConfig:
    """The plan's withdrawal-strategy decision rides every run (10.3)."""

    def test_no_household_keeps_the_engine_default(self) -> None:
        """With nothing to read, the config carries fixed real spending."""
        config = plan_run_config(None, today=TODAY)
        assert config.withdrawal_strategy == FixedRealWithdrawalStrategy()

    def test_no_choice_keeps_the_engine_default(self) -> None:
        """A household without the decision runs fixed real spending."""
        config = plan_run_config(household(), today=TODAY)
        assert config.withdrawal_strategy == FixedRealWithdrawalStrategy()

    def test_the_recorded_choice_configures_the_strategy(self) -> None:
        """A fixed-percentage decision arrives with its rate."""
        chosen = Household(
            persons=household().persons,
            withdrawal_strategy=Decision(
                value=WithdrawalRule(
                    kind=WithdrawalRuleKind.FIXED_PERCENT,
                    rate=Rate(Decimal("0.04")),
                ),
                recorded_on=RECORDED,
            ),
        )
        config = plan_run_config(chosen, today=TODAY)
        assert config.withdrawal_strategy == FixedPercentWithdrawalStrategy(
            rate=Rate(Decimal("0.04"))
        )

    def test_mode_and_seed_pass_through(self) -> None:
        """The Monte Carlo parameters ride alongside the strategy."""
        config = plan_run_config(
            household(), today=TODAY, mode=RunMode.MONTE_CARLO, seed=7
        )
        assert config.mode is RunMode.MONTE_CARLO
        assert config.seed == 7
        assert config.today == TODAY

    def test_the_projection_runs_under_the_chosen_strategy(self) -> None:
        """The held result's config carries the plan's own decision."""
        chosen = Household(
            persons=household().persons,
            spending=household().spending,
            withdrawal_strategy=Decision(
                value=WithdrawalRule(kind=WithdrawalRuleKind.GUARDRAILS),
                recorded_on=RECORDED,
            ),
        )
        state = state_with_household(initial_plan_state(), chosen, today=TODAY)
        assert state.run_error is None
        assert state.result is not None
        assert state.result.config.withdrawal_strategy == GuardrailsWithdrawalStrategy()


class TestStateWithOverride:
    """Assumptions are overridable in place, with full provenance (§1)."""

    def test_decimal_override_re_stamps_provenance(self) -> None:
        """A decimal override carries value, source, and date (§1)."""
        recorded = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
        outcome = state_with_override(
            initial_plan_state(),
            AssumptionKey.INFLATION_CPI.value,
            "0.03",
            recorded_on=recorded,
            today=TODAY,
        )
        assert outcome.error is None
        overridden = outcome.state.assumptions.get(AssumptionKey.INFLATION_CPI)
        assert overridden.value == Decimal("0.03")
        assert overridden.provenance is Provenance.USER_OVERRIDE
        assert overridden.source == OVERRIDE_SOURCE
        assert overridden.recorded_on == recorded
        assert overridden.default_value == Decimal("0.02")

    def test_int_override_parses_whole_numbers(self) -> None:
        """An integer-valued assumption takes an integer override."""
        outcome = state_with_override(
            initial_plan_state(),
            AssumptionKey.HORIZON_PLANNING_AGE.value,
            "92",
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is None
        assert (
            outcome.state.assumptions.get(AssumptionKey.HORIZON_PLANNING_AGE).value
            == 92
        )

    def test_table_override_parses_and_re_stamps(self) -> None:
        """A structured default takes ``key = value`` text (issue #71)."""
        recorded = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
        outcome = state_with_override(
            initial_plan_state(),
            AssumptionKey.POLICY_STATE_PENSION_UPRATING.value,
            "rule = cpi",
            recorded_on=recorded,
            today=TODAY,
        )
        assert outcome.error is None
        overridden = outcome.state.assumptions.get(
            AssumptionKey.POLICY_STATE_PENSION_UPRATING
        )
        assert overridden.value == {"rule": "cpi"}
        assert overridden.provenance is Provenance.USER_OVERRIDE
        assert overridden.source == OVERRIDE_SOURCE
        assert overridden.recorded_on == recorded

    def test_table_override_edits_one_figure_in_place(self) -> None:
        """Editing one figure of the glide shape keeps the others."""
        base = initial_plan_state()
        default = base.assumptions.get(AssumptionKey.GLIDEPATH_DEFAULT_SHAPE).value
        text = table_edit_text(default).replace("0.80", "0.70")
        outcome = state_with_override(
            base,
            AssumptionKey.GLIDEPATH_DEFAULT_SHAPE.value,
            text,
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is None
        value = outcome.state.assumptions.get(
            AssumptionKey.GLIDEPATH_DEFAULT_SHAPE
        ).value
        assert value["equity_start"] == Decimal("0.70")
        assert value["derisk_years_before_retirement"] == 15

    def test_table_override_with_bad_content_is_rejected(self) -> None:
        """The policy parser vets a table before it reaches the state."""
        state = initial_plan_state()
        outcome = state_with_override(
            state,
            AssumptionKey.POLICY_STATE_PENSION_UPRATING.value,
            "rule = quadruple_lock",
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is not None
        assert outcome.state is state

    def test_future_years_table_override_is_vetted(self) -> None:
        """The future-years policy arm vets its table like the others."""
        state = initial_plan_state()
        outcome = state_with_override(
            state,
            AssumptionKey.POLICY_TAX_FUTURE_YEARS.value,
            "mode = perpetual_freeze",
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is not None
        assert outcome.state is state

    def test_table_override_missing_figure_is_named(self) -> None:
        """A missing required figure is named in the rejection."""
        state = initial_plan_state()
        outcome = state_with_override(
            state,
            AssumptionKey.GLIDEPATH_DEFAULT_SHAPE.value,
            "equity_start = 0.80",
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is not None
        assert "missing required key" in outcome.error
        assert outcome.state is state

    def test_table_override_whole_number_fraction_is_rejected(self) -> None:
        """A fraction typed without a decimal point fails by type."""
        state = initial_plan_state()
        outcome = state_with_override(
            state,
            AssumptionKey.GLIDEPATH_DEFAULT_SHAPE.value,
            "equity_start = 1",
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is not None
        assert "Decimal fraction" in outcome.error
        assert outcome.state is state

    def test_annuity_table_override_round_trips_nested_keys(self) -> None:
        """The nested annuity table edits through dotted keys."""
        base = initial_plan_state()
        default = base.assumptions.get(AssumptionKey.ANNUITY_AGE_ADJUSTMENT).value
        text = table_edit_text(default).replace("0.846", "0.850")
        outcome = state_with_override(
            base,
            AssumptionKey.ANNUITY_AGE_ADJUSTMENT.value,
            text,
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is None
        value = outcome.state.assumptions.get(
            AssumptionKey.ANNUITY_AGE_ADJUSTMENT
        ).value
        assert value["level"]["55"] == Decimal("0.850")

    def test_blank_restores_a_structured_default(self) -> None:
        """A blank value undoes a table override entirely."""
        overridden = state_with_override(
            initial_plan_state(),
            AssumptionKey.POLICY_STATE_PENSION_UPRATING.value,
            "rule = cpi",
            recorded_on=RECORDED,
            today=TODAY,
        ).state
        outcome = state_with_override(
            overridden,
            AssumptionKey.POLICY_STATE_PENSION_UPRATING.value,
            "",
            recorded_on=RECORDED,
            today=TODAY,
        )
        restored = outcome.state.assumptions.get(
            AssumptionKey.POLICY_STATE_PENSION_UPRATING
        )
        assert restored.provenance is Provenance.DEFAULT_ASSUMPTION
        assert restored.value == restored.default_value

    def test_non_finite_decimal_override_is_rejected(self) -> None:
        """Infinity never reaches the assumption set."""
        state = initial_plan_state()
        outcome = state_with_override(
            state,
            AssumptionKey.INFLATION_CPI.value,
            "Infinity",
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is not None
        assert outcome.state is state

    def test_unparsable_int_override_is_rejected(self) -> None:
        """An integer assumption refuses non-integer text."""
        state = initial_plan_state()
        outcome = state_with_override(
            state,
            AssumptionKey.HORIZON_PLANNING_AGE.value,
            "ninety",
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is not None
        assert "whole number" in outcome.error
        assert outcome.state is state

    def test_blank_restores_the_shipped_default(self) -> None:
        """A blank value undoes the override entirely."""
        overridden = state_with_override(
            initial_plan_state(),
            AssumptionKey.INFLATION_CPI.value,
            "0.03",
            recorded_on=RECORDED,
            today=TODAY,
        ).state
        outcome = state_with_override(
            overridden,
            AssumptionKey.INFLATION_CPI.value,
            "",
            recorded_on=RECORDED,
            today=TODAY,
        )
        restored = outcome.state.assumptions.get(AssumptionKey.INFLATION_CPI)
        assert restored.provenance is Provenance.DEFAULT_ASSUMPTION
        assert restored.value == restored.default_value

    def test_unparsable_override_leaves_the_state_untouched(self) -> None:
        """A bad value reports why and changes nothing."""
        state = initial_plan_state()
        outcome = state_with_override(
            state,
            AssumptionKey.INFLATION_CPI.value,
            "three percent",
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is not None
        assert outcome.state is state

    def test_table_override_needs_key_value_lines(self) -> None:
        """A bare scalar cannot stand in for a structured table."""
        state = initial_plan_state()
        outcome = state_with_override(
            state,
            AssumptionKey.GLIDEPATH_DEFAULT_SHAPE.value,
            "0.5",
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is not None
        assert "key = value" in outcome.error
        assert outcome.state is state

    def test_table_override_re_projects_a_captured_household(
        self, projected: PlanState
    ) -> None:
        """A table override re-runs the projection like any other."""
        outcome = state_with_override(
            projected,
            AssumptionKey.POLICY_STATE_PENSION_UPRATING.value,
            "rule = cpi",
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is None
        assert outcome.state.result is not None
        assert outcome.state.result is not projected.result

    def test_unknown_key_is_rejected(self) -> None:
        """A key outside the catalogue is rejected, not KeyError'd."""
        state = initial_plan_state()
        outcome = state_with_override(
            state, "not.a.key", "1", recorded_on=RECORDED, today=TODAY
        )
        assert outcome.error is not None
        assert outcome.state is state

    def test_override_re_projects_a_captured_household(
        self, projected: PlanState
    ) -> None:
        """With a household in place, an override re-runs the projection."""
        outcome = state_with_override(
            projected,
            AssumptionKey.RETURNS_EQUITY_REAL.value,
            "0.05",
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is None
        assert outcome.state.result is not None
        assert outcome.state.result is not projected.result
        read = {
            assumption.key: assumption
            for assumption in outcome.state.result.provenance.assumptions
        }
        assert read[AssumptionKey.RETURNS_EQUITY_REAL].value == Decimal("0.05")


class TestModifiedFlag:
    """The unsaved-changes flag over the pure transitions (issue #136)."""

    def test_a_fresh_session_is_clean(self) -> None:
        """Nothing has touched a fresh session, so nothing is unsaved."""
        assert initial_plan_state().modified is False

    def test_capturing_a_household_marks_the_state(self, projected: PlanState) -> None:
        """A facts capture is a plan edit."""
        assert projected.modified is True

    def test_an_override_marks_the_state(self, projected: PlanState) -> None:
        """An assumption override is a plan edit, even on a saved state."""
        saved = state_marked_saved(projected)
        outcome = state_with_override(
            saved,
            AssumptionKey.INFLATION_CPI.value,
            "0.03",
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is None
        assert outcome.state.modified is True

    def test_a_scenario_edit_marks_the_state(self, projected: PlanState) -> None:
        """Replacing the scenario list is a plan edit."""
        saved = state_marked_saved(projected)
        edited = state_with_scenarios(
            saved, (Scenario(name="Retire later"),), today=TODAY
        )
        assert edited.modified is True

    def test_state_marked_saved_clears_only_the_flag(
        self, projected: PlanState
    ) -> None:
        """Clearing the flag leaves the rest of the session in place."""
        saved = state_marked_saved(projected)
        assert saved.modified is False
        assert saved.household is projected.household
        assert saved.result is projected.result

    def test_state_marked_saved_on_a_clean_state_is_a_no_op(self) -> None:
        """A clean state has nothing to clear, so it comes back as is."""
        state = initial_plan_state()
        assert state_marked_saved(state) is state
