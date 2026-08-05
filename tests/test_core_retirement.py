"""Tests for the "When can I retire?" solver (issue 9.14).

End-to-end over a minimal stub region — a saver spending from one
tax-free wrapper under zero tax, zero returns, and zero inflation — so
every expectation is hand-computable: a 100,000 pot funds exactly four
years of 25,000 spending, making age 60 the earliest retirement age
over a 2026-2033 horizon for a person born on 1970-01-01. The
acceptance criterion is pinned directly: the answer is consistent with
re-running the plan at that age, and the solver is deterministic.
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
    AssumptionSet,
    ContributionTaxTreatment,
    ContributionTerms,
    Decision,
    EngineError,
    EntityId,
    Fact,
    FeeSchedule,
    GrowthTaxTreatment,
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
    RetirementAgeSearch,
    RunConfig,
    RunMode,
    SpendingPlan,
    StatePensionEntitlement,
    StatePensionRecord,
    TaxInput,
    TaxResidencyId,
    TaxResult,
    WithdrawalTaxTreatment,
    Wrapper,
    WrapperKindId,
    WrapperTaxTreatment,
    date_age_attained,
    earliest_retirement_age,
    is_age_attained_by_period_start,
    run,
)

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 1, 1)
"""Matches the runs' ``today``: balances dated after it are §4.8 errors."""
RESIDENCY = TaxResidencyId("test.main")
FREE = WrapperKindId("test.free")
SEED = 20260804

ZERO = Money(Decimal(0))
ZERO_RATE = Rate(Decimal(0))
EQUITY_ONLY = AssetAllocation(equity=Decimal(1), bonds=Decimal(0))
NO_FEES = FeeSchedule(platform=ZERO_RATE, fund=ZERO_RATE)
TARGET = Money(Decimal(25000))
"""Exactly four years of a 100,000 pot under zero returns."""

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

    def bears_default_fees(self, kind: WrapperKindId) -> bool:
        """Every kind bears the default fee assumptions."""
        del kind
        return True

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


def flat_assumptions() -> AssumptionSet:
    """Zero inflation, zero returns, zero volatility, no fees."""
    values: dict[AssumptionKey, object] = {
        AssumptionKey.INFLATION_CPI: Decimal(0),
        AssumptionKey.RETURNS_EQUITY_REAL: Decimal(0),
        AssumptionKey.RETURNS_BONDS_REAL: Decimal(0),
        AssumptionKey.RETURNS_CASH_REAL: Decimal(0),
        AssumptionKey.VOLATILITY_EQUITY: Decimal(0),
        AssumptionKey.VOLATILITY_BONDS: Decimal(0),
        AssumptionKey.VOLATILITY_CASH: Decimal(0),
        AssumptionKey.CORRELATION_EQUITY_BONDS: Decimal("0.2"),
        AssumptionKey.CORRELATION_EQUITY_CASH: Decimal(0),
        AssumptionKey.CORRELATION_BONDS_CASH: Decimal("0.2"),
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


def household_of(
    balance: str = "100000",
    spending: str | None = None,
    stated_retirement_age: int = 65,
) -> Household:
    """A saver born 1970-01-01 with one tax-free wrapper.

    Aged exactly 56 on the runs' ``today`` (2026-01-01); attains age
    *A* on 1 January of ``1970 + A``, so retiring at *A* makes that
    whole calendar year the first retired period (§4.1 gate).
    """
    wrapper = Wrapper(
        id=EntityId("wrapper-1"),
        kind=FREE,
        balance=money_fact(balance),
        allocation=EQUITY_ONLY,
        fees=NO_FEES,
    )
    person = Person(
        id=EntityId("person-1"),
        date_of_birth=Fact(value=date(1970, 1, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(
            value=stated_retirement_age, recorded_on=RECORDED
        ),
        tax_residency=RESIDENCY,
        wrappers=(wrapper,),
    )
    plan = None
    if spending is not None:
        plan = SpendingPlan(annual_spending_real=money_fact(spending))
    return Household(persons=(person,), spending=plan)


def deterministic_config() -> RunConfig:
    """Whole calendar-year periods 2026 through 2033."""
    return RunConfig(today=date(2026, 1, 1), horizon_end=date(2033, 12, 31))


def mc_config(seed: int | None = SEED) -> RunConfig:
    """The same horizon under seeded Monte Carlo."""
    return RunConfig(
        today=date(2026, 1, 1),
        horizon_end=date(2033, 12, 31),
        mode=RunMode.MONTE_CARLO,
        seed=seed,
    )


def search_of(
    target: Money = TARGET,
    minimum_age: int = 56,
    maximum_age: int = 63,
    paths: int = 1,
    target_success_rate: Decimal = Decimal(1),
) -> RetirementAgeSearch:
    """A search over the ages the 2026-2033 horizon can express."""
    return RetirementAgeSearch(
        target_income=target,
        minimum_age=minimum_age,
        maximum_age=maximum_age,
        paths=paths,
        target_success_rate=target_success_rate,
    )


class TestRetirementAgeSearch:
    """The search parameter validation."""

    def test_rejects_a_non_positive_target_income(self) -> None:
        """A zero target answers nothing."""
        with pytest.raises(ValueError, match="target_income must be positive"):
            RetirementAgeSearch(target_income=ZERO, minimum_age=56, maximum_age=63)

    def test_rejects_a_negative_minimum_age(self) -> None:
        """Ages are non-negative."""
        with pytest.raises(ValueError, match="minimum_age must be non-negative"):
            RetirementAgeSearch(target_income=TARGET, minimum_age=-1, maximum_age=63)

    def test_rejects_a_backwards_bracket(self) -> None:
        """The bracket must contain at least one age."""
        with pytest.raises(ValueError, match="precedes"):
            RetirementAgeSearch(target_income=TARGET, minimum_age=63, maximum_age=56)

    def test_rejects_a_non_positive_path_count(self) -> None:
        """Zero paths measure nothing."""
        with pytest.raises(ValueError, match="paths must be positive"):
            RetirementAgeSearch(
                target_income=TARGET, minimum_age=56, maximum_age=63, paths=0
            )

    def test_rejects_an_off_range_success_target(self) -> None:
        """A target success rate lives in (0, 1]."""
        excessive = Decimal("1.5")
        with pytest.raises(ValueError, match=r"lie in \(0, 1\]"):
            RetirementAgeSearch(
                target_income=TARGET,
                minimum_age=56,
                maximum_age=63,
                target_success_rate=excessive,
            )


class TestDeterministicSolver:
    """The ascending scan under a deterministic config."""

    def test_finds_the_exact_earliest_age(self) -> None:
        """A 100,000 pot funds four years: age 60 on a 2033 horizon."""
        age = earliest_retirement_age(
            household_of(),
            flat_assumptions(),
            stub_region(),
            deterministic_config(),
            search_of(),
        )
        assert age == 60

    def test_the_answer_is_consistent_with_re_running_at_that_age(self) -> None:
        """Acceptance criterion: the answer reproduces under ``run``.

        The plan re-run with the answer as its retirement age and the
        target income as its spending shows no unmet need; one year
        earlier falls short.
        """
        answer = 60
        succeeding = household_of(spending="25000", stated_retirement_age=answer)
        failing = household_of(spending="25000", stated_retirement_age=answer - 1)
        at_answer = run(
            succeeding, flat_assumptions(), stub_region(), deterministic_config()
        )
        one_earlier = run(
            failing, flat_assumptions(), stub_region(), deterministic_config()
        )
        shortfalls_at_answer = [
            person.shortfall
            for snapshot in at_answer.snapshots
            for person in snapshot.persons
        ]
        assert all(shortfall == ZERO for shortfall in shortfalls_at_answer)
        assert any(
            person.shortfall > ZERO
            for snapshot in one_earlier.snapshots
            for person in snapshot.persons
        )

    def test_the_solver_is_deterministic(self) -> None:
        """Acceptance criterion: the same inputs answer the same age."""
        first = earliest_retirement_age(
            household_of(),
            flat_assumptions(),
            stub_region(),
            deterministic_config(),
            search_of(),
        )
        second = earliest_retirement_age(
            household_of(),
            flat_assumptions(),
            stub_region(),
            deterministic_config(),
            search_of(),
        )
        assert first == second

    def test_returns_none_when_no_age_in_the_bracket_meets(self) -> None:
        """A bracket ending before age 60 leaves every candidate short."""
        age = earliest_retirement_age(
            household_of(),
            flat_assumptions(),
            stub_region(),
            deterministic_config(),
            search_of(maximum_age=59),
        )
        assert age is None

    def test_returns_the_minimum_when_even_it_succeeds(self) -> None:
        """An age at or below the current age retires the plan now.

        Eight years of 10,000 fit a 100,000 pot, so even the
        already-attained age 50 candidate — retirement from the first
        period — succeeds and is returned as the earliest.
        """
        affordable = Money(Decimal(10000))
        age = earliest_retirement_age(
            household_of(),
            flat_assumptions(),
            stub_region(),
            deterministic_config(),
            search_of(target=affordable, minimum_age=50),
        )
        assert age == 50

    def test_the_stated_age_and_spending_are_irrelevant(self) -> None:
        """The search probes candidates, not the plan's stated choices."""
        stated_low = earliest_retirement_age(
            household_of(spending="99999", stated_retirement_age=57),
            flat_assumptions(),
            stub_region(),
            deterministic_config(),
            search_of(),
        )
        stated_high = earliest_retirement_age(
            household_of(spending="1", stated_retirement_age=63),
            flat_assumptions(),
            stub_region(),
            deterministic_config(),
            search_of(),
        )
        assert stated_low == stated_high
        assert stated_low == 60


class TestRetirementExposure:
    """Candidates past the horizon never succeed vacuously."""

    def test_an_age_beyond_the_horizon_is_not_a_vacuous_success(self) -> None:
        """Ages 64+ retire after the 2033 horizon and must fail.

        150,000 a year genuinely fails at every age with a retired
        period (even age 63's single year outspends the pot). Without
        the exposure gate, age 64 would activate no spending at all
        and "succeed" with the target never tested.
        """
        unaffordable = Money(Decimal(150000))
        age = earliest_retirement_age(
            household_of(),
            flat_assumptions(),
            stub_region(),
            deterministic_config(),
            search_of(target=unaffordable, minimum_age=60, maximum_age=70),
        )
        assert age is None

    def test_the_last_age_inside_the_horizon_can_still_succeed(self) -> None:
        """Age 63's single retired year is real exposure, not vacuous."""
        one_year_of_the_pot = Money(Decimal(100000))
        age = earliest_retirement_age(
            household_of(),
            flat_assumptions(),
            stub_region(),
            deterministic_config(),
            search_of(target=one_year_of_the_pot, minimum_age=60, maximum_age=70),
        )
        assert age == 63

    def test_monte_carlo_candidates_past_the_horizon_also_fail(self) -> None:
        """The exposure gate is mode-independent."""
        age = earliest_retirement_age(
            household_of(),
            flat_assumptions(),
            stub_region(),
            mc_config(),
            search_of(minimum_age=64, maximum_age=70, paths=2),
        )
        assert age is None

    def test_the_default_horizon_comes_from_the_planning_age(self) -> None:
        """Without an explicit horizon the planning-age assumption governs."""
        config = RunConfig(today=date(2026, 1, 1))
        affordable = Money(Decimal(2000))
        age = earliest_retirement_age(
            household_of(),
            flat_assumptions(),
            stub_region(),
            config,
            search_of(target=affordable, minimum_age=56, maximum_age=56),
        )
        assert age == 56


class TestMonteCarloSolver:
    """The same scan under a seeded Monte Carlo config."""

    def test_zero_volatility_matches_the_deterministic_answer(self) -> None:
        """With no volatility every path repeats the expected run."""
        age = earliest_retirement_age(
            household_of(),
            flat_assumptions(),
            stub_region(),
            mc_config(),
            search_of(paths=3),
        )
        assert age == 60

    def test_the_search_is_reproducible_from_the_seed(self) -> None:
        """Same plan, seed, and search → the same answer (§4.6)."""
        first = earliest_retirement_age(
            household_of(),
            flat_assumptions(),
            stub_region(),
            mc_config(),
            search_of(paths=2),
        )
        second = earliest_retirement_age(
            household_of(),
            flat_assumptions(),
            stub_region(),
            mc_config(),
            search_of(paths=2),
        )
        assert first == second

    def test_an_unseeded_monte_carlo_config_is_rejected(self) -> None:
        """An unseeded stochastic search could never be reproduced."""
        household = household_of()
        assumptions = flat_assumptions()
        region = stub_region()
        config = mc_config(seed=None)
        search = search_of(paths=2)
        with pytest.raises(EngineError, match=r"requires RunConfig\.seed"):
            earliest_retirement_age(household, assumptions, region, config, search)

    def test_a_parallel_search_reproduces_the_serial_answer(self) -> None:
        """One executor shared across candidates changes nothing (§4.6)."""
        serial = earliest_retirement_age(
            household_of(),
            flat_assumptions(),
            stub_region(),
            mc_config(),
            search_of(paths=4),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            parallelism = PathParallelism(executor=executor, workers=2)
            parallel = earliest_retirement_age(
                household_of(),
                flat_assumptions(),
                stub_region(),
                mc_config(),
                search_of(paths=4),
                parallelism=parallelism,
            )
        assert parallel == serial
        assert parallel == 60
