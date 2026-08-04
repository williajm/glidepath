"""Tests for the Monte Carlo path runner and success metrics (issue 7.3).

Unit tests pin the metric arithmetic — probability of ruin, success
rate, ending-pot percentile interpolation — over hand-built outcomes,
and end-to-end tests drive :func:`run_paths` and
:func:`sustainable_income` through the engine over a minimal stub
region: a retired person spending from one tax-free wrapper under zero
tax, so every deterministic expectation is hand-computable.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.core import (
    AnnualCalendar,
    AssetAllocation,
    Assumption,
    AssumptionKey,
    AssumptionSet,
    ContributionTaxTreatment,
    ContributionTerms,
    Decision,
    DeterministicReturnModel,
    EngineError,
    EntityId,
    Fact,
    FeeSchedule,
    GrowthTaxTreatment,
    GuardrailsWithdrawalStrategy,
    Household,
    MemberContributionOutcome,
    MemberContributionRequest,
    Money,
    MonteCarloResult,
    PathOutcome,
    Period,
    Person,
    PlannedOutflow,
    Provenance,
    Rate,
    Region,
    ReliefMechanic,
    ReturnModel,
    RunConfig,
    RunMode,
    RunProvenance,
    SpendingPlan,
    StatePensionEntitlement,
    StatePensionRecord,
    SustainableIncomeSearch,
    TaxInput,
    TaxResidencyId,
    TaxResult,
    TrackedAssumptions,
    WithdrawalTaxTreatment,
    Wrapper,
    WrapperKindId,
    WrapperTaxTreatment,
    date_age_attained,
    is_age_attained_by_period_start,
    run,
    run_paths,
    sustainable_income,
)
from glidepath.core.montecarlo import _merged_provenance

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 1, 1)
"""Matches the runs' ``today``: balances dated after it are §4.8 errors."""
RESIDENCY = TaxResidencyId("test.main")
FREE = WrapperKindId("test.free")
SEED = 20260803

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

ZERO_VOLATILITY: dict[AssumptionKey, object] = {
    AssumptionKey.VOLATILITY_EQUITY: Decimal(0),
    AssumptionKey.VOLATILITY_BONDS: Decimal(0),
    AssumptionKey.VOLATILITY_CASH: Decimal(0),
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


def assumptions_with(
    overrides: dict[AssumptionKey, object] | None = None,
) -> AssumptionSet:
    """Zero inflation, 3% real equity with 15% volatility, no fees."""
    values: dict[AssumptionKey, object] = {
        AssumptionKey.INFLATION_CPI: Decimal(0),
        AssumptionKey.RETURNS_EQUITY_REAL: Decimal("0.03"),
        AssumptionKey.RETURNS_BONDS_REAL: Decimal(0),
        AssumptionKey.RETURNS_CASH_REAL: Decimal(0),
        AssumptionKey.VOLATILITY_EQUITY: Decimal("0.15"),
        AssumptionKey.VOLATILITY_BONDS: Decimal("0.05"),
        AssumptionKey.VOLATILITY_CASH: Decimal("0.01"),
        AssumptionKey.CORRELATION_EQUITY_BONDS: Decimal("0.2"),
        AssumptionKey.CORRELATION_EQUITY_CASH: Decimal(0),
        AssumptionKey.CORRELATION_BONDS_CASH: Decimal("0.2"),
        AssumptionKey.FEES_PLATFORM: Decimal(0),
        AssumptionKey.FEES_FUND: Decimal(0),
        AssumptionKey.HORIZON_PLANNING_AGE: 95,
        AssumptionKey.GLIDEPATH_DEFAULT_SHAPE: DEFAULT_SHAPE,
    }
    values |= overrides or {}
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


def assumption_of(key: AssumptionKey, value: object) -> Assumption[object]:
    """A shipped-default assumption for hand-built provenance."""
    return Assumption(
        key=key,
        value=value,
        default_value=value,
        provenance=Provenance.DEFAULT_ASSUMPTION,
        source="test basis",
        recorded_on=RECORDED,
        description="test assumption",
    )


def deterministic_model(tracked: TrackedAssumptions) -> ReturnModel:
    """A stand-in injected return-model factory."""
    return DeterministicReturnModel(assumptions=tracked)


def household_of(
    balance: str = "100000",
    spending: str | None = "10000",
    outflows: tuple[PlannedOutflow, ...] = (),
) -> Household:
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
    plan = None
    if spending is not None:
        plan = SpendingPlan(annual_spending_real=money_fact(spending))
    return Household(persons=(person,), spending=plan, planned_outflows=outflows)


def mc_config(seed: int | None = SEED, years: int = 4) -> RunConfig:
    """A Monte Carlo config over whole calendar-year periods from 2026."""
    return RunConfig(
        today=date(2026, 1, 1),
        horizon_end=date(2025 + years, 12, 31),
        mode=RunMode.MONTE_CARLO,
        seed=seed,
    )


def outcome_of(
    path: int,
    ending: str,
    shortfall_year: int | None = None,
    balances: tuple[str, ...] | None = None,
) -> PathOutcome:
    """A hand-built path outcome, ruined when a shortfall year is given."""
    period = None
    if shortfall_year is not None:
        period = Period(
            start=date(shortfall_year, 1, 1), end=date(shortfall_year, 12, 31)
        )
    closing: tuple[Money, ...] = ()
    if balances is not None:
        closing = tuple(Money(Decimal(balance)) for balance in balances)
    return PathOutcome(
        path=path,
        first_shortfall_period=period,
        ending_balance=Money(Decimal(ending)),
        closing_balances=closing,
    )


def result_of(*outcomes: PathOutcome) -> MonteCarloResult:
    """A Monte Carlo result wrapping hand-built outcomes."""
    provenance = RunProvenance(
        facts=(),
        decisions=(),
        assumptions=(),
        region_data_version="stub region v1",
        seed=SEED,
    )
    return MonteCarloResult(
        outcomes=outcomes, config=mc_config(), provenance=provenance
    )


class TestRunConfigPath:
    """The path index on the run configuration (roadmap 7.3)."""

    def test_path_defaults_to_zero(self) -> None:
        """A config names path 0 unless told otherwise."""
        assert mc_config().path == 0

    def test_negative_path_is_rejected(self) -> None:
        """A path index below zero names no substream."""
        today = date(2026, 1, 1)
        with pytest.raises(EngineError, match="non-negative"):
            RunConfig(today=today, path=-1)


class TestEngineModes:
    """Mode selection in the engine: one step function, two models."""

    def test_monte_carlo_without_seed_is_rejected(self) -> None:
        """An unseeded stochastic run could never be reproduced."""
        household = household_of()
        assumptions = assumptions_with()
        region = stub_region()
        config = mc_config(seed=None)
        with pytest.raises(EngineError, match=r"requires RunConfig\.seed"):
            run(household, assumptions, region, config)

    def test_an_injected_model_does_not_bypass_the_seed_requirement(self) -> None:
        """A Monte Carlo result must carry a seed, factory or not.

        The result's config labels it a Monte Carlo run; unseeded it
        could never be reproduced from its manifest (planning §4.6),
        however its returns were actually produced.
        """
        household = household_of()
        assumptions = assumptions_with()
        region = stub_region()
        config = mc_config(seed=None)
        with pytest.raises(EngineError, match=r"requires RunConfig\.seed"):
            run(
                household,
                assumptions,
                region,
                config,
                return_model_factory=deterministic_model,
            )

    def test_deterministic_run_ignores_the_path_index(self) -> None:
        """The deterministic model returns the same values on every path."""
        household = household_of()
        assumptions = assumptions_with()
        region = stub_region()
        base = RunConfig(today=date(2026, 1, 1), horizon_end=date(2029, 12, 31))
        other = RunConfig(
            today=date(2026, 1, 1), horizon_end=date(2029, 12, 31), path=7
        )
        first = run(household, assumptions, region, base)
        second = run(household, assumptions, region, other)
        assert first.snapshots == second.snapshots

    def test_monte_carlo_paths_draw_different_returns(self) -> None:
        """Distinct path indices read distinct substreams (planning §4.6)."""
        household = household_of()
        assumptions = assumptions_with()
        region = stub_region()
        result = run_paths(household, assumptions, region, mc_config(), paths=2)
        first, second = result.outcomes
        assert first.ending_balance != second.ending_balance


class TestRunPaths:
    """The path runner end to end over the stub region."""

    def test_projects_one_outcome_per_path_in_order(self) -> None:
        """Outcomes arrive as paths 0 through N-1."""
        result = run_paths(
            household_of(), assumptions_with(), stub_region(), mc_config(), paths=3
        )
        assert result.path_count == 3
        assert [outcome.path for outcome in result.outcomes] == [0, 1, 2]

    def test_runs_are_reproducible(self) -> None:
        """Same plan, seed, and path count → identical outcomes (§4.6)."""
        first = run_paths(
            household_of(), assumptions_with(), stub_region(), mc_config(), paths=3
        )
        second = run_paths(
            household_of(), assumptions_with(), stub_region(), mc_config(), paths=3
        )
        assert first.outcomes == second.outcomes

    def test_different_seeds_produce_different_outcomes(self) -> None:
        """The seed determines every draw."""
        first = run_paths(
            household_of(), assumptions_with(), stub_region(), mc_config(), paths=2
        )
        reseeded = mc_config(seed=SEED + 1)
        second = run_paths(
            household_of(), assumptions_with(), stub_region(), reseeded, paths=2
        )
        assert first.outcomes != second.outcomes

    def test_zero_volatility_degenerates_to_the_deterministic_run(self) -> None:
        """With no volatility every path repeats the expected-return run."""
        assumptions = assumptions_with(ZERO_VOLATILITY)
        deterministic = RunConfig(
            today=date(2026, 1, 1), horizon_end=date(2029, 12, 31)
        )
        expected = run(household_of(), assumptions, stub_region(), deterministic)
        ending = ZERO
        for wrapper in expected.snapshots[-1].persons[0].wrappers:
            ending = ending + wrapper.closing_balance
        result = run_paths(
            household_of(), assumptions, stub_region(), mc_config(), paths=3
        )
        assert all(outcome.ending_balance == ending for outcome in result.outcomes)

    def test_generous_pot_never_ruins(self) -> None:
        """Tiny spending from a large pot succeeds on every path."""
        result = run_paths(
            household_of(spending="1"),
            assumptions_with(),
            stub_region(),
            mc_config(),
            paths=4,
        )
        assert result.probability_of_ruin == Decimal(0)
        assert result.success_rate == Decimal(1)

    def test_impossible_spending_ruins_every_path_immediately(self) -> None:
        """Spending double the pot falls short in the first period."""
        first_period = Period(start=date(2026, 1, 1), end=date(2026, 12, 31))
        result = run_paths(
            household_of(spending="200000"),
            assumptions_with(),
            stub_region(),
            mc_config(),
            paths=3,
        )
        assert result.probability_of_ruin == Decimal(1)
        assert all(
            outcome.first_shortfall_period == first_period
            for outcome in result.outcomes
        )

    def test_outcomes_carry_the_per_period_household_balances(self) -> None:
        """Each path reduces to one closing balance per period (9.13)."""
        result = run_paths(
            household_of(), assumptions_with(), stub_region(), mc_config(), paths=2
        )
        for outcome in result.outcomes:
            assert len(outcome.closing_balances) == 4
            assert outcome.closing_balances[-1] == outcome.ending_balance
            assert all(balance >= ZERO for balance in outcome.closing_balances)

    def test_records_the_config_and_merged_provenance(self) -> None:
        """The result carries the §4.6 manifest side."""
        config = mc_config()
        result = run_paths(
            household_of(), assumptions_with(), stub_region(), config, paths=2
        )
        assert result.config is config
        assert result.provenance.seed == SEED

    def test_provenance_unions_assumptions_across_paths(self) -> None:
        """A key read on one path only still lands in the manifest.

        Almost every read is identical across paths, but natural-yield
        pricing fires only while a source holds money, so a stronger
        path can read keys an exhausted path never does — the union
        keeps the manifest exhaustive, in first-read order.
        """
        cpi = assumption_of(AssumptionKey.INFLATION_CPI, Decimal(0))
        equity_yield = assumption_of(AssumptionKey.YIELD_EQUITY, Decimal("0.02"))
        exhausted = RunProvenance(
            facts=(),
            decisions=(),
            assumptions=(cpi,),
            region_data_version="stub region v1",
            seed=SEED,
        )
        solvent = RunProvenance(
            facts=(),
            decisions=(),
            assumptions=(cpi, equity_yield),
            region_data_version="stub region v1",
            seed=SEED,
        )
        merged = _merged_provenance((exhausted, solvent))
        keys = [entry.key for entry in merged.assumptions]
        assert keys == [AssumptionKey.INFLATION_CPI, AssumptionKey.YIELD_EQUITY]

    def test_rejects_a_non_positive_path_count(self) -> None:
        """Zero paths measure nothing."""
        household = household_of()
        assumptions = assumptions_with()
        region = stub_region()
        config = mc_config()
        with pytest.raises(EngineError, match="paths must be positive"):
            run_paths(household, assumptions, region, config, paths=0)

    def test_rejects_a_deterministic_config(self) -> None:
        """The runner projects Monte Carlo configs only."""
        household = household_of()
        assumptions = assumptions_with()
        region = stub_region()
        config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2029, 12, 31))
        with pytest.raises(EngineError, match="MONTE_CARLO"):
            run_paths(household, assumptions, region, config, paths=2)


class TestPathOutcome:
    """The per-path reduction's invariants."""

    def test_ruined_reflects_the_shortfall_period(self) -> None:
        """A path is ruined exactly when a period fell short."""
        assert outcome_of(0, "100", shortfall_year=2027).ruined
        assert not outcome_of(0, "100").ruined

    def test_rejects_a_negative_path(self) -> None:
        """Path indices are non-negative."""
        balance = Money(Decimal(100))
        with pytest.raises(ValueError, match="non-negative"):
            PathOutcome(path=-1, first_shortfall_period=None, ending_balance=balance)

    def test_rejects_a_negative_ending_balance(self) -> None:
        """Balances never go below zero."""
        negative = Money(Decimal(-1))
        with pytest.raises(ValueError, match="non-negative"):
            PathOutcome(path=0, first_shortfall_period=None, ending_balance=negative)

    def test_rejects_balances_that_do_not_end_at_the_ending_balance(self) -> None:
        """The per-period series and the ending balance are one record."""
        ending = Money(Decimal(100))
        drifted = (Money(Decimal(300)), Money(Decimal(200)))
        with pytest.raises(ValueError, match="must end at ending_balance"):
            PathOutcome(
                path=0,
                first_shortfall_period=None,
                ending_balance=ending,
                closing_balances=drifted,
            )


class TestMonteCarloMetrics:
    """Metric arithmetic over hand-built outcomes."""

    def test_probability_of_ruin_is_the_ruined_fraction(self) -> None:
        """One ruined path in four is a 25% ruin probability."""
        result = result_of(
            outcome_of(0, "100"),
            outcome_of(1, "0", shortfall_year=2028),
            outcome_of(2, "200"),
            outcome_of(3, "300"),
        )
        assert result.probability_of_ruin == Decimal("0.25")
        assert result.success_rate == Decimal("0.75")

    def test_percentile_bounds_are_the_extremes(self) -> None:
        """Percentile 0 is the worst path; 100 the best."""
        result = result_of(
            outcome_of(0, "300"), outcome_of(1, "100"), outcome_of(2, "200")
        )
        assert result.ending_pot_percentile(Decimal(0)) == Money(Decimal(100))
        assert result.ending_pot_percentile(Decimal(100)) == Money(Decimal(300))

    def test_percentiles_interpolate_between_order_statistics(self) -> None:
        """Fractional ranks interpolate linearly (module convention)."""
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
        """Bands over unaligned paths would chart the wrong periods."""
        result = result_of(
            outcome_of(0, "50", balances=("100", "50")),
            outcome_of(1, "150", balances=("150",)),
        )
        median = Decimal(50)
        with pytest.raises(ValueError, match="same periods"):
            result.balance_percentile(median)

    def test_balance_percentiles_reject_an_out_of_range_percentile(self) -> None:
        """Percentiles live in [0, 100] here too."""
        result = result_of(outcome_of(0, "50", balances=("100", "50")))
        negative = Decimal(-1)
        with pytest.raises(ValueError, match="between 0 and 100"):
            result.balance_percentile(negative)

    def test_rejects_an_empty_outcome_set(self) -> None:
        """Metrics over no paths are undefined."""
        config = mc_config()
        provenance = RunProvenance(
            facts=(),
            decisions=(),
            assumptions=(),
            region_data_version="stub region v1",
            seed=SEED,
        )
        with pytest.raises(ValueError, match="at least one path"):
            MonteCarloResult(outcomes=(), config=config, provenance=provenance)


class TestSustainableIncomeSearch:
    """The search parameter validation."""

    def test_rejects_a_non_positive_path_count(self) -> None:
        """Zero paths measure nothing."""
        target = Decimal("0.9")
        maximum = Money(Decimal(50000))
        with pytest.raises(ValueError, match="paths must be positive"):
            SustainableIncomeSearch(
                paths=0, target_success_rate=target, maximum=maximum
            )

    def test_rejects_an_off_range_target(self) -> None:
        """A target success rate lives in (0, 1]."""
        excessive = Decimal("1.5")
        maximum = Money(Decimal(50000))
        with pytest.raises(ValueError, match=r"lie in \(0, 1\]"):
            SustainableIncomeSearch(
                paths=2, target_success_rate=excessive, maximum=maximum
            )

    def test_rejects_a_non_positive_maximum(self) -> None:
        """The search bracket needs a positive ceiling."""
        target = Decimal(1)
        zero = Money(Decimal(0))
        with pytest.raises(ValueError, match="maximum must be positive"):
            SustainableIncomeSearch(paths=2, target_success_rate=target, maximum=zero)

    def test_rejects_a_non_positive_tolerance(self) -> None:
        """A zero tolerance would bisect forever."""
        target = Decimal(1)
        maximum = Money(Decimal(50000))
        zero = Money(Decimal(0))
        with pytest.raises(ValueError, match="tolerance must be positive"):
            SustainableIncomeSearch(
                paths=2, target_success_rate=target, maximum=maximum, tolerance=zero
            )

    def test_rejects_a_non_positive_scan_step_count(self) -> None:
        """At least one scan cell is needed to bracket the search."""
        target = Decimal(1)
        maximum = Money(Decimal(50000))
        with pytest.raises(ValueError, match="scan_steps must be positive"):
            SustainableIncomeSearch(
                paths=2, target_success_rate=target, maximum=maximum, scan_steps=0
            )


class TestSustainableIncome:
    """The bisection over the stub region, pinned by exact arithmetic.

    Zero volatility, zero returns, and zero tax make the search exact:
    a 100,000 pot over four whole years sustains precisely 25,000 of
    annual spending, and the first bisection midpoint of a [0, 50,000]
    bracket lands on it exactly.
    """

    def test_finds_the_exact_sustainable_level(self) -> None:
        """A 100,000 pot over four years sustains 25,000 a year."""
        assumptions = assumptions_with(
            ZERO_VOLATILITY | {AssumptionKey.RETURNS_EQUITY_REAL: Decimal(0)}
        )
        search = SustainableIncomeSearch(
            paths=2,
            target_success_rate=Decimal(1),
            maximum=Money(Decimal(50000)),
            tolerance=Money(Decimal(100)),
        )
        income = sustainable_income(
            household_of(spending=None),
            assumptions,
            stub_region(),
            mc_config(),
            search,
        )
        assert income == Money(Decimal(25000))

    def test_returns_the_maximum_when_it_meets_the_target(self) -> None:
        """A ceiling the pot outlasts is returned unprobed further."""
        assumptions = assumptions_with(
            ZERO_VOLATILITY | {AssumptionKey.RETURNS_EQUITY_REAL: Decimal(0)}
        )
        search = SustainableIncomeSearch(
            paths=2,
            target_success_rate=Decimal(1),
            maximum=Money(Decimal(20000)),
        )
        income = sustainable_income(
            household_of(spending=None),
            assumptions,
            stub_region(),
            mc_config(),
            search,
        )
        assert income == Money(Decimal(20000))

    def test_returns_none_when_even_zero_spending_fails(self) -> None:
        """Outflows beyond the pot leave no sustainable income at all."""
        outflow = PlannedOutflow(
            id=EntityId("outflow-1"),
            label="unaffordable one-off",
            amount_real=Decision(value=Money(Decimal(200000)), recorded_on=RECORDED),
            at_age_of=(EntityId("person-1"), 67),
        )
        assumptions = assumptions_with(
            ZERO_VOLATILITY | {AssumptionKey.RETURNS_EQUITY_REAL: Decimal(0)}
        )
        search = SustainableIncomeSearch(
            paths=2,
            target_success_rate=Decimal(1),
            maximum=Money(Decimal(50000)),
        )
        income = sustainable_income(
            household_of(spending=None, outflows=(outflow,)),
            assumptions,
            stub_region(),
            mc_config(),
            search,
        )
        assert income is None

    def test_the_stated_spending_level_is_irrelevant(self) -> None:
        """The search probes spending levels, not the plan's stated one."""
        assumptions = assumptions_with(
            ZERO_VOLATILITY | {AssumptionKey.RETURNS_EQUITY_REAL: Decimal(0)}
        )
        search = SustainableIncomeSearch(
            paths=2,
            target_success_rate=Decimal(1),
            maximum=Money(Decimal(50000)),
            tolerance=Money(Decimal(100)),
        )
        frugal = sustainable_income(
            household_of(spending="1"),
            assumptions,
            stub_region(),
            mc_config(),
            search,
        )
        lavish = sustainable_income(
            household_of(spending="99999"),
            assumptions,
            stub_region(),
            mc_config(),
            search,
        )
        assert frugal == lavish
        assert frugal == Money(Decimal(25000))

    def test_the_scan_rescues_a_non_monotone_success_island(self) -> None:
        """Guardrails make success non-monotone; the scan still finds it.

        A 100,000 pot, zero returns, nine years, and a prosperity rise
        of 100%: spending just below 4,000 trips the 4% lower
        guardrail (an immediate doubled draw) and drains the pot into
        a capital-preservation cut — reported as shortfall, i.e. ruin
        — while exactly 4,000 sits on the guardrail (the trigger is
        strict) and survives every year. Pure bisection walks past
        that island: its midpoints below 4,000 fail, so it settles on
        3,828.125. The descending scan probes the 4,000 grid point
        directly and the upward refinement holds it (4,062.50 and
        above fail the 6% upper guardrail in the final year).
        """
        strategy = GuardrailsWithdrawalStrategy(rise_fraction=Decimal(1))
        config = RunConfig(
            today=date(2026, 1, 1),
            horizon_end=date(2034, 12, 31),
            mode=RunMode.MONTE_CARLO,
            seed=SEED,
            withdrawal_strategy=strategy,
        )
        assumptions = assumptions_with(
            ZERO_VOLATILITY | {AssumptionKey.RETURNS_EQUITY_REAL: Decimal(0)}
        )
        search = SustainableIncomeSearch(
            paths=2,
            target_success_rate=Decimal(1),
            maximum=Money(Decimal(10000)),
            tolerance=Money(Decimal(100)),
        )
        income = sustainable_income(
            household_of(spending=None), assumptions, stub_region(), config, search
        )
        assert income == Money(Decimal(4000))
