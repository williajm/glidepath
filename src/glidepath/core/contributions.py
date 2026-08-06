"""Contribution schedules and the relief-mechanics boundary (roadmap 3.2).

A :class:`ContributionSchedule` records what a person has *chosen* to pay
into one wrapper each year — the employee amount is a
:class:`~glidepath.core.provenance.Decision` (a scenario-overridable
choice, planning §4.3), the employer amount a
:class:`~glidepath.core.provenance.Fact` (employment terms). How tax
relief is delivered is the region's concern: the core defines the
mechanics vocabulary (:class:`~glidepath.core.wrappers.ReliefMechanic`)
and the outcome shape (:class:`MemberContributionOutcome`); a region's
:class:`ContributionRuleset` turns a gross contribution into cash flows
under its own relief rules and limits (planning §5.1).

Amounts are *gross* annual contributions — the amount intended to land
in the wrapper — so schedules are comparable across relief mechanics:
under relief at source the member pays less cash and the provider tops
the pot up to the gross amount, while under net pay the member's pay is
reduced by the full gross amount before tax.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from glidepath.core.money import Money

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core.entities import EntityId
    from glidepath.core.periods import Period
    from glidepath.core.provenance import AssumptionKey, Decision, Fact
    from glidepath.core.wrappers import ReliefMechanic

_ZERO = Money(Decimal(0))


@dataclass(frozen=True, slots=True)
class ContributionSchedule:
    """One wrapper's planned annual contributions (planning §5.1).

    ``employee_amount`` is the chosen *gross* annual contribution;
    ``employer_amount`` is the employer's annual contribution under the
    employment terms (pension wrappers only). ``relief_mechanic`` is
    ``None`` for wrapper kinds whose contributions attract no relief
    (the region's permitted-mechanics set is empty, e.g. an ISA).
    ``escalation`` names the assumption the engine grows the amounts by
    (e.g. the earnings-growth assumption); applying it is the engine
    step's job (roadmap 4.1).
    """

    employee_amount: Decision[Money]
    employer_amount: Fact[Money] | None = None
    relief_mechanic: ReliefMechanic | None = None
    escalation: AssumptionKey | None = None

    def __post_init__(self) -> None:
        """Reject negative contribution amounts."""
        if self.employee_amount.value < _ZERO:
            msg = "ContributionSchedule.employee_amount must be non-negative"
            raise ValueError(msg)
        if self.employer_amount is not None and self.employer_amount.value < _ZERO:
            msg = "ContributionSchedule.employer_amount must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class MemberContributionRequest:
    """One member contribution to resolve through a region's relief rules.

    ``gross`` is the intended gross contribution to one wrapper;
    ``relevant_earnings`` is the period's earned income the region's
    relief limit measures against; ``date_of_birth`` lets the region
    apply any relief age limits. Relief limits are per *person* per
    period, shared across every wrapper and mechanic — so
    ``already_relieved_gross`` must carry the gross member
    contributions relief has already been granted on this period
    (across all the person's wrappers); the region grants relief only
    on the remaining headroom. ``mechanic`` is ``None`` when the
    wrapper kind attracts no relief.
    """

    gross: Money
    relevant_earnings: Money
    date_of_birth: date
    mechanic: ReliefMechanic | None = None
    already_relieved_gross: Money = _ZERO

    def __post_init__(self) -> None:
        """Reject negative monetary inputs."""
        amounts = (self.gross, self.relevant_earnings, self.already_relieved_gross)
        if any(amount < _ZERO for amount in amounts):
            msg = "MemberContributionRequest amounts must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class MemberContributionOutcome:
    """One member contribution resolved through a region's relief rules.

    ``gross_to_pot`` is what lands in the wrapper;
    ``member_cash_cost`` is the cash the member pays (from taxed income
    under relief at source, from gross pay under net pay);
    ``provider_relief`` is the top-up the provider reclaims at source;
    ``taxable_pay_deduction`` is the pre-tax pay reduction (net pay);
    ``assessment_relief_gross`` is the gross amount the tax assessment
    must grant further relief on (feeds
    :attr:`~glidepath.core.tax.TaxInput.relief_at_source_contributions`);
    ``unrelieved_excess`` is intended gross beyond the region's relief
    limit — clipped and reported, never contributed or rerouted: a
    schedule states intent for one wrapper, and a member's forgone
    cash under a relief mechanic is not the gross amount (someone who
    wants taxable saving schedules a GIA contribution directly).

    The identity ``gross_to_pot == member_cash_cost + provider_relief``
    is enforced, so any outcome that exists is internally consistent.
    """

    gross_to_pot: Money
    member_cash_cost: Money
    provider_relief: Money
    taxable_pay_deduction: Money
    assessment_relief_gross: Money
    unrelieved_excess: Money

    def __post_init__(self) -> None:
        """Require non-negative amounts and the pot-cash-relief identity."""
        amounts = (
            self.gross_to_pot,
            self.member_cash_cost,
            self.provider_relief,
            self.taxable_pay_deduction,
            self.assessment_relief_gross,
            self.unrelieved_excess,
        )
        if any(amount < _ZERO for amount in amounts):
            msg = "MemberContributionOutcome amounts must be non-negative"
            raise ValueError(msg)
        if self.gross_to_pot != self.member_cash_cost + self.provider_relief:
            msg = (
                "MemberContributionOutcome.gross_to_pot must equal"
                " member_cash_cost + provider_relief"
            )
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class DbArrangementInput:
    """One DB arrangement's annual entitlement over a measured year.

    ``opening_annual`` is the accrued annual pension at the period's
    open (before the year's accrual credit); ``closing_annual`` is the
    entitlement at the period's close, the year's accrual and
    revaluation included. How the pair values into a pension input
    amount — valuation factor, inflation uplift, flooring — is wholly
    the region's concern (planning §4.2).
    """

    opening_annual: Money
    closing_annual: Money

    def __post_init__(self) -> None:
        """Reject negative entitlements."""
        if self.opening_annual < _ZERO or self.closing_annual < _ZERO:
            msg = "DbArrangementInput amounts must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class AnnualAllowanceMeasurement:
    """One person's pension inputs and income for one period (§5.2 step 5).

    The engine's region-agnostic record of everything a region needs
    to measure a year's pension savings against its cross-pension
    allowances (roadmap 3.3): ``member_money_purchase`` is the gross
    member contribution landed in pension wrappers (provider relief
    included), ``employer_money_purchase`` the employer contributions
    alongside them, and ``db_arrangements`` the DB entitlements the
    region values into pension input amounts. ``total_income`` is the
    period's taxable income *before* any member pension deduction;
    ``net_pay_contributions`` and ``relief_at_source_gross`` are the
    member amounts each mechanic relieved, for the region's income
    measures. ``cpi`` is the period's inflation rate (a region may
    uprate DB opening values by it). ``mpaa_triggered_on`` is the
    flexible-access trigger date *as it stood when the period's
    contributions were made* — inputs paid before an in-period trigger
    are measured pre-trigger (planning §5.2). ``scheme_member`` marks
    membership of at least one pension arrangement this year, and
    ``carry_forward`` is the unused-allowance pool prior years left,
    earliest first.
    """

    member_money_purchase: Money
    employer_money_purchase: Money
    db_arrangements: tuple[DbArrangementInput, ...]
    total_income: Money
    net_pay_contributions: Money
    relief_at_source_gross: Money
    cpi: Decimal
    mpaa_triggered_on: date | None
    scheme_member: bool
    carry_forward: tuple[Money, ...]

    def __post_init__(self) -> None:
        """Reject negative monetary inputs."""
        amounts = (
            self.member_money_purchase,
            self.employer_money_purchase,
            self.total_income,
            self.net_pay_contributions,
            self.relief_at_source_gross,
            *self.carry_forward,
        )
        if any(amount < _ZERO for amount in amounts):
            msg = "AnnualAllowanceMeasurement amounts must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class AnnualAllowanceOutcome:
    """A region's annual-allowance answer for one period (roadmap 3.3).

    ``chargeable_excess`` is the pension input beyond every allowance
    the region operates — taper, money-purchase cap and carry-forward
    already applied — for the tax assessment to charge at the region's
    rates (:meth:`~glidepath.core.tax.TaxSystem.annual_allowance_charge`);
    ``carry_forward`` is the pool rolled forward one year for the next
    period's measurement. A region without such machinery returns a
    zero excess and an empty pool.
    """

    chargeable_excess: Money
    carry_forward: tuple[Money, ...]

    def __post_init__(self) -> None:
        """Reject negative amounts."""
        if self.chargeable_excess < _ZERO or any(
            amount < _ZERO for amount in self.carry_forward
        ):
            msg = "AnnualAllowanceOutcome amounts must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class SchemeInput:
    """One pension wrapper's own input amount, for the funding split (#124).

    ``input_amount`` is the wrapper's own money-purchase pension input
    for the period — member gross (provider relief included) plus
    employer. DB streams are not schemes here: they have no modelled
    pot to debit, so a charge their input generates always takes the
    cash route (planning §5.2).
    """

    wrapper_id: EntityId
    input_amount: Money

    def __post_init__(self) -> None:
        """Reject a negative input amount."""
        if self.input_amount < _ZERO:
            msg = "SchemeInput.input_amount must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class SchemePayment:
    """One scheme-funded debit of a period's priced AA charge (#124)."""

    wrapper_id: EntityId
    amount: Money

    def __post_init__(self) -> None:
        """Reject a negative payment."""
        if self.amount < _ZERO:
            msg = "SchemePayment.amount must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class AnnualAllowanceFunding:
    """How a period's priced AA charge is funded (planning §5.2, #124).

    ``scheme_payments`` are debits against pension wrappers (the UK's
    Scheme Pays — a scheme-administrator payment, not a member
    withdrawal); ``cash`` falls to the person's bare taxable accounts
    at period close, alongside the portfolio-income tax charge. The
    split covers the whole charge; what a drained wrapper cannot fund
    joins the person's shortfall (planning §5.2).
    """

    scheme_payments: tuple[SchemePayment, ...]
    cash: Money

    def __post_init__(self) -> None:
        """Reject a negative cash share."""
        if self.cash < _ZERO:
            msg = "AnnualAllowanceFunding.cash must be non-negative"
            raise ValueError(msg)


class ContributionRuleset(Protocol):
    """Region-supplied contribution relief mechanics (planning §4.2).

    Resolves one member contribution for one period under the region's
    relief rules — gross-up at source, pre-tax deduction, and the
    region's member relief limits. Which mechanics a wrapper kind may
    operate is the wrapper ruleset's call
    (:meth:`~glidepath.core.wrappers.WrapperRuleset.permitted_relief_mechanics`).
    Cross-wrapper contribution measures — the pension annual allowance,
    its taper, and any money-purchase cap — are the per-period
    :meth:`annual_allowance` measurement (roadmap 3.3).
    """

    def member_contribution(
        self, request: MemberContributionRequest, period: Period
    ) -> MemberContributionOutcome:
        """Resolve one gross member contribution for ``period``."""
        ...

    def annual_allowance(
        self, measurement: AnnualAllowanceMeasurement, period: Period
    ) -> AnnualAllowanceOutcome:
        """Measure a period's pension inputs against the region's allowances."""
        ...

    def annual_allowance_funding(
        self, charge: Money, schemes: tuple[SchemeInput, ...], period: Period
    ) -> AnnualAllowanceFunding:
        """Split a period's priced AA charge between scheme pays and cash.

        ``charge`` is the priced charge the tax system appended to the
        period's assessment; ``schemes`` are the person's pension
        wrappers with their own input amounts. A region without
        scheme-funded payment returns the whole charge as cash
        (planning §5.2, #124).
        """
        ...
