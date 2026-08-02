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

Contributions beyond the relief limit are clipped and reported, not
contributed unrelieved (planning §5.1 keeps v1 wrappers relief-clean;
routing excess to a taxable wrapper is roadmap 9.2 territory).

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

from glidepath.core import MemberContributionOutcome, Money, ReliefMechanic
from glidepath.regions.uk.loader import available_tax_years, load_tax_year
from glidepath.regions.uk.years import TaxYearSeries, UkTaxYearError

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core import Period
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
        self,
        *,
        gross: Money,
        relevant_earnings: Money,
        mechanic: ReliefMechanic | None,
        period: Period,
    ) -> MemberContributionOutcome:
        """Resolve a gross member contribution for ``period`` (module doc).

        With no mechanic the contribution is plain post-tax cash (e.g.
        an ISA): no relief, no limit, nothing for the assessment.
        """
        _require_non_negative(gross, "gross")
        _require_non_negative(relevant_earnings, "relevant_earnings")
        if mechanic is None:
            return MemberContributionOutcome(
                gross_to_pot=gross,
                member_cash_cost=gross,
                provider_relief=_ZERO,
                taxable_pay_deduction=_ZERO,
                assessment_relief_gross=_ZERO,
                unrelieved_excess=_ZERO,
            )
        pension = self._year_for(period).pension
        if mechanic is ReliefMechanic.NET_PAY:
            relievable = min(gross, relevant_earnings)
            return MemberContributionOutcome(
                gross_to_pot=relievable,
                member_cash_cost=relievable,
                provider_relief=_ZERO,
                taxable_pay_deduction=relievable,
                assessment_relief_gross=_ZERO,
                unrelieved_excess=gross - relievable,
            )
        limit = max(relevant_earnings, pension.member_relief_basic_amount)
        relievable = min(gross, limit)
        relief = pension.relief_at_source_rate.of(relievable)
        return MemberContributionOutcome(
            gross_to_pot=relievable,
            member_cash_cost=relievable - relief,
            provider_relief=relief,
            taxable_pay_deduction=_ZERO,
            assessment_relief_gross=relievable,
            unrelieved_excess=gross - relievable,
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
    are deducted by definition). Salary-sacrifice add-backs are out of
    scope for v1 (planning §6 models the 2029 NICs change as data).
    """
    _require_non_negative(total_income, "total_income")
    _require_non_negative(net_pay_contributions, "net_pay_contributions")
    _require_non_negative(relief_at_source_gross, "relief_at_source_gross")
    remaining = total_income - net_pay_contributions - relief_at_source_gross
    return max(remaining, _ZERO)


def adjusted_income(*, total_income: Money, employer_contributions: Money) -> Money:
    """Adjusted income for the AA taper (planning §6).

    ``total_income`` is taxable income before any member pension
    deduction, so member net-pay amounts are already included (HMRC
    adds them back to net income); employer contributions are added.
    """
    _require_non_negative(total_income, "total_income")
    _require_non_negative(employer_contributions, "employer_contributions")
    return total_income + employer_contributions


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

    Active from the period containing the trigger date onward. Applying
    it to the whole trigger period is deliberately conservative at
    annual resolution (the §4.1 convention): the model never allows
    relief the person could not get in reality.
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
    member gross plus employer DC contributions; ``other_inputs`` is
    non-money-purchase input, i.e. DB accrual (zero until Phase 4/9.6).
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
