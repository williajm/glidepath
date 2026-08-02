"""UK age rules tests (issue 2.4; planning §4.1, §6).

The SPA golden dates are hand-worked from the gov.uk state pension age
timetable: 66 for DOBs to 1960-04-05, then 66y1m..66y11m one-month DOB
bands, 67 for DOBs 1961-03-06..1977-04-05, then the legislated fixed
reach dates to 1978-04-05, then 68. Every band edge is tested on both
sides, plus the NMPA 2028-04-06 step and the §4.1 access-gate /
pro-rated-income conventions.
"""

from datetime import date
from decimal import Decimal

import pytest

from glidepath.core import AgeRules, Period, prorata_fraction
from glidepath.regions.uk import UkAgeError, UkAgeRules


@pytest.fixture(scope="module", name="rules")
def rules_fixture() -> UkAgeRules:
    """Age rules over the shipped ``age_rules.toml``."""
    return UkAgeRules.from_shipped_data()


def tax_year(start_year: int) -> Period:
    """The UK tax-year period starting 6 April of ``start_year``."""
    return Period(start=date(start_year, 4, 6), end=date(start_year + 1, 4, 5))


def test_satisfies_core_protocol() -> None:
    """UkAgeRules satisfies the core AgeRules protocol (mypy-verified)."""
    rules: AgeRules = UkAgeRules.from_shipped_data()
    assert rules.state_pension_date(date(1961, 3, 6)) == date(2028, 3, 6)


@pytest.mark.parametrize(
    ("dob", "expected"),
    [
        # -- SPA 66 band: first covered DOB, and both sides of its top edge.
        (date(1954, 10, 6), date(2020, 10, 6)),
        (date(1960, 4, 5), date(2026, 4, 5)),
        # -- 66 -> 67 phasing: one-month DOB bands, +1 SPA month each.
        (date(1960, 4, 6), date(2026, 5, 6)),  # 66y1m (gov.uk worked example)
        (date(1960, 5, 5), date(2026, 6, 5)),
        (date(1960, 5, 6), date(2026, 7, 6)),  # 66y2m
        (date(1960, 6, 5), date(2026, 8, 5)),
        (date(1960, 6, 6), date(2026, 9, 6)),  # 66y3m
        (date(1960, 7, 5), date(2026, 10, 5)),
        (date(1960, 7, 6), date(2026, 11, 6)),  # 66y4m
        (date(1960, 8, 5), date(2026, 12, 5)),
        (date(1960, 8, 6), date(2027, 1, 6)),  # 66y5m
        (date(1960, 9, 5), date(2027, 2, 5)),
        (date(1960, 9, 6), date(2027, 3, 6)),  # 66y6m
        (date(1960, 10, 5), date(2027, 4, 5)),
        (date(1960, 10, 6), date(2027, 5, 6)),  # 66y7m
        (date(1960, 11, 5), date(2027, 6, 5)),
        (date(1960, 11, 6), date(2027, 7, 6)),  # 66y8m
        (date(1960, 12, 5), date(2027, 8, 5)),
        (date(1960, 12, 6), date(2027, 9, 6)),  # 66y9m
        (date(1961, 1, 5), date(2027, 10, 5)),
        (date(1961, 1, 6), date(2027, 11, 6)),  # 66y10m
        (date(1961, 2, 5), date(2027, 12, 5)),
        (date(1961, 2, 6), date(2028, 1, 6)),  # 66y11m
        (date(1961, 3, 5), date(2028, 2, 5)),
        # -- SPA 67 band.
        (date(1961, 3, 6), date(2028, 3, 6)),
        (date(1977, 4, 5), date(2044, 4, 5)),
        # -- 67 -> 68 phasing: fixed legislated reach dates per DOB band.
        (date(1977, 4, 6), date(2044, 5, 6)),
        (date(1977, 5, 5), date(2044, 5, 6)),
        (date(1977, 5, 6), date(2044, 7, 6)),
        (date(1977, 6, 5), date(2044, 7, 6)),
        (date(1977, 6, 6), date(2044, 9, 6)),
        (date(1977, 7, 5), date(2044, 9, 6)),
        (date(1977, 7, 6), date(2044, 11, 6)),
        (date(1977, 8, 5), date(2044, 11, 6)),
        (date(1977, 8, 6), date(2045, 1, 6)),
        (date(1977, 9, 5), date(2045, 1, 6)),
        (date(1977, 9, 6), date(2045, 3, 6)),
        (date(1977, 10, 5), date(2045, 3, 6)),
        (date(1977, 10, 6), date(2045, 5, 6)),
        (date(1977, 11, 5), date(2045, 5, 6)),
        (date(1977, 11, 6), date(2045, 7, 6)),
        (date(1977, 12, 5), date(2045, 7, 6)),
        (date(1977, 12, 6), date(2045, 9, 6)),
        (date(1978, 1, 5), date(2045, 9, 6)),
        (date(1978, 1, 6), date(2045, 11, 6)),
        (date(1978, 2, 5), date(2045, 11, 6)),
        (date(1978, 2, 6), date(2046, 1, 6)),
        (date(1978, 3, 5), date(2046, 1, 6)),
        (date(1978, 3, 6), date(2046, 3, 6)),
        (date(1978, 4, 5), date(2046, 3, 6)),
        # -- SPA 68 open-ended band.
        (date(1978, 4, 6), date(2046, 4, 6)),
    ],
)
def test_spa_golden_dates(rules: UkAgeRules, dob: date, expected: date) -> None:
    """SPA dates match the gov.uk timetable at every band edge."""
    assert rules.state_pension_date(dob) == expected


def test_spa_months_clamp_to_month_end(rules: UkAgeRules) -> None:
    """Adding SPA months lands on the target month's last existing day."""
    assert rules.state_pension_date(date(1960, 7, 31)) == date(2026, 11, 30)


def test_spa_leap_day_birthday_deemed_1_march(rules: UkAgeRules) -> None:
    """A 29 February DOB attains 66 on 1 March of the non-leap year."""
    assert rules.state_pension_date(date(1960, 2, 29)) == date(2026, 3, 1)


def test_spa_rejects_dob_before_timetable_coverage(rules: UkAgeRules) -> None:
    """Cohorts before the first band are rejected, never guessed."""
    day_before_coverage = date(1954, 10, 5)
    with pytest.raises(UkAgeError, match="predates SPA timetable coverage"):
        rules.state_pension_date(day_before_coverage)


@pytest.mark.parametrize(
    ("on", "expected"),
    [
        (date(2026, 4, 6), 55),
        (date(2028, 4, 5), 55),  # last day of the outgoing age
        (date(2028, 4, 6), 57),  # the legislated step-up day
        (date(2050, 1, 1), 57),
    ],
)
def test_nmpa_in_force(rules: UkAgeRules, on: date, expected: int) -> None:
    """The NMPA schedule steps exactly on its effective date."""
    assert rules.normal_minimum_pension_age(on) == expected


@pytest.mark.parametrize(
    ("dob", "start_year"),
    [
        (date(1972, 4, 6), 2027),  # 55 attained exactly on the first day
        (date(1972, 4, 6), 2029),  # 57 attained exactly on the step-up
        (date(1972, 4, 7), 2030),  # first period after the 57th birthday
    ],
)
def test_pension_access_open(rules: UkAgeRules, dob: date, start_year: int) -> None:
    """The gate opens once the NMPA in force is attained by period start."""
    assert rules.is_pension_access_open(dob, tax_year(start_year))


@pytest.mark.parametrize(
    ("dob", "start_year"),
    [
        (date(1972, 4, 6), 2026),  # not yet 55
        (date(1972, 4, 6), 2028),  # the 2028 cliff: 56 when the age steps to 57
        (date(1972, 4, 7), 2027),  # 55th birthday one day into the period
        (date(1972, 4, 7), 2029),  # 57th birthday one day into the period
    ],
)
def test_pension_access_closed(rules: UkAgeRules, dob: date, start_year: int) -> None:
    """The gate stays shut before the NMPA in force is attained (§4.1)."""
    assert not rules.is_pension_access_open(dob, tax_year(start_year))


@pytest.mark.parametrize(
    ("dob", "start_year"),
    [
        (date(2009, 4, 6), 2027),  # 18 attained exactly on the first day
        (date(1988, 4, 6), 2027),  # 39 at period start
        (date(1989, 1, 1), 2028),  # 39 at start; turns 40 mid-period
    ],
)
def test_lisa_opening_allowed(rules: UkAgeRules, dob: date, start_year: int) -> None:
    """Opening is allowed while 18 is attained and 40 is not (§4.1)."""
    assert rules.is_lisa_opening_allowed(dob, tax_year(start_year))


@pytest.mark.parametrize(
    ("dob", "start_year"),
    [
        (date(2009, 4, 6), 2026),  # still 17 at period start
        (date(2009, 4, 7), 2027),  # 18th birthday one day into the period
        (date(1988, 4, 6), 2028),  # 40 attained exactly on the first day
    ],
)
def test_lisa_opening_not_allowed(
    rules: UkAgeRules, dob: date, start_year: int
) -> None:
    """Opening is refused outside the 18-39 window at period start."""
    assert not rules.is_lisa_opening_allowed(dob, tax_year(start_year))


@pytest.mark.parametrize(
    ("dob", "start_year"),
    [
        (date(1978, 4, 6), 2027),  # 49 at period start
        (date(1979, 1, 1), 2028),  # 49 at start; turns 50 mid-period
        (date(2009, 4, 6), 2027),  # 18 attained exactly on the first day
    ],
)
def test_lisa_contribution_allowed(
    rules: UkAgeRules, dob: date, start_year: int
) -> None:
    """Contributions run from 18 until the 50th birthday (§4.1)."""
    assert rules.is_lisa_contribution_allowed(dob, tax_year(start_year))


@pytest.mark.parametrize(
    ("dob", "start_year"),
    [
        (date(1978, 4, 6), 2028),  # 50 attained exactly on the first day
        (date(2009, 4, 7), 2027),  # not yet 18 at period start
    ],
)
def test_lisa_contribution_not_allowed(
    rules: UkAgeRules, dob: date, start_year: int
) -> None:
    """Contributions stop once 50 is attained by the period's first day."""
    assert not rules.is_lisa_contribution_allowed(dob, tax_year(start_year))


def test_lisa_access_gate(rules: UkAgeRules) -> None:
    """Charge-free access opens with 60 attained by period start (§4.1)."""
    assert rules.is_lisa_access_open(date(1968, 4, 6), tax_year(2028))
    assert not rules.is_lisa_access_open(date(1968, 4, 7), tax_year(2028))
    assert rules.is_lisa_access_open(date(1968, 4, 7), tax_year(2029))


def test_spa_feeds_prorata_income_convention(rules: UkAgeRules) -> None:
    """An SPA falling mid-period pro-rates by whole months (§4.1).

    DOB 1960-10-06 reaches SPA (66y7m) on 2027-05-06, one month into
    the 2027/28 tax year, so the entitlement covers 11 of 12 months.
    """
    spa_date = rules.state_pension_date(date(1960, 10, 6))
    assert spa_date == date(2027, 5, 6)
    fraction = prorata_fraction(spa_date, tax_year(2027))
    assert fraction == Decimal(11) / Decimal(12)
