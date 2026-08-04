"""UK income tax assessment (roadmap 2.3; planning §4.2, §5.3, §6).

Implements the core :class:`~glidepath.core.TaxSystem` protocol. Every
figure — personal allowance, taper threshold and rate, the band
ladder — comes from the tax-year data files (§5.3); nothing is hardcoded
here (guard-tested). Non-savings income is assessed under the rUK or
Scottish schedule per the taxpayer's residency (roadmap 9.1);
savings/dividend taxation needs the GIA wrapper (roadmap 9.2).

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
measure — deducts it. Net-pay contributions need no assessment
adjustment: they leave pay before tax, so the caller excludes them from
the assessed income.
"""

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import TYPE_CHECKING

from glidepath.core import Money, TaxInput, TaxLine, TaxResidencyId, TaxResult
from glidepath.regions.uk.loader import available_tax_years, load_tax_year
from glidepath.regions.uk.schema import BASIC_BAND_NAME, TaxBand
from glidepath.regions.uk.years import TaxYearSeries, UkTaxYearError

if TYPE_CHECKING:
    from glidepath.core import Period
    from glidepath.regions.uk.extension import FutureYearsExtension
    from glidepath.regions.uk.schema import IncomeTaxSchedule, TaxYearFile

RUK_RESIDENCY = TaxResidencyId("uk.ruk")
"""Residency id for England, Wales and Northern Ireland (rUK)."""

SCOTLAND_RESIDENCY = TaxResidencyId("uk.scotland")
"""Residency id for Scottish taxpayers."""

_ZERO = Money(Decimal(0))
_POUND = Decimal(1)
_PENNY = Decimal("0.01")


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
        """Assess one period's non-savings income (planning §4.2)."""
        year = self._tax_year_for(period)
        schedule = _schedule_for(year, tax_input.residency)
        return _assess_schedule(
            schedule,
            tax_input.non_savings_income,
            tax_input.relief_at_source_contributions,
        )

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


def _band_lines(bands: tuple[TaxBand, ...], taxable: Money) -> tuple[TaxLine, ...]:
    """Charge ``taxable`` income through the ascending band ladder.

    Band uppers are cumulative taxable income above the allowance
    (§5.3); each band's tax is rounded down to the penny per HMRC's
    calculation. Bands with nothing in them are omitted.
    """
    lines: list[TaxLine] = []
    lower = _ZERO
    for band in bands:
        ceiling = taxable if band.upper is None else min(taxable, band.upper)
        in_band = ceiling - lower
        if in_band <= _ZERO:
            break
        lines.append(
            TaxLine(
                band=band.name,
                rate=band.rate,
                taxed=in_band,
                tax=_round_down(band.rate.of(in_band), _PENNY),
            )
        )
        if band.upper is None or taxable <= band.upper:
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


def _assess_schedule(
    schedule: IncomeTaxSchedule, income: Money, ras_gross: Money
) -> TaxResult:
    """Assess non-savings ``income`` under one regime's schedule.

    ``ras_gross`` — the period's gross relief-at-source pension
    contributions — extends the bounded band thresholds and comes off
    adjusted net income for the personal-allowance taper (module
    docstring).
    """
    adjusted_net_income = max(income - ras_gross, _ZERO)
    allowance = _tapered_allowance(schedule, adjusted_net_income)
    taxable = max(income - allowance, _ZERO)
    lines = _band_lines(_extended_bands(schedule.bands, ras_gross), taxable)
    tax_due = sum((line.tax for line in lines), start=_ZERO)
    return TaxResult(
        tax_due=tax_due,
        taxable_income=taxable,
        tax_free_allowance=allowance,
        lines=lines,
    )
