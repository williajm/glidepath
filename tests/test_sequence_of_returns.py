"""Sequence-of-returns fixtures (issue 7.4; planning §4.1, §5.2).

The same multiset of annual returns, applied in different orders during
decumulation, produces measurably different outcomes — the risk Monte
Carlo exists to surface (planning §5.2). A scripted return model plays
a fixed sequence through the ordinary engine via the
``return_model_factory`` injection point, so the demonstration runs the
exact step function real projections use:

- with no withdrawals the outcome is order-independent (growth factors
  commute), pinning that the demonstrated difference comes from the
  interaction of withdrawals and return order, nothing else;
- with fixed real withdrawals the good-returns-first order ends
  measurably richer than the bad-returns-first order; and
- at a higher spending level the bad-first order is ruined outright
  while the good-first order survives — same returns, different order,
  opposite outcomes.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from glidepath.core import (
    AnnualCalendar,
    AssetAllocation,
    AssetReturns,
    Assumption,
    AssumptionKey,
    AssumptionSet,
    ContributionTaxTreatment,
    ContributionTerms,
    Decision,
    EntityId,
    Fact,
    FeeSchedule,
    GrowthTaxTreatment,
    Household,
    MemberContributionOutcome,
    MemberContributionRequest,
    Money,
    Period,
    PeriodReturns,
    Person,
    Provenance,
    Rate,
    Region,
    ReliefMechanic,
    ReturnModel,
    ReturnModelFactory,
    RunConfig,
    SpendingPlan,
    StatePensionEntitlement,
    StatePensionRecord,
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
)

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 1, 1)
"""Matches the runs' ``today``: balances dated after it are §4.8 errors."""
RESIDENCY = TaxResidencyId("test.main")
FREE = WrapperKindId("test.free")
FIRST_YEAR = 2026

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

GOOD_FIRST = (
    Decimal("0.20"),
    Decimal("0.10"),
    Decimal("-0.05"),
    Decimal("-0.25"),
)
"""Four annual equity returns, best years first."""

BAD_FIRST = tuple(reversed(GOOD_FIRST))
"""The same four returns, worst years first."""


@dataclass(frozen=True)
class ZeroTaxSystem:
    """No tax on anything — outcomes stay hand-computable."""

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


@dataclass(frozen=True)
class ScriptedReturnModel:
    """Plays back a fixed all-equity return sequence at zero CPI.

    Year offset *n* from the run's first year draws the sequence's
    *n*-th return, whatever the path — the fixture scripts the market,
    so both compared runs see exactly the intended order.
    """

    sequence: tuple[Decimal, ...]

    def returns_for(self, period: Period, path: int, /) -> PeriodReturns:
        """The scripted return for the period's year offset."""
        del path
        rate = Rate(self.sequence[period.start.year - FIRST_YEAR])
        return PeriodReturns(
            assets=AssetReturns(equity=rate, bonds=ZERO_RATE, cash=ZERO_RATE),
            cpi=ZERO_RATE,
        )


def scripted(sequence: tuple[Decimal, ...]) -> ReturnModelFactory:
    """A factory injecting the scripted sequence into a run."""
    model = ScriptedReturnModel(sequence=sequence)

    def build(_tracked: TrackedAssumptions) -> ReturnModel:
        """The scripted model, whatever the run's assumptions."""
        return model

    return build


def money_fact(amount: str) -> Fact[Money]:
    """A user-stated monetary fact."""
    return Fact(value=Money(Decimal(amount)), as_of=AS_OF, recorded_on=RECORDED)


def household_of(spending: str | None) -> Household:
    """A retired person drawing from one 100,000 tax-free wrapper."""
    wrapper = Wrapper(
        id=EntityId("wrapper-1"),
        kind=FREE,
        balance=money_fact("100000"),
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
    return Household(persons=(person,), spending=plan)


def assumptions() -> AssumptionSet:
    """The keys a scripted run still reads: CPI and the glide shape."""
    values: dict[AssumptionKey, object] = {
        AssumptionKey.INFLATION_CPI: Decimal(0),
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


def four_year_config() -> RunConfig:
    """Four whole calendar-year periods, 2026 through 2029."""
    return RunConfig(today=date(2026, 1, 1), horizon_end=date(2029, 12, 31))


def project(spending: str | None, sequence: tuple[Decimal, ...]) -> tuple[Money, Money]:
    """Run the fixture; return the ending balance and total shortfall."""
    result = run(
        household_of(spending),
        assumptions(),
        stub_region(),
        four_year_config(),
        return_model_factory=scripted(sequence),
    )
    ending = ZERO
    shortfall = ZERO
    for snapshot in result.snapshots:
        for person in snapshot.persons:
            shortfall = shortfall + person.shortfall
    for person in result.snapshots[-1].persons:
        for wrapper in person.wrappers:
            ending = ending + wrapper.closing_balance
    return ending, shortfall


class TestSequenceOfReturns:
    """Same returns, different order → demonstrably different outcome."""

    def test_order_is_irrelevant_without_withdrawals(self) -> None:
        """Growth factors commute: no withdrawals, no sequence risk.

        100,000 x 1.20 x 1.10 x 0.95 x 0.75 = 94,050 whichever way
        the sequence runs — pinning that the differences below come
        from the interaction of withdrawals and return order alone.
        """
        good_ending, good_shortfall = project(None, GOOD_FIRST)
        bad_ending, bad_shortfall = project(None, BAD_FIRST)
        assert good_ending == Money(Decimal(94050))
        assert bad_ending == Money(Decimal(94050))
        assert good_shortfall == ZERO
        assert bad_shortfall == ZERO

    def test_withdrawals_make_the_order_matter(self) -> None:
        """Fixed 10,000 withdrawals: bad years first end 15,277.50 poorer.

        Each year withdraws before growth applies (§5.2 operation
        order), so the pot compounds to
        (((90,000 x 1.20 - 10,000) x 1.10 - 10,000) x 0.95 - 10,000)
        x 0.75 = 62,182.50 good-first, and 46,905 with the same
        returns reversed.
        """
        good_ending, good_shortfall = project("10000", GOOD_FIRST)
        bad_ending, bad_shortfall = project("10000", BAD_FIRST)
        assert good_ending == Money(Decimal("62182.50"))
        assert bad_ending == Money(Decimal(46905))
        assert good_shortfall == ZERO
        assert bad_shortfall == ZERO

    def test_adverse_early_returns_can_ruin_the_plan(self) -> None:
        """Fixed 25,000 withdrawals: only the bad-first order is ruined.

        Good-first the pot always covers the year's need and ends at
        14,381.25; reversed, the pot holds 5,156.25 entering 2029
        against a 25,000 need — ruin from exactly the same returns.
        """
        good_ending, good_shortfall = project("25000", GOOD_FIRST)
        bad_ending, bad_shortfall = project("25000", BAD_FIRST)
        assert good_ending == Money(Decimal("14381.25"))
        assert good_shortfall == ZERO
        assert bad_ending == ZERO
        assert bad_shortfall > ZERO
