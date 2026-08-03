"""Plan session state tests (issues 8.2/8.3, §4.7).

The controller transitions are pure: capturing a household re-runs
the projection through the real UK region; overriding an assumption
re-stamps provenance (§1) and re-projects; failures become messages,
never shell-visible exceptions.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.app import (
    OVERRIDE_SOURCE,
    PlanState,
    facts_saved_message,
    initial_plan_state,
    state_with_household,
    state_with_override,
)
from glidepath.core import (
    AssumptionKey,
    Decision,
    EntityId,
    Fact,
    Household,
    Money,
    Person,
    Provenance,
    SpendingPlan,
    TaxResidencyId,
    Wrapper,
)
from glidepath.regions.uk import ISA_KIND, RUK_RESIDENCY, SCOTLAND_RESIDENCY

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)


def money_fact(amount: str) -> Fact[Money]:
    """A user-stated monetary fact."""
    return Fact(value=Money(Decimal(amount)), as_of=AS_OF, recorded_on=RECORDED)


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

    def test_run_failure_becomes_a_message(self) -> None:
        """A Scottish taxpayer fails loudly but safely (roadmap 9.1)."""
        state = state_with_household(
            initial_plan_state(), household(SCOTLAND_RESIDENCY), today=TODAY
        )
        assert state.result is None
        assert state.run_error is not None
        assert "Scottish" in state.run_error

    def test_saved_message_reports_the_outcome(self, projected: PlanState) -> None:
        """The status line distinguishes success from a failed run."""
        assert "projection run" in facts_saved_message(projected)
        failed = state_with_household(
            initial_plan_state(), household(SCOTLAND_RESIDENCY), today=TODAY
        )
        assert "failed" in facts_saved_message(failed)


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

    def test_policy_tables_are_structured_and_display_only(self) -> None:
        """The uprating policy is a table, so in-place edits are refused."""
        state = initial_plan_state()
        outcome = state_with_override(
            state,
            AssumptionKey.POLICY_STATE_PENSION_UPRATING.value,
            "cpi",
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is not None
        assert "structured" in outcome.error
        assert outcome.state is state

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

    def test_structured_assumption_is_not_editable(self) -> None:
        """Table-valued assumptions refuse in-place edits."""
        state = initial_plan_state()
        outcome = state_with_override(
            state,
            AssumptionKey.GLIDEPATH_DEFAULT_SHAPE.value,
            "0.5",
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is not None
        assert "structured" in outcome.error
        assert outcome.state is state

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
