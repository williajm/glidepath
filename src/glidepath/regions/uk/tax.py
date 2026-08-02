"""UK income tax assessment (roadmap 2.3; planning §4.2, §5.3, §6).

Implements the core :class:`~glidepath.core.TaxSystem` protocol. Every
figure — personal allowance, taper threshold and rate, the band
ladder — comes from the tax-year data files (§5.3); nothing is hardcoded
here (guard-tested). v1 assesses rUK non-savings income only: the
Scottish schedule ships in data (designed-for) and activates in roadmap
9.1; savings/dividend taxation needs the GIA wrapper (roadmap 9.2).

Rounding follows HMRC's published calculation logic (the Tax Logic
service guide,
https://developer.service.hmrc.gov.uk/guides/tax-logic-service-guide/):
the personal-allowance reduction is rounded *down* to the whole pound
and the resulting allowance *up* to the whole pound, and each band's
tax is rounded *down* to the penny. These statutory roundings are the
region's own — the core ledger policy (half-even at ledger writes,
planning §4.6) is unchanged elsewhere. With no reliefs modelled yet
(contributions arrive in Phase 3), adjusted net income equals the
period's non-savings income.
"""

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from itertools import pairwise
from typing import TYPE_CHECKING

from glidepath.core import Money, TaxInput, TaxLine, TaxResidencyId, TaxResult
from glidepath.regions.uk.loader import available_tax_years, load_tax_year

if TYPE_CHECKING:
    from glidepath.core import Period
    from glidepath.regions.uk.schema import IncomeTaxSchedule, TaxBand, TaxYearFile

RUK_RESIDENCY = TaxResidencyId("uk.ruk")
"""Residency id for England, Wales and Northern Ireland (rUK)."""

SCOTLAND_RESIDENCY = TaxResidencyId("uk.scotland")
"""Residency id for Scottish taxpayers (activates in roadmap 9.1)."""

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
    last shipped year fail until the future-year extension policy lands
    (roadmap 2.5).
    """

    tax_years: tuple[TaxYearFile, ...]

    def __post_init__(self) -> None:
        """Require at least one year, ascending and non-overlapping."""
        if not self.tax_years:
            msg = "UkTaxSystem needs at least one tax-year file"
            raise UkTaxError(msg)
        for previous, current in pairwise(self.tax_years):
            if current.meta.start_date <= previous.meta.end_date:
                msg = "tax-year files must be in ascending, non-overlapping order"
                raise UkTaxError(msg)

    @classmethod
    def from_shipped_data(cls) -> UkTaxSystem:
        """Build a system over every shipped ``tax_year_*.toml``."""
        years = available_tax_years()
        return cls(tax_years=tuple(load_tax_year(year) for year in years))

    def assess(self, period: Period, tax_input: TaxInput) -> TaxResult:
        """Assess one period's non-savings income (planning §4.2)."""
        year = self._tax_year_for(period)
        schedule = _schedule_for(year, tax_input.residency)
        return _assess_schedule(schedule, tax_input.non_savings_income)

    def _tax_year_for(self, period: Period) -> TaxYearFile:
        """The shipped file whose tax year fully contains ``period``."""
        for year in self.tax_years:
            if year.meta.start_date <= period.start <= year.meta.end_date:
                if period.end > year.meta.end_date:
                    msg = (
                        f"period {period.start}..{period.end} extends beyond"
                        f" tax year {year.meta.tax_year}"
                    )
                    raise UkTaxError(msg)
                return year
        msg = (
            f"no shipped tax-year data covers {period.start}"
            " (future-year extension is roadmap 2.5)"
        )
        raise UkTaxError(msg)


def _schedule_for(year: TaxYearFile, residency: TaxResidencyId) -> IncomeTaxSchedule:
    """The income-tax schedule for ``residency`` in ``year``."""
    if residency == RUK_RESIDENCY:
        return year.income_tax_ruk
    if residency == SCOTLAND_RESIDENCY:
        msg = "Scottish assessment is not yet active (roadmap 9.1)"
        raise UkTaxError(msg)
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


def _assess_schedule(schedule: IncomeTaxSchedule, income: Money) -> TaxResult:
    """Assess non-savings ``income`` under one regime's schedule."""
    allowance = _tapered_allowance(schedule, income)
    taxable = max(income - allowance, _ZERO)
    lines = _band_lines(schedule.bands, taxable)
    tax_due = sum((line.tax for line in lines), start=_ZERO)
    return TaxResult(
        tax_due=tax_due,
        taxable_income=taxable,
        tax_free_allowance=allowance,
        lines=lines,
    )
