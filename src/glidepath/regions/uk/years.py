"""Tax-year data resolution shared by the UK rule implementations (§5.3).

Both the tax system (roadmap 2.3) and the wrapper ruleset (roadmap 3.1)
answer period queries from the same per-tax-year data files, with the
same coverage semantics: shipped data always beats extrapolation, the
future-years extension (roadmap 2.5) only reaches *past* the last
shipped year, and a query outside coverage fails loudly. This module
holds that resolution once; the consumers wrap
:class:`UkTaxYearError` in their own error types.
"""

from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING

from glidepath.regions.uk.extension import extend_tax_year
from glidepath.regions.uk.schema import tax_year_start_year

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core import Period
    from glidepath.regions.uk.extension import FutureYearsExtension
    from glidepath.regions.uk.schema import TaxYearFile


class UkTaxYearError(ValueError):
    """A query no shipped or synthesized tax-year data can answer."""


@dataclass(frozen=True, slots=True)
class TaxYearSeries:
    """An ascending run of tax-year files plus an optional extension.

    The single source of "which year's figures govern this period" for
    every UK ruleset (planning §5.3).
    """

    tax_years: tuple[TaxYearFile, ...]
    future_years: FutureYearsExtension | None = None

    def __post_init__(self) -> None:
        """Require at least one year, ascending and non-overlapping."""
        if not self.tax_years:
            msg = "at least one tax-year file is required"
            raise UkTaxYearError(msg)
        for previous, current in pairwise(self.tax_years):
            if current.meta.start_date <= previous.meta.end_date:
                msg = "tax-year files must be in ascending, non-overlapping order"
                raise UkTaxYearError(msg)

    def year_for(self, period: Period) -> TaxYearFile:
        """The shipped or synthesized file fully containing ``period``.

        Raises:
            UkTaxYearError: If no file covers the period's start, or the
                period extends beyond that file's tax year.
        """
        year = self.year_containing(period.start)
        if period.end > year.meta.end_date:
            msg = (
                f"period {period.start}..{period.end} extends beyond"
                f" tax year {year.meta.tax_year}"
            )
            raise UkTaxYearError(msg)
        return year

    def year_containing(self, day: date) -> TaxYearFile:
        """The shipped file covering ``day``, or an extension past the last.

        Raises:
            UkTaxYearError: If ``day`` falls outside shipped coverage and
                the extension cannot reach it.
        """
        for year in self.tax_years:
            if year.meta.start_date <= day <= year.meta.end_date:
                return year
        last = self.tax_years[-1]
        if self.future_years is not None and day > last.meta.end_date:
            return extend_tax_year(
                last,
                tax_year_start_year(day),
                policy=self.future_years.policy,
                cpi=self.future_years.cpi,
            )
        problem = (
            "the future-years extension only reaches past the last shipped year"
            if self.future_years is not None
            else "no future-years extension is configured (planning §5.3)"
        )
        msg = f"no shipped tax-year data covers {day}; {problem}"
        raise UkTaxYearError(msg)
