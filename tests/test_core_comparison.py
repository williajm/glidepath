"""Tests for the scenario comparison report (issue 6.3, planning §4.3).

Unit tests drive :func:`compare_scenario_results` over hand-built
projection results — alignment, deltas, period-union rows, per-period
person totals — and an end-to-end test drives :func:`run_scenarios`
through the engine over a minimal stub region, proving a scenario's
resolved run diffs against the base run with exact numbers.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.core import (
    AnnualCalendar,
    AssetAllocation,
    AssetReturns,
    Assumption,
    AssumptionKey,
    AssumptionSet,
    AssumptionTarget,
    ContributionTaxTreatment,
    Decision,
    EntityId,
    Fact,
    FeeSchedule,
    GrowthTaxTreatment,
    Household,
    LifeStage,
    MemberContributionOutcome,
    MemberContributionRequest,
    Money,
    Override,
    Period,
    PeriodReturns,
    PeriodSnapshot,
    Person,
    PersonPeriodResult,
    Provenance,
    Rate,
    Region,
    ReliefMechanic,
    ReportBasis,
    RunConfig,
    RunProvenance,
    Scenario,
    ScenarioError,
    StatePensionEntitlement,
    StatePensionRecord,
    TaxInput,
    TaxResidencyId,
    TaxResult,
    WithdrawalTaxTreatment,
    Wrapper,
    WrapperKindId,
    WrapperPeriodResult,
    WrapperTaxTreatment,
    compare_scenario_results,
    date_age_attained,
    is_age_attained_by_period_start,
    run_scenarios,
)
from glidepath.core.results import ProjectionResult

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 1, 1)
"""Matches the runs' ``today``: balances dated after it are §4.8 errors."""
RESIDENCY = TaxResidencyId("test.main")
FREE = WrapperKindId("test.free")

ZERO = Money(Decimal(0))
ZERO_RATE = Rate(Decimal(0))
EQUITY_ONLY = AssetAllocation(equity=Decimal(1), bonds=Decimal(0))
NO_FEES = FeeSchedule(platform=ZERO_RATE, fund=ZERO_RATE)

DEFAULT_SHAPE = {
    "equity_start": Decimal("0.8"),
    "derisk_years_before_retirement": 15,
    "equity_at_retirement": Decimal("0.4"),
    "transition": "linear",
    "in_drawdown": "hold",
}


def period_of(year: int) -> Period:
    """One whole calendar-year period."""
    return Period(start=date(year, 1, 1), end=date(year, 12, 31))


def zero_returns() -> PeriodReturns:
    """Flat markets and zero inflation."""
    assets = AssetReturns(equity=ZERO_RATE, bonds=ZERO_RATE, cash=ZERO_RATE)
    return PeriodReturns(assets=assets, cpi=ZERO_RATE)


def zero_tax() -> TaxResult:
    """No tax assessed."""
    return TaxResult(
        tax_due=ZERO, taxable_income=ZERO, tax_free_allowance=ZERO, lines=()
    )


def wrapper_result(closing: str) -> WrapperPeriodResult:
    """An all-zero wrapper year ending at ``closing``."""
    return WrapperPeriodResult(
        wrapper_id=EntityId("wrapper-1"),
        kind=FREE,
        allocation=EQUITY_ONLY,
        opening_uncrystallised=ZERO,
        opening_crystallised=ZERO,
        employee_contribution=ZERO,
        employer_contribution=ZERO,
        provider_relief=ZERO,
        contribution_shortfall=ZERO,
        withdrawal_tax_free=ZERO,
        withdrawal_taxable=ZERO,
        fee=ZERO,
        growth=ZERO,
        closing_uncrystallised=Money(Decimal(closing)),
        closing_crystallised=ZERO,
    )


def person_result(
    person_id: str, closing: str, employment: str = "0"
) -> PersonPeriodResult:
    """A quiet accumulation year for one person."""
    return PersonPeriodResult(
        person_id=EntityId(person_id),
        age_at_period_start=36,
        years_to_retirement=29,
        stage=LifeStage.EARLY_ACCUMULATION,
        employment_income=Money(Decimal(employment)),
        tax=zero_tax(),
        spending_need=ZERO,
        net_withdrawn=ZERO,
        shortfall=ZERO,
        wrappers=(wrapper_result(closing),),
    )


def snapshot_of(
    year: int,
    *persons: PersonPeriodResult,
    year_fraction: Decimal = Decimal(1),
) -> PeriodSnapshot:
    """One period's snapshot at zero inflation."""
    return PeriodSnapshot(
        period=period_of(year),
        returns=zero_returns(),
        inflation_factor=Decimal(1),
        persons=persons,
        year_fraction=year_fraction,
    )


def result_of(*snapshots: PeriodSnapshot) -> ProjectionResult:
    """A projection result wrapping hand-built snapshots."""
    provenance = RunProvenance(
        facts=(),
        decisions=(),
        assumptions=(),
        region_data_version="stub region v1",
        seed=None,
    )
    config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2027, 12, 31))
    return ProjectionResult(snapshots=snapshots, provenance=provenance, config=config)


class TestCompareScenarioResults:
    """Alignment and delta arithmetic over hand-built runs."""

    def test_requires_at_least_two_runs(self) -> None:
        """A comparison needs a baseline plus one other run."""
        runs = [("base", result_of(snapshot_of(2026, person_result("p", "100"))))]
        with pytest.raises(ValueError, match="at least two runs"):
            compare_scenario_results(runs)

    def test_requires_unique_run_names(self) -> None:
        """Two runs under one name would be indistinguishable."""
        result = result_of(snapshot_of(2026, person_result("p", "100")))
        runs = [("base", result), ("base", result)]
        with pytest.raises(ValueError, match="names must be unique"):
            compare_scenario_results(runs)

    def test_deltas_diff_each_run_against_the_base(self) -> None:
        """The base entry carries no delta; others diff field-wise."""
        base = result_of(snapshot_of(2026, person_result("p", "100")))
        what_if = result_of(snapshot_of(2026, person_result("p", "120", "50")))
        comparison = compare_scenario_results([("base", base), ("save", what_if)])
        assert comparison.run_names == ("base", "save")
        (row,) = comparison.rows
        assert row.period == period_of(2026)
        base_entry, save_entry = row.entries
        assert base_entry.run_name == "base"
        assert base_entry.delta_vs_base is None
        assert base_entry.metrics.closing_balance == Money(Decimal(100))
        assert save_entry.delta_vs_base is not None
        assert save_entry.delta_vs_base.closing_balance == Money(Decimal(20))
        assert save_entry.delta_vs_base.income_total == Money(Decimal(50))
        assert save_entry.delta_vs_base.tax_due == ZERO

    def test_rows_cover_the_union_of_periods(self) -> None:
        """A longer run contributes rows the base never modelled."""
        base = result_of(snapshot_of(2026, person_result("p", "100")))
        longer = result_of(
            snapshot_of(2026, person_result("p", "100")),
            snapshot_of(2027, person_result("p", "110")),
        )
        comparison = compare_scenario_results([("base", base), ("longer", longer)])
        first, second = comparison.rows
        assert [entry.run_name for entry in first.entries] == ["base", "longer"]
        (longer_only,) = second.entries
        assert longer_only.run_name == "longer"
        assert longer_only.delta_vs_base is None

    def test_persons_total_within_a_period(self) -> None:
        """Metrics are household-level: person rows sum per period."""
        base = result_of(snapshot_of(2026, person_result("p", "100")))
        couple = result_of(
            snapshot_of(2026, person_result("p", "100"), person_result("q", "60", "10"))
        )
        comparison = compare_scenario_results([("base", base), ("couple", couple)])
        (row,) = comparison.rows
        couple_entry = row.entries[1]
        assert couple_entry.metrics.closing_balance == Money(Decimal(160))
        assert couple_entry.metrics.income_total == Money(Decimal(10))

    def test_mismatched_intervals_suppress_the_delta(self) -> None:
        """A partial period never diffs against a whole one."""
        base = result_of(snapshot_of(2026, person_result("p", "100")))
        clipped = result_of(
            snapshot_of(2026, person_result("p", "50"), year_fraction=Decimal("0.5"))
        )
        comparison = compare_scenario_results([("base", base), ("clipped", clipped)])
        (row,) = comparison.rows
        base_entry, clipped_entry = row.entries
        assert base_entry.year_fraction == Decimal(1)
        assert clipped_entry.year_fraction == Decimal("0.5")
        assert clipped_entry.delta_vs_base is None

    def test_basis_is_recorded(self) -> None:
        """The chosen presentation basis lands on the comparison."""
        base = result_of(snapshot_of(2026, person_result("p", "100")))
        what_if = result_of(snapshot_of(2026, person_result("p", "120")))
        comparison = compare_scenario_results(
            [("base", base), ("save", what_if)], basis=ReportBasis.NOMINAL
        )
        assert comparison.basis is ReportBasis.NOMINAL


@dataclass(frozen=True)
class ZeroTaxSystem:
    """No tax on anything — comparison numbers stay hand-computable."""

    def assess(self, period: Period, tax_input: TaxInput) -> TaxResult:
        """Zero tax on the assessed income."""
        del period
        return TaxResult(
            tax_due=ZERO,
            taxable_income=tax_input.non_savings_income,
            tax_free_allowance=ZERO,
            lines=(),
        )


@dataclass(frozen=True)
class StubAges:
    """Flat state pension and access ages."""

    def state_pension_date(self, date_of_birth: date) -> date:
        """A flat SPA of 67."""
        return date_age_attained(date_of_birth, 67)

    def is_pension_access_open(self, date_of_birth: date, period: Period) -> bool:
        """A flat access age of 55."""
        return is_age_attained_by_period_start(date_of_birth, 55, period)


@dataclass(frozen=True)
class FreeWrapperRules:
    """One TEE wrapper kind: taxed in, tax-free growth and out."""

    def tax_treatment(self, kind: WrapperKindId, period: Period) -> WrapperTaxTreatment:
        """Wholly tax-free withdrawals."""
        del kind, period
        return WrapperTaxTreatment(
            contributions=ContributionTaxTreatment.FROM_TAXED_INCOME,
            growth=GrowthTaxTreatment.TAX_FREE,
            withdrawals=WithdrawalTaxTreatment.TAX_FREE,
        )

    def annual_contribution_limit(
        self, kind: WrapperKindId, period: Period
    ) -> Money | None:
        """Uncapped."""
        del kind, period
        return None

    def permitted_relief_mechanics(
        self, kind: WrapperKindId
    ) -> frozenset[ReliefMechanic]:
        """No relief mechanics."""
        del kind
        return frozenset()

    def lump_sum_allowance(self, period: Period) -> Money | None:
        """Uncapped."""
        del period
        return None

    def is_access_open(
        self, kind: WrapperKindId, date_of_birth: date, period: Period
    ) -> bool:
        """Always accessible."""
        del kind, date_of_birth, period
        return True


@dataclass(frozen=True)
class PassThroughContributions:
    """Contributions land gross with no relief."""

    def member_contribution(
        self, request: MemberContributionRequest, period: Period
    ) -> MemberContributionOutcome:
        """Gross in, gross cost, nothing else."""
        del period
        return MemberContributionOutcome(
            gross_to_pot=request.gross,
            member_cash_cost=request.gross,
            provider_relief=ZERO,
            taxable_pay_deduction=ZERO,
            assessment_relief_gross=ZERO,
            unrelieved_excess=ZERO,
        )


@dataclass(frozen=True)
class NoStatePension:
    """No entitlement for anyone."""

    def entitlement(
        self, record: StatePensionRecord, date_of_birth: date, today: date
    ) -> StatePensionEntitlement:
        """A zero entitlement starting at SPA."""
        del record, today
        return StatePensionEntitlement(
            start_date=date_age_attained(date_of_birth, 67),
            annual_amount=ZERO,
            cpi_uprated_annual_amount=ZERO,
            deferral_uplift=Decimal(0),
        )


def stub_region() -> Region:
    """A calendar-year region over the minimal stubs above."""
    return Region(
        calendar=AnnualCalendar(),
        ages=StubAges(),
        tax=ZeroTaxSystem(),
        wrappers=FreeWrapperRules(),
        contributions=PassThroughContributions(),
        state_pension=NoStatePension(),
        data_version="stub region v1",
    )


def stub_region_for(assumptions: AssumptionSet) -> Region:
    """A region factory for runs; the stubs carry no assumption-derived data."""
    del assumptions
    return stub_region()


def base_assumptions() -> AssumptionSet:
    """Zero inflation, 10% real equity return, no fees, planning age 95."""
    values: dict[AssumptionKey, object] = {
        AssumptionKey.INFLATION_CPI: Decimal(0),
        AssumptionKey.RETURNS_EQUITY_REAL: Decimal("0.10"),
        AssumptionKey.RETURNS_BONDS_REAL: Decimal(0),
        AssumptionKey.RETURNS_CASH_REAL: Decimal(0),
        AssumptionKey.EARNINGS_GROWTH_REAL: Decimal(0),
        AssumptionKey.FEES_PLATFORM: Decimal(0),
        AssumptionKey.FEES_FUND: Decimal(0),
        AssumptionKey.HORIZON_PLANNING_AGE: 95,
        AssumptionKey.GLIDEPATH_DEFAULT_SHAPE: DEFAULT_SHAPE,
    }
    return AssumptionSet(
        Assumption(
            key=key,
            value=value,
            default_value=value,
            provenance=Provenance.DEFAULT_ASSUMPTION,
            source="test basis",
            recorded_on=RECORDED,
            description="test assumption",
        )
        for key, value in values.items()
    )


def one_wrapper_household() -> Household:
    """One person, one tax-free wrapper holding 1000, no spending plan."""
    wrapper = Wrapper(
        id=EntityId("wrapper-1"),
        kind=FREE,
        balance=Fact(value=Money(Decimal(1000)), as_of=AS_OF, recorded_on=RECORDED),
        crystallised_balance=None,
        contributions=None,
        allocation=EQUITY_ONLY,
        fees=NO_FEES,
    )
    person = Person(
        id=EntityId("person-1"),
        date_of_birth=Fact(value=date(1990, 1, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=65, recorded_on=RECORDED),
        tax_residency=RESIDENCY,
        wrappers=(wrapper,),
    )
    return Household(persons=(person,))


def flat_markets_scenario() -> Scenario:
    """A what-if flattening the equity return to zero."""
    override = Override(
        target=AssumptionTarget(key=AssumptionKey.RETURNS_EQUITY_REAL),
        value=Decimal(0),
    )
    return Scenario(name="flat-markets", overrides=(override,))


class TestRunScenarios:
    """End-to-end: resolved scenario runs diffed against the base run."""

    def test_scenario_run_diffs_against_base(self) -> None:
        """A flat-markets scenario loses exactly the base's 10% growth."""
        config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2026, 12, 31))
        runs = run_scenarios(
            one_wrapper_household(),
            base_assumptions(),
            (flat_markets_scenario(),),
            stub_region_for,
            config,
        )
        assert [name for name, _ in runs] == ["base", "flat-markets"]
        comparison = compare_scenario_results(runs)
        (row,) = comparison.rows
        base_entry, flat_entry = row.entries
        assert base_entry.metrics.closing_balance == Money(Decimal(1100))
        assert flat_entry.metrics.closing_balance == Money(Decimal(1000))
        assert flat_entry.delta_vs_base is not None
        assert flat_entry.delta_vs_base.closing_balance == Money(Decimal(-100))

    def test_region_factory_receives_resolved_assumptions(self) -> None:
        """Each run's region builds from that run's effective assumptions.

        Regions derive data from assumptions at build time (the UK
        future-years extension and uprating policy), so a scenario's
        region must see the scenario's overrides, not the base values.
        """
        seen: list[AssumptionSet] = []

        def recording_region_for(assumptions: AssumptionSet) -> Region:
            seen.append(assumptions)
            return stub_region()

        config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2026, 12, 31))
        run_scenarios(
            one_wrapper_household(),
            base_assumptions(),
            (flat_markets_scenario(),),
            recording_region_for,
            config,
        )
        base_seen, scenario_seen = seen
        base_value = base_seen.get(AssumptionKey.RETURNS_EQUITY_REAL)
        assert base_value.value == Decimal("0.10")
        resolved = scenario_seen.get(AssumptionKey.RETURNS_EQUITY_REAL)
        assert resolved.value == Decimal(0)
        assert resolved.provenance is Provenance.SCENARIO_OVERRIDE

    def test_reserved_base_name_is_rejected(self) -> None:
        """No scenario may shadow the base run's name."""
        household = one_wrapper_household()
        assumptions = base_assumptions()
        config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2026, 12, 31))
        scenarios = (Scenario(name="base"),)
        with pytest.raises(ScenarioError, match="none may be 'base'"):
            run_scenarios(household, assumptions, scenarios, stub_region_for, config)

    def test_duplicate_scenario_names_are_rejected(self) -> None:
        """Two scenarios under one name would be indistinguishable."""
        household = one_wrapper_household()
        assumptions = base_assumptions()
        config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2026, 12, 31))
        scenarios = (Scenario(name="twin"), Scenario(name="twin"))
        with pytest.raises(ScenarioError, match="unique"):
            run_scenarios(household, assumptions, scenarios, stub_region_for, config)
