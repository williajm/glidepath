"""UK contribution relief mechanics and pension allowances (roadmap 3.2, 3.3).

Implements the core
:class:`~glidepath.core.ContributionRuleset` protocol plus the UK's
cross-pension contribution measures: the annual allowance and its taper,
and the money purchase annual allowance (MPAA). Every figure — the
relief-at-source rate, the member relief basic amount, the allowances
and taper parameters — comes from the tax-year data files (§5.3);
nothing is hardcoded here (guard-tested).

**Relief mechanics** (planning §5.1, §6). Member amounts are *gross*:

- Relief at source: the member pays the gross amount less basic-rate
  relief from taxed income and the provider reclaims the difference, so
  the pot receives the gross amount; higher and additional rates arrive
  via assessment (:class:`~glidepath.regions.uk.tax.UkTaxSystem` extends
  its band thresholds by the gross amount). Member relief is limited to
  100% of relevant UK earnings, or the basic amount for low/no earners —
  a floor available through relief at source only (FA 2004 s190).
- Net pay: the gross amount leaves pay before tax, so full marginal
  relief is immediate and no assessment adjustment applies. Relief
  cannot exceed pay, and the basic-amount floor does not apply.

The relief limit is a per-person, per-tax-year aggregate over every
scheme and mechanic (PTM044220), threaded through
``already_relieved_gross`` on the request; and contributions paid from
the member's ``member_relief_max_age`` birthday on are never
relievable, whatever the earnings (FA 2004 s188(3)(a), PTM044100).
Contributions beyond the relief limit are clipped and reported, not
contributed unrelieved (planning §5.1 keeps wrappers relief-clean;
they are never rerouted — a schedule states intent for one wrapper,
and taxable saving is scheduled on a GIA directly, roadmap 9.2).

**Annual allowance** (planning §5.2 step 3, §6). The AA measures total
*pension input amounts* — member gross plus employer contributions and
(from Phase 4) DB accrual — distinct from the member relief limit. High
income tapers it: £1 less per £2 of adjusted income over the threshold
(reduction rounded down to the whole pound, PTM057100), floored. After
flexible access the MPAA caps money-purchase inputs, leaving the
*alternative* annual allowance — the (possibly tapered) AA minus the
MPAA — for other accrual; the chargeable excess is the greater of the
default and alternative computations (FA 2004 s227ZA). Carry-forward of
unused allowance is roadmap 9.5.
"""

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING

from glidepath.core import (
    MemberContributionOutcome,
    Money,
    ReliefMechanic,
    date_age_attained,
)
from glidepath.regions.uk.loader import available_tax_years, load_tax_year
from glidepath.regions.uk.years import TaxYearSeries, UkTaxYearError

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core import MemberContributionRequest, Period
    from glidepath.regions.uk.extension import FutureYearsExtension
    from glidepath.regions.uk.schema import PensionRules, TaxYearFile

_ZERO = Money(Decimal(0))
_POUND = Decimal(1)


class UkContributionError(ValueError):
    """A contribution query the shipped UK data cannot answer."""


def _require_non_negative(amount: Money, name: str) -> None:
    """Reject a negative monetary input."""
    if amount < _ZERO:
        msg = f"{name} must be non-negative"
        raise UkContributionError(msg)


def _unrelieved(gross: Money) -> MemberContributionOutcome:
    """The whole contribution is clipped: no relief is available."""
    return MemberContributionOutcome(
        gross_to_pot=_ZERO,
        member_cash_cost=_ZERO,
        provider_relief=_ZERO,
        taxable_pay_deduction=_ZERO,
        assessment_relief_gross=_ZERO,
        unrelieved_excess=gross,
    )


def _headroom(request: MemberContributionRequest, *, cap: Money) -> Money:
    """The relievable part of the request under a per-person cap.

    The member relief limit is shared across every wrapper and
    mechanic, so relief already granted this period comes off the cap
    before this contribution is measured against it.
    """
    remaining = max(cap - request.already_relieved_gross, _ZERO)
    return min(request.gross, remaining)


@dataclass(frozen=True, slots=True)
class UkContributionRuleset:
    """UK implementation of the core ``ContributionRuleset`` protocol.

    Holds the tax-year files its figures come from, sharing the tax
    system's coverage semantics: shipped data beats extrapolation, and
    a query outside coverage fails loudly rather than answering from
    the wrong year.
    """

    tax_years: tuple[TaxYearFile, ...]
    future_years: FutureYearsExtension | None = None

    def __post_init__(self) -> None:
        """Require at least one year, ascending and non-overlapping."""
        try:
            self._series()
        except UkTaxYearError as exc:
            raise UkContributionError(str(exc)) from exc

    @classmethod
    def from_shipped_data(
        cls, future_years: FutureYearsExtension | None = None
    ) -> UkContributionRuleset:
        """Build a ruleset over every shipped data file."""
        years = available_tax_years()
        return cls(
            tax_years=tuple(load_tax_year(year) for year in years),
            future_years=future_years,
        )

    def member_contribution(
        self, request: MemberContributionRequest, period: Period
    ) -> MemberContributionOutcome:
        """Resolve one gross member contribution for ``period`` (module doc).

        With no mechanic the contribution is plain post-tax cash (e.g.
        an ISA): no relief, no limit, nothing for the assessment. The
        period's tax year is always resolved — even on that path — so a
        query outside data coverage fails loudly (class docstring).

        Relief shuts off from the period in which the member's
        ``member_relief_max_age`` birthday falls: contributions paid
        after that birthday are never relievable (FA 2004 s188(3)(a)),
        and at annual resolution the whole period is treated that way —
        conservative in the §4.1 sense, so the model never grants
        relief the person could not get in reality.
        """
        pension = self._year_for(period).pension
        if request.mechanic is None:
            return MemberContributionOutcome(
                gross_to_pot=request.gross,
                member_cash_cost=request.gross,
                provider_relief=_ZERO,
                taxable_pay_deduction=_ZERO,
                assessment_relief_gross=_ZERO,
                unrelieved_excess=_ZERO,
            )
        max_age_birthday = date_age_attained(
            request.date_of_birth, pension.member_relief_max_age
        )
        if max_age_birthday <= period.end:
            return _unrelieved(request.gross)
        if request.mechanic is ReliefMechanic.NET_PAY:
            relievable = _headroom(request, cap=request.relevant_earnings)
            return MemberContributionOutcome(
                gross_to_pot=relievable,
                member_cash_cost=relievable,
                provider_relief=_ZERO,
                taxable_pay_deduction=relievable,
                assessment_relief_gross=_ZERO,
                unrelieved_excess=request.gross - relievable,
            )
        limit = max(request.relevant_earnings, pension.member_relief_basic_amount)
        relievable = _headroom(request, cap=limit)
        relief = pension.relief_at_source_rate.of(relievable)
        return MemberContributionOutcome(
            gross_to_pot=relievable,
            member_cash_cost=relievable - relief,
            provider_relief=relief,
            taxable_pay_deduction=_ZERO,
            assessment_relief_gross=relievable,
            unrelieved_excess=request.gross - relievable,
        )

    def _series(self) -> TaxYearSeries:
        """The shared year-resolution series over this ruleset's files."""
        return TaxYearSeries(tax_years=self.tax_years, future_years=self.future_years)

    def _year_for(self, period: Period) -> TaxYearFile:
        """The shipped or synthesized file fully containing ``period``."""
        try:
            return self._series().year_for(period)
        except UkTaxYearError as exc:
            raise UkContributionError(str(exc)) from exc


def threshold_income(
    *,
    total_income: Money,
    net_pay_contributions: Money,
    relief_at_source_gross: Money,
) -> Money:
    """Threshold income for the AA taper (planning §6).

    ``total_income`` is taxable income before any member pension
    deduction; both member contribution routes come off it (net-pay
    amounts never reached taxable pay; relief-at-source gross amounts
    are deducted by definition).

    Known limitation: HMRC adds back employment income given up under
    salary-sacrifice arrangements made on or after 9 July 2015
    (PTM057100). v1 has no salary-sacrifice concept, so there is
    nothing to add back — a user who models a sacrifice arrangement as
    employer contributions will understate threshold income here.
    """
    _require_non_negative(total_income, "total_income")
    _require_non_negative(net_pay_contributions, "net_pay_contributions")
    _require_non_negative(relief_at_source_gross, "relief_at_source_gross")
    remaining = total_income - net_pay_contributions - relief_at_source_gross
    return max(remaining, _ZERO)


def adjusted_income(*, total_income: Money, employer_pension_inputs: Money) -> Money:
    """Adjusted income for the AA taper (planning §6).

    ``total_income`` is taxable income before any member pension
    deduction, so member net-pay amounts are already included (HMRC
    adds them back to net income). ``employer_pension_inputs`` is every
    employer-funded pension input: DC employer contributions plus, for
    DB arrangements, the pension input amount net of the member's own
    contributions (PTM057100) — from Phase 4 the DB accrual must be
    included here, not only in the AA measure itself.
    """
    _require_non_negative(total_income, "total_income")
    _require_non_negative(employer_pension_inputs, "employer_pension_inputs")
    return total_income + employer_pension_inputs


def tapered_annual_allowance(
    pension: PensionRules,
    *,
    threshold: Money,
    adjusted: Money,
) -> Money:
    """The year's annual allowance after the high-income taper (§6).

    No taper unless *both* incomes exceed their limits: threshold
    income over ``aa_taper_threshold_income`` and adjusted income over
    ``aa_taper_adjusted_income``. The reduction — ``aa_taper_rate`` of
    the adjusted-income excess, rounded down to the whole pound
    (PTM057100) — is floored at ``aa_taper_floor``.
    """
    _require_non_negative(threshold, "threshold")
    _require_non_negative(adjusted, "adjusted")
    if threshold <= pension.aa_taper_threshold_income:
        return pension.annual_allowance
    excess = adjusted - pension.aa_taper_adjusted_income
    if excess <= _ZERO:
        return pension.annual_allowance
    reduction = Money(
        pension.aa_taper_rate.of(excess).amount.quantize(_POUND, rounding=ROUND_DOWN)
    )
    return max(pension.annual_allowance - reduction, pension.aa_taper_floor)


def is_mpaa_active(triggered_on: date | None, period: Period) -> bool:
    """Whether the MPAA constrains money-purchase inputs in ``period``.

    Active from the period containing the trigger date onward. Within
    the trigger period itself, statute tests only money-purchase inputs
    made *after* the trigger against the MPAA, counting earlier ones on
    the other side of the comparison (HS345) — that split is the
    caller's job via :func:`assess_annual_allowance`'s inputs. For the
    v1 pre-plan trigger fact (planning §5.1) every projected period is
    wholly post-trigger, so no split arises.
    """
    return triggered_on is not None and triggered_on <= period.end


@dataclass(frozen=True, slots=True)
class AnnualAllowanceAssessment:
    """One person's annual-allowance position for a period (§5.2 step 3).

    ``annual_allowance`` is the (possibly tapered) allowance measured
    against total pension input amounts. When the MPAA is active,
    ``alternative_annual_allowance`` — the allowance minus the MPAA —
    is what remains for non-money-purchase accrual, and
    ``money_purchase_excess`` is the money-purchase input over the
    MPAA. ``chargeable_excess`` is the amount subject to the AA charge
    (the greater of the default and alternative computations, FA 2004
    s227ZA); taxing it is a later concern, as is carry-forward
    (roadmap 9.5), which can top up every allowance here except the
    MPAA itself.
    """

    annual_allowance: Money
    mpaa_active: bool
    money_purchase_excess: Money
    alternative_annual_allowance: Money | None
    chargeable_excess: Money

    def __post_init__(self) -> None:
        """Require the MPAA-dependent fields exactly when the MPAA is active."""
        if self.mpaa_active == (self.alternative_annual_allowance is None):
            msg = (
                "alternative_annual_allowance is required exactly when"
                " the MPAA is active"
            )
            raise UkContributionError(msg)


def assess_annual_allowance(
    pension: PensionRules,
    *,
    annual_allowance: Money,
    money_purchase_inputs: Money,
    other_inputs: Money,
    mpaa_active: bool,
) -> AnnualAllowanceAssessment:
    """Measure a period's pension input amounts against the allowances.

    ``annual_allowance`` is the year's allowance after any taper
    (:func:`tapered_annual_allowance`); ``money_purchase_inputs`` is
    member gross plus employer DC contributions *made while the MPAA
    applies*; ``other_inputs`` is everything else measured by the AA —
    DB accrual (zero until Phase 4/9.6) and, in the MPAA trigger
    period, any money-purchase inputs made before the trigger (HS345;
    see :func:`is_mpaa_active`).
    """
    _require_non_negative(annual_allowance, "annual_allowance")
    _require_non_negative(money_purchase_inputs, "money_purchase_inputs")
    _require_non_negative(other_inputs, "other_inputs")
    total = money_purchase_inputs + other_inputs
    default_excess = max(total - annual_allowance, _ZERO)
    if not mpaa_active:
        return AnnualAllowanceAssessment(
            annual_allowance=annual_allowance,
            mpaa_active=False,
            money_purchase_excess=_ZERO,
            alternative_annual_allowance=None,
            chargeable_excess=default_excess,
        )
    alternative_allowance = max(annual_allowance - pension.mpaa, _ZERO)
    money_purchase_excess = max(money_purchase_inputs - pension.mpaa, _ZERO)
    chargeable = default_excess
    if money_purchase_excess > _ZERO:
        alternative_excess = money_purchase_excess + max(
            other_inputs - alternative_allowance, _ZERO
        )
        chargeable = max(default_excess, alternative_excess)
    return AnnualAllowanceAssessment(
        annual_allowance=annual_allowance,
        mpaa_active=True,
        money_purchase_excess=money_purchase_excess,
        alternative_annual_allowance=alternative_allowance,
        chargeable_excess=chargeable,
    )
