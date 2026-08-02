"""Tests for the deterministic engine step loop (issue 4.1, planning §5.2).

The stub region here is deliberately simple — a flat 25% tax rounded
down to the penny, two wrapper kinds (an EET pension and a tax-free
account, the latter optionally capped), and pass-through relief
mechanics — so every expected number is hand-computable and the
operation order of planning §5.2 is pinned by exact values, not
approximations.
"""

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, Decimal

import pytest

from glidepath.core import (
    AnnualCalendar,
    AssetAllocation,
    Assumption,
    AssumptionKey,
    AssumptionSet,
    ContributionSchedule,
    ContributionTaxTreatment,
    Decision,
    EngineError,
    EntityId,
    Fact,
    FeeSchedule,
    GlidePathConfig,
    GlidePathPoint,
    GrowthTaxTreatment,
    Household,
    LifeStage,
    MemberContributionOutcome,
    MemberContributionRequest,
    Money,
    Period,
    Person,
    Provenance,
    Rate,
    Region,
    ReliefMechanic,
    RunConfig,
    SpendingPlan,
    TaxInput,
    TaxLine,
    TaxResidencyId,
    TaxResult,
    WithdrawalTaxTreatment,
    Wrapper,
    WrapperKindId,
    WrapperTaxTreatment,
    date_age_attained,
    is_age_attained_by_period_start,
    run,
)

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)
RESIDENCY = TaxResidencyId("test.main")

PENSION = WrapperKindId("test.pension")
FREE = WrapperKindId("test.free")

TAX_RATE = Decimal("0.25")
PENSION_FREE_FRACTION = Decimal("0.25")
RAS_RATE = Decimal("0.20")

ZERO = Money(Decimal(0))
PENNY = Decimal("0.01")
EQUITY_ONLY = AssetAllocation(equity=Decimal(1), bonds=Decimal(0))
NO_FEES = FeeSchedule(platform=Rate(Decimal(0)), fund=Rate(Decimal(0)))

DEFAULT_SHAPE = {
    "equity_start": Decimal("0.8"),
    "derisk_years_before_retirement": 15,
    "equity_at_retirement": Decimal("0.4"),
    "transition": "linear",
    "in_drawdown": "hold",
}


def money_fact(amount: str) -> Fact[Money]:
    """A user-stated monetary fact."""
    return Fact(value=Money(Decimal(amount)), as_of=AS_OF, recorded_on=RECORDED)


@dataclass(frozen=True)
class FlatTaxSystem:
    """Flat 25% on every pound of non-savings income, floored to the penny."""

    def assess(self, period: Period, tax_input: TaxInput) -> TaxResult:
        """One flat band; relief-at-source amounts are ignored by design."""
        del period
        taxed = tax_input.non_savings_income
        if taxed <= ZERO:
            return TaxResult(
                tax_due=ZERO, taxable_income=taxed, tax_free_allowance=ZERO, lines=()
            )
        tax = Money((TAX_RATE * taxed.amount).quantize(PENNY, rounding=ROUND_DOWN))
        line = TaxLine(band="flat", rate=Rate(TAX_RATE), taxed=taxed, tax=tax)
        return TaxResult(
            tax_due=tax, taxable_income=taxed, tax_free_allowance=ZERO, lines=(line,)
        )


@dataclass(frozen=True)
class StubAges:
    """Minimal age rules for the region bundle."""

    def state_pension_date(self, date_of_birth: date) -> date:
        """A flat SPA of 67."""
        return date_age_attained(date_of_birth, 67)

    def is_pension_access_open(self, date_of_birth: date, period: Period) -> bool:
        """A flat access age of 55, per the §4.1 gate convention."""
        return is_age_attained_by_period_start(date_of_birth, 55, period)


@dataclass(frozen=True)
class StubWrapperRules:
    """Two wrapper kinds: an EET pension and a TEE tax-free account."""

    access_age: int = 55
    free_kind_cap: Money | None = None

    def tax_treatment(self, kind: WrapperKindId, period: Period) -> WrapperTaxTreatment:
        """The pension pays out 25% tax-free; the free kind wholly so."""
        del period
        if kind == PENSION:
            return WrapperTaxTreatment(
                contributions=ContributionTaxTreatment.TAX_RELIEVED,
                growth=GrowthTaxTreatment.TAX_FREE,
                withdrawals=WithdrawalTaxTreatment.PARTIALLY_TAX_FREE,
                tax_free_fraction=Rate(PENSION_FREE_FRACTION),
            )
        if kind == FREE:
            return WrapperTaxTreatment(
                contributions=ContributionTaxTreatment.FROM_TAXED_INCOME,
                growth=GrowthTaxTreatment.TAX_FREE,
                withdrawals=WithdrawalTaxTreatment.TAX_FREE,
            )
        msg = f"unknown stub wrapper kind {kind!r}"
        raise ValueError(msg)

    def annual_contribution_limit(
        self, kind: WrapperKindId, period: Period
    ) -> Money | None:
        """Only the free kind carries a per-kind cap here."""
        del period
        if kind == FREE:
            return self.free_kind_cap
        return None

    def permitted_relief_mechanics(
        self, kind: WrapperKindId
    ) -> frozenset[ReliefMechanic]:
        """The pension may operate either mechanic; the free kind none."""
        if kind == PENSION:
            return frozenset({ReliefMechanic.RELIEF_AT_SOURCE, ReliefMechanic.NET_PAY})
        return frozenset()

    def is_access_open(
        self, kind: WrapperKindId, date_of_birth: date, period: Period
    ) -> bool:
        """Pension access is age-gated; the free kind is always open."""
        if kind == PENSION:
            return is_age_attained_by_period_start(
                date_of_birth, self.access_age, period
            )
        return True


@dataclass
class RecordingContributionRules:
    """Pass-through relief mechanics that record every request."""

    requests: list[MemberContributionRequest] = dataclass_field(default_factory=list)

    def member_contribution(
        self, request: MemberContributionRequest, period: Period
    ) -> MemberContributionOutcome:
        """RAS grants 20% at source; net pay deducts from pay; no limits."""
        del period
        self.requests.append(request)
        if request.mechanic is None:
            return MemberContributionOutcome(
                gross_to_pot=request.gross,
                member_cash_cost=request.gross,
                provider_relief=ZERO,
                taxable_pay_deduction=ZERO,
                assessment_relief_gross=ZERO,
                unrelieved_excess=ZERO,
            )
        if request.mechanic is ReliefMechanic.NET_PAY:
            return MemberContributionOutcome(
                gross_to_pot=request.gross,
                member_cash_cost=request.gross,
                provider_relief=ZERO,
                taxable_pay_deduction=request.gross,
                assessment_relief_gross=ZERO,
                unrelieved_excess=ZERO,
            )
        relief = Money(RAS_RATE * request.gross.amount)
        return MemberContributionOutcome(
            gross_to_pot=request.gross,
            member_cash_cost=request.gross - relief,
            provider_relief=relief,
            taxable_pay_deduction=ZERO,
            assessment_relief_gross=request.gross,
            unrelieved_excess=ZERO,
        )


def stub_region(
    contributions: RecordingContributionRules | None = None,
    free_kind_cap: Money | None = None,
) -> Region:
    """A calendar-year region over the stub implementations."""
    return Region(
        calendar=AnnualCalendar(),
        ages=StubAges(),
        tax=FlatTaxSystem(),
        wrappers=StubWrapperRules(free_kind_cap=free_kind_cap),
        contributions=contributions or RecordingContributionRules(),
        data_version="stub region v1",
    )


def assumption(key: AssumptionKey, value: object) -> Assumption[object]:
    """A shipped-default assumption for tests."""
    return Assumption(
        key=key,
        value=value,
        default_value=value,
        provenance=Provenance.DEFAULT_ASSUMPTION,
        source="test basis",
        recorded_on=RECORDED,
        description="test assumption",
    )


def assumptions_with(overrides: dict[str, object] | None = None) -> AssumptionSet:
    """The baseline stub assumption set, with per-test value overrides.

    Baseline: zero inflation, 10% real equity, zero bond/cash returns,
    zero default fees, planning age 95, the standard glide shape.
    """
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
    for name, value in (overrides or {}).items():
        values[AssumptionKey(name)] = value
    return AssumptionSet(assumption(key, value) for key, value in values.items())


def wrapper_of(
    kind: WrapperKindId,
    balance: str,
    *,
    crystallised: str | None = None,
    schedule: ContributionSchedule | None = None,
    allocation: AssetAllocation | None = EQUITY_ONLY,
) -> Wrapper:
    """A zero-fee wrapper, allocated wholly to equity unless overridden."""
    return Wrapper(
        id=EntityId(f"wrapper-{kind}-{balance}-{crystallised}"),
        kind=kind,
        balance=money_fact(balance),
        crystallised_balance=None if crystallised is None else money_fact(crystallised),
        contributions=schedule,
        allocation=allocation,
        fees=NO_FEES,
    )


def person_of(
    wrappers: tuple[Wrapper, ...],
    *,
    date_of_birth: date = date(1990, 1, 1),
    retire_at: int = 65,
    employment: str | None = None,
) -> Person:
    """A single test person."""
    return Person(
        id=EntityId("person-1"),
        date_of_birth=Fact(value=date_of_birth, as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=retire_at, recorded_on=RECORDED),
        tax_residency=RESIDENCY,
        employment_income=None if employment is None else money_fact(employment),
        wrappers=wrappers,
    )


def household_of(person: Person, *, spending: str | None = None) -> Household:
    """A single-person household, optionally with a spending plan."""
    plan = None
    if spending is not None:
        plan = SpendingPlan(annual_spending_real=money_fact(spending))
    return Household(persons=(person,), spending=plan)


def one_period_config() -> RunConfig:
    """A single calendar-year period starting 2026."""
    return RunConfig(today=date(2026, 1, 1), horizon_end=date(2026, 6, 1))


class TestOperationOrder:
    """The §5.2 in-period operation order, pinned by exact hand-worked sums."""

    def test_accumulation_period_follows_the_spec_order(self) -> None:
        """Contributions land before fees; fees come off before growth.

        Opening 10,000; contributions 4,000 (RAS employee) + 2,000
        (employer) make the post-flow balance 16,000. The 1% platform
        fee applies to the average of 10,000 and 16,000 → 130. Growth
        of 10% then applies to 15,870 → 1,587. Closing: 17,457. Any
        other ordering produces a different number.
        """
        schedule = ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(4000)), recorded_on=RECORDED),
            employer_amount=money_fact("2000"),
            relief_mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
        )
        pension = Wrapper(
            id=EntityId("wrapper-fee-charging"),
            kind=PENSION,
            balance=money_fact("10000"),
            contributions=schedule,
            allocation=EQUITY_ONLY,
            fees=FeeSchedule(platform=Rate(Decimal("0.01")), fund=Rate(Decimal(0))),
        )
        plan = household_of(person_of((pension,), employment="50000"))
        result = run(plan, assumptions_with(), stub_region(), one_period_config())

        assert len(result.snapshots) == 1
        [person_result] = result.snapshots[0].persons
        [wrapper_result] = person_result.wrappers
        assert person_result.employment_income == Money(Decimal("50000.00"))
        assert wrapper_result.opening_uncrystallised == Money(Decimal("10000.00"))
        assert wrapper_result.employee_contribution == Money(Decimal("4000.00"))
        assert wrapper_result.employer_contribution == Money(Decimal("2000.00"))
        assert wrapper_result.provider_relief == Money(Decimal("800.00"))
        assert wrapper_result.fee == Money(Decimal("130.00"))
        assert wrapper_result.growth == Money(Decimal("1587.00"))
        assert wrapper_result.closing_uncrystallised == Money(Decimal("17457.00"))

    def test_income_is_assessed_before_the_period_closes(self) -> None:
        """Step 5 assesses the full income picture: flat 25% of 50,000."""
        plan = household_of(person_of((), employment="50000"))
        result = run(plan, assumptions_with(), stub_region(), one_period_config())
        [person_result] = result.snapshots[0].persons
        assert person_result.tax.tax_due == Money(Decimal("12500.00"))

    def test_net_pay_contributions_reduce_assessed_income(self) -> None:
        """A net-pay deduction leaves pay before tax (planning §5.1)."""
        schedule = ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(4000)), recorded_on=RECORDED),
            relief_mechanic=ReliefMechanic.NET_PAY,
        )
        pension = wrapper_of(PENSION, "0", schedule=schedule)
        plan = household_of(person_of((pension,), employment="50000"))
        result = run(plan, assumptions_with(), stub_region(), one_period_config())
        [person_result] = result.snapshots[0].persons
        assert person_result.tax.taxable_income == Money(Decimal(46000))
        assert person_result.tax.tax_due == Money(Decimal("11500.00"))


class TestContributions:
    """Step 3: schedules, shared relief headroom, and per-kind caps."""

    def test_relief_headroom_threads_across_wrappers(self) -> None:
        """The second wrapper's request carries the first's relieved gross."""
        recording = RecordingContributionRules()
        schedule_a = ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(4000)), recorded_on=RECORDED),
            relief_mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
        )
        schedule_b = ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(1500)), recorded_on=RECORDED),
            relief_mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
        )
        pension_a = wrapper_of(PENSION, "1000", schedule=schedule_a)
        pension_b = Wrapper(
            id=EntityId("wrapper-second-pension"),
            kind=PENSION,
            balance=money_fact("2000"),
            contributions=schedule_b,
            allocation=EQUITY_ONLY,
            fees=NO_FEES,
        )
        plan = household_of(person_of((pension_a, pension_b), employment="50000"))
        run(plan, assumptions_with(), stub_region(recording), one_period_config())
        first, second = recording.requests
        assert first.already_relieved_gross == ZERO
        assert second.already_relieved_gross == Money(Decimal(4000))
        assert first.relevant_earnings == Money(Decimal(50000))

    def test_per_kind_cap_clips_and_reports_shortfall(self) -> None:
        """Contributions beyond a kind's cap are clipped, not contributed."""
        schedule = ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(6000)), recorded_on=RECORDED),
        )
        account = wrapper_of(FREE, "0", schedule=schedule)
        plan = household_of(person_of((account,), employment="50000"))
        result = run(
            plan,
            assumptions_with(),
            stub_region(free_kind_cap=Money(Decimal(5000))),
            one_period_config(),
        )
        [wrapper_result] = result.snapshots[0].persons[0].wrappers
        assert wrapper_result.employee_contribution == Money(Decimal("5000.00"))
        assert wrapper_result.contribution_shortfall == Money(Decimal("1000.00"))

    def test_per_kind_cap_is_shared_across_wrappers_of_the_kind(self) -> None:
        """A second wrapper of a capped kind gets only the remaining room."""
        schedule = ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(4000)), recorded_on=RECORDED),
        )
        account_a = wrapper_of(FREE, "0", schedule=schedule)
        account_b = Wrapper(
            id=EntityId("wrapper-second-free"),
            kind=FREE,
            balance=money_fact("0"),
            contributions=schedule,
            allocation=EQUITY_ONLY,
            fees=NO_FEES,
        )
        plan = household_of(person_of((account_a, account_b), employment="50000"))
        result = run(
            plan,
            assumptions_with(),
            stub_region(free_kind_cap=Money(Decimal(5000))),
            one_period_config(),
        )
        first, second = result.snapshots[0].persons[0].wrappers
        assert first.employee_contribution == Money(Decimal("4000.00"))
        assert second.employee_contribution == Money(Decimal("1000.00"))
        assert second.contribution_shortfall == Money(Decimal("3000.00"))

    def test_mechanic_not_permitted_for_the_kind_is_rejected(self) -> None:
        """Relief at source into a relief-free kind is a plan error.

        The region's permitted-mechanics set is the authority (planning
        §4.2); without this check the schedule would fabricate provider
        and assessment relief inside a tax-free account.
        """
        schedule = ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(1000)), recorded_on=RECORDED),
            relief_mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
        )
        account = wrapper_of(FREE, "0", schedule=schedule)
        plan = household_of(person_of((account,), employment="20000"))
        assumptions = assumptions_with()
        region = stub_region()
        config = one_period_config()
        with pytest.raises(EngineError, match="not permitted"):
            run(plan, assumptions, region, config)

    def test_missing_mechanic_on_a_relieved_kind_is_rejected(self) -> None:
        """A pension schedule must state its mechanic, not bypass relief."""
        schedule = ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(1000)), recorded_on=RECORDED),
        )
        pension = wrapper_of(PENSION, "0", schedule=schedule)
        plan = household_of(person_of((pension,), employment="20000"))
        assumptions = assumptions_with()
        region = stub_region()
        config = one_period_config()
        with pytest.raises(EngineError, match="require a relief mechanic"):
            run(plan, assumptions, region, config)

    def test_contributions_stop_at_retirement(self) -> None:
        """No employment income and no contributions once decumulating."""
        schedule = ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(4000)), recorded_on=RECORDED),
        )
        pension = wrapper_of(PENSION, "10000", schedule=schedule)
        plan = household_of(
            person_of(
                (pension,),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                employment="50000",
            )
        )
        result = run(plan, assumptions_with(), stub_region(), one_period_config())
        [person_result] = result.snapshots[0].persons
        assert person_result.stage is LifeStage.DECUMULATION
        assert person_result.employment_income == ZERO
        assert person_result.wrappers[0].employee_contribution == ZERO


class TestEscalation:
    """Escalated amounts compound nominally from the second period."""

    def test_employment_and_contributions_escalate_nominally(self) -> None:
        """10% real growth and 10% CPI compound to 21% nominal.

        Period one uses the stated amounts unchanged; period two
        multiplies both employment income and the escalated schedule
        by 1.1 x 1.1 = 1.21.
        """
        schedule = ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(1000)), recorded_on=RECORDED),
            relief_mechanic=ReliefMechanic.NET_PAY,
            escalation=AssumptionKey.EARNINGS_GROWTH_REAL,
        )
        pension = wrapper_of(PENSION, "0", schedule=schedule)
        plan = household_of(person_of((pension,), employment="10000"))
        result = run(
            plan,
            assumptions_with(
                {
                    "inflation.cpi": Decimal("0.10"),
                    "earnings.growth.real": Decimal("0.10"),
                    "returns.equity.real": Decimal(0),
                }
            ),
            stub_region(),
            RunConfig(today=date(2026, 1, 1), horizon_end=date(2027, 6, 1)),
        )
        first, second = result.snapshots
        assert first.inflation_factor == Decimal(1)
        assert second.inflation_factor == Decimal("1.10")
        assert first.persons[0].employment_income == Money(Decimal("10000.00"))
        assert second.persons[0].employment_income == Money(Decimal("12100.00"))
        assert first.persons[0].wrappers[0].employee_contribution == Money(
            Decimal("1000.00")
        )
        assert second.persons[0].wrappers[0].employee_contribution == Money(
            Decimal("1210.00")
        )

    def test_unescalated_schedule_stays_flat_in_nominal_terms(self) -> None:
        """Without an escalation key the stated amount never changes."""
        schedule = ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(1000)), recorded_on=RECORDED),
            relief_mechanic=ReliefMechanic.NET_PAY,
        )
        pension = wrapper_of(PENSION, "0", schedule=schedule)
        plan = household_of(person_of((pension,), employment="10000"))
        result = run(
            plan,
            assumptions_with(
                {
                    "inflation.cpi": Decimal("0.10"),
                    "returns.equity.real": Decimal(0),
                }
            ),
            stub_region(),
            RunConfig(today=date(2026, 1, 1), horizon_end=date(2027, 6, 1)),
        )
        second = result.snapshots[1]
        assert second.persons[0].wrappers[0].employee_contribution == Money(
            Decimal("1000.00")
        )


def retiree_plan(wrappers: tuple[Wrapper, ...], spending: str = "12000") -> Household:
    """A 66-year-old retiree household with a net spending need."""
    return household_of(
        person_of(wrappers, date_of_birth=date(1960, 1, 1), retire_at=60),
        spending=spending,
    )


class TestWithdrawals:
    """Step 4: net-need withdrawals, gross-up, ordering, gates, shortfall."""

    def test_tax_free_wrappers_are_drawn_first(self) -> None:
        """The free account empties before any taxable draw happens.

        Need 12,000: 5,000 net from the free account, then 7,000 net
        from crystallised pension funds grossed up at the flat 25% —
        9,333.33 gross, 2,333.33 tax.
        """
        free_account = wrapper_of(FREE, "5000")
        pension = wrapper_of(PENSION, "0", crystallised="20000")
        plan = retiree_plan((free_account, pension))
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        free_result, pension_result = person_result.wrappers
        assert free_result.withdrawal_tax_free == Money(Decimal("5000.00"))
        assert pension_result.withdrawal_taxable == Money(Decimal("9333.33"))
        assert person_result.tax.tax_due == Money(Decimal("2333.33"))
        assert person_result.spending_need == Money(Decimal("12000.00"))
        assert person_result.net_withdrawn == Money(Decimal("12000.00"))
        assert person_result.shortfall == ZERO

    def test_partially_tax_free_draw_grosses_up_the_taxable_part(self) -> None:
        """A 25%-tax-free draw needs less gross than a fully taxed one.

        Need 7,500 net from uncrystallised pension funds: gross
        9,230.76 splits into 2,307.69 tax-free and 6,923.07 taxable;
        tax of 1,730.76 leaves exactly 7,500.
        """
        pension = wrapper_of(PENSION, "100000")
        plan = retiree_plan((pension,), spending="7500")
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        [pension_result] = person_result.wrappers
        assert pension_result.withdrawal_tax_free == Money(Decimal("2307.69"))
        assert pension_result.withdrawal_taxable == Money(Decimal("6923.07"))
        assert person_result.tax.tax_due == Money(Decimal("1730.76"))
        assert person_result.net_withdrawn == Money(Decimal("7500.00"))
        assert person_result.shortfall == ZERO

    def test_crystallised_funds_are_drawn_before_uncrystallised(self) -> None:
        """Funds already in drawdown are spent first, fully taxable.

        Need 3,000 net against crystallised 5,000: the whole draw is
        taxable (no fresh tax-free cash on crystallised funds,
        planning §5.1) — 3,999.99 gross, 999.99 tax — and the
        uncrystallised balance is untouched.
        """
        pension = wrapper_of(PENSION, "100000", crystallised="5000")
        plan = retiree_plan((pension,), spending="3000")
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        [pension_result] = person_result.wrappers
        assert pension_result.withdrawal_tax_free == ZERO
        assert pension_result.withdrawal_taxable == Money(Decimal("3999.99"))
        assert pension_result.closing_uncrystallised == Money(Decimal("100000.00"))
        assert pension_result.closing_crystallised == Money(Decimal("1000.01"))
        assert person_result.net_withdrawn == Money(Decimal("3000.00"))

    def test_access_gate_blocks_uncrystallised_pension_funds(self) -> None:
        """A retiree under the access age cannot draw uncrystallised funds."""
        pension = wrapper_of(PENSION, "100000")
        plan = household_of(
            person_of((pension,), date_of_birth=date(1975, 6, 1), retire_at=45),
            spending="12000",
        )
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        assert person_result.stage is LifeStage.DECUMULATION
        assert person_result.wrappers[0].withdrawal_gross == ZERO
        assert person_result.net_withdrawn == ZERO
        assert person_result.shortfall == Money(Decimal("12000.00"))

    def test_exhausted_pots_leave_a_shortfall(self) -> None:
        """Balances cap the draw; the unmet need is reported.

        Need 12,000 against a 5,000 free account and 2,000 of
        accessible pension: the pension draw is balance-capped at
        2,000 gross (1,500 taxable, 375 tax, 1,625 net), leaving a
        5,375 shortfall.
        """
        free_account = wrapper_of(FREE, "5000")
        pension = wrapper_of(PENSION, "2000")
        plan = retiree_plan((free_account, pension))
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        free_result, pension_result = person_result.wrappers
        assert free_result.closing_uncrystallised == ZERO
        assert pension_result.withdrawal_gross == Money(Decimal("2000.00"))
        assert person_result.net_withdrawn == Money(Decimal("6625.00"))
        assert person_result.shortfall == Money(Decimal("5375.00"))

    def test_stage_multiplier_scales_the_spending_need(self) -> None:
        """A decumulation multiplier scales the real need before inflation."""
        free_account = wrapper_of(FREE, "100000")
        person = person_of(
            (free_account,), date_of_birth=date(1960, 1, 1), retire_at=60
        )
        spending = SpendingPlan(
            annual_spending_real=money_fact("12000"),
            stage_multipliers={LifeStage.DECUMULATION: Decimal("1.5")},
        )
        plan = Household(persons=(person,), spending=spending)
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        assert person_result.spending_need == Money(Decimal("18000.00"))
        assert person_result.net_withdrawn == Money(Decimal("18000.00"))

    def test_fee_is_charged_on_the_aggregate_wrapper_balance(self) -> None:
        """Emptying one sub-balance must not shrink the account's fee.

        Uncrystallised 100,000 and crystallised 5,000; the withdrawal
        step empties the crystallised funds (5,000 gross, balance-capped)
        and draws 307.69 more from uncrystallised funds. The 1% fee
        applies to the aggregate average of 105,000 and 99,692.31 —
        1,023.46 — not to each sub-balance with the cap zeroing the
        crystallised share.
        """
        pension = Wrapper(
            id=EntityId("wrapper-aggregate-fee"),
            kind=PENSION,
            balance=money_fact("100000"),
            crystallised_balance=money_fact("5000"),
            allocation=EQUITY_ONLY,
            fees=FeeSchedule(platform=Rate(Decimal("0.01")), fund=Rate(Decimal(0))),
        )
        plan = retiree_plan((pension,), spending="4000")
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        [pension_result] = person_result.wrappers
        assert person_result.net_withdrawn == Money(Decimal("4000.00"))
        assert person_result.tax.tax_due == Money(Decimal("1307.69"))
        assert pension_result.fee == Money(Decimal("1023.46"))
        assert pension_result.closing_crystallised == ZERO
        assert pension_result.closing_uncrystallised == Money(Decimal("98668.85"))

    def test_spending_need_inflates_with_the_cpi_path(self) -> None:
        """The real spending need is inflated by the run's CPI factor."""
        free_account = wrapper_of(FREE, "100000")
        plan = retiree_plan((free_account,), spending="10000")
        result = run(
            plan,
            assumptions_with(
                {
                    "inflation.cpi": Decimal("0.10"),
                    "returns.equity.real": Decimal(0),
                }
            ),
            stub_region(),
            RunConfig(today=date(2026, 1, 1), horizon_end=date(2027, 6, 1)),
        )
        first, second = result.snapshots
        assert first.persons[0].spending_need == Money(Decimal("10000.00"))
        assert second.persons[0].spending_need == Money(Decimal("11000.00"))


class TestStagesAndAllocation:
    """Step 1: stage derivation and glide-path allocation resolution."""

    def test_stage_boundaries_follow_the_default_shape(self) -> None:
        """31/20/10 years out map to early/mid/pre with a 15-year window."""
        cases = (
            (date(1990, 1, 1), 67, LifeStage.EARLY_ACCUMULATION),
            (date(1990, 1, 1), 56, LifeStage.MID_ACCUMULATION),
            (date(1990, 1, 1), 46, LifeStage.PRE_RETIREMENT),
            (date(1960, 1, 1), 60, LifeStage.DECUMULATION),
        )
        for date_of_birth, retire_at, expected in cases:
            plan = household_of(
                person_of((), date_of_birth=date_of_birth, retire_at=retire_at)
            )
            result = run(plan, assumptions_with(), stub_region(), one_period_config())
            assert result.snapshots[0].persons[0].stage is expected

    def test_personal_glide_path_overrides_the_default_shape(self) -> None:
        """A person's own glide path wins; the default shape goes unread."""
        own_glide = GlidePathConfig(
            points=(
                GlidePathPoint(
                    years_to_retirement=0,
                    allocation=AssetAllocation(
                        equity=Decimal("0.5"), bonds=Decimal("0.5")
                    ),
                ),
            )
        )
        wrapper = wrapper_of(PENSION, "1000", allocation=None)
        person = Person(
            id=EntityId("person-1"),
            date_of_birth=Fact(
                value=date(1990, 1, 1), as_of=AS_OF, recorded_on=RECORDED
            ),
            target_retirement_age=Decision(value=65, recorded_on=RECORDED),
            tax_residency=RESIDENCY,
            wrappers=(wrapper,),
            glide_path=own_glide,
        )
        plan = Household(persons=(person,))
        result = run(plan, assumptions_with(), stub_region(), one_period_config())
        allocation = result.snapshots[0].persons[0].wrappers[0].allocation
        assert allocation.equity == Decimal("0.5")
        keys_read = {entry.key for entry in result.provenance.assumptions}
        assert AssumptionKey.GLIDEPATH_DEFAULT_SHAPE not in keys_read

    def test_glide_path_supplies_missing_wrapper_allocations(self) -> None:
        """A wrapper without its own allocation gets the glide path's.

        At exactly the de-risking window (15 years out) the default
        shape still holds 80% equity; at retirement it holds 40%.
        """
        accumulating = wrapper_of(PENSION, "1000", allocation=None)
        plan = household_of(
            person_of((accumulating,), date_of_birth=date(1990, 1, 1), retire_at=51)
        )
        result = run(plan, assumptions_with(), stub_region(), one_period_config())
        allocation = result.snapshots[0].persons[0].wrappers[0].allocation
        assert allocation.equity == Decimal("0.8")

        retired = wrapper_of(FREE, "1000", allocation=None)
        retiree = household_of(
            person_of((retired,), date_of_birth=date(1960, 1, 1), retire_at=60)
        )
        retired_result = run(
            retiree, assumptions_with(), stub_region(), one_period_config()
        )
        retired_allocation = (
            retired_result.snapshots[0].persons[0].wrappers[0].allocation
        )
        assert retired_allocation.equity == Decimal("0.4")


class TestProvenanceAndDeterminism:
    """The run manifest: reads, facts, decisions, version, seed; purity."""

    def test_provenance_lists_exactly_the_assumptions_read(self) -> None:
        """Every key read is listed; keys never read are absent."""
        schedule = ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(1000)), recorded_on=RECORDED),
            relief_mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
            escalation=AssumptionKey.EARNINGS_GROWTH_REAL,
        )
        pension = Wrapper(
            id=EntityId("wrapper-defaulted"),
            kind=PENSION,
            balance=money_fact("1000"),
            contributions=schedule,
        )
        plan = household_of(person_of((pension,), employment="20000"))
        result = run(
            plan,
            assumptions_with(),
            stub_region(),
            RunConfig(today=date(2026, 1, 1), seed=4711),
        )
        keys_read = {entry.key for entry in result.provenance.assumptions}
        assert keys_read == {
            AssumptionKey.HORIZON_PLANNING_AGE,
            AssumptionKey.EARNINGS_GROWTH_REAL,
            AssumptionKey.INFLATION_CPI,
            AssumptionKey.RETURNS_EQUITY_REAL,
            AssumptionKey.RETURNS_BONDS_REAL,
            AssumptionKey.RETURNS_CASH_REAL,
            AssumptionKey.GLIDEPATH_DEFAULT_SHAPE,
            AssumptionKey.FEES_PLATFORM,
            AssumptionKey.FEES_FUND,
        }
        assert result.provenance.seed == 4711
        assert result.provenance.region_data_version == "stub region v1"

    def test_provenance_lists_plan_facts_and_decisions(self) -> None:
        """Facts and decisions appear under stable entity-id labels."""
        pension = wrapper_of(PENSION, "1000")
        plan = household_of(person_of((pension,), employment="20000"))
        result = run(plan, assumptions_with(), stub_region(), one_period_config())
        fact_labels = {entry.label for entry in result.provenance.facts}
        decision_labels = {entry.label for entry in result.provenance.decisions}
        assert "person[person-1].date_of_birth" in fact_labels
        assert "person[person-1].employment_income" in fact_labels
        assert f"wrapper[{pension.id}].balance" in fact_labels
        assert "person[person-1].target_retirement_age" in decision_labels

    def test_identical_inputs_produce_identical_results(self) -> None:
        """Purity (planning §4.6): same manifest, same output."""
        pension = wrapper_of(PENSION, "10000")
        plan = household_of(person_of((pension,), employment="30000"))
        config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2028, 6, 1))
        first = run(plan, assumptions_with(), stub_region(), config)
        second = run(plan, assumptions_with(), stub_region(), config)
        assert first == second
        assert first.config == config

    def test_default_horizon_comes_from_the_planning_age(self) -> None:
        """Without an explicit horizon the planning-age assumption rules."""
        plan = household_of(person_of(()))
        result = run(
            plan,
            assumptions_with({"horizon.planning_age": 40}),
            stub_region(),
            RunConfig(today=date(2026, 1, 1)),
        )
        assert len(result.snapshots) == 5
        keys_read = {entry.key for entry in result.provenance.assumptions}
        assert AssumptionKey.HORIZON_PLANNING_AGE in keys_read


class TestEngineErrors:
    """Requests the engine must refuse loudly."""

    def test_two_person_household_is_rejected(self) -> None:
        """v1 projects exactly one person (planning §4.4)."""
        first = person_of(())
        second = Person(
            id=EntityId("person-2"),
            date_of_birth=Fact(
                value=date(1992, 3, 4), as_of=AS_OF, recorded_on=RECORDED
            ),
            target_retirement_age=Decision(value=65, recorded_on=RECORDED),
            tax_residency=RESIDENCY,
        )
        plan = Household(persons=(first, second))
        assumptions = assumptions_with()
        region = stub_region()
        config = one_period_config()
        with pytest.raises(EngineError, match="one person"):
            run(plan, assumptions, region, config)

    def test_horizon_before_today_is_rejected(self) -> None:
        """A horizon that ends before it starts is a config error."""
        today = date(2026, 1, 1)
        horizon_end = date(2025, 12, 31)
        with pytest.raises(EngineError, match="precedes today"):
            RunConfig(today=today, horizon_end=horizon_end)

    def test_planning_age_already_attained_is_rejected(self) -> None:
        """The default horizon cannot end before the run starts."""
        plan = household_of(person_of(()))
        assumptions = assumptions_with({"horizon.planning_age": 30})
        region = stub_region()
        config = RunConfig(today=date(2026, 1, 1))
        with pytest.raises(EngineError, match="planning age"):
            run(plan, assumptions, region, config)
