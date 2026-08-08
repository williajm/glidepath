"""Stated-vs-assumed inspector tests (issue 8.3, §1, §5.1).

The acceptance criterion: the surface renders from
``ProjectionResult.provenance`` — facts stated, assumptions used with
default-vs-overridden status, decisions in effect — and every default
is overridable in place with source and date shown.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from factories import money_fact
from glidepath.app import (
    InspectorViewModel,
    PlanState,
    build_inspector_view_model,
    format_value,
    initial_plan_state,
    state_with_household,
    state_with_override,
)
from glidepath.app.display import ASSUMPTION_NAMES
from glidepath.app.inspector import _assumption_row
from glidepath.app.tables import parse_table_text
from glidepath.core import (
    AnnuityPurchase,
    AssetAllocation,
    Assumption,
    AssumptionKey,
    ContributionSchedule,
    DBActiveMembership,
    DBPension,
    Decision,
    EntityId,
    Fact,
    FactorTable,
    FeeSchedule,
    GlidePathConfig,
    GlidePathPoint,
    Household,
    LifeStage,
    Money,
    Person,
    PlannedOutflow,
    Provenance,
    Rate,
    ReliefMechanic,
    RevaluationBasis,
    RevaluationReference,
    SpendingPlan,
    TaxResidencyId,
    Wrapper,
)
from glidepath.regions.uk import (
    ISA_KIND,
    RUK_RESIDENCY,
    SIPP_KIND,
)

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)


def household(balances_as_of: date = AS_OF) -> Household:
    """A small projectable household: one ISA saver retiring at 60."""
    isa = Wrapper(
        id=EntityId("inspector-isa"),
        kind=ISA_KIND,
        balance=Fact(
            value=Money(Decimal(25000)),
            as_of=balances_as_of,
            recorded_on=RECORDED,
        ),
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
        assert view_model.summary_detail == ""

    def test_full_catalogue_is_shown_and_marked_unread(self) -> None:
        """Every shipped default is a row, so it is overridable now."""
        state = initial_plan_state()
        view_model = build_inspector_view_model(state)
        assert len(view_model.assumptions) == len(state.assumptions.keys)
        assert all(row.usage == "No projection yet" for row in view_model.assumptions)
        assert all(row.status == "Shipped default" for row in view_model.assumptions)
        assert all(row.default_value == "—" for row in view_model.assumptions)

    def test_structured_rows_carry_round_trippable_edit_text(self) -> None:
        """Table rows edit as text that parses back exactly (issue #71)."""
        state = initial_plan_state()
        view_model = build_inspector_view_model(state)
        by_key = {row.key: row for row in view_model.assumptions}
        row = by_key[AssumptionKey.GLIDEPATH_DEFAULT_SHAPE.value]
        assert row.structured
        default = state.assumptions.get(AssumptionKey.GLIDEPATH_DEFAULT_SHAPE).value
        assert parse_table_text(row.edit_text) == default
        scalar = by_key[AssumptionKey.INFLATION_CPI.value]
        assert not scalar.structured
        assert scalar.edit_text == scalar.value


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

    def test_fresh_balances_render_no_roll_forwards(
        self, view_model: InspectorViewModel
    ) -> None:
        """A balance stated within a month of today needs no §4.8 row."""
        assert view_model.roll_forwards == ()
        assert view_model.roll_forwards_heading

    def test_stale_balance_renders_a_roll_forward_row(self) -> None:
        """A year-old statement surfaces the §4.8 adjustment (issue #72)."""
        stale = household(balances_as_of=date(2025, 8, 1))
        state = state_with_household(initial_plan_state(), stale, today=TODAY)
        view_model = build_inspector_view_model(state)
        [row] = view_model.roll_forwards
        assert row.label == "Wrapper 1 (ISA) — balance"
        assert row.stated == "£25,000.00"
        assert row.as_of == "2025-08-01"
        assert row.months == "12"
        assert row.opening.startswith("£")
        assert row.opening != row.stated

    def test_assumptions_used_come_first_with_status_and_source(
        self, view_model: InspectorViewModel, projected: PlanState
    ) -> None:
        """Read assumptions lead, each with status, source, and date."""
        assert projected.result is not None
        read = [a.key.value for a in projected.result.provenance.assumptions]
        leading = [row.key for row in view_model.assumptions[: len(read)]]
        assert leading == read
        first = view_model.assumptions[0]
        assert first.usage == "Used"
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

    def test_region_build_keys_are_not_labelled_unused(
        self, view_model: InspectorViewModel
    ) -> None:
        """Policy tables read at region build say so, not "not used"."""
        by_key = {row.key: row for row in view_model.assumptions}
        for key in ("policy.tax.future_years", "policy.state_pension.uprating"):
            assert by_key[key].usage == "Applied at region build"

    def test_summary_is_human_with_the_manifest_as_detail(
        self, view_model: InspectorViewModel
    ) -> None:
        """The summary reads for humans; the exact manifest rides as detail."""
        assert "sha256" not in view_model.summary
        assert "deterministic run" in view_model.summary
        assert "Projected" in view_model.summary
        assert "Run manifest" in view_model.summary_detail
        assert "seed: none (deterministic)" in view_model.summary_detail

    def test_every_assumption_key_has_a_display_name(self) -> None:
        """The human-name catalogue covers the whole shipped key set."""
        assert set(ASSUMPTION_NAMES) == {key.value for key in AssumptionKey}

    def test_rows_carry_human_labels_beside_their_keys(
        self, view_model: InspectorViewModel
    ) -> None:
        """Every row pairs the dotted id with a human display name."""
        by_key = {row.key: row for row in view_model.assumptions}
        assert by_key["inflation.cpi"].label == "Inflation (CPI)"
        assert by_key["earnings.growth.real"].label == (
            "Earnings growth (above inflation)"
        )
        assert all(
            row.label == ASSUMPTION_NAMES[row.key] for row in view_model.assumptions
        )

    def test_default_column_compares_values_not_rendered_text(self) -> None:
        """A late-field table override shows its default despite truncation."""
        default = {f"key_{index:02d}": Decimal(1) for index in range(20)}
        overridden = {**default, "key_19": Decimal(2)}
        rendered_override = format_value(overridden)
        rendered_default = format_value(default)
        assert rendered_override == rendered_default  # truncation collides
        assumption = Assumption(
            key=AssumptionKey.ANNUITY_AGE_ADJUSTMENT,
            value=overridden,
            default_value=default,
            provenance=Provenance.USER_OVERRIDE,
            source="test",
            recorded_on=RECORDED,
            description="test",
        )
        row = _assumption_row(assumption, "Used")
        assert row.default_value != "—"
        assert row.default_value == rendered_default

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
            id=EntityId("inspector-unassessable"),
            date_of_birth=Fact(
                value=date(1991, 2, 1), as_of=AS_OF, recorded_on=RECORDED
            ),
            target_retirement_age=Decision(value=60, recorded_on=RECORDED),
            tax_residency=TaxResidencyId("uk.mars"),
            employment_income=money_fact("42000"),
        )
        state = state_with_household(
            initial_plan_state(), Household(persons=(failed_person,)), today=TODAY
        )
        view_model = build_inspector_view_model(state)
        assert "The projection failed" in view_model.summary


def structural_household() -> Household:
    """A household exercising every structural input (issue #70)."""
    sipp = Wrapper(
        id=EntityId("structure-sipp"),
        kind=SIPP_KIND,
        balance=money_fact("100000"),
        contributions=ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(6000)), recorded_on=RECORDED),
            relief_mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
            escalation=AssumptionKey.EARNINGS_GROWTH_REAL,
        ),
        allocation=AssetAllocation(equity=Decimal("0.6"), bonds=Decimal("0.4")),
        fees=FeeSchedule(platform=Rate(Decimal("0.002")), fund=Rate(Decimal("0.001"))),
    )
    isa = Wrapper(
        id=EntityId("structure-isa"),
        kind=ISA_KIND,
        balance=money_fact("25000"),
        contributions=ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(4000)), recorded_on=RECORDED),
        ),
    )
    capped_cpi = DBPension(
        id=EntityId("structure-db-cpi"),
        accrued_annual_pension=money_fact("9000"),
        statement_date=date(2025, 4, 6),
        normal_pension_age=Fact(value=65, as_of=AS_OF, recorded_on=RECORDED),
        revaluation_basis=RevaluationBasis(
            reference=RevaluationReference.CPI, cap=Rate(Decimal("0.05"))
        ),
        early_late_factors=FactorTable(factors={58: Decimal("0.80")}),
        commuted_fraction=Decision(value=Decimal(0), recorded_on=RECORDED),
        active_membership=DBActiveMembership(
            accrual_rate=Fact(
                value=Decimal("0.0166667"), as_of=AS_OF, recorded_on=RECORDED
            ),
            pensionable_salary=money_fact("42000"),
        ),
    )
    frozen = DBPension(
        id=EntityId("structure-db-frozen"),
        accrued_annual_pension=money_fact("2000"),
        statement_date=date(2020, 1, 1),
        normal_pension_age=Fact(value=65, as_of=AS_OF, recorded_on=RECORDED),
        revaluation_basis=RevaluationBasis(reference=RevaluationReference.NONE),
        early_late_factors=FactorTable(factors={}),
        commuted_fraction=Decision(value=Decimal(0), recorded_on=RECORDED),
    )
    fixed = DBPension(
        id=EntityId("structure-db-fixed"),
        accrued_annual_pension=money_fact("3000"),
        statement_date=date(2022, 6, 1),
        normal_pension_age=Fact(value=65, as_of=AS_OF, recorded_on=RECORDED),
        revaluation_basis=RevaluationBasis(
            reference=RevaluationReference.FIXED, fixed_rate=Rate(Decimal("0.03"))
        ),
        early_late_factors=FactorTable(factors={}),
        commuted_fraction=Decision(value=Decimal(0), recorded_on=RECORDED),
    )
    uncapped = DBPension(
        id=EntityId("structure-db-uncapped"),
        accrued_annual_pension=money_fact("1000"),
        statement_date=date(2023, 9, 1),
        normal_pension_age=Fact(value=65, as_of=AS_OF, recorded_on=RECORDED),
        revaluation_basis=RevaluationBasis(reference=RevaluationReference.CPI),
        early_late_factors=FactorTable(factors={}),
        commuted_fraction=Decision(value=Decimal(0), recorded_on=RECORDED),
    )
    purchase = AnnuityPurchase(
        id=EntityId("structure-annuity"),
        at_age=Decision(value=70, recorded_on=RECORDED),
        fraction_of_pot=Decision(value=Decimal("0.5"), recorded_on=RECORDED),
    )
    person = Person(
        id=EntityId("structure-person"),
        date_of_birth=Fact(value=date(1991, 2, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        wrappers=(sipp, isa),
        db_pensions=(capped_cpi, frozen, fixed, uncapped),
        annuity_purchases=(purchase,),
    )
    outflow = PlannedOutflow(
        id=EntityId("structure-outflow"),
        label="Mortgage payoff",
        amount_real=Decision(value=Money(Decimal(40000)), recorded_on=RECORDED),
        at_age_of=(EntityId("structure-person"), 62),
    )
    return Household(
        persons=(person,),
        spending=SpendingPlan(
            annual_spending_real=money_fact("18000"),
            stage_multipliers={LifeStage.DECUMULATION: Decimal("0.9")},
        ),
        planned_outflows=(outflow,),
    )


class TestPlanStructure:
    """Structural inputs surface entity-level (issue #70, §5.1)."""

    @staticmethod
    def structure_values(household: Household) -> dict[tuple[str, str], str]:
        """The structure rows keyed by (entity, setting), without a run."""
        state = PlanState(
            assumptions=initial_plan_state().assumptions, household=household
        )
        view_model = build_inspector_view_model(state)
        return {(row.entity, row.setting): row.value for row in view_model.structure}

    def test_no_household_means_no_structure_rows(self) -> None:
        """Before facts are saved the section is simply empty."""
        view_model = build_inspector_view_model(initial_plan_state())
        assert view_model.structure == ()
        assert view_model.structure_heading == "Plan structure"

    def test_person_rows_show_residency_and_glide_path(self) -> None:
        """Tax residency and the glide-path source surface per person."""
        rows = self.structure_values(structural_household())
        assert rows["You", "Tax residency"] == (
            "UK (England, Wales, or Northern Ireland)"
        )
        assert rows["You", "Glide path"] == "Default glide path shape assumption"

    def test_wrapper_rows_show_kind_relief_allocation_and_fees(self) -> None:
        """Wrapper kind, relief mechanics, allocation, and fees surface."""
        rows = self.structure_values(structural_household())
        assert rows["Wrapper 1 (SIPP)", "Wrapper kind"] == "SIPP"
        assert rows["Wrapper 1 (SIPP)", "Contribution relief"] == "Relief at source"
        assert rows["Wrapper 1 (SIPP)", "Contribution escalation"] == (
            "Grows with earnings"
        )
        assert rows["Wrapper 1 (SIPP)", "Asset allocation"] == (
            "Equity 0.6 / bonds 0.4 / cash 0"
        )
        assert rows["Wrapper 1 (SIPP)", "Fees"] == "Platform 0.002 / fund 0.001"
        assert rows["Wrapper 2 (ISA)", "Contribution relief"] == "No tax relief"
        assert rows["Wrapper 2 (ISA)", "Contribution escalation"] == (
            "None (fixed amounts)"
        )
        assert rows["Wrapper 2 (ISA)", "Asset allocation"] == "From the glide path"
        assert rows["Wrapper 2 (ISA)", "Fees"] == (
            "Shipped platform and fund fee assumptions"
        )

    def test_db_rows_show_scheme_structure(self) -> None:
        """Statement date, revaluation basis, and factors surface per scheme."""
        rows = self.structure_values(structural_household())
        assert rows["DB pension 1", "Membership"] == "Active (accruing)"
        assert rows["DB pension 1", "Statement date"] == "2025-04-06"
        assert rows["DB pension 1", "Revaluation"] == "CPI, capped at 0.05"
        assert rows["DB pension 1", "Early/late factors"] == "58: 0.80"
        assert rows["DB pension 2", "Membership"] == "Deferred"
        assert rows["DB pension 2", "Revaluation"] == "None (frozen in nominal terms)"
        assert rows["DB pension 2", "Early/late factors"] == (
            "None (taken at the normal pension age)"
        )
        assert rows["DB pension 3", "Revaluation"] == "Fixed 0.03 per year"
        assert rows["DB pension 4", "Revaluation"] == "CPI"

    def test_annuity_rows_show_type_and_basis(self) -> None:
        """The annuity purchase's structural choices surface per purchase."""
        rows = self.structure_values(structural_household())
        assert rows["Annuity purchase 1", "Annuity type"] == "Level"
        assert rows["Annuity purchase 1", "Annuity basis"] == "Single"

    def test_household_rows_show_spending_stages_and_outflow_timing(self) -> None:
        """Stage multipliers and outflow timing surface at household level."""
        rows = self.structure_values(structural_household())
        assert rows["Household", "Spending stages"] == "Decumulation: 0.9"
        assert rows["Mortgage payoff", "Due"] == "You at age 62"

    def test_flat_spending_says_so(self) -> None:
        """A spending plan without stage multipliers reads as flat."""
        rows = self.structure_values(household())
        assert rows["Household", "Spending stages"] == "None (flat spending)"

    def test_custom_glide_path_renders_its_knots(self) -> None:
        """A custom glide path shows its allocations, not just a count."""
        config = GlidePathConfig(
            points=(
                GlidePathPoint(
                    years_to_retirement=0,
                    allocation=AssetAllocation(
                        equity=Decimal("0.4"), bonds=Decimal("0.6")
                    ),
                ),
                GlidePathPoint(
                    years_to_retirement=15,
                    allocation=AssetAllocation(
                        equity=Decimal("0.8"), bonds=Decimal("0.2")
                    ),
                ),
            )
        )
        person = Person(
            id=EntityId("structure-glide-person"),
            date_of_birth=Fact(
                value=date(1991, 2, 1), as_of=AS_OF, recorded_on=RECORDED
            ),
            target_retirement_age=Decision(value=60, recorded_on=RECORDED),
            tax_residency=RUK_RESIDENCY,
            glide_path=config,
        )
        rows = self.structure_values(Household(persons=(person,)))
        assert rows["You", "Glide path"] == (
            "Custom — 0y out: Equity 0.4 / bonds 0.6 / cash 0;"
            " 15y out: Equity 0.8 / bonds 0.2 / cash 0"
        )
