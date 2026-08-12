"""UK income tax assessment (roadmap 2.3, 9.2; planning §4.2, §5.3, §6).

Implements the core :class:`~glidepath.core.TaxSystem` protocol. Every
figure — personal allowance, taper threshold and rate, the band
ladders, the savings and dividend nil rates — comes from the tax-year
data files (§5.3); nothing is hardcoded here (guard-tested).

Income stacks in HMRC's default order — non-savings, then savings,
then dividends — up one ladder of band widths (planning §6).
Non-savings income is assessed under the rUK or Scottish schedule per
the taxpayer's residency (roadmap 9.1); savings and dividend income is
UK-wide and always uses the rUK bands, positioned above the taxpayer's
non-savings taxable income (roadmap 9.2). The savings layer applies
the starting rate for savings (reduced £1 per £1 of non-savings
taxable income) and then the personal savings allowance; the dividend
layer applies the dividend allowance and then the per-band dividend
rates. All three reliefs are nil *rates*, not deductions: nil-rated
income still consumes band width (§6). The PSA tier follows the band
the taxpayer's total taxable income reaches on the (relief-extended)
rUK ladder.

Rounding follows HMRC's published calculation logic (the Tax Logic
service guide,
https://developer.service.hmrc.gov.uk/guides/tax-logic-service-guide/):
the personal-allowance reduction is rounded *down* to the whole pound
and the resulting allowance *up* to the whole pound, and each band's
tax is rounded *down* to the penny. These statutory roundings are the
region's own — the core ledger policy (half-even at ledger writes,
planning §4.6) is unchanged elsewhere.

Relief-at-source pension contributions (roadmap 3.2) receive their
higher and additional rates of relief here, by HMRC's own mechanism:
the basic rate limit and every rate limit above it are extended by the
gross contribution (limits below basic — the Scottish starter rate —
never move), and adjusted net income — the personal-allowance taper
measure — deducts it. The extension applies to the rUK ladder wherever
it is used, so savings and dividend band positions move with it too
(SI 2018/459 extends the limits for Scottish taxpayers likewise).
Net-pay contributions need no assessment adjustment: they leave pay
before tax, so the caller excludes them from the assessed income.

The annual-allowance charge (roadmap 3.3) prices separately from the
assessment: the chargeable excess a period's pension inputs produced
(:meth:`~glidepath.regions.uk.contributions.UkContributionRuleset.annual_allowance`)
is charged as the top slice of the taxpayer's income at their own
schedule's rates (FA 2004 s227B) via
:meth:`UkTaxSystem.annual_allowance_charge`, whose lines the engine
appends to the period's final result — never fed back through
``assess``, since the excess is a charge, not income.

The marriage allowance (roadmap 9.32) is the §4.11 household
adjustment step: ``assess`` stays strictly per-person, and
:meth:`UkTaxSystem.adjust_household` — called by the engine after
both persons' final assessments — applies the ITA 2007 s55B tax
reducer to the eligible direction's recipient. Because the reducer is
flat and capped it has no marginal rate, so the withdrawal gross-up
and income-offset pricing (differences of ``assess`` results) are
unaffected; the reduction lands in the reported assessment only — a
recorded §4.11 simplification.
"""

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import TYPE_CHECKING

from glidepath.core import (
    HouseholdAssessment,
    Money,
    Rate,
    TaxInput,
    TaxLine,
    TaxResidencyId,
    TaxResult,
)
from glidepath.regions.uk.loader import available_tax_years, load_tax_year
from glidepath.regions.uk.schema import BASIC_BAND_NAME, TaxBand
from glidepath.regions.uk.years import TaxYearSeries, UkTaxYearError

if TYPE_CHECKING:
    from collections.abc import Callable

    from glidepath.core import Period
    from glidepath.regions.uk.extension import FutureYearsExtension
    from glidepath.regions.uk.schema import (
        DividendRules,
        IncomeTaxSchedule,
        SavingsRules,
        TaxYearFile,
    )

RUK_RESIDENCY = TaxResidencyId("uk.ruk")
"""Residency id for England, Wales and Northern Ireland (rUK)."""

SCOTLAND_RESIDENCY = TaxResidencyId("uk.scotland")
"""Residency id for Scottish taxpayers."""

SAVINGS_STARTING_RATE_BAND = "savings_starting_rate"
"""Line label of the 0% starting rate for savings (planning §6)."""

SAVINGS_NIL_RATE_BAND = "savings_nil_rate"
"""Line label of the personal savings allowance nil rate (planning §6)."""

DIVIDEND_NIL_RATE_BAND = "dividend_nil_rate"
"""Line label of the dividend allowance nil rate (planning §6)."""

MARRIAGE_ALLOWANCE_BAND = "marriage_allowance"
"""Line label of the s55B marriage allowance tax reducer (planning §4.11)."""

_ZERO = Money(Decimal(0))
_POUND = Decimal(1)
_PENNY = Decimal("0.01")
_NIL_RATE = Rate(Decimal(0))
_AA_CHARGE_PREFIX = "aa_charge_"
_COUPLE = 2


def _round_down(money: Money, unit: Decimal) -> Money:
    """HMRC ``roundDown``: truncate a non-negative amount to ``unit``."""
    return Money(money.amount.quantize(unit, rounding=ROUND_DOWN))


def _round_up(money: Money, unit: Decimal) -> Money:
    """HMRC ``roundUp``: round a non-negative amount up to ``unit``."""
    return Money(money.amount.quantize(unit, rounding=ROUND_UP))


class UkTaxError(ValueError):
    """An assessment the shipped UK data cannot perform."""


@dataclass(frozen=True, slots=True)
class UkTaxSystem:
    """UK implementation of the core ``TaxSystem`` protocol.

    Holds the tax-year files it may assess against. Periods past the
    last shipped year are synthesized per the future-years extension
    when one is configured (roadmap 2.5; planning §5.3); shipped data
    always beats extrapolation. Without an extension — and always for
    periods before the first shipped year — assessment fails.
    """

    tax_years: tuple[TaxYearFile, ...]
    future_years: FutureYearsExtension | None = None

    def __post_init__(self) -> None:
        """Require at least one year, ascending and non-overlapping."""
        try:
            self._series()
        except UkTaxYearError as exc:
            raise UkTaxError(str(exc)) from exc

    @classmethod
    def from_shipped_data(
        cls, future_years: FutureYearsExtension | None = None
    ) -> UkTaxSystem:
        """Build a system over every shipped ``tax_year_*.toml``."""
        years = available_tax_years()
        return cls(
            tax_years=tuple(load_tax_year(year) for year in years),
            future_years=future_years,
        )

    def assess(self, period: Period, tax_input: TaxInput) -> TaxResult:
        """Assess one period's categorised income (planning §4.2)."""
        return _assess_year(self._tax_year_for(period), tax_input)

    def annual_allowance_charge(
        self, period: Period, tax_input: TaxInput, excess: Money
    ) -> tuple[TaxLine, ...]:
        """Price the annual-allowance charge on a pension-input excess.

        FA 2004 s227B: the chargeable amount is treated as the top
        slice of the individual's reduced net income and charged at
        the income-tax rates it falls into — a freestanding charge,
        not income, so the personal allowance and its taper never
        move. The lines stack from the assessment's total taxable
        income (the s23 step-3 position) up the taxpayer's own
        schedule — Scottish rates for Scottish taxpayers — with the
        rate limits extended by any relief-at-source gross exactly as
        the assessment extends them (FA 2004 s192(4) applies for all
        income-tax purposes). Known simplification: savings and
        dividend income consume width on the taxpayer's schedule here
        even though the assessment positions those layers on the rUK
        ladder — the divergence only affects Scottish taxpayers with
        portfolio income, and only the charge's band boundaries. Each
        line's tax is rounded down to the penny like every band line
        (module docstring).
        """
        if excess <= _ZERO:
            return ()
        year = self._tax_year_for(period)
        position = _assess_year(year, tax_input).taxable_income
        bands = _extended_bands(
            _schedule_for(year, tax_input.residency).bands,
            tax_input.relief_at_source_contributions,
        )
        return _ladder_lines(
            bands,
            start=position,
            amount=excess,
            line_name=_aa_charge_band_name,
            line_rate=_band_own_rate,
        )

    def adjust_household(
        self, period: Period, assessments: tuple[HouseholdAssessment, ...]
    ) -> tuple[TaxResult, ...]:
        """Apply the marriage allowance across a couple (planning §4.11).

        The engine calls this after both strictly per-person
        assessments, only when the household holds two persons and has
        not declined the claim; the two persons are assumed to qualify
        as spouses/civil partners (§4.11 scope). Each direction is
        tested against the year's eligibility rules — transferor's
        income within their personal allowance, recipient liable at no
        rate above the data file's band gates — and at most one
        direction can yield a reduction, so the first that does wins.
        The recipient's result gains the ITA 2007 s55B tax reducer as
        a negative no-income line: the rUK basic-rate percentage of
        the transferable amount, capped at their income-tax liability,
        never refundable, never a PA transfer in computation.
        """
        results = tuple(entry.result for entry in assessments)
        if len(assessments) != _COUPLE:
            return results
        year = self._tax_year_for(period)
        for transferor, recipient in ((0, 1), (1, 0)):
            adjusted = _marriage_allowance_adjusted(
                year, assessments[transferor], assessments[recipient]
            )
            if adjusted is not None:
                return tuple(
                    adjusted if index == recipient else result
                    for index, result in enumerate(results)
                )
        return results

    def _series(self) -> TaxYearSeries:
        """The shared year-resolution series over this system's files."""
        return TaxYearSeries(tax_years=self.tax_years, future_years=self.future_years)

    def _tax_year_for(self, period: Period) -> TaxYearFile:
        """The shipped or synthesized file fully containing ``period``."""
        try:
            return self._series().year_for(period)
        except UkTaxYearError as exc:
            raise UkTaxError(str(exc)) from exc


def _schedule_for(year: TaxYearFile, residency: TaxResidencyId) -> IncomeTaxSchedule:
    """The income-tax schedule for ``residency`` in ``year``."""
    if residency == RUK_RESIDENCY:
        return year.income_tax_ruk
    if residency == SCOTLAND_RESIDENCY:
        return year.income_tax_scotland
    msg = f"unknown UK tax residency {residency!r}"
    raise UkTaxError(msg)


def _ruk_basic_rate(year: TaxYearFile) -> Rate:
    """The rUK basic rate — the s55B 'appropriate percentage'.

    The marriage allowance reducer is a reserved UK-wide relief given
    at the basic rate for Scottish recipients too (ITA 2007 s55B; the
    Scottish rates apply only to non-savings income under s6A).
    """
    return next(
        band.rate for band in year.income_tax_ruk.bands if band.name == BASIC_BAND_NAME
    )


def _permitted_recipient_bands(
    year: TaxYearFile, residency: TaxResidencyId
) -> frozenset[str]:
    """The line labels a marriage-allowance recipient may be liable at.

    ITA 2007 s55B(2)(b) lists the qualifying rates; the data file
    names each schedule's highest qualifying band and every band at or
    below it is permitted. Savings and dividend layers stack on the
    rUK ladder whatever the residency, so their permitted labels
    follow the rUK gate — the nil-rate labels (starting rate, PSA,
    dividend allowance) are qualifying rates in their own right.
    """
    gate = (
        year.marriage_allowance.recipient_top_band_scotland
        if residency == SCOTLAND_RESIDENCY
        else year.marriage_allowance.recipient_top_band_ruk
    )
    permitted = {
        SAVINGS_STARTING_RATE_BAND,
        SAVINGS_NIL_RATE_BAND,
        DIVIDEND_NIL_RATE_BAND,
    }
    for band in _schedule_for(year, residency).bands:
        permitted.add(band.name)
        if band.name == gate:
            break
    ruk_gate = year.marriage_allowance.recipient_top_band_ruk
    for index, band in enumerate(year.income_tax_ruk.bands):
        permitted.add(_savings_band_name(index, band))
        permitted.add(f"dividend_{year.dividend.rates[index].name}")
        if band.name == ruk_gate:
            break
    return frozenset(permitted)


def _marriage_allowance_adjusted(
    year: TaxYearFile,
    transferor: HouseholdAssessment,
    recipient: HouseholdAssessment,
) -> TaxResult | None:
    """The recipient's result with the s55B reducer, or ``None``.

    Eligibility (planning §6 "Couples"): the transferor's income sits
    within their personal allowance (zero taxable income — the PA is
    never actually transferred in computation, a recorded
    simplification when their income falls inside the transferable
    band), and the recipient is liable at no rate above the data
    file's band gates. Annual-allowance-charge lines are ignored on
    both sides of the test and the cap: the s55B reducer lands at step
    6 of the ITA 2007 s23 calculation, before the step-7 charge.
    """
    if transferor.result.taxable_income > _ZERO:
        return None
    permitted = _permitted_recipient_bands(year, recipient.tax_input.residency)
    lines = recipient.result.lines
    income_tax_lines = [
        line for line in lines if not line.band.startswith(_AA_CHARGE_PREFIX)
    ]
    if any(line.band not in permitted for line in income_tax_lines):
        return None
    rate = _ruk_basic_rate(year)
    reducer = _round_down(rate.of(year.marriage_allowance.transferable_amount), _PENNY)
    liability = sum((line.tax for line in income_tax_lines), start=_ZERO)
    reduction = min(reducer, liability)
    if reduction <= _ZERO:
        return None
    result = recipient.result
    reducer_line = TaxLine(
        band=MARRIAGE_ALLOWANCE_BAND, rate=rate, taxed=_ZERO, tax=-reduction
    )
    return TaxResult(
        tax_due=result.tax_due - reduction,
        taxable_income=result.taxable_income,
        tax_free_allowance=result.tax_free_allowance,
        lines=(*lines, reducer_line),
    )


def _tapered_allowance(
    schedule: IncomeTaxSchedule, adjusted_net_income: Money
) -> Money:
    """The personal allowance after the taper (planning §6).

    Per HMRC's calculation, the reduction (``pa_taper_rate`` of adjusted
    net income above ``pa_taper_threshold``) is rounded down to the
    whole pound and the resulting allowance rounded up to the whole
    pound, so the allowance steps down £1 per full £2 of excess with the
    shipped rate, floored at zero.
    """
    excess = adjusted_net_income - schedule.pa_taper_threshold
    if excess <= _ZERO:
        return schedule.personal_allowance
    reduction = _round_down(schedule.pa_taper_rate.of(excess), _POUND)
    allowance = _round_up(schedule.personal_allowance - reduction, _POUND)
    return max(allowance, _ZERO)


def _ladder_lines(
    bands: tuple[TaxBand, ...],
    *,
    start: Money,
    amount: Money,
    line_name: Callable[[int, TaxBand], str],
    line_rate: Callable[[int, TaxBand], Rate],
) -> tuple[TaxLine, ...]:
    """Charge ``amount`` of income stacked from ``start`` up the ladder.

    Band uppers are cumulative taxable income above the allowance
    (§5.3): the slice of each band between ``start`` and
    ``start + amount`` is charged at the band's line rate — the band's
    own for non-savings and savings layers, the aligned dividend rate
    for dividends — each rounded down to the penny per HMRC's
    calculation. Bands with nothing in them are omitted.
    """
    lines: list[TaxLine] = []
    end = start + amount
    lower = _ZERO
    for index, band in enumerate(bands):
        ceiling = end if band.upper is None else min(band.upper, end)
        floor = max(lower, start)
        in_band = ceiling - floor
        if in_band > _ZERO:
            rate = line_rate(index, band)
            lines.append(
                TaxLine(
                    band=line_name(index, band),
                    rate=rate,
                    taxed=in_band,
                    tax=_round_down(rate.of(in_band), _PENNY),
                )
            )
        if band.upper is None or end <= band.upper:
            break
        lower = band.upper
    return tuple(lines)


def _extended_bands(
    bands: tuple[TaxBand, ...], extension: Money
) -> tuple[TaxBand, ...]:
    """Extend the basic and later band thresholds by a relief-at-source gross.

    HMRC's relief-at-source mechanism: the basic rate limit and every
    rate limit above it grow by the gross contribution, so more income
    is taxed at the lower rates. Limits below the basic band — the
    Scottish starter rate — never move (FA 2004 s192; SI 2018/459 for
    Scottish taxpayers), and the unbounded top band needs no move.
    """
    if extension <= _ZERO:
        return bands
    basic_index = next(
        index for index, band in enumerate(bands) if band.name == BASIC_BAND_NAME
    )
    return tuple(
        TaxBand(name=band.name, rate=band.rate, upper=band.upper + extension)
        if index >= basic_index and band.upper is not None
        else band
        for index, band in enumerate(bands)
    )


def _band_own_name(_index: int, band: TaxBand) -> str:
    """A band's own name — the non-savings line label."""
    return band.name


def _band_own_rate(_index: int, band: TaxBand) -> Rate:
    """A band's own rate — the non-savings and savings line rate."""
    return band.rate


def _savings_band_name(_index: int, band: TaxBand) -> str:
    """A savings line label: the band name under the layer prefix."""
    return f"savings_{band.name}"


def _aa_charge_band_name(_index: int, band: TaxBand) -> str:
    """An annual-allowance-charge line label under the layer prefix."""
    return f"{_AA_CHARGE_PREFIX}{band.name}"


def _nil_line(name: str, taxed: Money) -> TaxLine:
    """A zero-tax line that still consumes band width (planning §6)."""
    return TaxLine(band=name, rate=_NIL_RATE, taxed=taxed, tax=_ZERO)


def _psa_amount(
    savings: SavingsRules, bands: tuple[TaxBand, ...], total_taxable: Money
) -> Money:
    """The personal savings allowance tier (planning §6).

    The tier follows the band ``total_taxable`` reaches on the
    (relief-extended) rUK ladder: within the basic band's limit the
    basic tier applies, in the unbounded top band the additional tier,
    and the higher tier between.
    """
    basic_upper = next(band.upper for band in bands if band.name == BASIC_BAND_NAME)
    if basic_upper is None or total_taxable <= basic_upper:
        return savings.psa_basic
    top_floor = bands[-2].upper if len(bands) > 1 else None
    if top_floor is not None and total_taxable > top_floor:
        return savings.psa_additional
    return savings.psa_higher


def _savings_lines(
    savings: SavingsRules,
    bands: tuple[TaxBand, ...],
    *,
    taxable_savings: Money,
    taxable_non_savings: Money,
    total_taxable: Money,
) -> tuple[Money, tuple[TaxLine, ...]]:
    """The savings layer: starting rate, PSA, then the band rates.

    The layer stacks directly above the non-savings taxable income on
    the rUK ladder. The starting rate for savings covers what remains
    of its limit after non-savings taxable income eats it £1 per £1
    (planning §6); the PSA nil rate follows; the remainder is charged
    at the rUK band rates (savings rates separate from the main rates
    ship as data from 2027/28, §6). Returns the ladder position after
    the layer plus the layer's lines.
    """
    lines: list[TaxLine] = []
    position = taxable_non_savings
    remaining = taxable_savings
    starting_headroom = max(savings.starting_rate_limit - taxable_non_savings, _ZERO)
    starting = min(remaining, starting_headroom)
    if starting > _ZERO:
        lines.append(_nil_line(SAVINGS_STARTING_RATE_BAND, starting))
        position = position + starting
        remaining = remaining - starting
    psa = min(remaining, _psa_amount(savings, bands, total_taxable))
    if psa > _ZERO:
        lines.append(_nil_line(SAVINGS_NIL_RATE_BAND, psa))
        position = position + psa
        remaining = remaining - psa
    if remaining > _ZERO:
        lines.extend(
            _ladder_lines(
                bands,
                start=position,
                amount=remaining,
                line_name=_savings_band_name,
                line_rate=_band_own_rate,
            )
        )
        position = position + remaining
    return position, tuple(lines)


def _dividend_lines(
    dividend: DividendRules,
    bands: tuple[TaxBand, ...],
    *,
    position: Money,
    taxable_dividends: Money,
) -> tuple[TaxLine, ...]:
    """The dividend layer: the allowance nil rate, then dividend rates.

    The dividend allowance is a nil rate consuming band width (planning
    §6); the remainder is charged at the dividend rates aligned
    positionally with the rUK bands (schema invariant).
    """
    lines: list[TaxLine] = []
    remaining = taxable_dividends
    nil = min(remaining, dividend.allowance)
    if nil > _ZERO:
        lines.append(_nil_line(DIVIDEND_NIL_RATE_BAND, nil))
        position = position + nil
        remaining = remaining - nil
    if remaining > _ZERO:
        lines.extend(
            _ladder_lines(
                bands,
                start=position,
                amount=remaining,
                line_name=lambda index, _band: f"dividend_{dividend.rates[index].name}",
                line_rate=lambda index, _band: dividend.rates[index].rate,
            )
        )
    return tuple(lines)


def _assess_year(year: TaxYearFile, tax_input: TaxInput) -> TaxResult:
    """Assess one period's categorised income (module docstring).

    The personal allowance is set against income in the stacking order
    — non-savings, savings, dividends (HMRC's default ordering) — and
    each category's taxable remainder is charged through its layer.
    Savings and dividends always stack on the rUK ladder, above the
    non-savings taxable income, whatever schedule taxed that income
    (planning §6).
    """
    schedule = _schedule_for(year, tax_input.residency)
    ras_gross = tax_input.relief_at_source_contributions
    non_savings = tax_input.non_savings_income
    savings = tax_input.savings_income
    dividends = tax_input.dividend_income
    adjusted_net_income = max(non_savings + savings + dividends - ras_gross, _ZERO)
    allowance = _tapered_allowance(schedule, adjusted_net_income)
    taxable_non_savings = max(non_savings - allowance, _ZERO)
    allowance_left = max(allowance - non_savings, _ZERO)
    taxable_savings = max(savings - allowance_left, _ZERO)
    allowance_left = max(allowance_left - savings, _ZERO)
    taxable_dividends = max(dividends - allowance_left, _ZERO)
    total_taxable = taxable_non_savings + taxable_savings + taxable_dividends
    lines = list(
        _ladder_lines(
            _extended_bands(schedule.bands, ras_gross),
            start=_ZERO,
            amount=taxable_non_savings,
            line_name=_band_own_name,
            line_rate=_band_own_rate,
        )
    )
    if taxable_savings > _ZERO or taxable_dividends > _ZERO:
        ruk_bands = _extended_bands(year.income_tax_ruk.bands, ras_gross)
        position, savings_lines = _savings_lines(
            year.savings,
            ruk_bands,
            taxable_savings=taxable_savings,
            taxable_non_savings=taxable_non_savings,
            total_taxable=total_taxable,
        )
        lines.extend(savings_lines)
        lines.extend(
            _dividend_lines(
                year.dividend,
                ruk_bands,
                position=position,
                taxable_dividends=taxable_dividends,
            )
        )
    tax_due = sum((line.tax for line in lines), start=_ZERO)
    return TaxResult(
        tax_due=tax_due,
        taxable_income=total_taxable,
        tax_free_allowance=allowance,
        lines=tuple(lines),
    )
