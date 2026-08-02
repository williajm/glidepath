"""Tests for result types, the return model, and assumption-value readers.

Covers the pieces the engine composes (issue 4.1): the snapshot and
provenance dataclass invariants, the deterministic return model's
nominal composition, the typed assumption-value readers, and the plan
fact/decision collectors.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import MappingProxyType

import pytest

from glidepath.core import (
    AnnualCalendar,
    AssetAllocation,
    Assumption,
    AssumptionKey,
    AssumptionReadRecorder,
    AssumptionSet,
    ContributionSchedule,
    Decision,
    DeterministicReturnModel,
    EntityId,
    Fact,
    Household,
    LifeStage,
    Money,
    Period,
    PeriodReturns,
    PeriodSnapshot,
    Person,
    PersonPeriodResult,
    Provenance,
    Rate,
    Region,
    SpendingPlan,
    TaxResidencyId,
    TaxResult,
    TrackedAssumptions,
    Wrapper,
    WrapperKindId,
    WrapperPeriodResult,
    collect_plan_decisions,
    collect_plan_facts,
    decimal_assumption_value,
    int_assumption_value,
    mapping_assumption_value,
    nominal_rate,
)
from glidepath.core.investments import AssetReturns

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)
ZERO = Money(Decimal(0))
ALLOCATION = AssetAllocation(equity=Decimal(1), bonds=Decimal(0))
KIND = WrapperKindId("test.kind")


def assumption(key: AssumptionKey, value: object) -> Assumption[object]:
    """A default-provenance assumption for tests."""
    return Assumption(
        key=key,
        value=value,
        default_value=value,
        provenance=Provenance.DEFAULT_ASSUMPTION,
        source="test basis",
        recorded_on=RECORDED,
        description="test assumption",
    )


def wrapper_result(**overrides: Money) -> WrapperPeriodResult:
    """An all-zero wrapper result, with per-field overrides."""
    fields: dict[str, Money] = {
        "opening_uncrystallised": ZERO,
        "opening_crystallised": ZERO,
        "employee_contribution": ZERO,
        "employer_contribution": ZERO,
        "provider_relief": ZERO,
        "contribution_shortfall": ZERO,
        "withdrawal_tax_free": ZERO,
        "withdrawal_taxable": ZERO,
        "fee": ZERO,
        "growth": ZERO,
        "closing_uncrystallised": ZERO,
        "closing_crystallised": ZERO,
    }
    fields.update(overrides)
    return WrapperPeriodResult(
        wrapper_id=EntityId("wrapper-1"), kind=KIND, allocation=ALLOCATION, **fields
    )


class TestResultInvariants:
    """Construction-time validation of the snapshot dataclasses."""

    def test_wrapper_result_rejects_negative_flows(self) -> None:
        """Every flow except growth must be non-negative."""
        negative_fee = Money(Decimal(-1))
        with pytest.raises(ValueError, match="non-negative"):
            wrapper_result(fee=negative_fee)

    def test_wrapper_result_allows_negative_growth(self) -> None:
        """A down period is a legitimate ledger entry."""
        result = wrapper_result(growth=Money(Decimal(-250)))
        assert result.growth == Money(Decimal(-250))

    def test_wrapper_result_totals(self) -> None:
        """The convenience totals combine the sub-balances and tax split."""
        result = wrapper_result(
            opening_uncrystallised=Money(Decimal(100)),
            opening_crystallised=Money(Decimal(50)),
            closing_uncrystallised=Money(Decimal(80)),
            closing_crystallised=Money(Decimal(40)),
            withdrawal_tax_free=Money(Decimal(10)),
            withdrawal_taxable=Money(Decimal(30)),
        )
        assert result.opening_balance == Money(Decimal(150))
        assert result.closing_balance == Money(Decimal(120))
        assert result.withdrawal_gross == Money(Decimal(40))

    def test_person_result_rejects_negative_amounts(self) -> None:
        """Income, need, delivery, and shortfall are all non-negative."""
        tax = TaxResult(
            tax_due=ZERO, taxable_income=ZERO, tax_free_allowance=ZERO, lines=()
        )
        person_id = EntityId("person-1")
        negative_income = Money(Decimal(-1))
        with pytest.raises(ValueError, match="non-negative"):
            PersonPeriodResult(
                person_id=person_id,
                age_at_period_start=40,
                years_to_retirement=25,
                stage=LifeStage.EARLY_ACCUMULATION,
                employment_income=negative_income,
                tax=tax,
                spending_need=ZERO,
                net_withdrawn=ZERO,
                shortfall=ZERO,
                wrappers=(),
            )

    def test_snapshot_rejects_non_positive_inflation_factor(self) -> None:
        """The cumulative CPI factor is strictly positive."""
        returns = PeriodReturns(
            assets=AssetReturns(
                equity=Rate(Decimal(0)), bonds=Rate(Decimal(0)), cash=Rate(Decimal(0))
            ),
            cpi=Rate(Decimal(0)),
        )
        period = Period(date(2026, 1, 1), date(2026, 12, 31))
        zero_factor = Decimal(0)
        with pytest.raises(ValueError, match="positive"):
            PeriodSnapshot(
                period=period,
                returns=returns,
                inflation_factor=zero_factor,
                persons=(),
            )

    def test_snapshot_rejects_a_year_fraction_outside_the_unit_interval(self) -> None:
        """The active fraction of a period is a share of it: [0, 1]."""
        returns = PeriodReturns(
            assets=AssetReturns(
                equity=Rate(Decimal(0)), bonds=Rate(Decimal(0)), cash=Rate(Decimal(0))
            ),
            cpi=Rate(Decimal(0)),
        )
        period = Period(date(2026, 1, 1), date(2026, 12, 31))
        beyond_whole = Decimal("1.01")
        whole_inflation = Decimal(1)
        with pytest.raises(ValueError, match="between 0 and 1"):
            PeriodSnapshot(
                period=period,
                returns=returns,
                inflation_factor=whole_inflation,
                persons=(),
                year_fraction=beyond_whole,
            )

    def test_period_returns_rejects_cpi_at_or_below_total_deflation(self) -> None:
        """A CPI at or below -100% is rejected up front.

        Exactly -1 would zero the cumulative inflation factor and blow
        up one period later, so it fails at construction instead.
        """
        assets = AssetReturns(
            equity=Rate(Decimal(0)), bonds=Rate(Decimal(0)), cash=Rate(Decimal(0))
        )
        total_deflation = Rate(Decimal(-1))
        with pytest.raises(ValueError, match="greater than -1"):
            PeriodReturns(assets=assets, cpi=total_deflation)


class TestSpendingPlanAndRegion:
    """Validation of the new plan and region bundle types."""

    def test_spending_plan_rejects_negative_spending(self) -> None:
        """A negative net need is a data error."""
        fact = Fact(value=Money(Decimal(-1)), as_of=AS_OF, recorded_on=RECORDED)
        with pytest.raises(ValueError, match="non-negative"):
            SpendingPlan(annual_spending_real=fact)

    def test_spending_plan_rejects_non_positive_multipliers(self) -> None:
        """Stage multipliers scale a need; zero or negative make no sense."""
        fact = Fact(value=Money(Decimal(1000)), as_of=AS_OF, recorded_on=RECORDED)
        multipliers = {LifeStage.DECUMULATION: Decimal(0)}
        with pytest.raises(ValueError, match="positive"):
            SpendingPlan(annual_spending_real=fact, stage_multipliers=multipliers)

    def test_region_requires_a_data_version(self) -> None:
        """The data version is part of the run manifest (planning §4.6)."""
        calendar = AnnualCalendar()
        with pytest.raises(ValueError, match="data_version"):
            Region(
                calendar=calendar,
                ages=None,  # type: ignore[arg-type]
                tax=None,  # type: ignore[arg-type]
                wrappers=None,  # type: ignore[arg-type]
                contributions=None,  # type: ignore[arg-type]
                data_version="",
            )


class TestReturnModel:
    """The deterministic return model and the nominal composition."""

    def test_nominal_rate_composes_exactly(self) -> None:
        """(1 + 0.04)(1 + 0.02) - 1 = 0.0608, exact in Decimal."""
        assert nominal_rate(Decimal("0.04"), Decimal("0.02")).value == Decimal("0.0608")

    def test_deterministic_model_reads_and_composes_assumptions(self) -> None:
        """Real returns and CPI become nominal rates; reads are recorded."""
        assumptions = AssumptionSet(
            (
                assumption(AssumptionKey.INFLATION_CPI, Decimal("0.02")),
                assumption(AssumptionKey.RETURNS_EQUITY_REAL, Decimal("0.04")),
                assumption(AssumptionKey.RETURNS_BONDS_REAL, Decimal("0.005")),
                assumption(AssumptionKey.RETURNS_CASH_REAL, Decimal("-0.005")),
            )
        )
        recorder = AssumptionReadRecorder()
        model = DeterministicReturnModel(
            assumptions=TrackedAssumptions(assumptions=assumptions, recorder=recorder)
        )
        period = Period(date(2026, 1, 1), date(2026, 12, 31))
        returns = model.returns_for(period, 0)
        assert returns.cpi == Rate(Decimal("0.02"))
        assert returns.assets.equity.value == Decimal("0.0608")
        assert returns.assets.bonds.value == Decimal("0.02510")
        assert returns.assets.cash.value == Decimal("0.01490")
        assert set(recorder.keys_read) == {
            AssumptionKey.INFLATION_CPI,
            AssumptionKey.RETURNS_EQUITY_REAL,
            AssumptionKey.RETURNS_BONDS_REAL,
            AssumptionKey.RETURNS_CASH_REAL,
        }


class TestAssumptionValueReaders:
    """The typed readers fail loudly on the wrong value shape."""

    def test_decimal_reader_accepts_decimal(self) -> None:
        """A Decimal value passes through exactly."""
        entry = assumption(AssumptionKey.INFLATION_CPI, Decimal("0.02"))
        assert decimal_assumption_value(entry) == Decimal("0.02")

    def test_decimal_reader_rejects_non_decimal(self) -> None:
        """An int is not a rate; no silent coercion (planning §4.6)."""
        entry = assumption(AssumptionKey.INFLATION_CPI, 2)
        with pytest.raises(TypeError, match="Decimal"):
            decimal_assumption_value(entry)

    def test_int_reader_accepts_int(self) -> None:
        """A whole number passes through."""
        entry = assumption(AssumptionKey.HORIZON_PLANNING_AGE, 95)
        assert int_assumption_value(entry) == 95

    def test_int_reader_rejects_bool_and_non_int(self) -> None:
        """A bool is an int subtype but never a count; both are rejected."""
        true_value: object = True
        as_bool = assumption(AssumptionKey.HORIZON_PLANNING_AGE, true_value)
        with pytest.raises(TypeError, match="integer"):
            int_assumption_value(as_bool)
        as_text = assumption(AssumptionKey.HORIZON_PLANNING_AGE, "95")
        with pytest.raises(TypeError, match="integer"):
            int_assumption_value(as_text)

    def test_mapping_reader_accepts_mapping_views(self) -> None:
        """Loader-produced mapping proxies are mappings."""
        shape = MappingProxyType({"equity_start": Decimal("0.8")})
        entry = assumption(AssumptionKey.GLIDEPATH_DEFAULT_SHAPE, shape)
        assert mapping_assumption_value(entry)["equity_start"] == Decimal("0.8")

    def test_mapping_reader_rejects_scalars(self) -> None:
        """A scalar where a table is expected is a configuration error."""
        entry = assumption(AssumptionKey.GLIDEPATH_DEFAULT_SHAPE, Decimal(1))
        with pytest.raises(TypeError, match="table"):
            mapping_assumption_value(entry)


def sample_household() -> Household:
    """A household exercising every optional fact and decision path."""
    schedule = ContributionSchedule(
        employee_amount=Decision(value=Money(Decimal(3000)), recorded_on=RECORDED),
        employer_amount=Fact(
            value=Money(Decimal(2000)), as_of=AS_OF, recorded_on=RECORDED
        ),
    )
    with_schedule = Wrapper(
        id=EntityId("wrapper-scheduled"),
        kind=KIND,
        balance=Fact(value=Money(Decimal(500)), as_of=AS_OF, recorded_on=RECORDED),
        contributions=schedule,
    )
    bare = Wrapper(
        id=EntityId("wrapper-bare"),
        kind=KIND,
        balance=Fact(value=Money(Decimal(100)), as_of=AS_OF, recorded_on=RECORDED),
    )
    person = Person(
        id=EntityId("person-1"),
        date_of_birth=Fact(value=date(1990, 1, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=65, recorded_on=RECORDED),
        tax_residency=TaxResidencyId("test.main"),
        employment_income=Fact(
            value=Money(Decimal(40000)), as_of=AS_OF, recorded_on=RECORDED
        ),
        wrappers=(with_schedule, bare),
    )
    spending = SpendingPlan(
        annual_spending_real=Fact(
            value=Money(Decimal(24000)), as_of=AS_OF, recorded_on=RECORDED
        )
    )
    return Household(persons=(person,), spending=spending)


class TestPlanCollectors:
    """The plan walkers behind ``RunProvenance.facts``/``decisions``."""

    def test_collect_plan_facts_labels_every_present_fact(self) -> None:
        """Facts appear under stable entity-id paths; absent ones do not."""
        labels = {entry.label for entry in collect_plan_facts(sample_household())}
        assert labels == {
            "household.spending.annual_spending_real",
            "person[person-1].date_of_birth",
            "person[person-1].employment_income",
            "wrapper[wrapper-scheduled].balance",
            "wrapper[wrapper-scheduled].contributions.employer_amount",
            "wrapper[wrapper-bare].balance",
        }

    def test_collect_plan_decisions_labels_every_choice(self) -> None:
        """Decisions are the scenario what-if whitelist (planning §4.3)."""
        labels = {entry.label for entry in collect_plan_decisions(sample_household())}
        assert labels == {
            "person[person-1].target_retirement_age",
            "wrapper[wrapper-scheduled].contributions.employee_amount",
        }
