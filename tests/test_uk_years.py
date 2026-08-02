"""Tests for the shared tax-year resolution series (issue 3.1)."""

from datetime import date
from decimal import Decimal

import pytest

from glidepath.core import Period, Rate
from glidepath.regions.uk import (
    FutureYearsExtension,
    FutureYearsMode,
    FutureYearsPolicy,
    TaxYearSeries,
    UkTaxYearError,
    load_tax_year,
)

TAX_YEAR_2026_27 = Period(start=date(2026, 4, 6), end=date(2027, 4, 5))

FROZEN_EXTENSION = FutureYearsExtension(
    policy=FutureYearsPolicy(mode=FutureYearsMode.FROZEN), cpi=Rate(Decimal("0.02"))
)


@pytest.fixture(scope="module", name="series")
def series_fixture() -> TaxYearSeries:
    """A series over the shipped data, without an extension."""
    return TaxYearSeries(tax_years=(load_tax_year(2026),))


def test_year_for_returns_the_covering_file(series: TaxYearSeries) -> None:
    """A period inside one shipped tax year resolves to that file."""
    assert series.year_for(TAX_YEAR_2026_27).meta.tax_year == "2026/27"


def test_year_for_accepts_a_sub_period(series: TaxYearSeries) -> None:
    """A period inside the year resolves like the full year."""
    part = Period(start=date(2026, 4, 6), end=date(2026, 12, 31))
    assert series.year_for(part).meta.tax_year == "2026/27"


def test_year_for_rejects_a_spanning_period(series: TaxYearSeries) -> None:
    """A period crossing 6 April has no single governing year."""
    spanning = Period(start=date(2027, 3, 1), end=date(2027, 6, 30))
    with pytest.raises(UkTaxYearError, match="extends beyond"):
        series.year_for(spanning)


@pytest.mark.parametrize(
    "day",
    [
        date(2026, 4, 5),  # before the first shipped year
        date(2027, 4, 6),  # after the last shipped year
    ],
)
def test_uncovered_day_is_rejected_without_extension(
    series: TaxYearSeries, day: date
) -> None:
    """Without a future-years extension, only shipped years resolve."""
    with pytest.raises(UkTaxYearError, match="no future-years extension"):
        series.year_containing(day)


def test_extension_synthesizes_future_years() -> None:
    """Past the last shipped year, the extension supplies the file."""
    extended = TaxYearSeries(
        tax_years=(load_tax_year(2026),), future_years=FROZEN_EXTENSION
    )
    synthesized = extended.year_containing(date(2028, 6, 1))
    assert synthesized.meta.tax_year == "2028/29"
    assert synthesized.isa == load_tax_year(2026).isa  # frozen: carried forward


def test_extension_never_reaches_backwards() -> None:
    """The extension only reaches past the last shipped year."""
    extended = TaxYearSeries(
        tax_years=(load_tax_year(2026),), future_years=FROZEN_EXTENSION
    )
    with pytest.raises(UkTaxYearError, match="only reaches past"):
        extended.year_containing(date(2026, 4, 5))


def test_empty_series_is_rejected() -> None:
    """A series needs at least one tax-year file."""
    with pytest.raises(UkTaxYearError, match="at least one"):
        TaxYearSeries(tax_years=())


def test_overlapping_years_are_rejected() -> None:
    """Duplicate or out-of-order year files are a construction error."""
    year = load_tax_year(2026)
    with pytest.raises(UkTaxYearError, match="ascending"):
        TaxYearSeries(tax_years=(year, year))
