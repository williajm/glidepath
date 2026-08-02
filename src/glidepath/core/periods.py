"""Periods, fiscal calendars, and the age/pro-rating conventions.

Implements planning §4.1: the engine steps annually over *periods*
supplied by a region's :class:`FiscalCalendar` (the core never knows what
"6 April" is). Age-triggered changes follow one convention, tested at
boundaries:

- **Access gates** are open for a period only if the age is attained on or
  before the period's first day (:func:`is_age_attained_by_period_start`).
- **Income entitlements** begin at their exact date and are pro-rated by
  whole months within their starting period (:func:`prorata_fraction`).
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterator

_FEB_29 = (2, 29)
_NON_LEAP_YEAR = 2001  # any non-leap year; used to validate calendar anchors


@dataclass(frozen=True, slots=True, order=True)
class Period:
    """A consecutive span of days, inclusive of both endpoints."""

    start: date
    end: date

    def __post_init__(self) -> None:
        """Reject periods that end before they start."""
        if self.end < self.start:
            msg = f"Period end {self.end} precedes start {self.start}"
            raise ValueError(msg)

    def contains(self, day: date) -> bool:
        """Whether ``day`` falls within this period."""
        return self.start <= day <= self.end


class FiscalCalendar(Protocol):
    """Region-supplied period scheme (planning §4.1, §4.2).

    The UK region implements this with tax years (6 Apr to 5 Apr); the
    core only ever sees opaque :class:`Period` values.
    """

    def period_containing(self, day: date) -> Period:
        """Return the period that contains ``day``."""
        ...

    def periods(self, first: date, last: date) -> Iterator[Period]:
        """Yield consecutive periods covering ``first`` through ``last``."""
        ...


@dataclass(frozen=True, slots=True)
class AnnualCalendar:
    """Generic annual calendar: each period runs one year from an anchor.

    The default anchor is 1 January. The anchor must be a day that exists
    in every year, so 29 February is rejected.
    """

    anchor_month: int = 1
    anchor_day: int = 1

    def __post_init__(self) -> None:
        """Reject anchors that do not exist in every year."""
        try:
            date(_NON_LEAP_YEAR, self.anchor_month, self.anchor_day)
        except ValueError as exc:
            anchor = f"{self.anchor_month:02d}-{self.anchor_day:02d}"
            msg = f"anchor {anchor} must be a valid day in every year (no 29 February)"
            raise ValueError(msg) from exc

    def _anchor_in_year(self, year: int) -> date:
        """The period start date falling in calendar ``year``."""
        return date(year, self.anchor_month, self.anchor_day)

    def period_containing(self, day: date) -> Period:
        """Return the annual period that contains ``day``."""
        start_year = day.year if day >= self._anchor_in_year(day.year) else day.year - 1
        start = self._anchor_in_year(start_year)
        return Period(start, self._anchor_in_year(start_year + 1) - timedelta(days=1))

    def periods(self, first: date, last: date) -> Iterator[Period]:
        """Yield consecutive annual periods covering ``first`` to ``last``.

        Raises:
            ValueError: If ``last`` precedes ``first``.
        """
        if last < first:
            msg = f"horizon end {last} precedes start {first}"
            raise ValueError(msg)
        current = self.period_containing(first)
        while current.start <= last:
            yield current
            current = self.period_containing(current.end + timedelta(days=1))


def birthday_in_year(date_of_birth: date, year: int) -> date:
    """The date the birthday falls (or is deemed to fall) in ``year``.

    A 29 February birthday is deemed to fall on 1 March in non-leap years,
    matching the UK legal convention for attaining an age.
    """
    if (date_of_birth.month, date_of_birth.day) == _FEB_29 and not calendar.isleap(
        year
    ):
        return date(year, 3, 1)
    return date(year, date_of_birth.month, date_of_birth.day)


def date_age_attained(date_of_birth: date, age: int) -> date:
    """The exact date on which the person attains ``age`` whole years.

    Raises:
        ValueError: If ``age`` is negative.
    """
    if age < 0:
        msg = "age must be non-negative"
        raise ValueError(msg)
    return birthday_in_year(date_of_birth, date_of_birth.year + age)


def age_on(date_of_birth: date, day: date) -> int:
    """Whole years of age on ``day``.

    Raises:
        ValueError: If ``day`` precedes the date of birth.
    """
    if day < date_of_birth:
        msg = f"day {day} precedes date of birth {date_of_birth}"
        raise ValueError(msg)
    years = day.year - date_of_birth.year
    if day < birthday_in_year(date_of_birth, day.year):
        years -= 1
    return years


def is_age_attained_by_period_start(
    date_of_birth: date, age: int, period: Period
) -> bool:
    """The access-gate convention of planning §4.1.

    A gate (NMPA, LISA access, ...) is open for a period only if ``age``
    is attained on or before the period's first day — conservative, so the
    model never simulates a withdrawal that would be unauthorised in
    reality. A birthday on the period's last day unlocks the *next*
    period, never the current one.
    """
    return date_age_attained(date_of_birth, age) <= period.start


def add_months(day: date, months: int) -> date:
    """Shift ``day`` by calendar months, clamping to the target month's end.

    ``add_months(date(2026, 1, 31), 1)`` is 28 February 2026.
    """
    total = day.month - 1 + months
    year = day.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def whole_months_between(start: date, end_exclusive: date) -> int:
    """Complete calendar months from ``start`` up to ``end_exclusive``.

    The count is the largest ``n`` such that ``add_months(start, n)`` does
    not pass ``end_exclusive`` (day-clamped month arithmetic).

    Raises:
        ValueError: If ``end_exclusive`` precedes ``start``.
    """
    if end_exclusive < start:
        msg = f"end {end_exclusive} precedes start {start}"
        raise ValueError(msg)
    months = (end_exclusive.year - start.year) * 12 + (
        end_exclusive.month - start.month
    )
    while months > 0 and add_months(start, months) > end_exclusive:
        months -= 1
    return max(months, 0)


def _whole_months_in(period: Period) -> int:
    """Whole months in ``period``, which pro-rating divides by.

    Raises:
        ValueError: If ``period`` is shorter than one whole month.
    """
    months = whole_months_between(period.start, period.end + timedelta(days=1))
    if months == 0:
        msg = "period is shorter than one whole month; cannot pro-rate"
        raise ValueError(msg)
    return months


def prorata_fraction(entitlement_start: date, period: Period) -> Decimal:
    """Fraction of ``period`` an entitlement starting mid-period is paid for.

    Implements the income-entitlement convention of planning §4.1: incomes
    (state pension from SPA, DB from NPA, annuity start) begin at their
    exact date and are pro-rated by whole months within their starting
    period, as an exact ``Decimal`` fraction.

    Returns:
        ``1`` if the entitlement started on or before the period began,
        ``0`` if it starts after the period ends, otherwise
        ``whole months in payment / whole months in the period``.

    Raises:
        ValueError: If ``period`` is shorter than one whole month.
    """
    if entitlement_start <= period.start:
        return Decimal(1)
    if entitlement_start > period.end:
        return Decimal(0)
    months_in_period = _whole_months_in(period)
    end_exclusive = period.end + timedelta(days=1)
    months_in_payment = whole_months_between(entitlement_start, end_exclusive)
    return Decimal(months_in_payment) / Decimal(months_in_period)


def entitlement_active_fraction(
    entitlement_start: date,
    period: Period,
    window_start: date,
    window_end: date,
) -> Decimal:
    """Fraction of ``period`` an entitlement is in payment inside the window.

    Combines the two §4.1/§5.2 conventions for income entitlements (DB
    from its start date, state pension from SPA plus deferral): payment
    runs from ``entitlement_start`` and the run models only
    ``[window_start, window_end]`` (roadmap 4.6), so the period's
    payable share is the whole months of the overlap over the whole
    months of the period. With the window covering the period this is
    exactly :func:`prorata_fraction`; with the entitlement predating the
    window it is exactly :func:`period_active_fraction`.

    Returns:
        ``1`` if payment covers the whole period inside the window,
        ``0`` if the overlap is under one whole month, otherwise the
        exact ``Decimal`` month ratio.

    Raises:
        ValueError: If ``window_end`` precedes ``window_start``, or the
            period is shorter than one whole month.
    """
    if window_end < window_start:
        msg = f"window end {window_end} precedes window start {window_start}"
        raise ValueError(msg)
    payable_start = max(entitlement_start, window_start, period.start)
    payable_end = min(window_end, period.end)
    if payable_end < payable_start:
        return Decimal(0)
    if payable_start == period.start and payable_end == period.end:
        return Decimal(1)
    months_in_period = _whole_months_in(period)
    end_exclusive = payable_end + timedelta(days=1)
    months_payable = whole_months_between(payable_start, end_exclusive)
    return Decimal(months_payable) / Decimal(months_in_period)


def period_active_fraction(
    period: Period, window_start: date, window_end: date
) -> Decimal:
    """Fraction of ``period`` inside the run window, by whole months.

    Implements the partial first/last period convention (planning §5.2,
    roadmap 4.6): the engine models only the window from ``window_start``
    (the run's ``today``) through ``window_end`` (the horizon end), both
    inclusive. A period partly outside that window is scaled by
    ``whole months inside the window / whole months in the period`` —
    the same §4.1 whole-month convention as other partial years. For a
    window open at the end, this agrees with :func:`prorata_fraction`.

    Returns:
        ``1`` if the period lies wholly inside the window, ``0`` if less
        than one whole month of it does, otherwise the exact ``Decimal``
        month ratio.

    Raises:
        ValueError: If ``window_end`` precedes ``window_start``, or the
            period is shorter than one whole month.
    """
    if window_end < window_start:
        msg = f"window end {window_end} precedes window start {window_start}"
        raise ValueError(msg)
    if window_start <= period.start and window_end >= period.end:
        return Decimal(1)
    active_start = max(window_start, period.start)
    active_end_exclusive = min(window_end, period.end) + timedelta(days=1)
    if active_end_exclusive <= active_start:
        return Decimal(0)
    months_in_period = _whole_months_in(period)
    months_active = whole_months_between(active_start, active_end_exclusive)
    return Decimal(months_active) / Decimal(months_in_period)


class AgeRules(Protocol):
    """Region-supplied age rules (planning §4.1, §4.2).

    Turns a date of birth into the exact dates and period gates behind
    age-triggered changes: state pension entitlement is an *income
    entitlement* (begins at its exact date, pro-rated via
    :func:`prorata_fraction`) while private pension access is an
    *access gate* (following :func:`is_age_attained_by_period_start`).
    Wrapper-specific age gates (e.g. UK LISA ages) stay behind the
    region's wrapper rules rather than crossing the boundary here.
    """

    def state_pension_date(self, date_of_birth: date) -> date:
        """The exact date state pension entitlement begins."""
        ...

    def is_pension_access_open(self, date_of_birth: date, period: Period) -> bool:
        """Whether new pension benefits may be accessed in ``period``."""
        ...
