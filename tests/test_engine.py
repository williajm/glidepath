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
from typing import ClassVar

import pytest

from glidepath.core import (
    AnnualCalendar,
    AnnuityBasis,
    AnnuityPurchase,
    AnnuityType,
    AssetAllocation,
    Assumption,
    AssumptionKey,
    AssumptionSet,
    ContributionSchedule,
    ContributionTaxTreatment,
    DBPension,
    Decision,
    EngineError,
    EntityId,
    Fact,
    FactorTable,
    FeeSchedule,
    FixedPercentWithdrawalStrategy,
    FixedRealWithdrawalStrategy,
    GlidePathConfig,
    GlidePathPoint,
    GrowthTaxTreatment,
    GuardrailsWithdrawalStrategy,
    Household,
    LifeStage,
    MemberContributionOutcome,
    MemberContributionRequest,
    Money,
    NaturalYieldWithdrawalStrategy,
    NetWithdrawalPlan,
    Period,
    Person,
    PlannedOutflow,
    Provenance,
    Rate,
    Region,
    ReliefMechanic,
    RevaluationBasis,
    RevaluationReference,
    RunConfig,
    SpendingPlan,
    StatePensionEntitlement,
    StatePensionRecord,
    TaxFreeCashStrategy,
    TaxInput,
    TaxLine,
    TaxResidencyId,
    TaxResult,
    WithdrawalPlan,
    WithdrawalSourceId,
    WithdrawalState,
    WithdrawalTaxTreatment,
    Wrapper,
    WrapperKindId,
    WrapperTaxTreatment,
    add_months,
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
    """Two wrapper kinds: an EET pension and a TEE tax-free account.

    ``free_access_age`` age-gates the tax-free kind when set (a
    LISA-like account, roadmap 9.2): tax treatment says nothing about
    accessibility, so the engine must consult the gate for every kind.
    ``lsa_cap`` is the lifetime cap on pension tax-free cash (roadmap
    5.2); ``None`` — the default — means uncapped.
    """

    access_age: int = 55
    free_kind_cap: Money | None = None
    free_access_age: int | None = None
    lsa_cap: Money | None = None

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

    def lump_sum_allowance(self, period: Period) -> Money | None:
        """The configured lifetime tax-free cash cap (None: uncapped)."""
        del period
        return self.lsa_cap

    def is_access_open(
        self, kind: WrapperKindId, date_of_birth: date, period: Period
    ) -> bool:
        """Pension access is age-gated; the free kind only if configured."""
        if kind == PENSION:
            return is_age_attained_by_period_start(
                date_of_birth, self.access_age, period
            )
        if self.free_access_age is not None:
            return is_age_attained_by_period_start(
                date_of_birth, self.free_access_age, period
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


@dataclass(frozen=True)
class StubStatePension:
    """A pass-through scheme: configured amounts, entitled at ``start_age``.

    The engine owns uprating, pro-rating, the deferral increment, and
    the spending offset; this stub only answers the region question —
    the entitlement in today's rates, its start date, and the uplift
    fraction deferral earned.
    """

    annual: Money = ZERO
    cpi_annual: Money = ZERO
    start_age: int = 67
    uplift: Decimal = Decimal(0)

    def entitlement(
        self, record: StatePensionRecord, date_of_birth: date, today: date
    ) -> StatePensionEntitlement:
        """The configured entitlement, deferral shifting the start."""
        del today
        start = date_age_attained(date_of_birth, self.start_age)
        months = int(record.deferral_years.value * Decimal(12))
        return StatePensionEntitlement(
            start_date=add_months(start, months),
            annual_amount=self.annual,
            cpi_uprated_annual_amount=self.cpi_annual,
            deferral_uplift=self.uplift,
        )


def stub_region(
    contributions: RecordingContributionRules | None = None,
    free_kind_cap: Money | None = None,
    state_pension: StubStatePension | None = None,
    free_access_age: int | None = None,
    lsa_cap: Money | None = None,
) -> Region:
    """A calendar-year region over the stub implementations."""
    return Region(
        calendar=AnnualCalendar(),
        ages=StubAges(),
        tax=FlatTaxSystem(),
        wrappers=StubWrapperRules(
            free_kind_cap=free_kind_cap,
            free_access_age=free_access_age,
            lsa_cap=lsa_cap,
        ),
        contributions=contributions or RecordingContributionRules(),
        state_pension=state_pension or StubStatePension(),
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
    db_pensions: tuple[DBPension, ...] = (),
    annuity_purchases: tuple[AnnuityPurchase, ...] = (),
    state_pension: StatePensionRecord | None = None,
    lsa_used: str | None = None,
    mpaa_triggered_on: date | None = None,
) -> Person:
    """A single test person."""
    return Person(
        id=EntityId("person-1"),
        date_of_birth=Fact(value=date_of_birth, as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=retire_at, recorded_on=RECORDED),
        tax_residency=RESIDENCY,
        employment_income=None if employment is None else money_fact(employment),
        mpaa_triggered_on=None
        if mpaa_triggered_on is None
        else Fact(value=mpaa_triggered_on, as_of=AS_OF, recorded_on=RECORDED),
        lsa_used=None if lsa_used is None else money_fact(lsa_used),
        wrappers=wrappers,
        db_pensions=db_pensions,
        annuity_purchases=annuity_purchases,
        state_pension=state_pension,
    )


def db_pension_of(
    *,
    accrued: str = "8000",
    statement: date = date(2026, 1, 1),
    npa: int = 65,
    basis: RevaluationBasis | None = None,
    factors: dict[int, Decimal] | None = None,
    commuted: str = "0",
    commutation_factor: str | None = None,
    taken_at: int | None = None,
) -> DBPension:
    """A deferred DB pension; the default basis never revalues."""
    return DBPension(
        id=EntityId("db-1"),
        accrued_annual_pension=money_fact(accrued),
        statement_date=statement,
        normal_pension_age=Fact(value=npa, as_of=AS_OF, recorded_on=RECORDED),
        revaluation_basis=basis
        or RevaluationBasis(reference=RevaluationReference.NONE),
        early_late_factors=FactorTable(factors=factors or {}),
        commuted_fraction=Decision(value=Decimal(commuted), recorded_on=RECORDED),
        commutation_factor=None
        if commutation_factor is None
        else Fact(value=Decimal(commutation_factor), as_of=AS_OF, recorded_on=RECORDED),
        taken_at_age=None
        if taken_at is None
        else Decision(value=taken_at, recorded_on=RECORDED),
    )


def sp_record(deferral: str = "0") -> StatePensionRecord:
    """A forecast-backed record; the stub scheme reads only the deferral."""
    return StatePensionRecord(
        forecast_weekly_amount=money_fact("200"),
        protected_payment=None,
        ni_record_start=None,
        qualifying_years=None,
        planned_extra_years=Decision(value=0, recorded_on=RECORDED),
        deferral_years=Decision(value=Decimal(deferral), recorded_on=RECORDED),
    )


TRIPLE_LOCK = {
    "rule": "triple_lock",
    "floor": Decimal("0.025"),
    "deterministic_cpi_margin": Decimal("0.005"),
}


def household_of(person: Person, *, spending: str | None = None) -> Household:
    """A single-person household, optionally with a spending plan."""
    plan = None
    if spending is not None:
        plan = SpendingPlan(annual_spending_real=money_fact(spending))
    return Household(persons=(person,), spending=plan)


def one_period_config() -> RunConfig:
    """A single whole calendar-year period covering 2026."""
    return RunConfig(today=date(2026, 1, 1), horizon_end=date(2026, 12, 31))


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
            RunConfig(today=date(2026, 1, 1), horizon_end=date(2027, 12, 31)),
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
            RunConfig(today=date(2026, 1, 1), horizon_end=date(2027, 12, 31)),
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


@dataclass(frozen=True)
class UncrystallisedOnlyStrategy:
    """A net plan listing only uncrystallised sources.

    Probes the source-targeting contract (roadmap 5.2): execution of
    a phased draw on an uncrystallised source must never reach into
    pre-existing drawdown funds the plan did not select.
    """

    uses_natural_yield: ClassVar[bool] = False

    def withdraw(self, state: WithdrawalState, need: Money) -> WithdrawalPlan:
        """Target the need over open uncrystallised sources only."""
        order = tuple(
            entry.id
            for entry in state.sources
            if not entry.id.crystallised and entry.access_open
        )
        return NetWithdrawalPlan(target=need, order=order)


@dataclass
class RecordingWithdrawalStrategy:
    """Fixed-real behaviour that captures each period's state."""

    states: list[WithdrawalState] = dataclass_field(default_factory=list)

    uses_natural_yield: ClassVar[bool] = False

    def withdraw(self, state: WithdrawalState, need: Money) -> WithdrawalPlan:
        """Record the state, then plan exactly as fixed-real does."""
        self.states.append(state)
        return FixedRealWithdrawalStrategy().withdraw(state, need)


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

    def test_access_gate_applies_to_tax_free_wrappers_too(self) -> None:
        """An age-gated tax-free account is not drawable before its gate.

        Tax treatment says nothing about accessibility (a LISA-like
        account, roadmap 9.2): a 50-year-old retiree with the free kind
        gated at 60 cannot draw it, whatever its tax treatment.
        """
        free_account = wrapper_of(FREE, "50000")
        plan = household_of(
            person_of((free_account,), date_of_birth=date(1975, 6, 1), retire_at=45),
            spending="12000",
        )
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(free_access_age=60),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
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
            RunConfig(today=date(2026, 1, 1), horizon_end=date(2027, 12, 31)),
        )
        first, second = result.snapshots
        assert first.persons[0].spending_need == Money(Decimal("10000.00"))
        assert second.persons[0].spending_need == Money(Decimal("11000.00"))


class TestTaxFreeCash:
    """Roadmap 5.2: LSA-capped tax-free cash, MPAA triggers, strategies.

    All numbers are hand-worked against the stub region: flat 25% tax
    floored to the penny, a 25% pension tax-free fraction, access at
    55. The lump-sum cap is uncapped unless a test sets ``lsa_cap``.
    """

    def test_split_payment_tax_free_element_is_capped_by_headroom(self) -> None:
        """A split payment beyond the cap keeps flowing, fully taxable.

        Need 7,500 net against a 1,500 cap: the free-bearing slice is
        balance-limited to 6,000 gross (1,500 free + 4,500 taxable,
        1,125 tax, 4,875 net); the remaining 2,625 net comes wholly
        taxable — 3,499.99 gross. The cap is exhausted and the first
        taxable payment marks flexible access.
        """
        pension = wrapper_of(PENSION, "100000")
        plan = retiree_plan((pension,), spending="7500")
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(lsa_cap=Money(Decimal(1500))),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        [pension_result] = person_result.wrappers
        assert pension_result.withdrawal_tax_free == Money(Decimal("1500.00"))
        assert pension_result.withdrawal_taxable == Money(Decimal("7999.99"))
        assert pension_result.closing_uncrystallised == Money(Decimal("90500.01"))
        assert person_result.tax.tax_due == Money(Decimal("1999.99"))
        assert person_result.net_withdrawn == Money(Decimal("7500.00"))
        assert person_result.shortfall == ZERO
        assert person_result.lsa_used == Money(Decimal("1500.00"))
        assert person_result.mpaa_triggered_on == date(2026, 1, 1)

    def test_lsa_used_fact_seeds_the_headroom(self) -> None:
        """Pre-plan tax-free cash reduces what the run may still pay.

        A 1,500 cap with 1,100 already used leaves 400 of headroom:
        the free-bearing slice caps at 1,600 gross (400 free), and the
        snapshot's cumulative figure includes the fact.
        """
        pension = wrapper_of(PENSION, "100000")
        plan = household_of(
            person_of(
                (pension,),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                lsa_used="1100",
            ),
            spending="7500",
        )
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(lsa_cap=Money(Decimal(1500))),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        [pension_result] = person_result.wrappers
        assert pension_result.withdrawal_tax_free == Money(Decimal("400.00"))
        assert pension_result.withdrawal_taxable == Money(Decimal("9466.66"))
        assert person_result.net_withdrawn == Money(Decimal("7500.00"))
        assert person_result.lsa_used == Money(Decimal("1500.00"))

    def test_free_wrapper_draws_never_consume_the_allowance(self) -> None:
        """Tax-free cash from a non-pension wrapper is not pension cash."""
        free_account = wrapper_of(FREE, "50000")
        plan = retiree_plan((free_account,))
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(lsa_cap=Money(Decimal(1500))),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        assert person_result.net_withdrawn == Money(Decimal("12000.00"))
        assert person_result.lsa_used == ZERO
        assert person_result.mpaa_triggered_on is None

    def test_mpaa_fact_wins_over_in_run_triggers(self) -> None:
        """A pre-plan trigger date is reported unchanged, never moved."""
        pension = wrapper_of(PENSION, "0", crystallised="50000")
        plan = household_of(
            person_of(
                (pension,),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                mpaa_triggered_on=date(2020, 5, 5),
            ),
            spending="3000",
        )
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        assert person_result.wrappers[0].withdrawal_taxable > ZERO
        assert person_result.mpaa_triggered_on == date(2020, 5, 5)

    def test_mpaa_trigger_date_is_the_first_modelled_day(self) -> None:
        """Mid-period ``today`` dates the trigger, not the period start."""
        pension = wrapper_of(PENSION, "100000")
        plan = retiree_plan((pension,), spending="6000")
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            RunConfig(today=date(2026, 3, 1), horizon_end=date(2026, 12, 31)),
        )
        [person_result] = result.snapshots[0].persons
        assert person_result.wrappers[0].withdrawal_taxable > ZERO
        assert person_result.mpaa_triggered_on == date(2026, 3, 1)

    def test_lump_sum_as_needed_pays_tax_free_cash_first(self) -> None:
        """Phased designation: tax-free cash now, income only when needed.

        Need 600 net in each of two periods (zero returns and CPI).
        Period one crystallises 2,400: 600 arrives tax-free and 1,800
        is designated to the drawdown sub-balance — no taxable income,
        so flexible access is not marked. Period two draws its 600
        net from that residue as taxable income (799.99 gross), and
        the MPAA trigger fires then.
        """
        pension = wrapper_of(PENSION, "100000")
        plan = retiree_plan((pension,), spending="600")
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            RunConfig(
                today=date(2026, 1, 1),
                horizon_end=date(2027, 12, 31),
                tax_free_cash=TaxFreeCashStrategy.LUMP_SUM_AS_NEEDED,
            ),
        )
        first, second = result.snapshots
        [person_one] = first.persons
        [pension_one] = person_one.wrappers
        assert pension_one.withdrawal_tax_free == Money(Decimal("600.00"))
        assert pension_one.withdrawal_taxable == ZERO
        assert pension_one.closing_uncrystallised == Money(Decimal("97600.00"))
        assert pension_one.closing_crystallised == Money(Decimal("1800.00"))
        assert person_one.net_withdrawn == Money(Decimal("600.00"))
        assert person_one.lsa_used == Money(Decimal("600.00"))
        assert person_one.mpaa_triggered_on is None
        [person_two] = second.persons
        [pension_two] = person_two.wrappers
        assert pension_two.withdrawal_tax_free == ZERO
        assert pension_two.withdrawal_taxable == Money(Decimal("799.99"))
        assert pension_two.closing_uncrystallised == Money(Decimal("97600.00"))
        assert pension_two.closing_crystallised == Money(Decimal("1000.01"))
        assert person_two.lsa_used == Money(Decimal("600.00"))
        assert person_two.mpaa_triggered_on == date(2027, 1, 1)

    def test_lump_sum_as_needed_draws_income_once_the_cap_is_gone(self) -> None:
        """With no headroom a phased draw crystallises straight to income."""
        pension = wrapper_of(PENSION, "100000")
        plan = retiree_plan((pension,), spending="600")
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(lsa_cap=Money(Decimal(0))),
            RunConfig(
                today=date(2026, 1, 1),
                horizon_end=date(2026, 12, 31),
                tax_free_cash=TaxFreeCashStrategy.LUMP_SUM_AS_NEEDED,
            ),
        )
        [person_result] = result.snapshots[0].persons
        [pension_result] = person_result.wrappers
        assert pension_result.withdrawal_tax_free == ZERO
        assert pension_result.withdrawal_taxable == Money(Decimal("799.99"))
        assert pension_result.closing_uncrystallised == Money(Decimal("99200.01"))
        assert pension_result.closing_crystallised == ZERO
        assert person_result.net_withdrawn == Money(Decimal("600.00"))
        assert person_result.mpaa_triggered_on == date(2026, 1, 1)

    def test_split_payment_with_no_headroom_is_wholly_taxable(self) -> None:
        """An exhausted cap leaves split payments with no free element."""
        pension = wrapper_of(PENSION, "100000")
        plan = retiree_plan((pension,), spending="600")
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(lsa_cap=Money(Decimal(0))),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        [pension_result] = person_result.wrappers
        assert pension_result.withdrawal_tax_free == ZERO
        assert pension_result.withdrawal_taxable == Money(Decimal("799.99"))
        assert person_result.lsa_used == ZERO

    def test_up_front_lump_sum_crystallises_the_whole_pot(self) -> None:
        """The retirement event pays the capped lump sum, once.

        Period one: the 100,000 pot crystallises whole — 25,000
        arrives tax-free and meets the 12,000 need entirely (the
        excess is spent, not banked — planning §5.2's accepted v1
        cost), so no wrapper is drawn and the free account is
        untouched. Pure tax-free cash never marks flexible access.
        Period two: the pots are already crystallised, so the need is
        met from the free account and then drawdown income — and that
        first taxable draw fires the trigger.
        """
        free_account = wrapper_of(FREE, "1000")
        pension = wrapper_of(PENSION, "100000")
        plan = retiree_plan((free_account, pension))
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            RunConfig(
                today=date(2026, 1, 1),
                horizon_end=date(2027, 12, 31),
                tax_free_cash=TaxFreeCashStrategy.UP_FRONT_LUMP_SUM,
            ),
        )
        first, second = result.snapshots
        [person_one] = first.persons
        free_one, pension_one = person_one.wrappers
        assert person_one.pension_lump_sum == Money(Decimal("25000.00"))
        assert pension_one.withdrawal_tax_free == Money(Decimal("25000.00"))
        assert pension_one.closing_uncrystallised == ZERO
        assert pension_one.closing_crystallised == Money(Decimal("75000.00"))
        assert free_one.withdrawal_gross == ZERO
        assert person_one.net_withdrawn == ZERO
        assert person_one.shortfall == ZERO
        assert person_one.lsa_used == Money(Decimal("25000.00"))
        assert person_one.mpaa_triggered_on is None
        [person_two] = second.persons
        free_two, pension_two = person_two.wrappers
        assert person_two.pension_lump_sum == ZERO
        assert free_two.withdrawal_tax_free == Money(Decimal("1000.00"))
        assert pension_two.withdrawal_taxable == Money(Decimal("14666.66"))
        assert person_two.mpaa_triggered_on == date(2027, 1, 1)

    def test_up_front_lump_sum_is_capped_by_the_allowance(self) -> None:
        """The event pays only the remaining headroom tax-free.

        A 10,000 cap on the 100,000 pot: the lump sum is 10,000, the
        full 90,000 remainder is designated, and the unmet 2,000 of
        the need is drawn from drawdown funds as taxable income —
        2,666.66 gross — which marks flexible access.
        """
        pension = wrapper_of(PENSION, "100000")
        plan = retiree_plan((pension,))
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(lsa_cap=Money(Decimal(10000))),
            RunConfig(
                today=date(2026, 1, 1),
                horizon_end=date(2026, 12, 31),
                tax_free_cash=TaxFreeCashStrategy.UP_FRONT_LUMP_SUM,
            ),
        )
        [person_result] = result.snapshots[0].persons
        [pension_result] = person_result.wrappers
        assert person_result.pension_lump_sum == Money(Decimal("10000.00"))
        assert pension_result.withdrawal_taxable == Money(Decimal("2666.66"))
        assert person_result.net_withdrawn == Money(Decimal("2000.00"))
        assert person_result.shortfall == ZERO
        assert person_result.lsa_used == Money(Decimal("10000.00"))
        assert person_result.mpaa_triggered_on == date(2026, 1, 1)

    def test_up_front_lump_sum_waits_for_the_access_gate(self) -> None:
        """A gate still shut defers the event to the period it opens.

        Born 1 June 1975 and retired early, the person attains the
        stub's access age of 55 on 1 June 2030 — after the 2030
        period's first day (§4.1), so the event fires with the 2031
        period, and fires even with no spending need (the strategy
        says take the cash; the excess is spent, planning §5.2).
        """
        pension = wrapper_of(PENSION, "100000")
        plan = household_of(
            person_of((pension,), date_of_birth=date(1975, 6, 1), retire_at=45)
        )
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            RunConfig(
                today=date(2026, 1, 1),
                horizon_end=date(2031, 12, 31),
                tax_free_cash=TaxFreeCashStrategy.UP_FRONT_LUMP_SUM,
            ),
        )
        for snapshot in result.snapshots[:-1]:
            [person_result] = snapshot.persons
            assert person_result.pension_lump_sum == ZERO
            assert person_result.wrappers[0].closing_uncrystallised == Money(
                Decimal("100000.00")
            )
        [person_last] = result.snapshots[-1].persons
        assert person_last.pension_lump_sum == Money(Decimal("25000.00"))
        assert person_last.wrappers[0].closing_crystallised == Money(
            Decimal("75000.00")
        )

    def test_db_lump_sum_consumes_the_allowance_and_excess_is_taxed(self) -> None:
        """A commencement lump sum counts against the cap, excess taxed.

        Commuting 25% of an 8,000 DB pension at factor 12 pays 24,000
        when benefits start; a 10,000 cap leaves 10,000 tax-free and
        14,000 taxed as income alongside the 6,000 residual pension —
        5,000 tax in all. The offset (6,000 + 24,000 - 5,000 = 25,000)
        covers the whole need, and a lump sum never marks flexible
        access.
        """
        pension = db_pension_of(
            accrued="8000", npa=66, commuted="0.25", commutation_factor="12"
        )
        plan = household_of(
            person_of(
                (),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                db_pensions=(pension,),
            ),
            spending="12000",
        )
        result = run(
            plan,
            assumptions_with(),
            stub_region(lsa_cap=Money(Decimal(10000))),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        assert person_result.db_lump_sum == Money(Decimal("24000.00"))
        assert person_result.tax.tax_due == Money(Decimal("5000.00"))
        assert person_result.lsa_used == Money(Decimal("10000.00"))
        assert person_result.shortfall == ZERO
        assert person_result.mpaa_triggered_on is None

    def test_db_lump_sum_headroom_is_consumed_before_wrapper_draws(self) -> None:
        """The income step's lump sum comes off the cap first.

        A 25,000 cap against a 24,000 DB lump sum leaves 1,000 for the
        wrapper draws: net income covers 28,500 of the 36,000 need,
        and the remaining 7,500 splits at the 4,000-gross free-bearing
        boundary (1,000 free + 3,000 taxable) before running wholly
        taxable (5,666.66 gross).
        """
        wrapper = wrapper_of(PENSION, "100000")
        pension = db_pension_of(
            accrued="8000", npa=66, commuted="0.25", commutation_factor="12"
        )
        plan = household_of(
            person_of(
                (wrapper,),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                db_pensions=(pension,),
            ),
            spending="36000",
        )
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(lsa_cap=Money(Decimal(25000))),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        [pension_result] = person_result.wrappers
        assert person_result.db_lump_sum == Money(Decimal("24000.00"))
        assert pension_result.withdrawal_tax_free == Money(Decimal("1000.00"))
        assert pension_result.withdrawal_taxable == Money(Decimal("8666.66"))
        assert person_result.tax.tax_due == Money(Decimal("3666.66"))
        assert person_result.net_withdrawn == Money(Decimal("7500.00"))
        assert person_result.shortfall == ZERO
        assert person_result.lsa_used == Money(Decimal("25000.00"))

    def test_phased_income_leg_never_taps_unselected_drawdown_funds(self) -> None:
        """A plan's source targeting is honoured exactly (roadmap 5.2).

        A plan listing only the uncrystallised source, phased mode,
        exhausted cap: the draw crystallises straight to income from
        the uncrystallised pot (799.99 gross) and the 50,000 of
        pre-existing drawdown funds — which the plan did not select —
        are untouched.
        """
        pension = wrapper_of(PENSION, "100000", crystallised="50000")
        plan = retiree_plan((pension,), spending="600")
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(lsa_cap=Money(Decimal(0))),
            RunConfig(
                today=date(2026, 1, 1),
                horizon_end=date(2026, 12, 31),
                withdrawal_strategy=UncrystallisedOnlyStrategy(),
                tax_free_cash=TaxFreeCashStrategy.LUMP_SUM_AS_NEEDED,
            ),
        )
        [person_result] = result.snapshots[0].persons
        [pension_result] = person_result.wrappers
        assert pension_result.withdrawal_taxable == Money(Decimal("799.99"))
        assert pension_result.closing_crystallised == Money(Decimal("50000.00"))
        assert pension_result.closing_uncrystallised == Money(Decimal("99200.01"))
        assert person_result.net_withdrawn == Money(Decimal("600.00"))

    def test_strategy_state_reports_remaining_headroom(self) -> None:
        """Strategies see the headroom the engine will enforce.

        With a 1,500 cap and 1,100 already used, the withdrawal step
        opens with 400 of headroom; with no cap the state reports
        ``None``.
        """
        recording_capped = RecordingWithdrawalStrategy()
        pension = wrapper_of(PENSION, "100000")
        capped_plan = household_of(
            person_of(
                (pension,),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                lsa_used="1100",
            ),
            spending="600",
        )
        run(
            capped_plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(lsa_cap=Money(Decimal(1500))),
            RunConfig(
                today=date(2026, 1, 1),
                horizon_end=date(2026, 12, 31),
                withdrawal_strategy=recording_capped,
            ),
        )
        assert recording_capped.states[0].tax_free_cash_headroom == Money(Decimal(400))
        recording_uncapped = RecordingWithdrawalStrategy()
        run(
            retiree_plan((wrapper_of(PENSION, "100000"),), spending="600"),
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            RunConfig(
                today=date(2026, 1, 1),
                horizon_end=date(2026, 12, 31),
                withdrawal_strategy=recording_uncapped,
            ),
        )
        assert recording_uncapped.states[0].tax_free_cash_headroom is None

    def test_gross_defined_plans_resolve_as_split_payments(self) -> None:
        """A fixed-% draw splits at the cap boundary, phased modes aside.

        10% of the 100,000 pot is a 10,000 gross instruction: 4,000
        through the free-bearing slice (1,000 free under the 1,000
        cap + 3,000 taxable) and 6,000 wholly taxable. Net delivered
        is 10,000 less 2,250 tax; the rest of the need is shortfall.
        """
        pension = wrapper_of(PENSION, "100000")
        plan = retiree_plan((pension,))
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(lsa_cap=Money(Decimal(1000))),
            RunConfig(
                today=date(2026, 1, 1),
                horizon_end=date(2026, 12, 31),
                withdrawal_strategy=FixedPercentWithdrawalStrategy(
                    rate=Rate(Decimal("0.10"))
                ),
                tax_free_cash=TaxFreeCashStrategy.LUMP_SUM_AS_NEEDED,
            ),
        )
        [person_result] = result.snapshots[0].persons
        [pension_result] = person_result.wrappers
        assert pension_result.withdrawal_tax_free == Money(Decimal("1000.00"))
        assert pension_result.withdrawal_taxable == Money(Decimal("9000.00"))
        assert pension_result.closing_crystallised == ZERO
        assert person_result.tax.tax_due == Money(Decimal("2250.00"))
        assert person_result.net_withdrawn == Money(Decimal("7750.00"))
        assert person_result.shortfall == Money(Decimal("4250.00"))
        assert person_result.lsa_used == Money(Decimal("1000.00"))
        assert person_result.mpaa_triggered_on == date(2026, 1, 1)


class TestPensionIncome:
    """Step 2 income streams: DB pensions and the state pension (4.2/4.3)."""

    def test_db_income_offsets_the_net_spending_need(self) -> None:
        """Net-of-tax DB income meets the need before wrappers are drawn.

        A 66-year-old with an 8,000 DB pension in payment (NPA 65, no
        revaluation) and a 12,000 net need: the flat 25% tax leaves
        6,000 net income, so only 6,000 comes from the free account.
        """
        free_account = wrapper_of(FREE, "100000")
        pension = db_pension_of(accrued="8000", statement=date(2024, 1, 1), npa=65)
        plan = household_of(
            person_of(
                (free_account,),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                db_pensions=(pension,),
            ),
            spending="12000",
        )
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        assert person_result.db_income == Money(Decimal("8000.00"))
        assert person_result.tax.tax_due == Money(Decimal("2000.00"))
        assert person_result.spending_need == Money(Decimal("12000.00"))
        assert person_result.net_withdrawn == Money(Decimal("6000.00"))
        assert person_result.shortfall == ZERO
        [free_result] = person_result.wrappers
        assert free_result.closing_uncrystallised == Money(Decimal("94000.00"))

    def test_db_income_starting_mid_period_is_pro_rated(self) -> None:
        """An entitlement beginning mid-period pays whole months only.

        NPA 66 with a 1 July birthday starts payment halfway through
        the calendar-year period: 6 of 12 months, so 4,000 of 8,000.
        """
        pension = db_pension_of(accrued="8000", npa=66)
        plan = household_of(
            person_of(
                (),
                date_of_birth=date(1960, 7, 1),
                retire_at=60,
                db_pensions=(pension,),
            )
        )
        result = run(plan, assumptions_with(), stub_region(), one_period_config())
        [person_result] = result.snapshots[0].persons
        assert person_result.db_income == Money(Decimal("4000.00"))

    def test_db_revaluation_compounds_before_and_within_the_run(self) -> None:
        """A fixed 10% basis revalues the deferment span and each period.

        Statement two years before today: the pre-run factor is
        exactly 1.21; the second period compounds one more year.
        """
        basis = RevaluationBasis(
            reference=RevaluationReference.FIXED, fixed_rate=Rate(Decimal("0.10"))
        )
        pension = db_pension_of(
            accrued="8000", statement=date(2024, 1, 1), npa=65, basis=basis
        )
        plan = household_of(
            person_of(
                (),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                db_pensions=(pension,),
            )
        )
        config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2027, 12, 31))
        result = run(plan, assumptions_with(), stub_region(), config)
        first, second = result.snapshots
        assert first.persons[0].db_income == Money(Decimal("9680.00"))
        assert second.persons[0].db_income == Money(Decimal("10648.00"))

    def test_cpi_revaluation_is_capped_by_the_scheme_basis(self) -> None:
        """CPI 8% against a 5% cap revalues at 5% (planning §5.1)."""
        basis = RevaluationBasis(
            reference=RevaluationReference.CPI, cap=Rate(Decimal("0.05"))
        )
        pension = db_pension_of(accrued="8000", npa=65, basis=basis)
        plan = household_of(
            person_of(
                (),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                db_pensions=(pension,),
            )
        )
        config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2027, 12, 31))
        result = run(
            plan,
            assumptions_with({"inflation.cpi": Decimal("0.08")}),
            stub_region(),
            config,
        )
        first, second = result.snapshots
        assert first.persons[0].db_income == Money(Decimal("8000.00"))
        assert second.persons[0].db_income == Money(Decimal("8400.00"))

    def test_commutation_trades_pension_for_a_lump_sum_at_start(self) -> None:
        """Commuting 25% at factor 12 pays 24,000 once, pension drops.

        8,000 x 25% = 2,000 given up buys 2,000 x 12 = 24,000 tax-free
        in the starting period only; the residual pension is 6,000. The
        lump sum plus net pension income covers the whole 12,000 need,
        so nothing is drawn from wrappers.
        """
        free_account = wrapper_of(FREE, "100000")
        pension = db_pension_of(
            accrued="8000", npa=66, commuted="0.25", commutation_factor="12"
        )
        plan = household_of(
            person_of(
                (free_account,),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                db_pensions=(pension,),
            ),
            spending="12000",
        )
        config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2027, 12, 31))
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            config,
        )
        first, second = result.snapshots
        assert first.persons[0].db_income == Money(Decimal("6000.00"))
        assert first.persons[0].db_lump_sum == Money(Decimal("24000.00"))
        assert first.persons[0].net_withdrawn == ZERO
        assert first.persons[0].shortfall == ZERO
        assert second.persons[0].db_lump_sum == ZERO

    def test_a_lump_sum_already_taken_stays_out_of_the_run(self) -> None:
        """Benefits started before today never re-pay the lump sum.

        The start date (NPA 65, 2025) precedes today, so the run sees
        the reduced pension in payment and no commutation cash — that
        money already lives in the user's stated balances.
        """
        pension = db_pension_of(
            accrued="8000", npa=65, commuted="0.25", commutation_factor="12"
        )
        plan = household_of(
            person_of(
                (),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                db_pensions=(pension,),
            )
        )
        result = run(plan, assumptions_with(), stub_region(), one_period_config())
        [person_result] = result.snapshots[0].persons
        assert person_result.db_income == Money(Decimal("6000.00"))
        assert person_result.db_lump_sum == ZERO

    def test_early_retirement_factor_scales_the_pension(self) -> None:
        """Taking at 63 with a stated 0.85 factor pays 6,800 of 8,000."""
        pension = db_pension_of(
            accrued="8000", npa=65, taken_at=63, factors={63: Decimal("0.85")}
        )
        plan = household_of(
            person_of(
                (),
                date_of_birth=date(1963, 1, 1),
                retire_at=63,
                db_pensions=(pension,),
            )
        )
        result = run(plan, assumptions_with(), stub_region(), one_period_config())
        [person_result] = result.snapshots[0].persons
        assert person_result.db_income == Money(Decimal("6800.00"))

    def test_state_pension_slices_uprate_by_rule_and_cpi(self) -> None:
        """The main slice follows the triple-lock proxy, the rest CPI.

        With CPI 2%, floor 2.5%, margin 0.5%: the 10,000 main slice
        grows 2.5% while the 1,000 CPI slice grows 2% — 11,270 in the
        second period.
        """
        plan = household_of(
            person_of(
                (),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                state_pension=sp_record(),
            )
        )
        scheme = StubStatePension(
            annual=Money(Decimal(10000)), cpi_annual=Money(Decimal(1000)), start_age=66
        )
        config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2027, 12, 31))
        result = run(
            plan,
            assumptions_with(
                {
                    "inflation.cpi": Decimal("0.02"),
                    "policy.state_pension.uprating": TRIPLE_LOCK,
                }
            ),
            stub_region(state_pension=scheme),
            config,
        )
        first, second = result.snapshots
        assert first.persons[0].state_pension_income == Money(Decimal("11000.00"))
        assert second.persons[0].state_pension_income == Money(Decimal("11270.00"))
        keys_read = {entry.key for entry in result.provenance.assumptions}
        assert AssumptionKey.POLICY_STATE_PENSION_UPRATING in keys_read

    def test_state_pension_uprating_steps_whole_across_a_partial_year(self) -> None:
        """A mid-year start never dilutes the next boundary's uprating.

        Rates step by a full year's uprating at each period boundary
        (upratings take effect whole each April), so a run starting
        halfway through the first period still sees 10,000 x 1.025 +
        1,000 x 1.02 = 11,270 in the second period — not the
        fraction-scaled 11,135 the linear convention would give.
        """
        plan = household_of(
            person_of(
                (),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                state_pension=sp_record(),
            )
        )
        scheme = StubStatePension(
            annual=Money(Decimal(10000)), cpi_annual=Money(Decimal(1000)), start_age=66
        )
        config = RunConfig(today=date(2026, 7, 1), horizon_end=date(2027, 12, 31))
        result = run(
            plan,
            assumptions_with(
                {
                    "inflation.cpi": Decimal("0.02"),
                    "policy.state_pension.uprating": TRIPLE_LOCK,
                }
            ),
            stub_region(state_pension=scheme),
            config,
        )
        first, second = result.snapshots
        assert first.persons[0].state_pension_income == Money(Decimal("5500.00"))
        assert second.persons[0].state_pension_income == Money(Decimal("11270.00"))

    def test_deflation_freezes_the_state_pension(self) -> None:
        """A negative CPI leaves both slices unchanged, never cut.

        Statutory uprating does not apply a negative increase: with
        CPI -1% under the cpi rule, both the main and CPI-only slices
        pay the same 11,000 in the second period.
        """
        plan = household_of(
            person_of(
                (),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                state_pension=sp_record(),
            )
        )
        scheme = StubStatePension(
            annual=Money(Decimal(10000)), cpi_annual=Money(Decimal(1000)), start_age=66
        )
        config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2027, 12, 31))
        result = run(
            plan,
            assumptions_with(
                {
                    "inflation.cpi": Decimal("-0.01"),
                    "policy.state_pension.uprating": "cpi",
                }
            ),
            stub_region(state_pension=scheme),
            config,
        )
        first, second = result.snapshots
        assert first.persons[0].state_pension_income == Money(Decimal("11000.00"))
        assert second.persons[0].state_pension_income == Money(Decimal("11000.00"))

    def test_deferral_increment_builds_on_the_rate_at_claim(self) -> None:
        """The uplift applies to the uprated rate, then follows CPI.

        A 10% uplift claimed one year in: the base has uprated to
        10,250 (2.5% proxy), so the increment is 1,025 — 11,275 in the
        claim year. A year later the base is 10,506.25 but the
        increment follows CPI only: 1,025 x 1.02 = 1,045.50, paying
        11,551.75.
        """
        plan = household_of(
            person_of(
                (),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                state_pension=sp_record(deferral="1"),
            )
        )
        scheme = StubStatePension(
            annual=Money(Decimal(10000)), start_age=66, uplift=Decimal("0.10")
        )
        config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2028, 12, 31))
        result = run(
            plan,
            assumptions_with(
                {
                    "inflation.cpi": Decimal("0.02"),
                    "policy.state_pension.uprating": TRIPLE_LOCK,
                }
            ),
            stub_region(state_pension=scheme),
            config,
        )
        first, second, third = result.snapshots
        assert first.persons[0].state_pension_income == ZERO
        assert second.persons[0].state_pension_income == Money(Decimal("11275.00"))
        assert third.persons[0].state_pension_income == Money(Decimal("11551.75"))

    def test_state_pension_deferral_shifts_the_start(self) -> None:
        """A one-year deferral moves the entitlement into period two."""
        plan = household_of(
            person_of(
                (),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                state_pension=sp_record(deferral="1"),
            )
        )
        scheme = StubStatePension(annual=Money(Decimal(10000)), start_age=66)
        config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2027, 12, 31))
        result = run(
            plan,
            assumptions_with({"policy.state_pension.uprating": "cpi"}),
            stub_region(state_pension=scheme),
            config,
        )
        first, second = result.snapshots
        assert first.persons[0].state_pension_income == ZERO
        assert second.persons[0].state_pension_income == Money(Decimal("10000.00"))

    def test_a_zero_entitlement_reads_no_uprating_assumption(self) -> None:
        """A record earning nothing creates no stream and no reads.

        The baseline assumption set has no uprating key, so a read
        would fail loudly — and the provenance stays free of keys the
        result does not rest on.
        """
        plan = household_of(
            person_of(
                (),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                state_pension=sp_record(),
            )
        )
        result = run(
            plan,
            assumptions_with(),
            stub_region(state_pension=StubStatePension()),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        assert person_result.state_pension_income == ZERO
        keys_read = {entry.key for entry in result.provenance.assumptions}
        assert AssumptionKey.POLICY_STATE_PENSION_UPRATING not in keys_read

    def test_pension_income_is_taxed_alongside_employment(self) -> None:
        """DB income before retirement joins the assessed income picture."""
        pension = db_pension_of(accrued="8000", npa=65)
        plan = household_of(
            person_of(
                (),
                date_of_birth=date(1960, 1, 1),
                retire_at=70,
                employment="20000",
                db_pensions=(pension,),
            )
        )
        result = run(plan, assumptions_with(), stub_region(), one_period_config())
        [person_result] = result.snapshots[0].persons
        assert person_result.employment_income == Money(Decimal("20000.00"))
        assert person_result.db_income == Money(Decimal("8000.00"))
        assert person_result.tax.tax_due == Money(Decimal("7000.00"))

    def test_db_statement_after_today_is_rejected(self) -> None:
        """A statement date in the future cannot anchor revaluation."""
        pension = db_pension_of(accrued="8000", statement=date(2027, 6, 1), npa=65)
        plan = household_of(
            person_of(
                (),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                db_pensions=(pension,),
            )
        )
        assumptions = assumptions_with()
        region = stub_region()
        config = one_period_config()
        with pytest.raises(EngineError, match="statement date"):
            run(plan, assumptions, region, config)

    def test_pension_provenance_lists_facts_and_decisions(self) -> None:
        """DB and state pension entries land under stable labels."""
        pension = db_pension_of(
            accrued="8000",
            npa=65,
            taken_at=63,
            factors={63: Decimal("0.85")},
            commuted="0.25",
            commutation_factor="12",
        )
        plan = household_of(
            person_of(
                (),
                date_of_birth=date(1960, 1, 1),
                retire_at=60,
                db_pensions=(pension,),
                state_pension=sp_record(),
            )
        )
        result = run(
            plan,
            assumptions_with({"policy.state_pension.uprating": "cpi"}),
            stub_region(state_pension=StubStatePension(annual=Money(Decimal(10000)))),
            one_period_config(),
        )
        fact_labels = {entry.label for entry in result.provenance.facts}
        decision_labels = {entry.label for entry in result.provenance.decisions}
        assert "db_pension[db-1].accrued_annual_pension" in fact_labels
        assert "db_pension[db-1].normal_pension_age" in fact_labels
        assert "db_pension[db-1].commutation_factor" in fact_labels
        assert "person[person-1].state_pension.forecast_weekly_amount" in fact_labels
        assert "db_pension[db-1].taken_at_age" in decision_labels
        assert "db_pension[db-1].commuted_fraction" in decision_labels
        assert "person[person-1].state_pension.planned_extra_years" in decision_labels
        assert "person[person-1].state_pension.deferral_years" in decision_labels


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
        config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2028, 12, 31))
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


class TestPartialPeriods:
    """Roadmap 4.6: whole-month pro-rating of partial first/last periods."""

    def test_mid_period_today_pro_rates_flows_fees_and_growth(self) -> None:
        """A 1 July start models half the year, never the elapsed half.

        The fraction is 6/12: employment 50,000 → 25,000; the RAS
        employee 4,000 and employer 2,000 → 2,000 + 1,000 (relief 400).
        The post-flow balance is 13,000, so the 1% fee on the average
        of 10,000 and 13,000 scales to 115 x 1/2 = 57.50, and 10%
        growth scales to 5% of the post-fee 12,942.50 → 647.125.
        """
        schedule = ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(4000)), recorded_on=RECORDED),
            employer_amount=money_fact("2000"),
            relief_mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
        )
        pension = Wrapper(
            id=EntityId("wrapper-half-year"),
            kind=PENSION,
            balance=money_fact("10000"),
            contributions=schedule,
            allocation=EQUITY_ONLY,
            fees=FeeSchedule(platform=Rate(Decimal("0.01")), fund=Rate(Decimal(0))),
        )
        plan = household_of(person_of((pension,), employment="50000"))
        result = run(
            plan,
            assumptions_with(),
            stub_region(),
            RunConfig(today=date(2026, 7, 1), horizon_end=date(2026, 12, 31)),
        )
        [snapshot] = result.snapshots
        assert snapshot.year_fraction == Decimal("0.5")
        [person_result] = snapshot.persons
        [wrapper_result] = person_result.wrappers
        assert person_result.employment_income == Money(Decimal("25000.00"))
        assert person_result.tax.tax_due == Money(Decimal("6250.00"))
        assert wrapper_result.employee_contribution == Money(Decimal("2000.00"))
        assert wrapper_result.employer_contribution == Money(Decimal("1000.00"))
        assert wrapper_result.provider_relief == Money(Decimal("400.00"))
        assert wrapper_result.fee == Money(Decimal("57.50"))
        assert wrapper_result.growth == Money(Decimal("647.12"))
        assert wrapper_result.closing_uncrystallised == Money(Decimal("13589.62"))

    def test_escalation_advances_by_the_completed_fraction_only(self) -> None:
        """A July start advances levels by half a year, never a whole one.

        The completed first period covers 6/12, so the second period's
        price level is 1 + 0.10 x 1/2 = 1.05 and the earnings level is
        1 + 0.21 x 1/2 = 1.105 (annual nominal 21% from 10% real and
        10% CPI, scaled linearly per §5.2) — not the whole-year 1.10
        and 1.21.
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
            RunConfig(today=date(2026, 7, 1), horizon_end=date(2027, 12, 31)),
        )
        first, second = result.snapshots
        assert first.year_fraction == Decimal("0.5")
        assert first.persons[0].employment_income == Money(Decimal("5000.00"))
        assert second.inflation_factor == Decimal("1.05")
        assert second.persons[0].employment_income == Money(Decimal("11050.00"))
        assert second.persons[0].wrappers[0].employee_contribution == Money(
            Decimal("1105.00")
        )

    def test_final_period_stops_at_the_horizon_end(self) -> None:
        """A 30 June horizon halves the second period's flows."""
        plan = household_of(person_of((), employment="10000"))
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            RunConfig(today=date(2026, 1, 1), horizon_end=date(2027, 6, 30)),
        )
        first, second = result.snapshots
        assert first.year_fraction == Decimal(1)
        assert first.persons[0].employment_income == Money(Decimal("10000.00"))
        assert second.year_fraction == Decimal("0.5")
        assert second.persons[0].employment_income == Money(Decimal("5000.00"))

    def test_spending_need_is_pro_rated_in_a_partial_period(self) -> None:
        """A retiree starting mid-year needs half the annual spending."""
        free_account = wrapper_of(FREE, "100000")
        plan = retiree_plan((free_account,), spending="12000")
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            RunConfig(today=date(2026, 7, 1), horizon_end=date(2026, 12, 31)),
        )
        [person_result] = result.snapshots[0].persons
        assert person_result.spending_need == Money(Decimal("6000.00"))
        assert person_result.net_withdrawn == Money(Decimal("6000.00"))
        [wrapper_result] = person_result.wrappers
        assert wrapper_result.closing_uncrystallised == Money(Decimal("94000.00"))

    def test_under_one_whole_month_models_a_zero_flow_period(self) -> None:
        """Less than a whole month inside the window rounds to nothing.

        The §4.1 whole-month convention counts zero months from
        15 December, so the period is emitted with zero flows and
        zero growth — the run never invents part-month amounts.
        """
        pension = wrapper_of(PENSION, "10000")
        plan = household_of(person_of((pension,), employment="50000"))
        result = run(
            plan,
            assumptions_with(),
            stub_region(),
            RunConfig(today=date(2026, 12, 15), horizon_end=date(2026, 12, 31)),
        )
        [snapshot] = result.snapshots
        assert snapshot.year_fraction == Decimal(0)
        [person_result] = snapshot.persons
        assert person_result.employment_income == ZERO
        [wrapper_result] = person_result.wrappers
        assert wrapper_result.growth == ZERO
        assert wrapper_result.fee == ZERO
        assert wrapper_result.closing_uncrystallised == Money(Decimal("10000.00"))


@dataclass(frozen=True)
class OrderedNetStrategy:
    """A test strategy returning a fixed net-defined plan verbatim."""

    target: Money
    order: tuple[WithdrawalSourceId, ...]

    uses_natural_yield: ClassVar[bool] = False

    def withdraw(self, state: WithdrawalState, need: Money) -> WithdrawalPlan:
        """Ignore the state and need; the plan is preconfigured."""
        del state, need
        return NetWithdrawalPlan(target=self.target, order=self.order)


class TestWithdrawalStrategies:
    """Roadmap 5.1: the configured strategy drives step 4."""

    def test_default_strategy_is_fixed_real(self) -> None:
        """An unconfigured run meets the net need (planning §5.2)."""
        assert one_period_config().withdrawal_strategy == FixedRealWithdrawalStrategy()

    def test_fixed_percent_draws_its_share_without_a_spending_plan(self) -> None:
        """4% of a 10,000 free account is a 400 gross = 400 net draw.

        Gross-defined strategies run in every decumulation period, need
        or no need — here with no spending plan at all.
        """
        free_account = wrapper_of(FREE, "10000")
        plan = household_of(
            person_of((free_account,), date_of_birth=date(1960, 1, 1), retire_at=60)
        )
        config = RunConfig(
            today=date(2026, 1, 1),
            horizon_end=date(2026, 12, 31),
            withdrawal_strategy=FixedPercentWithdrawalStrategy(
                rate=Rate(Decimal("0.04"))
            ),
        )
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            config,
        )
        [person_result] = result.snapshots[0].persons
        [free_result] = person_result.wrappers
        assert free_result.withdrawal_tax_free == Money(Decimal("400.00"))
        assert person_result.net_withdrawn == Money(Decimal("400.00"))
        assert person_result.spending_need == ZERO
        assert person_result.shortfall == ZERO

    def test_fixed_percent_taxable_draw_skips_the_gross_up(self) -> None:
        """The gross draw is exact; the net is whatever survives tax.

        10% of a pot of 4,000 crystallised + 6,000 uncrystallised is a
        1,000 gross draw, taken from the crystallised funds first —
        fully taxable, so the flat 25% leaves 750 net (no gross-up to
        a net target, unlike the fixed-real draws of TestWithdrawals).
        """
        pension = wrapper_of(PENSION, "6000", crystallised="4000")
        plan = household_of(
            person_of((pension,), date_of_birth=date(1960, 1, 1), retire_at=60)
        )
        config = RunConfig(
            today=date(2026, 1, 1),
            horizon_end=date(2026, 12, 31),
            withdrawal_strategy=FixedPercentWithdrawalStrategy(
                rate=Rate(Decimal("0.10"))
            ),
        )
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            config,
        )
        [person_result] = result.snapshots[0].persons
        [pension_result] = person_result.wrappers
        assert pension_result.withdrawal_taxable == Money(Decimal("1000.00"))
        assert pension_result.withdrawal_tax_free == ZERO
        assert pension_result.closing_crystallised == Money(Decimal("3000.00"))
        assert person_result.tax.tax_due == Money(Decimal("250.00"))
        assert person_result.net_withdrawn == Money(Decimal("750.00"))

    def test_fixed_percent_pot_excludes_gate_closed_funds(self) -> None:
        """Uncrystallised pension funds under the access age stay out.

        The 45-year-old retiree's pot is only the 5,000 free account;
        10% draws 500 and the 20,000 pension is untouched (§4.1).
        """
        free_account = wrapper_of(FREE, "5000")
        pension = wrapper_of(PENSION, "20000")
        plan = household_of(
            person_of(
                (free_account, pension),
                date_of_birth=date(1975, 6, 1),
                retire_at=45,
            )
        )
        config = RunConfig(
            today=date(2026, 1, 1),
            horizon_end=date(2026, 12, 31),
            withdrawal_strategy=FixedPercentWithdrawalStrategy(
                rate=Rate(Decimal("0.10"))
            ),
        )
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            config,
        )
        [person_result] = result.snapshots[0].persons
        free_result, pension_result = person_result.wrappers
        assert free_result.withdrawal_tax_free == Money(Decimal("500.00"))
        assert pension_result.withdrawal_gross == ZERO

    def test_fixed_percent_pot_excludes_gated_tax_free_funds(self) -> None:
        """The gate holds for tax-free kinds in the pot base too.

        With the free kind gated at 60, the 50-year-old retiree's pot
        is only the 10,000 of crystallised pension funds: 10% draws
        1,000 gross there (fully taxable, 750 net) and the 50,000
        tax-free account is untouched.
        """
        free_account = wrapper_of(FREE, "50000")
        pension = wrapper_of(PENSION, "0", crystallised="10000")
        plan = household_of(
            person_of(
                (free_account, pension),
                date_of_birth=date(1975, 6, 1),
                retire_at=45,
            )
        )
        config = RunConfig(
            today=date(2026, 1, 1),
            horizon_end=date(2026, 12, 31),
            withdrawal_strategy=FixedPercentWithdrawalStrategy(
                rate=Rate(Decimal("0.10"))
            ),
        )
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(free_access_age=60),
            config,
        )
        [person_result] = result.snapshots[0].persons
        free_result, pension_result = person_result.wrappers
        assert free_result.withdrawal_gross == ZERO
        assert pension_result.withdrawal_taxable == Money(Decimal("1000.00"))
        assert person_result.net_withdrawn == Money(Decimal("750.00"))

    def test_fixed_percent_gap_to_the_need_is_shortfall(self) -> None:
        """A gross-defined draw below the need reports the miss.

        The ruin signal of roadmap 7.3 must not vanish because the
        strategy ignores the need: 4% of 10,000 delivers 400 against a
        12,000 need, leaving 11,600 unmet.
        """
        free_account = wrapper_of(FREE, "10000")
        plan = retiree_plan((free_account,))
        config = RunConfig(
            today=date(2026, 1, 1),
            horizon_end=date(2026, 12, 31),
            withdrawal_strategy=FixedPercentWithdrawalStrategy(
                rate=Rate(Decimal("0.04"))
            ),
        )
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            config,
        )
        [person_result] = result.snapshots[0].persons
        assert person_result.net_withdrawn == Money(Decimal("400.00"))
        assert person_result.shortfall == Money(Decimal("11600.00"))

    def test_plan_drawing_gate_closed_funds_is_rejected(self) -> None:
        """Execution enforces the access gates on any strategy (§4.1)."""
        pension = wrapper_of(PENSION, "100000")
        strategy = OrderedNetStrategy(
            target=Money(Decimal(1000)),
            order=(WithdrawalSourceId(wrapper_id=pension.id, crystallised=False),),
        )
        plan = household_of(
            person_of((pension,), date_of_birth=date(1975, 6, 1), retire_at=45),
            spending="1000",
        )
        assumptions = assumptions_with({"returns.equity.real": Decimal(0)})
        region = stub_region()
        config = RunConfig(
            today=date(2026, 1, 1),
            horizon_end=date(2026, 12, 31),
            withdrawal_strategy=strategy,
        )
        with pytest.raises(EngineError, match="access gate"):
            run(plan, assumptions, region, config)

    def test_plan_referencing_an_unknown_source_is_rejected(self) -> None:
        """A dangling source reference is a strategy bug, failed loudly."""
        free_account = wrapper_of(FREE, "10000")
        strategy = OrderedNetStrategy(
            target=Money(Decimal(1000)),
            order=(
                WithdrawalSourceId(
                    wrapper_id=EntityId("no-such-wrapper"), crystallised=False
                ),
            ),
        )
        plan = retiree_plan((free_account,))
        assumptions = assumptions_with({"returns.equity.real": Decimal(0)})
        region = stub_region()
        config = RunConfig(
            today=date(2026, 1, 1),
            horizon_end=date(2026, 12, 31),
            withdrawal_strategy=strategy,
        )
        with pytest.raises(EngineError, match="unknown source"):
            run(plan, assumptions, region, config)


def outflow_at(age: int, amount: str = "10000") -> PlannedOutflow:
    """An outflow for the standard test person at ``age`` (roadmap 5.4)."""
    return PlannedOutflow(
        id=EntityId("outflow-1"),
        label="one-off",
        amount_real=Decision(value=Money(Decimal(amount)), recorded_on=RECORDED),
        at_age_of=(EntityId("person-1"), age),
    )


class TestPlannedOutflows:
    """Roadmap 5.4: dated one-offs funded through the withdrawal machinery."""

    def test_outflow_hits_the_period_its_age_is_attained(self) -> None:
        """The outflow lands whole in its period and nowhere else."""
        free_account = wrapper_of(FREE, "50000")
        person = person_of((free_account,), date_of_birth=date(1990, 1, 1))
        plan = Household(persons=(person,), planned_outflows=(outflow_at(37),))
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            RunConfig(today=date(2026, 1, 1), horizon_end=date(2027, 12, 31)),
        )
        first, second = result.snapshots
        assert first.persons[0].planned_outflows == ZERO
        assert first.persons[0].net_withdrawn == ZERO
        assert second.persons[0].planned_outflows == Money(Decimal("10000.00"))
        assert second.persons[0].net_withdrawn == Money(Decimal("10000.00"))
        assert second.persons[0].shortfall == ZERO
        assert second.persons[0].wrappers[0].withdrawal_tax_free == Money(
            Decimal("10000.00")
        )

    def test_outflow_is_inflated_to_nominal_by_the_cpi_path(self) -> None:
        """A today's-money decision inflates like the spending need."""
        free_account = wrapper_of(FREE, "50000")
        person = person_of((free_account,), date_of_birth=date(1990, 1, 1))
        plan = Household(persons=(person,), planned_outflows=(outflow_at(37),))
        result = run(
            plan,
            assumptions_with(
                {
                    "inflation.cpi": Decimal("0.10"),
                    "returns.equity.real": Decimal(0),
                }
            ),
            stub_region(),
            RunConfig(today=date(2026, 1, 1), horizon_end=date(2027, 12, 31)),
        )
        second = result.snapshots[1]
        assert second.persons[0].planned_outflows == Money(Decimal("11000.00"))
        assert second.persons[0].net_withdrawn == Money(Decimal("11000.00"))

    def test_outflow_before_today_is_ignored(self) -> None:
        """A date already past lives in the stated balances, not the model."""
        free_account = wrapper_of(FREE, "50000")
        person = person_of((free_account,), date_of_birth=date(1990, 1, 1))
        plan = Household(persons=(person,), planned_outflows=(outflow_at(36),))
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            RunConfig(today=date(2026, 6, 1), horizon_end=date(2026, 12, 31)),
        )
        [person_result] = result.snapshots[0].persons
        assert person_result.planned_outflows == ZERO
        assert person_result.net_withdrawn == ZERO

    def test_outflow_after_the_horizon_end_is_ignored(self) -> None:
        """A date past the horizon is outside the modelled window."""
        free_account = wrapper_of(FREE, "50000")
        person = person_of((free_account,), date_of_birth=date(1989, 9, 1))
        plan = Household(persons=(person,), planned_outflows=(outflow_at(37),))
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            RunConfig(today=date(2026, 1, 1), horizon_end=date(2026, 6, 30)),
        )
        [person_result] = result.snapshots[0].persons
        assert person_result.planned_outflows == ZERO
        assert person_result.net_withdrawn == ZERO

    def test_pre_retirement_outflow_is_funded_tax_aware(self) -> None:
        """Before decumulation the default order funds the outflow.

        A 10,000 outflow against a 4,000 free account and an accessible
        pension: 4,000 net comes free, then the remaining 6,000 net
        grosses up through the 25%-tax-free pension draw — 7,384.61
        gross (1,846.15 tax-free, 5,538.46 taxable, 1,384.61 tax).
        """
        free_account = wrapper_of(FREE, "4000")
        pension = wrapper_of(PENSION, "100000")
        person = person_of((free_account, pension), date_of_birth=date(1970, 1, 1))
        plan = Household(persons=(person,), planned_outflows=(outflow_at(56),))
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        free_result, pension_result = person_result.wrappers
        assert person_result.stage is not LifeStage.DECUMULATION
        assert free_result.withdrawal_tax_free == Money(Decimal("4000.00"))
        assert pension_result.withdrawal_tax_free == Money(Decimal("1846.15"))
        assert pension_result.withdrawal_taxable == Money(Decimal("5538.46"))
        assert person_result.tax.tax_due == Money(Decimal("1384.61"))
        assert person_result.planned_outflows == Money(Decimal("10000.00"))
        assert person_result.net_withdrawn == Money(Decimal("10000.00"))
        assert person_result.shortfall == ZERO

    def test_outflow_joins_the_spending_need_in_decumulation(self) -> None:
        """In decumulation the outflow rides the configured strategy."""
        free_account = wrapper_of(FREE, "100000")
        person = person_of(
            (free_account,), date_of_birth=date(1960, 1, 1), retire_at=60
        )
        spending = SpendingPlan(annual_spending_real=money_fact("12000"))
        plan = Household(
            persons=(person,),
            spending=spending,
            planned_outflows=(outflow_at(66),),
        )
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        assert person_result.spending_need == Money(Decimal("12000.00"))
        assert person_result.planned_outflows == Money(Decimal("10000.00"))
        assert person_result.net_withdrawn == Money(Decimal("22000.00"))
        assert person_result.shortfall == ZERO


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


class TestGuardrailsStrategy:
    """Roadmap 5.3: guardrail crossings adjust spending in a real run."""

    def test_above_the_upper_guardrail_the_cut_reports_as_shortfall(self) -> None:
        """12,000 over 100,000 is 12%: the 10% cut leaves 1,200 unmet."""
        free_account = wrapper_of(FREE, "100000")
        plan = retiree_plan((free_account,))
        config = RunConfig(
            today=date(2026, 1, 1),
            horizon_end=date(2026, 12, 31),
            withdrawal_strategy=GuardrailsWithdrawalStrategy(),
        )
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            config,
        )
        [person_result] = result.snapshots[0].persons
        assert person_result.net_withdrawn == Money(Decimal("10800.00"))
        assert person_result.shortfall == Money(Decimal("1200.00"))

    def test_below_the_lower_guardrail_spending_rises(self) -> None:
        """12,000 over 1,000,000 is 1.2%: the prosperity rule adds 10%."""
        free_account = wrapper_of(FREE, "1000000")
        plan = retiree_plan((free_account,))
        config = RunConfig(
            today=date(2026, 1, 1),
            horizon_end=date(2026, 12, 31),
            withdrawal_strategy=GuardrailsWithdrawalStrategy(),
        )
        result = run(
            plan,
            assumptions_with({"returns.equity.real": Decimal(0)}),
            stub_region(),
            config,
        )
        [person_result] = result.snapshots[0].persons
        assert person_result.net_withdrawn == Money(Decimal("13200.00"))
        assert person_result.shortfall == ZERO


NATURAL_YIELDS = {
    "yield.equity": Decimal("0.03"),
    "yield.bonds": Decimal("0.02"),
    "yield.cash": Decimal("0.01"),
}


class TestNaturalYieldStrategy:
    """Roadmap 5.3: the engine prices yields only when a strategy asks."""

    def run_natural_yield(self) -> tuple[Money, Money, Money]:
        """One period on the income-only strategy; key wrapper figures.

        A 10,000 tax-free account and 20,000 of crystallised pension,
        both wholly in equity at a 3% yield: 300 arrives tax-free and
        600 as taxable income (150 tax at the flat 25%), so 750 net is
        delivered against the 12,000 need.
        """
        free_account = wrapper_of(FREE, "10000")
        pension = wrapper_of(PENSION, "0", crystallised="20000")
        plan = retiree_plan((free_account, pension))
        config = RunConfig(
            today=date(2026, 1, 1),
            horizon_end=date(2026, 12, 31),
            withdrawal_strategy=NaturalYieldWithdrawalStrategy(),
        )
        overrides: dict[str, object] = {"returns.equity.real": Decimal(0)}
        overrides.update(NATURAL_YIELDS)
        result = run(plan, assumptions_with(overrides), stub_region(), config)
        [person_result] = result.snapshots[0].persons
        free_result, pension_result = person_result.wrappers
        return (
            free_result.withdrawal_tax_free,
            pension_result.withdrawal_taxable,
            person_result.net_withdrawn,
        )

    def test_each_source_is_drawn_by_its_priced_yield(self) -> None:
        """The equity yield assumption prices every source's draw."""
        free_draw, pension_draw, net = self.run_natural_yield()
        assert free_draw == Money(Decimal("300.00"))
        assert pension_draw == Money(Decimal("600.00"))
        assert net == Money(Decimal("750.00"))

    def test_yield_keys_enter_provenance_only_when_priced(self) -> None:
        """A yield-aware run records the yield keys; fixed-real never does."""
        free_account = wrapper_of(FREE, "10000")
        plan = retiree_plan((free_account,))
        yield_config = RunConfig(
            today=date(2026, 1, 1),
            horizon_end=date(2026, 12, 31),
            withdrawal_strategy=NaturalYieldWithdrawalStrategy(),
        )
        overrides: dict[str, object] = {"returns.equity.real": Decimal(0)}
        overrides.update(NATURAL_YIELDS)
        assumptions = assumptions_with(overrides)
        region = stub_region()
        priced = run(plan, assumptions, region, yield_config)
        unpriced = run(plan, assumptions, region, one_period_config())
        priced_keys = {entry.key for entry in priced.provenance.assumptions}
        unpriced_keys = {entry.key for entry in unpriced.provenance.assumptions}
        assert AssumptionKey.YIELD_EQUITY in priced_keys
        assert AssumptionKey.YIELD_BONDS in priced_keys
        assert AssumptionKey.YIELD_CASH in priced_keys
        assert not unpriced_keys & set(NATURAL_YIELDS)


ANNUITY_TABLE: dict[str, object] = {
    "escalation": Decimal("0.03"),
    "joint_factor": Decimal("0.9"),
    "level": {"65": Decimal("1.0"), "67": Decimal("1.25"), "70": Decimal("1.5")},
    "escalating3": {"65": Decimal("1.0"), "67": Decimal("1.25")},
    "inflation_linked": {"65": Decimal("1.0"), "67": Decimal("1.25")},
}


def annuity_assumptions(overrides: dict[str, object] | None = None) -> AssumptionSet:
    """The stub assumptions plus zero returns and the annuity pricing."""
    values: dict[str, object] = {
        "returns.equity.real": Decimal(0),
        "annuity.level.single.65": Decimal("0.08"),
        "annuity.escalating3.single.65": Decimal("0.08"),
        "annuity.inflation_linked.single.65": Decimal("0.08"),
        "annuity.age_adjustment": ANNUITY_TABLE,
    }
    values.update(overrides or {})
    return assumptions_with(values)


def annuity_purchase_of(
    at_age: int = 67,
    fraction: str = "0.5",
    annuity_type: AnnuityType = AnnuityType.LEVEL,
    basis: AnnuityBasis = AnnuityBasis.SINGLE,
) -> AnnuityPurchase:
    """One annuity purchase decision record."""
    return AnnuityPurchase(
        id=EntityId("annuity-1"),
        at_age=Decision(value=at_age, recorded_on=RECORDED),
        fraction_of_pot=Decision(value=Decimal(fraction), recorded_on=RECORDED),
        annuity_type=annuity_type,
        basis=basis,
    )


def annuitant_plan(
    purchase: AnnuityPurchase,
    *,
    uncrystallised: str = "40000",
    crystallised: str = "20000",
    spending: str | None = None,
) -> Household:
    """A 66-year-old retiree holding one pension pot and one purchase.

    Born 1960, so the age-67 default purchase fires on 1 January 2027 —
    the second calendar-year period of a run from 1 January 2026.
    """
    pension = wrapper_of(PENSION, uncrystallised, crystallised=crystallised)
    return household_of(
        person_of(
            (pension,),
            date_of_birth=date(1960, 1, 1),
            retire_at=60,
            annuity_purchases=(purchase,),
        ),
        spending=spending,
    )


def three_period_config(**kwargs: object) -> RunConfig:
    """Calendar years 2026 through 2028."""
    return RunConfig(
        today=date(2026, 1, 1),
        horizon_end=date(2028, 12, 31),
        **kwargs,  # type: ignore[arg-type]
    )


class TestAnnuityPurchases:
    """Roadmap 5.5: purchases convert pot into priced lifetime income."""

    def test_the_purchase_converts_the_pot_fraction_into_income(self) -> None:
        """Half of a 40,000 + 20,000 pension pot annuitises at age 67.

        The uncrystallised draw of 20,000 crystallises with 5,000
        (25%) paid as tax-free cash; 15,000 of it plus the 10,000
        crystallised draw buy income at 8% x the 1.25 age-67
        multiplier = 10%: 2,500 a year.
        """
        result = run(
            annuitant_plan(annuity_purchase_of()),
            annuity_assumptions(),
            stub_region(),
            three_period_config(),
        )
        [before] = result.snapshots[0].persons
        assert before.annuity_income == ZERO
        assert before.annuity_lump_sum == ZERO
        [at_purchase] = result.snapshots[1].persons
        assert at_purchase.annuity_lump_sum == Money(Decimal("5000.00"))
        assert at_purchase.annuity_income == Money(Decimal("2500.00"))
        assert at_purchase.lsa_used == Money(Decimal("5000.00"))
        [pension_result] = at_purchase.wrappers
        assert pension_result.annuity_purchase == Money(Decimal("25000.00"))
        assert pension_result.withdrawal_tax_free == Money(Decimal("5000.00"))
        assert pension_result.closing_uncrystallised == Money(Decimal("20000.00"))
        assert pension_result.closing_crystallised == Money(Decimal("10000.00"))
        [after] = result.snapshots[2].persons
        assert after.annuity_income == Money(Decimal("2500.00"))
        assert after.annuity_lump_sum == ZERO
        [later_pension] = after.wrappers
        assert later_pension.annuity_purchase == ZERO

    def test_annuity_income_is_taxable_but_never_flexible_access(self) -> None:
        """The income is taxed as income; the purchase sets no MPAA date."""
        result = run(
            annuitant_plan(annuity_purchase_of()),
            annuity_assumptions(),
            stub_region(),
            three_period_config(),
        )
        [at_purchase] = result.snapshots[1].persons
        assert at_purchase.tax.tax_due == Money(Decimal("625.00"))
        assert at_purchase.mpaa_triggered_on is None

    def test_an_escalating_annuity_compounds_its_fixed_rate(self) -> None:
        """The 3% escalation lifts 2,500 to 2,575 in the second year."""
        purchase = annuity_purchase_of(annuity_type=AnnuityType.ESCALATING)
        result = run(
            annuitant_plan(purchase),
            annuity_assumptions(),
            stub_region(),
            three_period_config(),
        )
        [at_purchase] = result.snapshots[1].persons
        assert at_purchase.annuity_income == Money(Decimal("2500.00"))
        [after] = result.snapshots[2].persons
        assert after.annuity_income == Money(Decimal("2575.00"))

    def test_an_inflation_linked_annuity_tracks_the_cpi_path(self) -> None:
        """At 10% CPI the bought income rises 10% the following year.

        Balances grow 10% nominal through 2026, so the age-67 purchase
        annuitises half of 44,000 + 22,000: tax-free cash 5,500 and
        27,500 of capital — 2,750 of income, 3,025 the year after.
        """
        purchase = annuity_purchase_of(annuity_type=AnnuityType.INFLATION_LINKED)
        result = run(
            annuitant_plan(purchase),
            annuity_assumptions({"inflation.cpi": Decimal("0.10")}),
            stub_region(),
            three_period_config(),
        )
        [at_purchase] = result.snapshots[1].persons
        assert at_purchase.annuity_lump_sum == Money(Decimal("5500.00"))
        assert at_purchase.annuity_income == Money(Decimal("2750.00"))
        [after] = result.snapshots[2].persons
        assert after.annuity_income == Money(Decimal("3025.00"))

    def test_a_joint_basis_prices_at_the_joint_factor(self) -> None:
        """The 0.9 joint factor turns 2,500 of income into 2,250."""
        purchase = annuity_purchase_of(basis=AnnuityBasis.JOINT)
        result = run(
            annuitant_plan(purchase),
            annuity_assumptions(),
            stub_region(),
            three_period_config(),
        )
        [at_purchase] = result.snapshots[1].persons
        assert at_purchase.annuity_income == Money(Decimal("2250.00"))

    def test_annuity_income_and_lump_sum_offset_the_spending_need(self) -> None:
        """Net annuity cash meets the need before wrappers are drawn.

        2026's 12,000 need grosses 16,000 out of the crystallised
        funds, leaving 4,000 there; the 2027 purchase therefore
        annuitises 2,000 crystallised plus 15,000 of the 20,000
        uncrystallised draw (5,000 goes out tax-free), buying 1,700 a
        year. Net annuity cash — 1,700 less 425 tax plus the 5,000
        lump sum — leaves 5,725 of the 2027 need to draw from
        wrappers.
        """
        result = run(
            annuitant_plan(annuity_purchase_of(), spending="12000"),
            annuity_assumptions(),
            stub_region(),
            three_period_config(),
        )
        [at_purchase] = result.snapshots[1].persons
        assert at_purchase.annuity_income == Money(Decimal("1700.00"))
        assert at_purchase.net_withdrawn == Money(Decimal("5725.00"))
        assert at_purchase.shortfall == ZERO

    def test_purchase_resolves_before_an_up_front_crystallisation(self) -> None:
        """In the same period the annuity buys first, then PCLS fires.

        Born 1959, the age-67 purchase lands in the first period: half
        the pot annuitises (tax-free cash 5,000), then the up-front
        event crystallises the remaining 20,000 uncrystallised —
        another 5,000 tax-free with 15,000 designated to drawdown.
        """
        pension = wrapper_of(PENSION, "40000", crystallised="20000")
        plan = household_of(
            person_of(
                (pension,),
                date_of_birth=date(1959, 1, 1),
                retire_at=60,
                annuity_purchases=(annuity_purchase_of(),),
            )
        )
        config = three_period_config(
            tax_free_cash=TaxFreeCashStrategy.UP_FRONT_LUMP_SUM
        )
        result = run(plan, annuity_assumptions(), stub_region(), config)
        [first] = result.snapshots[0].persons
        assert first.annuity_lump_sum == Money(Decimal("5000.00"))
        assert first.pension_lump_sum == Money(Decimal("5000.00"))
        assert first.annuity_income == Money(Decimal("2500.00"))
        assert first.lsa_used == Money(Decimal("10000.00"))
        [pension_result] = first.wrappers
        assert pension_result.closing_uncrystallised == ZERO
        assert pension_result.closing_crystallised == Money(Decimal("25000.00"))

    def test_crystallised_funds_annuitise_before_the_access_age(self) -> None:
        """Drawdown funds are already accessed, so no gate binds them.

        A 36-year-old annuitises half of 10,000 of pre-existing
        drawdown funds at 40 — priced from a table with a 40 knot at
        the 8% base: 400 a year from 2030.
        """
        pension = wrapper_of(PENSION, "0", crystallised="10000")
        plan = household_of(
            person_of(
                (pension,),
                annuity_purchases=(annuity_purchase_of(at_age=40, fraction="0.5"),),
            )
        )
        table = dict(ANNUITY_TABLE)
        table["level"] = {"40": Decimal("1.0")}
        config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2030, 12, 31))
        result = run(
            plan,
            annuity_assumptions({"annuity.age_adjustment": table}),
            stub_region(),
            config,
        )
        [at_purchase] = result.snapshots[4].persons
        assert at_purchase.annuity_income == Money(Decimal("400.00"))
        [pension_result] = at_purchase.wrappers
        assert pension_result.closing_crystallised == Money(Decimal("5000.00"))

    def test_a_purchase_past_the_horizon_never_fires(self) -> None:
        """An age attained after the horizon end is simply not modelled."""
        result = run(
            annuitant_plan(annuity_purchase_of()),
            annuity_assumptions(),
            stub_region(),
            one_period_config(),
        )
        [person_result] = result.snapshots[0].persons
        assert person_result.annuity_income == ZERO
        [pension_result] = person_result.wrappers
        assert pension_result.closing_uncrystallised == Money(Decimal("40000.00"))
        assert pension_result.closing_crystallised == Money(Decimal("20000.00"))

    def test_purchase_decisions_enter_provenance(self) -> None:
        """The at-age and fraction decisions appear with stable labels."""
        result = run(
            annuitant_plan(annuity_purchase_of()),
            annuity_assumptions(),
            stub_region(),
            three_period_config(),
        )
        labels = {entry.label for entry in result.provenance.decisions}
        assert "annuity_purchase[annuity-1].at_age" in labels
        assert "annuity_purchase[annuity-1].fraction_of_pot" in labels
        keys = {entry.key for entry in result.provenance.assumptions}
        assert AssumptionKey.ANNUITY_LEVEL_SINGLE_65 in keys
        assert AssumptionKey.ANNUITY_AGE_ADJUSTMENT in keys

    def test_a_purchase_age_already_attained_is_rejected(self) -> None:
        """A past purchase cannot be priced from a modelled pot."""
        plan = annuitant_plan(annuity_purchase_of(at_age=65))
        assumptions = annuity_assumptions()
        region = stub_region()
        config = three_period_config()
        with pytest.raises(EngineError, match="attained before today"):
            run(plan, assumptions, region, config)

    def test_crystallising_a_gated_pot_is_rejected(self) -> None:
        """Uncrystallised funds cannot annuitise before access opens."""
        pension = wrapper_of(PENSION, "10000")
        table = dict(ANNUITY_TABLE)
        table["level"] = {"40": Decimal("1.0")}
        plan = household_of(
            person_of(
                (pension,),
                annuity_purchases=(annuity_purchase_of(at_age=40),),
            )
        )
        assumptions = annuity_assumptions({"annuity.age_adjustment": table})
        region = stub_region()
        config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2030, 12, 31))
        with pytest.raises(EngineError, match="access gate"):
            run(plan, assumptions, region, config)

    def test_a_purchase_age_outside_the_rate_table_is_rejected(self) -> None:
        """No extrapolation: an uncovered age fails loudly (§5.3)."""
        plan = annuitant_plan(annuity_purchase_of(at_age=80, fraction="0.5"))
        assumptions = annuity_assumptions()
        region = stub_region()
        config = RunConfig(today=date(2026, 1, 1), horizon_end=date(2040, 12, 31))
        with pytest.raises(EngineError, match="covers ages"):
            run(plan, assumptions, region, config)
