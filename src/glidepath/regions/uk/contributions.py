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
DB accrual (:func:`db_pension_input_amount`, roadmap 9.6: closing less
CPI-uprated opening value at the data file's ``db_valuation_factor``,
FA 2004 s234 and s235, floored at nil per PTM053301) — distinct from the
member relief limit. High
income tapers it: £1 less per £2 of adjusted income over the threshold
(reduction rounded down to the whole pound, PTM057100), floored. After
flexible access the MPAA caps money-purchase inputs, leaving the
*alternative* annual allowance — the (possibly tapered) AA minus the
MPAA — for other accrual; the chargeable excess is the greater of the
default and alternative computations (FA 2004 s227ZA).

**Carry-forward** (roadmap 9.5; rules verified 2026-08-04 against the
gov.uk guidance). Unused allowance from the previous
``pension.aa_carry_forward_years`` tax years (three, shipped as data)
raises the year's allowance before the excess is charged (FA 2004
s228A). :func:`apply_carry_forward` sets the pool against an assessed
excess, consuming years earliest-first and only to the extent that
reduces the charge; the MPAA is never topped up, so the money-purchase
excess is a floor no carry-forward can offset (HS345).
:func:`carry_forward_generated` is what a year adds to the pool — only
the unused *alternative* allowance once money-purchase inputs exceed
the MPAA (PTM056510), and nothing from a year without
registered-scheme membership.
:func:`roll_carry_forward` advances the pool one tax year, expiring the
oldest entry.

**Charge funding** (roadmap 9.21, #124; verified 2026-08-06 against
PTM056410). :meth:`UkContributionRuleset.annual_allowance_funding`
splits the priced charge between Scheme Pays — the whole charge debited
from the pension wrapper with the largest qualifying input when the
mandatory conditions hold (charge over ``scheme_pays_min_charge``,
that wrapper's own input over the standard AA; FA 2004 s237B) — and
cash from the person's taxable accounts otherwise (planning §5.2
records the whole-charge simplification).
"""

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING

from glidepath.core import (
    AnnualAllowanceFunding,
    AnnualAllowanceOutcome,
    MemberContributionOutcome,
    Money,
    ReliefMechanic,
    SchemePayment,
    date_age_attained,
)
from glidepath.regions.uk.loader import available_tax_years, load_tax_year
from glidepath.regions.uk.years import TaxYearSeries, UkTaxYearError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from glidepath.core import (
        AnnualAllowanceMeasurement,
        MemberContributionRequest,
        Period,
        SchemeInput,
    )
    from glidepath.regions.uk.extension import FutureYearsExtension
    from glidepath.regions.uk.schema import PensionRules, TaxYearFile

_ZERO = Money(Decimal(0))
_POUND = Decimal(1)
_ONE = Decimal(1)


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

    def annual_allowance(
        self, measurement: AnnualAllowanceMeasurement, period: Period
    ) -> AnnualAllowanceOutcome:
        """Measure a period's pension inputs against the UK allowances.

        The full module-docstring pipeline for one tax year: DB
        entitlements value into pension input amounts
        (:func:`db_pension_input_amount`), the taper measures resolve
        (:func:`threshold_income`, :func:`adjusted_income` — every DB
        input counts as employer-funded, since the model has no member
        DB contributions), the tapered allowance measures the inputs
        (:func:`assess_annual_allowance`), and prior years' unused
        allowance offsets the excess before this year's own unused
        allowance joins the rolled pool (:func:`apply_carry_forward`,
        :func:`roll_carry_forward`).

        The MPAA is active from the period containing the trigger date
        (:func:`is_mpaa_active`) — the measurement's trigger is the one
        standing when the period's contributions were made, so inputs
        paid before an in-period trigger are measured against the full
        allowance, matching the statute's pre/post-trigger split at
        the engine's own event order (HS345; planning §5.2).

        A pool longer than this year's statutory window — possible
        only if a data file ever shrinks ``aa_carry_forward_years``
        between years — keeps its most recent years, the oldest
        expiring, rather than failing the run.
        """
        pension = self._year_for(period).pension
        db_total = _ZERO
        for arrangement in measurement.db_arrangements:
            db_total = db_total + db_pension_input_amount(
                pension,
                opening_annual=arrangement.opening_annual,
                closing_annual=arrangement.closing_annual,
                cpi=measurement.cpi,
            )
        threshold = threshold_income(
            total_income=measurement.total_income,
            net_pay_contributions=measurement.net_pay_contributions,
            relief_at_source_gross=measurement.relief_at_source_gross,
        )
        adjusted = adjusted_income(
            total_income=measurement.total_income,
            employer_pension_inputs=measurement.employer_money_purchase + db_total,
        )
        allowance = tapered_annual_allowance(
            pension, threshold=threshold, adjusted=adjusted
        )
        assessment = assess_annual_allowance(
            pension,
            annual_allowance=allowance,
            money_purchase_inputs=(
                measurement.member_money_purchase + measurement.employer_money_purchase
            ),
            other_inputs=db_total,
            mpaa_active=is_mpaa_active(measurement.mpaa_triggered_on, period),
        )
        window = pension.aa_carry_forward_years
        pool = measurement.carry_forward[-window:] if window else ()
        set_off = apply_carry_forward(pension, assessment, pool)
        generated = carry_forward_generated(
            assessment, scheme_member=measurement.scheme_member
        )
        return AnnualAllowanceOutcome(
            chargeable_excess=set_off.chargeable_excess,
            carry_forward=roll_carry_forward(pension, set_off.remaining, generated),
        )

    def annual_allowance_funding(
        self, charge: Money, schemes: tuple[SchemeInput, ...], period: Period
    ) -> AnnualAllowanceFunding:
        """Split a priced AA charge per the Scheme Pays conditions (#124).

        Mandatory scheme pays (FA 2004 s237B; PTM056410) is modelled
        on its two conditions: the year's total charge exceeds
        ``scheme_pays_min_charge`` and the wrapper's own pension input
        amount exceeds the **standard** annual allowance — the s228
        amount, with the tapered allowance and MPAA ignored. The whole
        charge is then debited from the qualifying wrapper with the
        largest input (planning §5.2 records the whole-charge
        simplification: voluntary scheme pays covers the slice
        mandatory scheme pays strictly would not). Outside the
        conditions the charge falls to cash — the person's bare
        taxable accounts at period close.
        """
        _require_non_negative(charge, "charge")
        pension = self._year_for(period).pension
        if charge <= pension.scheme_pays_min_charge:
            return AnnualAllowanceFunding(scheme_payments=(), cash=charge)
        qualifying = [
            scheme
            for scheme in schemes
            if scheme.input_amount > pension.annual_allowance
        ]
        if not qualifying:
            return AnnualAllowanceFunding(scheme_payments=(), cash=charge)
        paying = max(qualifying, key=lambda scheme: scheme.input_amount.amount)
        return AnnualAllowanceFunding(
            scheme_payments=(
                SchemePayment(wrapper_id=paying.wrapper_id, amount=charge),
            ),
            cash=_ZERO,
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
    contributions (PTM057100) — the whole of
    :func:`db_pension_input_amount`, since the model has no member DB
    contributions (planning §5.1). The engine supplies both through
    each period's :meth:`UkContributionRuleset.annual_allowance`
    measurement (planning §5.2).
    """
    _require_non_negative(total_income, "total_income")
    _require_non_negative(employer_pension_inputs, "employer_pension_inputs")
    return total_income + employer_pension_inputs


def db_pension_input_amount(
    pension: PensionRules,
    *,
    opening_annual: Money,
    closing_annual: Money,
    cpi: Decimal,
) -> Money:
    """A DB arrangement's pension input amount for one tax year (§6).

    Closing value less opening value, each value the arrangement's
    annual pension times ``pension.db_valuation_factor`` (FA 2004
    s234; the model has no separate lump sum entitlement — commutation
    is not one, PTM053301). The opening value is uprated by ``cpi``
    (s235's "appropriate percentage" is the 12-month CPI increase to
    the September before the tax year; the run's CPI path stands in,
    planning §5.1/§6), never reduced by deflation, and a negative
    difference is nil (PTM053301) — so a deferred arrangement whose
    revaluation never outruns CPI generates nothing.
    :meth:`UkContributionRuleset.annual_allowance` calls this per
    arrangement each period (planning §5.2); amounts feed
    :func:`assess_annual_allowance` ``other_inputs`` and
    :func:`adjusted_income` ``employer_pension_inputs``.
    """
    _require_non_negative(opening_annual, "opening_annual")
    _require_non_negative(closing_annual, "closing_annual")
    factor = Decimal(pension.db_valuation_factor)
    uplift = _ONE + max(cpi, Decimal(0))
    opening_value = opening_annual * (factor * uplift)
    closing_value = closing_annual * factor
    return max(closing_value - opening_value, _ZERO)


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
    against the recorded pension input amounts. When the MPAA is
    active, ``alternative_annual_allowance`` — the allowance minus the
    MPAA — is what remains for non-money-purchase accrual, and
    ``money_purchase_excess`` is the money-purchase input over the
    MPAA. ``chargeable_excess`` is the amount subject to the AA charge
    (the greater of the default and alternative computations, FA 2004
    s227ZA) before any carry-forward; taxing it is a later concern.
    Carry-forward (:func:`apply_carry_forward`) can top up every
    allowance here except the MPAA itself, and :attr:`unused_allowance`
    is what the year offers future pools.
    """

    annual_allowance: Money
    money_purchase_inputs: Money
    other_inputs: Money
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

    @property
    def unused_allowance(self) -> Money:
        """The year's unused allowance for carry-forward purposes.

        In a year whose money-purchase inputs *exceed* the MPAA only
        the unused *alternative* allowance carries forward — unused
        MPAA headroom never does (HS345). Otherwise — including a
        flexibly-accessed year whose money-purchase inputs stay within
        the MPAA — the full allowance less total pension inputs
        carries (PTM056510). Whether the year in fact generates
        carry-forward also depends on scheme membership
        (:func:`carry_forward_generated`).
        """
        if (
            self.alternative_annual_allowance is not None
            and self.money_purchase_excess > _ZERO
        ):
            return max(self.alternative_annual_allowance - self.other_inputs, _ZERO)
        total = self.money_purchase_inputs + self.other_inputs
        return max(self.annual_allowance - total, _ZERO)


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
    DB pension input amounts (:func:`db_pension_input_amount`) and, in
    the MPAA trigger period, any money-purchase inputs made before the
    trigger (HS345;
    see :func:`is_mpaa_active`). Unused prior-year allowance is set
    against the assessed excess separately
    (:func:`apply_carry_forward`).
    """
    _require_non_negative(annual_allowance, "annual_allowance")
    _require_non_negative(money_purchase_inputs, "money_purchase_inputs")
    _require_non_negative(other_inputs, "other_inputs")
    total = money_purchase_inputs + other_inputs
    default_excess = max(total - annual_allowance, _ZERO)
    if not mpaa_active:
        return AnnualAllowanceAssessment(
            annual_allowance=annual_allowance,
            money_purchase_inputs=money_purchase_inputs,
            other_inputs=other_inputs,
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
        money_purchase_inputs=money_purchase_inputs,
        other_inputs=other_inputs,
        mpaa_active=True,
        money_purchase_excess=money_purchase_excess,
        alternative_annual_allowance=alternative_allowance,
        chargeable_excess=chargeable,
    )


@dataclass(frozen=True, slots=True)
class CarryForwardOutcome:
    """Carry-forward set against one year's annual-allowance excess.

    ``used`` and ``remaining`` align entry-by-entry with the supplied
    pool, earliest year first; ``chargeable_excess`` is the
    assessment's excess after the set-off.
    """

    chargeable_excess: Money
    used: tuple[Money, ...]
    remaining: tuple[Money, ...]


def _validated_pool(
    pension: PensionRules, pool: Sequence[Money], name: str
) -> tuple[Money, ...]:
    """Reject a pool longer than the statutory window or with negatives."""
    years = tuple(pool)
    if len(years) > pension.aa_carry_forward_years:
        msg = (
            f"{name} covers at most the previous"
            f" {pension.aa_carry_forward_years} tax years"
        )
        raise UkContributionError(msg)
    for amount in years:
        _require_non_negative(amount, name)
    return years


def _default_excess(assessment: AnnualAllowanceAssessment) -> Money:
    """The default computation's excess: total inputs over the allowance."""
    total = assessment.money_purchase_inputs + assessment.other_inputs
    return max(total - assessment.annual_allowance, _ZERO)


def _set_off_needed(assessment: AnnualAllowanceAssessment) -> Money:
    """The smallest set-off that minimises the chargeable excess.

    Without a money-purchase excess the whole excess is offsettable.
    With one, carry-forward tops up the allowance in both s227ZA
    computations but never the MPAA, so the set-off is worth applying
    only until the default computation falls to the money-purchase
    excess and the other inputs fit the topped-up alternative
    allowance — beyond that the charge cannot fall further.
    """
    alternative = assessment.alternative_annual_allowance
    if alternative is None or assessment.money_purchase_excess == _ZERO:
        return assessment.chargeable_excess
    other_needed = max(assessment.other_inputs - alternative, _ZERO)
    default_needed = max(
        _default_excess(assessment) - assessment.money_purchase_excess, _ZERO
    )
    return max(other_needed, default_needed)


def _chargeable_after_set_off(
    assessment: AnnualAllowanceAssessment, set_off: Money
) -> Money:
    """Re-run the s227ZA comparison with the allowances topped up."""
    default_after = max(_default_excess(assessment) - set_off, _ZERO)
    alternative = assessment.alternative_annual_allowance
    if alternative is None or assessment.money_purchase_excess == _ZERO:
        return default_after
    alternative_after = assessment.money_purchase_excess + max(
        assessment.other_inputs - alternative - set_off, _ZERO
    )
    return max(default_after, alternative_after)


def _consumed_earliest_first(
    years: tuple[Money, ...], needed: Money
) -> tuple[tuple[Money, ...], tuple[Money, ...], Money]:
    """Draw ``needed`` across ``years`` in order: used, remaining, total."""
    used: list[Money] = []
    left = needed
    for available in years:
        take = min(available, left)
        used.append(take)
        left = left - take
    remaining = tuple(
        available - taken for available, taken in zip(years, used, strict=True)
    )
    return tuple(used), remaining, needed - left


def apply_carry_forward(
    pension: PensionRules,
    assessment: AnnualAllowanceAssessment,
    carry_forward: Sequence[Money],
) -> CarryForwardOutcome:
    """Set unused prior-year allowance against an assessed excess.

    ``carry_forward`` holds the unused allowance of the previous tax
    years, earliest first — at most ``pension.aa_carry_forward_years``
    of them (the statutory window). Unused years are drawn in order of
    earliest to most recent and only to the extent that reduces the
    charge (gov.uk guidance, verified 2026-08-04), so what survives
    stays available — within its window — for later years
    (:func:`roll_carry_forward`). Carry-forward raises the annual
    allowance in both s227ZA computations but never the MPAA: the
    money-purchase excess is a floor the set-off cannot reach (HS345).
    """
    years = _validated_pool(pension, carry_forward, "carry_forward")
    needed = _set_off_needed(assessment)
    used, remaining, set_off = _consumed_earliest_first(years, needed)
    return CarryForwardOutcome(
        chargeable_excess=_chargeable_after_set_off(assessment, set_off),
        used=used,
        remaining=remaining,
    )


def carry_forward_generated(
    assessment: AnnualAllowanceAssessment, *, scheme_member: bool
) -> Money:
    """The unused allowance a year adds to the carry-forward pool.

    A tax year in which the person was not a member of at least one
    registered pension scheme (or qualifying overseas scheme)
    generates nothing, whatever allowance went unused (gov.uk
    guidance, verified 2026-08-04).
    """
    return assessment.unused_allowance if scheme_member else _ZERO


def roll_carry_forward(
    pension: PensionRules,
    remaining: Sequence[Money],
    generated: Money,
) -> tuple[Money, ...]:
    """Advance the carry-forward pool one tax year.

    ``remaining`` is what :func:`apply_carry_forward` left of the
    previous years' pool — earliest first, consecutive tax years
    ending with the immediately preceding one; ``generated`` is the
    assessed year's own contribution
    (:func:`carry_forward_generated`). The oldest entry expires as it
    leaves the ``aa_carry_forward_years`` window.
    """
    _require_non_negative(generated, "generated")
    years = _validated_pool(pension, remaining, "remaining")
    width = pension.aa_carry_forward_years
    if width == 0:
        return ()
    kept = years[max(len(years) - (width - 1), 0) :]
    return (*kept, generated)
