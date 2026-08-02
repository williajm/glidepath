"""Tests for the UK state pension scheme (issue 4.3, planning §5.1, §6).

Every expected amount is hand-derived from the shipped 2026/27 data:
full new state pension £241.30/week (£12,547.60/yr at 52 weeks), 35
qualifying years for the full rate, 10 minimum, deferral +1% per 9
weeks, and the new-system start on 6 April 2016.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.core import Decision, Fact, Money, StatePensionRecord
from glidepath.regions.uk import (
    UkStatePensionError,
    UkStatePensionScheme,
    default_assumption_set,
    future_years_extension,
)

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)
TODAY = date(2026, 8, 2)

DOB_SPA_67 = date(1970, 6, 15)  # SPA 67 band: reaches SPA on 2037-06-15
POST_2016_NI_START = date(2016, 4, 6)

FULL_WEEKLY = Decimal("241.30")
WEEKS = Decimal(52)


def scheme() -> UkStatePensionScheme:
    """The scheme over shipped data, with the future-years extension."""
    return UkStatePensionScheme.from_shipped_data(
        future_years_extension(default_assumption_set())
    )


def money_fact(amount: str) -> Fact[Money]:
    """A user-stated weekly amount."""
    return Fact(value=Money(Decimal(amount)), as_of=AS_OF, recorded_on=RECORDED)


def record_of(
    *,
    forecast: str | None = None,
    protected: str | None = None,
    ni_start: date | None = None,
    qualifying: int | None = None,
    extra_years: int = 0,
    deferral: str = "0",
) -> StatePensionRecord:
    """A state pension record built from compact test parameters."""
    return StatePensionRecord(
        forecast_weekly_amount=None if forecast is None else money_fact(forecast),
        protected_payment=None if protected is None else money_fact(protected),
        ni_record_start=None
        if ni_start is None
        else Fact(value=ni_start, as_of=AS_OF, recorded_on=RECORDED),
        qualifying_years=None
        if qualifying is None
        else Fact(value=qualifying, as_of=AS_OF, recorded_on=RECORDED),
        planned_extra_years=Decision(value=extra_years, recorded_on=RECORDED),
        deferral_years=Decision(value=Decimal(deferral), recorded_on=RECORDED),
    )


class TestForecastWins:
    """An official forecast, when present, is the fact and wins (§5.1)."""

    def test_forecast_beats_the_derivation(self) -> None:
        """With both stated, the forecast alone sets the amount."""
        record = record_of(
            forecast="230.25", ni_start=POST_2016_NI_START, qualifying=35
        )
        entitlement = scheme().entitlement(record, DOB_SPA_67, TODAY)
        assert entitlement.annual_amount == Money(Decimal("230.25") * WEEKS)
        assert entitlement.cpi_uprated_annual_amount == Money(Decimal(0))

    def test_protected_payment_splits_into_the_cpi_slice(self) -> None:
        """The protected slice uprates by CPI only, so it is split out."""
        record = record_of(forecast="250.00", protected="20.00")
        entitlement = scheme().entitlement(record, DOB_SPA_67, TODAY)
        assert entitlement.annual_amount == Money(Decimal("230.00") * WEEKS)
        assert entitlement.cpi_uprated_annual_amount == Money(Decimal("20.00") * WEEKS)

    def test_start_date_is_the_spa_date(self) -> None:
        """Without deferral the entitlement starts exactly at SPA."""
        record = record_of(forecast="230.25")
        entitlement = scheme().entitlement(record, DOB_SPA_67, TODAY)
        assert entitlement.start_date == date(2037, 6, 15)


class TestQualifyingYearsDerivation:
    """The ÷35 derivation over the current full rate (§5.1, §6)."""

    def test_full_record_earns_the_full_rate(self) -> None:
        """35 qualifying years earn £241.30 x 52 a year."""
        record = record_of(ni_start=POST_2016_NI_START, qualifying=35)
        entitlement = scheme().entitlement(record, DOB_SPA_67, TODAY)
        assert entitlement.annual_amount == Money(FULL_WEEKLY * WEEKS)

    def test_partial_record_scales_by_thirty_fifths(self) -> None:
        """20 years earn exactly 20/35 of the full rate."""
        record = record_of(ni_start=POST_2016_NI_START, qualifying=20)
        entitlement = scheme().entitlement(record, DOB_SPA_67, TODAY)
        expected = FULL_WEEKLY * (Decimal(20) / Decimal(35)) * WEEKS
        assert entitlement.annual_amount == Money(expected)

    def test_planned_extra_years_count(self) -> None:
        """Stated years plus planned years drive the fraction."""
        record = record_of(ni_start=POST_2016_NI_START, qualifying=20, extra_years=10)
        entitlement = scheme().entitlement(record, DOB_SPA_67, TODAY)
        expected = FULL_WEEKLY * (Decimal(30) / Decimal(35)) * WEEKS
        assert entitlement.annual_amount == Money(expected)

    def test_years_cap_at_the_full_count(self) -> None:
        """More than 35 years never earn more than the full rate."""
        record = record_of(ni_start=POST_2016_NI_START, qualifying=30, extra_years=10)
        entitlement = scheme().entitlement(record, DOB_SPA_67, TODAY)
        assert entitlement.annual_amount == Money(FULL_WEEKLY * WEEKS)

    def test_below_the_minimum_earns_nothing(self) -> None:
        """Fewer than 10 qualifying years earn no state pension."""
        record = record_of(ni_start=POST_2016_NI_START, qualifying=9)
        entitlement = scheme().entitlement(record, DOB_SPA_67, TODAY)
        assert entitlement.annual_amount == Money(Decimal(0))

    def test_planned_years_can_reach_the_minimum(self) -> None:
        """9 stated years plus 1 planned year cross the minimum."""
        record = record_of(ni_start=POST_2016_NI_START, qualifying=9, extra_years=1)
        entitlement = scheme().entitlement(record, DOB_SPA_67, TODAY)
        expected = FULL_WEEKLY * (Decimal(10) / Decimal(35)) * WEEKS
        assert entitlement.annual_amount == Money(expected)


class TestDerivationGates:
    """Pre-2016 and underspecified records are refused, never guessed."""

    def test_pre_2016_ni_record_is_refused(self) -> None:
        """A record started before 6 April 2016 needs a forecast (§5.1)."""
        uk = scheme()
        record = record_of(ni_start=date(2016, 4, 5), qualifying=35)
        with pytest.raises(UkStatePensionError, match="official forecast is required"):
            uk.entitlement(record, DOB_SPA_67, TODAY)

    def test_missing_ni_record_start_is_refused(self) -> None:
        """Without the record start the post-2016 gate cannot be proven."""
        uk = scheme()
        record = record_of(qualifying=35)
        with pytest.raises(UkStatePensionError, match="NI record start"):
            uk.entitlement(record, DOB_SPA_67, TODAY)

    def test_missing_qualifying_years_are_refused(self) -> None:
        """Neither forecast nor qualifying years means no answer."""
        uk = scheme()
        record = record_of(ni_start=POST_2016_NI_START)
        with pytest.raises(UkStatePensionError, match="qualifying-years count"):
            uk.entitlement(record, DOB_SPA_67, TODAY)


class TestDeferral:
    """Deferral shifts the start and earns CPI-uprated increments (§6)."""

    def test_one_year_deferral_shifts_the_start_and_uplifts(self) -> None:
        """One year is 52 whole weeks: 5 nine-week increments, +5%.

        The increment lands in the CPI-only slice alongside protected
        payments, because increments uprate by CPI (planning §6).
        """
        record = record_of(forecast="241.30", deferral="1")
        entitlement = scheme().entitlement(record, DOB_SPA_67, TODAY)
        assert entitlement.start_date == date(2038, 6, 15)
        assert entitlement.annual_amount == Money(FULL_WEEKLY * WEEKS)
        expected_increment = FULL_WEEKLY * Decimal("0.05") * WEEKS
        assert entitlement.cpi_uprated_annual_amount == Money(expected_increment)

    def test_short_deferral_earns_no_increment(self) -> None:
        """One month (about 4 weeks) is under the 9-week increment step."""
        record = record_of(forecast="241.30", deferral="0.25")
        entitlement = scheme().entitlement(record, DOB_SPA_67, TODAY)
        assert entitlement.start_date == date(2037, 9, 15)
        expected_weeks = (date(2037, 9, 15) - date(2037, 6, 15)).days // 7
        assert expected_weeks < 18
        assert entitlement.cpi_uprated_annual_amount == Money(
            FULL_WEEKLY * Decimal("0.01") * (expected_weeks // 9) * WEEKS
        )

    def test_deferral_uplifts_the_protected_slice_too(self) -> None:
        """Increments accrue on the whole weekly amount, protected included."""
        record = record_of(forecast="250.00", protected="20.00", deferral="1")
        entitlement = scheme().entitlement(record, DOB_SPA_67, TODAY)
        increment = Decimal("250.00") * Decimal("0.05")
        expected = (Decimal("20.00") + increment) * WEEKS
        assert entitlement.cpi_uprated_annual_amount == Money(expected)


class TestRateResolution:
    """The full rate comes from the tax year containing ``today`` (§5.3)."""

    def test_past_the_shipped_files_the_rate_carries_forward(self) -> None:
        """The extension never uprates the state pension rate (§5.3)."""
        record = record_of(ni_start=POST_2016_NI_START, qualifying=35)
        future_today = date(2033, 8, 2)
        entitlement = scheme().entitlement(record, DOB_SPA_67, future_today)
        assert entitlement.annual_amount == Money(FULL_WEEKLY * WEEKS)

    def test_without_an_extension_a_future_today_fails(self) -> None:
        """Past shipped coverage, no extension means no answer."""
        bare = UkStatePensionScheme.from_shipped_data()
        record = record_of(ni_start=POST_2016_NI_START, qualifying=35)
        future_today = date(2033, 8, 2)
        with pytest.raises(ValueError, match="no shipped tax-year data"):
            bare.entitlement(record, DOB_SPA_67, future_today)
