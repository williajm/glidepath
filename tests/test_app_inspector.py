"""Stated-vs-assumed inspector tests (issue 8.3, §1, §5.1).

The acceptance criterion: the surface renders from
``ProjectionResult.provenance`` — facts stated, assumptions used with
default-vs-overridden status, decisions in effect — and every default
is overridable in place with source and date shown.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.app import (
    InspectorViewModel,
    PlanState,
    build_inspector_view_model,
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
    SpendingPlan,
    Wrapper,
)
from glidepath.regions.uk import ISA_KIND, RUK_RESIDENCY, SCOTLAND_RESIDENCY

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)


def money_fact(amount: str) -> Fact[Money]:
    """A user-stated monetary fact."""
    return Fact(value=Money(Decimal(amount)), as_of=AS_OF, recorded_on=RECORDED)


def household() -> Household:
    """A small projectable household: one ISA saver retiring at 60."""
    isa = Wrapper(
        id=EntityId("inspector-isa"),
        kind=ISA_KIND,
        balance=money_fact("25000"),
    )
    person = Person(
        id=EntityId("inspector-person"),
        date_of_birth=Fact(value=date(1991, 2, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
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


@pytest.fixture(scope="module", name="view_model")
def view_model_fixture(projected: PlanState) -> InspectorViewModel:
    """The inspector over the projected state."""
    return build_inspector_view_model(projected)


class TestEmptySession:
    """Before any projection the catalogue still renders, overridable."""

    def test_no_facts_or_decisions_yet(self) -> None:
        """Facts and decisions wait for a projection."""
        view_model = build_inspector_view_model(initial_plan_state())
        assert view_model.facts == ()
        assert view_model.decisions == ()
        assert "No projection yet" in view_model.summary

    def test_full_catalogue_is_shown_and_marked_unread(self) -> None:
        """Every shipped default is a row, so it is overridable now."""
        state = initial_plan_state()
        view_model = build_inspector_view_model(state)
        assert len(view_model.assumptions) == len(state.assumptions.keys)
        assert all(row.usage == "No projection yet" for row in view_model.assumptions)
        assert all(row.status == "Shipped default" for row in view_model.assumptions)

    def test_structured_rows_are_not_editable(self) -> None:
        """Table-valued assumptions are display-only in place."""
        view_model = build_inspector_view_model(initial_plan_state())
        by_key = {row.key: row for row in view_model.assumptions}
        assert not by_key[AssumptionKey.GLIDEPATH_DEFAULT_SHAPE.value].editable
        assert by_key[AssumptionKey.INFLATION_CPI.value].editable


class TestProjectedSession:
    """The surface renders from ``ProjectionResult.provenance`` (8.3)."""

    def test_facts_column_shows_stated_facts(
        self, view_model: InspectorViewModel
    ) -> None:
        """Stated facts appear with value, as_of, and recorded date."""
        by_label = {row.label: row for row in view_model.facts}
        dob = by_label["You — date of birth"]
        assert dob.value == "1991-02-01"
        assert dob.as_of == "2026-08-01"
        assert dob.recorded == "2026-08-01"
        balance = by_label["Wrapper 1 (ISA) — balance"]
        assert balance.value == "£25,000.00"

    def test_decisions_column_shows_choices_in_effect(
        self, view_model: InspectorViewModel
    ) -> None:
        """Decisions render as the third column (§5.1)."""
        by_label = {row.label: row for row in view_model.decisions}
        assert by_label["You — target retirement age"].value == "60"

    def test_assumptions_used_come_first_with_status_and_source(
        self, view_model: InspectorViewModel, projected: PlanState
    ) -> None:
        """Read assumptions lead, each with status, source, and date."""
        assert projected.result is not None
        read = [a.key.value for a in projected.result.provenance.assumptions]
        leading = [row.key for row in view_model.assumptions[: len(read)]]
        assert leading == read
        first = view_model.assumptions[0]
        assert first.usage == "Used in this projection"
        assert first.status == "Shipped default"
        assert first.source
        assert first.recorded

    def test_unread_assumptions_follow_sorted(
        self, view_model: InspectorViewModel, projected: PlanState
    ) -> None:
        """The rest of the catalogue follows, so it stays overridable."""
        assert projected.result is not None
        read_count = len(projected.result.provenance.assumptions)
        trailing = [row.key for row in view_model.assumptions[read_count:]]
        assert trailing == sorted(trailing)
        assert len(view_model.assumptions) == len(projected.assumptions.keys)

    def test_summary_carries_the_run_manifest(
        self, view_model: InspectorViewModel
    ) -> None:
        """The manifest line names the region data version and seed."""
        assert "Run manifest" in view_model.summary
        assert "seed: none (deterministic)" in view_model.summary

    def test_override_shows_as_your_override_with_date(
        self, projected: PlanState
    ) -> None:
        """An in-place override re-renders with its provenance (§1)."""
        recorded = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
        outcome = state_with_override(
            projected,
            AssumptionKey.INFLATION_CPI.value,
            "0.03",
            recorded_on=recorded,
            today=TODAY,
        )
        view_model = build_inspector_view_model(outcome.state)
        row = next(
            row
            for row in view_model.assumptions
            if row.key == AssumptionKey.INFLATION_CPI.value
        )
        assert row.status == "Your override"
        assert row.value == "0.03"
        assert row.default_value == "0.02"
        assert row.recorded == "2026-08-03"

    def test_failed_run_reports_in_the_summary(self) -> None:
        """A run failure renders as the summary line, not a crash."""
        failed_person = Person(
            id=EntityId("inspector-scot"),
            date_of_birth=Fact(
                value=date(1991, 2, 1), as_of=AS_OF, recorded_on=RECORDED
            ),
            target_retirement_age=Decision(value=60, recorded_on=RECORDED),
            tax_residency=SCOTLAND_RESIDENCY,
            employment_income=money_fact("42000"),
        )
        state = state_with_household(
            initial_plan_state(), Household(persons=(failed_person,)), today=TODAY
        )
        view_model = build_inspector_view_model(state)
        assert "The projection failed" in view_model.summary
