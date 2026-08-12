"""Shared test-data factories and engine stubs.

One canonical home for the helpers that were previously copy-pasted
per test module: the ``money_fact`` factory and the minimal stub
region (one tax-free wrapper kind under zero tax, flat ages, no state
pension) that the core-engine suites drive ``run``-family functions
through, so every deterministic expectation stays hand-computable.

Modules whose plans are dated differently rebind the factory with
``functools.partial`` (e.g. ``money_fact = partial(factories.money_fact,
as_of=AS_OF)``) rather than redefining it.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from glidepath.core import (
    AnnualAllowanceFunding,
    AnnualAllowanceMeasurement,
    AnnualAllowanceOutcome,
    AnnualCalendar,
    ContributionTaxTreatment,
    ContributionTerms,
    Fact,
    GrowthTaxTreatment,
    HouseholdAssessment,
    MemberContributionOutcome,
    MemberContributionRequest,
    Money,
    Period,
    Region,
    ReliefMechanic,
    SchemeInput,
    StatePensionEntitlement,
    StatePensionRecord,
    TaxInput,
    TaxLine,
    TaxResult,
    WithdrawalTaxTreatment,
    WrapperKindId,
    WrapperTaxTreatment,
    date_age_attained,
    is_age_attained_by_period_start,
)

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
"""The provenance timestamp shared facts carry."""

AS_OF = date(2026, 8, 1)
"""The default statement date for shared facts."""

_ZERO = Money(Decimal(0))


def money_fact(
    amount: str | Decimal,
    *,
    as_of: date = AS_OF,
    recorded_on: datetime = RECORDED,
) -> Fact[Money]:
    """A user-stated monetary fact."""
    value = amount if isinstance(amount, Decimal) else Decimal(amount)
    return Fact(value=Money(value), as_of=as_of, recorded_on=recorded_on)


class NoHouseholdAdjustment:
    """TaxSystem mixin: a region with no joint reliefs (planning §4.11).

    The core protocol's ``adjust_household`` contract for such a
    region: every assessment passes through unchanged.
    """

    def adjust_household(
        self, period: Period, assessments: tuple[HouseholdAssessment, ...]
    ) -> tuple[TaxResult, ...]:
        """Return the results unchanged."""
        del period
        return tuple(entry.result for entry in assessments)


@dataclass(frozen=True)
class ZeroTaxSystem(NoHouseholdAdjustment):
    """No tax on anything — expectations stay hand-computable."""

    def assess(self, period: Period, tax_input: TaxInput) -> TaxResult:
        """Zero tax on the assessed income."""
        del period
        return TaxResult(
            tax_due=_ZERO,
            taxable_income=tax_input.non_savings_income,
            tax_free_allowance=_ZERO,
            lines=(),
        )

    def annual_allowance_charge(
        self, period: Period, tax_input: TaxInput, excess: Money
    ) -> tuple[TaxLine, ...]:
        """No annual-allowance charge in this region."""
        del period, tax_input, excess
        return ()


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
            provider_relief=_ZERO,
            taxable_pay_deduction=_ZERO,
            assessment_relief_gross=_ZERO,
            unrelieved_excess=_ZERO,
        )

    def annual_allowance(
        self, measurement: AnnualAllowanceMeasurement, period: Period
    ) -> AnnualAllowanceOutcome:
        """No allowance machinery: zero excess, empty pool."""
        del measurement, period
        return AnnualAllowanceOutcome(chargeable_excess=_ZERO, carry_forward=())

    def annual_allowance_funding(
        self, charge: Money, schemes: tuple[SchemeInput, ...], period: Period
    ) -> AnnualAllowanceFunding:
        """No scheme-funded route: the whole charge falls to cash."""
        del schemes, period
        return AnnualAllowanceFunding(scheme_payments=(), cash=charge)


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
            annual_amount=_ZERO,
            cpi_uprated_annual_amount=_ZERO,
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
