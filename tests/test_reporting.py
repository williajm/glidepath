"""Tests for the real/nominal reporting layer (issue 4.4; planning §5.2).

The snapshots here are hand-built ledgers with clean factors (CPI path
1 then 1.1), so every deflated figure is hand-computable: the real
basis divides by each snapshot's own inflation factor and nothing else
— the engine's CPI path is the only inflation truth in play.
"""

from datetime import date
from decimal import Decimal

from glidepath.core import (
    AssetAllocation,
    AssetReturns,
    EntityId,
    LifeStage,
    Money,
    Period,
    PeriodReturns,
    PeriodSnapshot,
    PersonPeriodResult,
    ProjectionResult,
    Rate,
    ReportBasis,
    RunConfig,
    RunProvenance,
    TaxLine,
    TaxResult,
    WrapperKindId,
    WrapperPeriodResult,
    build_report,
)

ZERO = Money(Decimal(0))
ALLOCATION = AssetAllocation(equity=Decimal(1), bonds=Decimal(0))
KIND = WrapperKindId("test.kind")
PERSON = EntityId("person-1")

FLAT_RETURNS = PeriodReturns(
    assets=AssetReturns(
        equity=Rate(Decimal(0)), bonds=Rate(Decimal(0)), cash=Rate(Decimal(0))
    ),
    cpi=Rate(Decimal("0.10")),
)


def money(amount: str) -> Money:
    """A quantized ledger amount."""
    return Money(Decimal(amount))


def tax_of(amount: str) -> TaxResult:
    """A minimal self-consistent assessed-tax result."""
    due = money(amount)
    line = TaxLine(band="flat", rate=Rate(Decimal("0.25")), taxed=due, tax=due)
    return TaxResult(
        tax_due=due, taxable_income=due, tax_free_allowance=ZERO, lines=(line,)
    )


def wrapper_result(wrapper_id: str, **overrides: Money) -> WrapperPeriodResult:
    """An all-zero wrapper ledger with per-field overrides."""
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
        wrapper_id=EntityId(wrapper_id), kind=KIND, allocation=ALLOCATION, **fields
    )


def sample_result() -> ProjectionResult:
    """Two periods: an accumulation year, then an inflated drawdown year.

    Period one (factor 1) contributes 4,000 + 2,000 into one wrapper
    and 1,000 into another; period two (factor 1.1) withdraws an
    11,000 gross to meet an 11,000 nominal need, with 9,900 growth,
    a 110 fee, and 100 tax — every figure divisible by 1.1 except the
    tax, which pins the presentation rounding.
    """
    accumulation = PersonPeriodResult(
        person_id=PERSON,
        age_at_period_start=40,
        years_to_retirement=20,
        stage=LifeStage.MID_ACCUMULATION,
        employment_income=money("50000.00"),
        tax=tax_of("7000.00"),
        spending_need=ZERO,
        net_withdrawn=ZERO,
        shortfall=ZERO,
        wrappers=(
            wrapper_result(
                "wrapper-a",
                opening_uncrystallised=money("100000.00"),
                employee_contribution=money("4000.00"),
                employer_contribution=money("2000.00"),
                fee=money("100.00"),
                growth=money("1000.00"),
                closing_uncrystallised=money("106900.00"),
            ),
            wrapper_result(
                "wrapper-b",
                employee_contribution=money("1000.00"),
                closing_uncrystallised=money("1000.00"),
            ),
        ),
    )
    drawdown = PersonPeriodResult(
        person_id=PERSON,
        age_at_period_start=41,
        years_to_retirement=-1,
        stage=LifeStage.DECUMULATION,
        employment_income=ZERO,
        tax=tax_of("100.00"),
        spending_need=money("11000.00"),
        net_withdrawn=money("11000.00"),
        shortfall=ZERO,
        wrappers=(
            wrapper_result(
                "wrapper-a",
                opening_uncrystallised=money("107900.00"),
                withdrawal_tax_free=money("5500.00"),
                withdrawal_taxable=money("5500.00"),
                fee=money("110.00"),
                growth=money("9900.00"),
                closing_uncrystallised=money("108900.00"),
            ),
        ),
    )
    snapshots = (
        PeriodSnapshot(
            period=Period(date(2026, 1, 1), date(2026, 12, 31)),
            returns=FLAT_RETURNS,
            inflation_factor=Decimal(1),
            persons=(accumulation,),
        ),
        PeriodSnapshot(
            period=Period(date(2027, 1, 1), date(2027, 12, 31)),
            returns=FLAT_RETURNS,
            inflation_factor=Decimal("1.1"),
            persons=(drawdown,),
            year_fraction=Decimal("0.5"),
        ),
    )
    provenance = RunProvenance(
        facts=(), decisions=(), assumptions=(), region_data_version="stub", seed=None
    )
    config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2027, 6, 30))
    return ProjectionResult(snapshots=snapshots, provenance=provenance, config=config)


class TestRealBasis:
    """Real (today's money) is the default presentation."""

    def test_real_is_the_default_basis(self) -> None:
        """Calling without a basis presents today's money."""
        report = build_report(sample_result())
        assert report.basis is ReportBasis.REAL

    def test_first_period_amounts_are_already_todays_money(self) -> None:
        """A factor of 1 leaves the first period's amounts unchanged."""
        row = build_report(sample_result()).rows[0]
        assert row.deflator == Decimal(1)
        assert row.employment_income == money("50000.00")
        assert row.contributions == money("7000.00")
        assert row.fees == money("100.00")
        assert row.growth == money("1000.00")
        assert row.closing_balance == money("107900.00")

    def test_later_amounts_deflate_by_the_snapshot_inflation_factor(self) -> None:
        """Every nominal amount divides by the run's own CPI factor."""
        row = build_report(sample_result()).rows[1]
        assert row.deflator == Decimal("1.1")
        assert row.spending_need == money("10000.00")
        assert row.net_withdrawn == money("10000.00")
        assert row.withdrawals_gross == money("10000.00")
        assert row.fees == money("100.00")
        assert row.growth == money("9000.00")
        assert row.closing_balance == money("99000.00")

    def test_presentation_amounts_are_quantized(self) -> None:
        """100 / 1.1 presents as 90.91 — pennies, half-even."""
        row = build_report(sample_result()).rows[1]
        assert row.tax_due == money("90.91")

    def test_wrapper_balances_deflate_like_the_totals(self) -> None:
        """Per-wrapper closing balances share the row's deflator."""
        row = build_report(sample_result()).rows[1]
        [balance] = row.wrapper_balances
        assert balance.wrapper_id == EntityId("wrapper-a")
        assert balance.kind == KIND
        assert balance.closing_balance == money("99000.00")


class TestNominalBasis:
    """Nominal presents the ledger amounts unchanged."""

    def test_nominal_amounts_match_the_snapshots(self) -> None:
        """The deflator is 1; amounts are the engine's own figures."""
        report = build_report(sample_result(), ReportBasis.NOMINAL)
        assert report.basis is ReportBasis.NOMINAL
        row = report.rows[1]
        assert row.deflator == Decimal(1)
        assert row.spending_need == money("11000.00")
        assert row.closing_balance == money("108900.00")
        assert row.tax_due == money("100.00")


class TestRowShape:
    """Row identity fields come straight from the snapshots."""

    def test_rows_carry_period_person_stage_and_fraction(self) -> None:
        """One row per period per person, in period order."""
        rows = build_report(sample_result()).rows
        assert len(rows) == 2
        first, second = rows
        assert first.period == Period(date(2026, 1, 1), date(2026, 12, 31))
        assert first.person_id == PERSON
        assert first.age_at_period_start == 40
        assert first.stage is LifeStage.MID_ACCUMULATION
        assert first.year_fraction == Decimal(1)
        assert second.stage is LifeStage.DECUMULATION
        assert second.year_fraction == Decimal("0.5")

    def test_multi_wrapper_totals_sum_across_wrappers(self) -> None:
        """Contributions and balances aggregate the person's wrappers."""
        row = build_report(sample_result()).rows[0]
        assert row.contributions == money("7000.00")
        assert row.closing_balance == money("107900.00")
        assert len(row.wrapper_balances) == 2
