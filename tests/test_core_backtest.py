"""Tests for the historical backtest window runner (issue 9.18).

Unit tests pin the series/window validation and the metric arithmetic
— success rate over windows, worst-window ordering, percentile
interpolation — over hand-built outcomes, and end-to-end tests drive
:func:`run_windows` through the engine over a minimal stub region: a
retired person spending from one tax-free wrapper under zero tax, so
every window's ledger is hand-computable. No randomness anywhere:
every expectation is exact.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.core import (
    AnnualCalendar,
    AssetAllocation,
    Assumption,
    AssumptionKey,
    AssumptionReadRecorder,
    AssumptionSet,
    BacktestResult,
    ContributionTaxTreatment,
    ContributionTerms,
    Decision,
    EngineError,
    EntityId,
    Fact,
    FeeSchedule,
    GrowthTaxTreatment,
    HistoricalSeries,
    HistoricalWindowModel,
    HistoricalYear,
    Household,
    MemberContributionOutcome,
    MemberContributionRequest,
    Money,
    PathParallelism,
    Period,
    Person,
    Provenance,
    Rate,
    Region,
    ReliefMechanic,
    RunConfig,
    RunMode,
    RunProvenance,
    SpendingPlan,
    StatePensionEntitlement,
    StatePensionRecord,
    TaxInput,
    TaxResidencyId,
    TaxResult,
    TrackedAssumptions,
    WindowOutcome,
    WithdrawalTaxTreatment,
    Wrapper,
    WrapperKindId,
    WrapperTaxTreatment,
    date_age_attained,
    is_age_attained_by_period_start,
    run_windows,
)

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 1, 1)
"""Matches the runs' ``today``: balances dated after it are §4.8 errors."""
RESIDENCY = TaxResidencyId("test.main")
FREE = WrapperKindId("test.free")
FIRST_SERIES_YEAR = 1900

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


@dataclass(frozen=True)
class ZeroTaxSystem:
    """No tax on anything — expectations stay hand-computable."""

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

    def contribution_terms(
        self, kind: WrapperKindId, date_of_birth: date, period: Period
    ) -> ContributionTerms:
        """Uncapped, no bonus, no window."""
        del kind, date_of_birth, period
        return ContributionTerms()

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
        self, record: StatePensionRecord, date_of_birth: date
    ) -> StatePensionEntitlement:
        """A zero entitlement starting at SPA."""
        del record
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


def assumptions_with(cpi: str = "0") -> AssumptionSet:
    """The minimal assumption catalogue a deterministic backtest reads."""
    values: dict[AssumptionKey, object] = {
        AssumptionKey.INFLATION_CPI: Decimal(cpi),
        AssumptionKey.RETURNS_EQUITY_REAL: Decimal("0.03"),
        AssumptionKey.RETURNS_BONDS_REAL: Decimal(0),
        AssumptionKey.RETURNS_CASH_REAL: Decimal(0),
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


def money_fact(amount: str) -> Fact[Money]:
    """A user-stated monetary fact."""
    return Fact(value=Money(Decimal(amount)), as_of=AS_OF, recorded_on=RECORDED)


def household_of(balance: str = "100000", spending: str = "10000") -> Household:
    """A retired person spending from one tax-free wrapper."""
    wrapper = Wrapper(
        id=EntityId("wrapper-1"),
        kind=FREE,
        balance=money_fact(balance),
        allocation=EQUITY_ONLY,
        fees=NO_FEES,
    )
    person = Person(
        id=EntityId("person-1"),
        date_of_birth=Fact(value=date(1960, 1, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=RESIDENCY,
        wrappers=(wrapper,),
    )
    plan = SpendingPlan(annual_spending_real=money_fact(spending))
    return Household(persons=(person,), spending=plan, planned_outflows=())


def config_of(years: int = 4) -> RunConfig:
    """A deterministic config over whole calendar-year periods from 2026."""
    return RunConfig(today=date(2026, 1, 1), horizon_end=date(2025 + years, 12, 31))


def year_of(
    year: int, equity: str, cpi: str = "0", bonds: str = "0", cash: str = "0"
) -> HistoricalYear:
    """One observed year, defaulting the classes this suite never spends."""
    return HistoricalYear(
        year=year,
        equity=Decimal(equity),
        bonds=Decimal(bonds),
        cash=Decimal(cash),
        cpi=Decimal(cpi),
    )


def series_of(*equity_rates: str, cpi: str = "0") -> HistoricalSeries:
    """A series of nominal equity rates from 1900, flat CPI, zero bonds/cash."""
    return HistoricalSeries(
        years=tuple(
            year_of(FIRST_SERIES_YEAR + offset, rate, cpi=cpi)
            for offset, rate in enumerate(equity_rates)
        )
    )


def tracked_view(assumptions: AssumptionSet) -> TrackedAssumptions:
    """A tracked read-through view for direct model tests."""
    return TrackedAssumptions(
        assumptions=assumptions, recorder=AssumptionReadRecorder()
    )


def outcome_of(
    window: int,
    ending: str,
    shortfall_year: int | None = None,
    balances: tuple[str, ...] | None = None,
) -> WindowOutcome:
    """A hand-built window outcome, ruined when a shortfall year is given."""
    period = None
    if shortfall_year is not None:
        period = Period(
            start=date(shortfall_year, 1, 1), end=date(shortfall_year, 12, 31)
        )
    closing: tuple[Money, ...] = ()
    if balances is not None:
        closing = tuple(Money(Decimal(balance)) for balance in balances)
    return WindowOutcome(
        window=window,
        start_year=FIRST_SERIES_YEAR + window,
        first_shortfall_period=period,
        ending_balance=Money(Decimal(ending)),
        closing_balances=closing,
    )


def result_of(*outcomes: WindowOutcome) -> BacktestResult:
    """A backtest result wrapping hand-built outcomes."""
    provenance = RunProvenance(
        facts=(),
        decisions=(),
        assumptions=(),
        region_data_version="stub region v1",
        seed=None,
    )
    return BacktestResult(
        outcomes=outcomes,
        config=config_of(),
        provenance=provenance,
        series=series_of("0"),
    )


class TestHistoricalYear:
    """The per-year observation's invariants."""

    def test_rejects_a_rate_at_or_below_minus_one(self) -> None:
        """A -100% observation cannot be recomposed into a real rate."""
        with pytest.raises(ValueError, match="cpi must be greater than -1"):
            year_of(1900, "0.05", cpi="-1")

    def test_real_rate_deflates_by_the_year_cpi(self) -> None:
        """(1 + 10%) / (1 + 10%) - 1 is exactly zero."""
        observed = year_of(1900, "0.10", cpi="0.10")
        assert observed.real_rate(observed.equity) == Decimal(0)


class TestHistoricalSeries:
    """The series invariants: contiguous ascending years."""

    def test_rejects_an_empty_series(self) -> None:
        """No observations, no windows."""
        with pytest.raises(ValueError, match="at least one year"):
            HistoricalSeries(years=())

    def test_rejects_a_gap_between_years(self) -> None:
        """A gap would silently splice unrelated history into a window."""
        years = (year_of(1900, "0.05"), year_of(1902, "0.05"))
        with pytest.raises(ValueError, match="contiguous"):
            HistoricalSeries(years=years)

    def test_reports_its_span(self) -> None:
        """First year, last year, and length read off the series."""
        series = series_of("0.01", "0.02", "0.03")
        assert series.first_year == 1900
        assert series.last_year == 1902
        assert series.length == 3


class TestHistoricalWindowModel:
    """The window model's mapping and recomposition."""

    def test_rejects_a_window_outside_the_series(self) -> None:
        """A window past the last observation replays nothing."""
        series = series_of("0.01", "0.02")
        tracked = tracked_view(assumptions_with())
        with pytest.raises(ValueError, match="within the series"):
            HistoricalWindowModel(
                series=series, window=2, anchor_year=2026, assumptions=tracked
            )

    def test_recomposes_the_real_rate_with_the_assumed_cpi(self) -> None:
        """A 10% nominal year under 10% realised CPI is a 0% real year.

        Recomposed with 2% assumed CPI the engine sees exactly 2%
        nominal — and the CPI the model reports is the assumed one,
        never the historical one (one inflation truth per run).
        """
        series = series_of("0.10", cpi="0.10")
        tracked = tracked_view(assumptions_with(cpi="0.02"))
        model = HistoricalWindowModel(
            series=series, window=0, anchor_year=2026, assumptions=tracked
        )
        period = Period(start=date(2026, 1, 1), end=date(2026, 12, 31))
        returns = model.returns_for(period, 0)
        assert returns.assets.equity.value == Decimal("0.02")
        assert returns.cpi.value == Decimal("0.02")

    def test_rejects_a_period_before_the_anchor(self) -> None:
        """A period predating the run anchor maps to no observation."""
        series = series_of("0.10")
        tracked = tracked_view(assumptions_with())
        model = HistoricalWindowModel(
            series=series, window=0, anchor_year=2026, assumptions=tracked
        )
        period = Period(start=date(2025, 1, 1), end=date(2025, 12, 31))
        with pytest.raises(EngineError, match="precedes the run anchor"):
            model.returns_for(period, 0)

    def test_rejects_a_period_past_the_series_end(self) -> None:
        """Running off the end of history is an error, never a wrap."""
        series = series_of("0.10", "0.10")
        tracked = tracked_view(assumptions_with())
        model = HistoricalWindowModel(
            series=series, window=1, anchor_year=2026, assumptions=tracked
        )
        period = Period(start=date(2027, 1, 1), end=date(2027, 12, 31))
        with pytest.raises(EngineError, match="series ends in 1901"):
            model.returns_for(period, 0)


class TestRunWindows:
    """The window runner end to end over the stub region."""

    def test_projects_one_window_per_complete_horizon(self) -> None:
        """A 6-year series over a 4-period horizon runs 3 windows."""
        result = run_windows(
            household_of(),
            assumptions_with(),
            stub_region(),
            config_of(years=4),
            series=series_of("0", "0", "0", "0", "0", "0"),
        )
        assert result.window_count == 3
        assert [outcome.start_year for outcome in result.outcomes] == [
            1900,
            1901,
            1902,
        ]

    def test_zero_real_history_is_hand_computable(self) -> None:
        """Zero returns and CPI drain the pot by exactly the spending."""
        result = run_windows(
            household_of(balance="100000", spending="10000"),
            assumptions_with(),
            stub_region(),
            config_of(years=4),
            series=series_of("0", "0", "0", "0"),
        )
        (outcome,) = result.outcomes
        expected = tuple(
            Money(Decimal(amount)) for amount in ("90000", "80000", "70000", "60000")
        )
        assert outcome.closing_balances == expected
        assert not outcome.ruined

    def test_equal_real_returns_project_identical_ledgers(self) -> None:
        """Only the real rate matters: 10% under 10% CPI equals 0% under 0%.

        The recomposition invariant end to end — two series with the
        same real returns produce byte-identical outcomes whatever
        their nominal split, because the engine's nominal path is
        rebuilt from the run's one assumed CPI.
        """
        flat = run_windows(
            household_of(),
            assumptions_with(cpi="0.02"),
            stub_region(),
            config_of(years=4),
            series=series_of("0", "0", "0", "0"),
        )
        inflated = run_windows(
            household_of(),
            assumptions_with(cpi="0.02"),
            stub_region(),
            config_of(years=4),
            series=series_of("0.10", "0.10", "0.10", "0.10", cpi="0.10"),
        )
        assert flat.outcomes == inflated.outcomes

    def test_a_crash_first_window_is_the_worst_starting_year(self) -> None:
        """Sequence of returns: the crash-first window ruins, later ones survive."""
        series = series_of("-0.5", "0.25", "0.25", "0.25", "0.25", "0.25")
        result = run_windows(
            household_of(balance="100000", spending="20000"),
            assumptions_with(),
            stub_region(),
            config_of(years=4),
            series=series,
        )
        assert result.failure_rate == Decimal(1) / Decimal(3)
        worst = result.worst_window
        assert worst.start_year == 1900
        assert worst.ruined

    def test_runs_are_reproducible(self) -> None:
        """Same plan and series → identical outcomes, no randomness (§4.6)."""
        series = series_of("-0.1", "0.2", "0.05", "0", "0.1")
        first = run_windows(
            household_of(),
            assumptions_with(),
            stub_region(),
            config_of(years=4),
            series=series,
        )
        second = run_windows(
            household_of(),
            assumptions_with(),
            stub_region(),
            config_of(years=4),
            series=series,
        )
        assert first.outcomes == second.outcomes

    def test_the_result_carries_the_series_it_replayed(self) -> None:
        """The §4.6 manifest side: any caller-supplied series is named.

        Two backtests over different series with the same plan and
        config must not be indistinguishable from their results.
        """
        series = series_of("0", "0", "0", "0")
        result = run_windows(
            household_of(),
            assumptions_with(),
            stub_region(),
            config_of(years=4),
            series=series,
        )
        assert result.series == series

    def test_provenance_records_the_cpi_but_no_expected_returns(self) -> None:
        """The manifest carries assumed CPI; series figures are data."""
        result = run_windows(
            household_of(),
            assumptions_with(),
            stub_region(),
            config_of(years=4),
            series=series_of("0", "0", "0", "0"),
        )
        keys = {entry.key for entry in result.provenance.assumptions}
        assert AssumptionKey.INFLATION_CPI in keys
        assert AssumptionKey.RETURNS_EQUITY_REAL not in keys

    def test_rejects_a_monte_carlo_config(self) -> None:
        """A backtest replays history; there is nothing to seed."""
        household = household_of()
        assumptions = assumptions_with()
        region = stub_region()
        config = RunConfig(
            today=date(2026, 1, 1),
            horizon_end=date(2029, 12, 31),
            mode=RunMode.MONTE_CARLO,
            seed=1,
        )
        series = series_of("0", "0", "0", "0")
        with pytest.raises(EngineError, match="DETERMINISTIC"):
            run_windows(household, assumptions, region, config, series=series)

    def test_rejects_a_series_shorter_than_the_horizon(self) -> None:
        """A 3-year series cannot cover a 4-period plan."""
        household = household_of()
        assumptions = assumptions_with()
        region = stub_region()
        config = config_of(years=4)
        series = series_of("0", "0", "0")
        with pytest.raises(EngineError, match="series ends in 1902"):
            run_windows(household, assumptions, region, config, series=series)

    def test_a_thread_executor_reproduces_the_serial_run(self) -> None:
        """Chunked execution changes nothing: same outcomes, provenance."""
        series = series_of("-0.1", "0.2", "0.05", "0", "0.1", "0.15", "-0.05")
        serial = run_windows(
            household_of(),
            assumptions_with(),
            stub_region(),
            config_of(years=4),
            series=series,
        )
        with ThreadPoolExecutor(max_workers=3) as executor:
            parallelism = PathParallelism(executor=executor, workers=3)
            parallel = run_windows(
                household_of(),
                assumptions_with(),
                stub_region(),
                config_of(years=4),
                series=series,
                parallelism=parallelism,
            )
        assert parallel == serial


class TestWindowOutcome:
    """The per-window reduction's invariants."""

    def test_ruined_reflects_the_shortfall_period(self) -> None:
        """A window is ruined exactly when a period fell short."""
        assert outcome_of(0, "100", shortfall_year=2027).ruined
        assert not outcome_of(0, "100").ruined

    def test_rejects_a_negative_window(self) -> None:
        """Window offsets are non-negative."""
        balance = Money(Decimal(100))
        with pytest.raises(ValueError, match="non-negative"):
            WindowOutcome(
                window=-1,
                start_year=1899,
                first_shortfall_period=None,
                ending_balance=balance,
            )

    def test_rejects_balances_that_do_not_end_at_the_ending_balance(self) -> None:
        """The per-period series and the ending balance are one record."""
        ending = Money(Decimal(100))
        drifted = (Money(Decimal(300)), Money(Decimal(200)))
        with pytest.raises(ValueError, match="must end at ending_balance"):
            WindowOutcome(
                window=0,
                start_year=1900,
                first_shortfall_period=None,
                ending_balance=ending,
                closing_balances=drifted,
            )


class TestBacktestMetrics:
    """Metric arithmetic over hand-built outcomes."""

    def test_failure_rate_is_the_ruined_fraction(self) -> None:
        """One ruined window in four is a 25% failure rate."""
        result = result_of(
            outcome_of(0, "100"),
            outcome_of(1, "0", shortfall_year=2028),
            outcome_of(2, "200"),
            outcome_of(3, "300"),
        )
        assert result.failure_rate == Decimal("0.25")
        assert result.success_rate == Decimal("0.75")

    def test_a_ruined_window_outranks_every_survivor(self) -> None:
        """Any window that fell short is worse than any that did not."""
        result = result_of(
            outcome_of(0, "1"),
            outcome_of(1, "500", shortfall_year=2029),
        )
        assert result.worst_window.start_year == 1901

    def test_the_earliest_shortfall_is_the_worst_ruin(self) -> None:
        """Among ruined windows, falling short sooner is worse."""
        result = result_of(
            outcome_of(0, "0", shortfall_year=2029),
            outcome_of(1, "0", shortfall_year=2027),
        )
        assert result.worst_window.start_year == 1901

    def test_the_lowest_ending_balance_is_the_worst_survivor(self) -> None:
        """With no ruin anywhere, the leanest ending pot is worst."""
        result = result_of(
            outcome_of(0, "300"), outcome_of(1, "100"), outcome_of(2, "200")
        )
        assert result.worst_window.start_year == 1901

    def test_ties_resolve_to_the_earliest_starting_year(self) -> None:
        """Identical outcomes name the earliest start, deterministically."""
        result = result_of(outcome_of(0, "100"), outcome_of(1, "100"))
        assert result.worst_window.start_year == 1900

    def test_percentiles_interpolate_between_order_statistics(self) -> None:
        """Fractional ranks interpolate linearly (the Monte Carlo rule)."""
        result = result_of(
            outcome_of(0, "100"),
            outcome_of(1, "200"),
            outcome_of(2, "300"),
            outcome_of(3, "400"),
        )
        assert result.ending_pot_percentile(Decimal(50)) == Money(Decimal(250))
        assert result.ending_pot_percentile(Decimal(25)) == Money(Decimal(175))

    def test_rejects_an_out_of_range_percentile(self) -> None:
        """Percentiles live in [0, 100]."""
        result = result_of(outcome_of(0, "100"))
        excessive = Decimal(101)
        with pytest.raises(ValueError, match="between 0 and 100"):
            result.ending_pot_percentile(excessive)

    def test_balance_percentiles_interpolate_period_by_period(self) -> None:
        """The band values apply the ending-pot convention per period."""
        result = result_of(
            outcome_of(0, "50", balances=("100", "50")),
            outcome_of(1, "150", balances=("200", "150")),
            outcome_of(2, "250", balances=("300", "250")),
        )
        median = result.balance_percentile(Decimal(50))
        floor = result.balance_percentile(Decimal(0))
        assert median == (Money(Decimal(200)), Money(Decimal(150)))
        assert floor == (Money(Decimal(100)), Money(Decimal(50)))

    def test_balance_percentiles_reject_mismatched_period_counts(self) -> None:
        """Bands over unaligned windows would chart the wrong periods."""
        result = result_of(
            outcome_of(0, "50", balances=("100", "50")),
            outcome_of(1, "150", balances=("150",)),
        )
        median = Decimal(50)
        with pytest.raises(ValueError, match="same periods"):
            result.balance_percentile(median)

    def test_balance_percentiles_validate_before_iterating(self) -> None:
        """Outcomes with no balance series must not swallow the check."""
        result = result_of(outcome_of(0, "50"))
        excessive = Decimal(101)
        with pytest.raises(ValueError, match="between 0 and 100"):
            result.balance_percentile(excessive)

    def test_rejects_an_empty_outcome_set(self) -> None:
        """Metrics over no windows are undefined."""
        config = config_of()
        provenance = RunProvenance(
            facts=(),
            decisions=(),
            assumptions=(),
            region_data_version="stub region v1",
            seed=None,
        )
        series = series_of("0")
        with pytest.raises(ValueError, match="at least one window"):
            BacktestResult(
                outcomes=(), config=config, provenance=provenance, series=series
            )
