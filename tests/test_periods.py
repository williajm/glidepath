"""Tests for periods, calendars and §4.1 boundary conventions (issue 1.2)."""

from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st

from glidepath.core.periods import (
    AnnualCalendar,
    FiscalCalendar,
    Period,
    add_months,
    age_on,
    birthday_in_year,
    date_age_attained,
    is_age_attained_by_period_start,
    period_active_fraction,
    prorata_fraction,
    whole_months_between,
)

# An arbitrary non-January anchor exercising mid-year periods. April 6 is
# deliberately UK-shaped so the §4.1 boundary examples read naturally, but
# to the core it is just a parameter — no policy lives here.
APRIL_6 = AnnualCalendar(anchor_month=4, anchor_day=6)

reasonable_dates = st.dates(min_value=date(1900, 1, 1), max_value=date(2200, 12, 31))


def test_period_contains_both_endpoints() -> None:
    """Periods are inclusive of start and end."""
    period = Period(date(2026, 4, 6), date(2027, 4, 5))
    assert period.contains(period.start)
    assert period.contains(period.end)
    assert not period.contains(period.start - timedelta(days=1))
    assert not period.contains(period.end + timedelta(days=1))


def test_period_rejects_end_before_start() -> None:
    """A backwards period is invalid."""
    start, end = date(2026, 1, 2), date(2026, 1, 1)
    with pytest.raises(ValueError, match="precedes start"):
        Period(start, end)


def test_annual_calendar_is_a_fiscal_calendar() -> None:
    """AnnualCalendar satisfies the FiscalCalendar protocol."""
    calendar: FiscalCalendar = AnnualCalendar()
    period = calendar.period_containing(date(2026, 6, 15))
    assert period == Period(date(2026, 1, 1), date(2026, 12, 31))


def test_period_containing_respects_anchor_boundary() -> None:
    """The day before an anchor belongs to the prior period."""
    assert APRIL_6.period_containing(date(2026, 4, 5)) == Period(
        date(2025, 4, 6), date(2026, 4, 5)
    )
    assert APRIL_6.period_containing(date(2026, 4, 6)) == Period(
        date(2026, 4, 6), date(2027, 4, 5)
    )


@given(day=reasonable_dates)
def test_period_containing_always_contains_the_day(day: date) -> None:
    """Every date falls inside the period claimed to contain it."""
    assert APRIL_6.period_containing(day).contains(day)


def test_periods_iterate_an_arbitrary_horizon() -> None:
    """Consecutive periods cover the horizon with no gaps or overlaps."""
    horizon = list(APRIL_6.periods(date(2026, 1, 1), date(2030, 6, 1)))
    assert horizon[0].contains(date(2026, 1, 1))
    assert horizon[-1].contains(date(2030, 6, 1))
    assert len(horizon) == 6
    for earlier, later in pairwise(horizon):
        assert later.start == earlier.end + timedelta(days=1)


def test_periods_reject_backwards_horizon() -> None:
    """A horizon ending before it starts is invalid."""
    backwards = APRIL_6.periods(date(2026, 1, 2), date(2026, 1, 1))
    with pytest.raises(ValueError, match="precedes start"):
        list(backwards)


def test_calendar_rejects_leap_day_anchor() -> None:
    """An anchor of 29 February would not exist in every year."""
    with pytest.raises(ValueError, match="valid day in every year"):
        AnnualCalendar(anchor_month=2, anchor_day=29)


def test_calendar_rejects_invalid_anchor() -> None:
    """Nonsense anchors are rejected outright."""
    with pytest.raises(ValueError, match="valid day in every year"):
        AnnualCalendar(anchor_month=13, anchor_day=1)


def test_birthday_in_year_ordinary() -> None:
    """Ordinary birthdays fall on the same month/day every year."""
    assert birthday_in_year(date(1971, 4, 5), 2026) == date(2026, 4, 5)


def test_leap_day_birthday_deemed_1_march_in_non_leap_years() -> None:
    """A 29 February birthday attains ages on 1 March in non-leap years."""
    dob = date(2000, 2, 29)
    assert birthday_in_year(dob, 2024) == date(2024, 2, 29)
    assert birthday_in_year(dob, 2026) == date(2026, 3, 1)
    assert date_age_attained(dob, 26) == date(2026, 3, 1)
    assert age_on(dob, date(2026, 2, 28)) == 25
    assert age_on(dob, date(2026, 3, 1)) == 26


def test_age_on_boundaries() -> None:
    """Age increments exactly on the birthday, not before."""
    dob = date(1971, 4, 5)
    assert age_on(dob, date(2026, 4, 4)) == 54
    assert age_on(dob, date(2026, 4, 5)) == 55
    assert age_on(dob, dob) == 0


def test_age_on_rejects_days_before_birth() -> None:
    """Asking for an age before birth is a caller error."""
    dob, day = date(1971, 4, 5), date(1971, 4, 4)
    with pytest.raises(ValueError, match="precedes date of birth"):
        age_on(dob, day)


def test_date_age_attained_rejects_negative_age() -> None:
    """Negative ages are invalid."""
    dob = date(1971, 4, 5)
    with pytest.raises(ValueError, match="non-negative"):
        date_age_attained(dob, -1)


def test_access_gate_convention_last_day_birthday_unlocks_next_period() -> None:
    """Planning §4.1: a period-final birthday unlocks the *next* period.

    A person whose birthday falls on the period's last day has not
    attained the age by the period's first day, so the gate stays shut
    until the following period — never the preceding period start.
    """
    dob = date(1971, 4, 5)  # birthday on the last day of an April-6 period
    attained_period = APRIL_6.period_containing(date(2025, 4, 6))  # ends 2026-04-05
    next_period = APRIL_6.period_containing(date(2026, 4, 6))
    assert not is_age_attained_by_period_start(dob, 55, attained_period)
    assert is_age_attained_by_period_start(dob, 55, next_period)


def test_access_gate_open_when_attained_exactly_on_period_start() -> None:
    """Attaining the age on the period's first day opens the gate."""
    dob = date(1971, 4, 6)
    period = APRIL_6.period_containing(date(2026, 4, 6))
    assert is_age_attained_by_period_start(dob, 55, period)


def test_add_months_clamps_to_month_end() -> None:
    """Month arithmetic clamps to the target month's last day."""
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


def test_whole_months_between_counts_complete_months() -> None:
    """Only complete calendar months count."""
    assert whole_months_between(date(2026, 4, 6), date(2027, 4, 6)) == 12
    assert whole_months_between(date(2026, 10, 6), date(2027, 4, 6)) == 6
    assert whole_months_between(date(2026, 10, 7), date(2027, 4, 6)) == 5
    assert whole_months_between(date(2026, 1, 1), date(2026, 1, 31)) == 0


def test_whole_months_between_rejects_backwards_range() -> None:
    """A range ending before it starts is invalid."""
    start, end = date(2026, 2, 1), date(2026, 1, 1)
    with pytest.raises(ValueError, match="precedes start"):
        whole_months_between(start, end)


def test_prorata_full_period_when_entitlement_predates_period() -> None:
    """An entitlement already in payment gets the whole period."""
    period = Period(date(2026, 4, 6), date(2027, 4, 5))
    assert prorata_fraction(date(2020, 1, 1), period) == Decimal(1)
    assert prorata_fraction(period.start, period) == Decimal(1)


def test_prorata_zero_when_entitlement_starts_after_period() -> None:
    """An entitlement starting later contributes nothing to this period."""
    period = Period(date(2026, 4, 6), date(2027, 4, 5))
    assert prorata_fraction(date(2027, 4, 6), period) == Decimal(0)


def test_prorata_whole_months_mid_period() -> None:
    """Planning §4.1: mid-period starts pro-rate by whole months."""
    period = Period(date(2026, 4, 6), date(2027, 4, 5))
    assert prorata_fraction(date(2026, 10, 6), period) == Decimal(6) / Decimal(12)
    assert prorata_fraction(date(2026, 10, 7), period) == Decimal(5) / Decimal(12)
    assert prorata_fraction(period.end, period) == Decimal(0)


def test_prorata_rejects_sub_month_period() -> None:
    """Pro-rating needs at least one whole month to divide by."""
    period = Period(date(2026, 1, 1), date(2026, 1, 15))
    mid_period = date(2026, 1, 2)
    with pytest.raises(ValueError, match="shorter than one whole month"):
        prorata_fraction(mid_period, period)


def test_active_fraction_is_one_for_a_period_wholly_inside_the_window() -> None:
    """A fully covered period needs no pro-rating."""
    period = Period(date(2026, 4, 6), date(2027, 4, 5))
    assert period_active_fraction(period, date(2026, 1, 1), date(2028, 1, 1)) == 1
    assert period_active_fraction(period, period.start, period.end) == 1


def test_active_fraction_agrees_with_prorata_for_a_mid_period_start() -> None:
    """A window starting mid-period matches the §4.1 entitlement fraction."""
    period = Period(date(2026, 4, 6), date(2027, 4, 5))
    start = date(2026, 8, 15)
    fraction = period_active_fraction(period, start, date(2030, 1, 1))
    assert fraction == prorata_fraction(start, period)
    assert fraction == Decimal(7) / Decimal(12)


def test_active_fraction_counts_whole_months_up_to_the_window_end() -> None:
    """A window ending mid-period keeps only the whole months reached."""
    period = Period(date(2026, 4, 6), date(2027, 4, 5))
    assert period_active_fraction(period, date(2020, 1, 1), date(2026, 10, 5)) == (
        Decimal(6) / Decimal(12)
    )


def test_active_fraction_intersects_a_window_inside_the_period() -> None:
    """Both window ends inside the period leave the months between them."""
    period = Period(date(2026, 1, 1), date(2026, 12, 31))
    assert period_active_fraction(period, date(2026, 3, 1), date(2026, 8, 31)) == (
        Decimal(6) / Decimal(12)
    )


def test_active_fraction_is_zero_without_a_whole_month_of_overlap() -> None:
    """Under one whole month inside the window models nothing (§4.1)."""
    period = Period(date(2026, 1, 1), date(2026, 12, 31))
    assert period_active_fraction(period, date(2026, 12, 20), date(2027, 6, 1)) == 0
    assert period_active_fraction(period, date(2025, 1, 1), date(2025, 12, 31)) == 0


def test_active_fraction_rejects_a_backwards_window() -> None:
    """A window ending before it starts is a caller error."""
    period = Period(date(2026, 1, 1), date(2026, 12, 31))
    window_start, window_end = date(2026, 6, 1), date(2026, 5, 1)
    with pytest.raises(ValueError, match="precedes window start"):
        period_active_fraction(period, window_start, window_end)


def test_active_fraction_rejects_sub_month_period() -> None:
    """Pro-rating needs at least one whole month to divide by."""
    period = Period(date(2026, 1, 1), date(2026, 1, 15))
    window_start, window_end = date(2026, 1, 5), date(2026, 3, 1)
    with pytest.raises(ValueError, match="shorter than one whole month"):
        period_active_fraction(period, window_start, window_end)
