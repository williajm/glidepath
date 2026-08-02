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
    state_pension_uprating,
)

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)
TODAY = date(2026, 8, 2)

DOB_SPA_67 = date(1970, 6, 15)  # SPA 67 band: reaches SPA on 2037-06-15
POST_2016_NI_START = date(2016, 4, 6)

FULL_WEEKLY = Decimal("241.30")
WEEKS = Decimal(52)


def scheme() -> UkStatePensionScheme:
    """The scheme over shipped data, extension and uprating configured."""
    assumptions = default_assumption_set()
    return UkStatePensionScheme.from_shipped_data(
        future_years_extension(assumptions),
        state_pension_uprating(assumptions),
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
    """Deferral shifts the start and earns per-week increments (§6)."""

    def test_one_year_deferral_accrues_per_whole_week(self) -> None:
        """52 whole weeks earn 52 ninths of 1% — just under 5.8% (§6).

        The uplift is returned as a fraction: it applies to the rate
        payable at claim, which only the engine knows (upratings earned
        during deferment included).
        """
        record = record_of(forecast="241.30", deferral="1")
        entitlement = scheme().entitlement(record, DOB_SPA_67, TODAY)
        assert entitlement.start_date == date(2038, 6, 15)
        assert entitlement.annual_amount == Money(FULL_WEEKLY * WEEKS)
        assert entitlement.cpi_uprated_annual_amount == Money(Decimal(0))
        expected = Decimal("0.01") * (Decimal(52) / Decimal(9))
        assert entitlement.deferral_uplift == expected

    def test_three_month_deferral_counts_thirteen_weeks(self) -> None:
        """92 days are 13 whole weeks: past the 9-week minimum, 13/9%."""
        record = record_of(forecast="241.30", deferral="0.25")
        entitlement = scheme().entitlement(record, DOB_SPA_67, TODAY)
        assert entitlement.start_date == date(2037, 9, 15)
        assert (date(2037, 9, 15) - date(2037, 6, 15)).days // 7 == 13
        expected = Decimal("0.01") * (Decimal(13) / Decimal(9))
        assert entitlement.deferral_uplift == expected

    def test_no_deferral_earns_no_uplift(self) -> None:
        """Zero weeks is under the 9-week statutory minimum."""
        record = record_of(forecast="241.30")
        entitlement = scheme().entitlement(record, DOB_SPA_67, TODAY)
        assert entitlement.deferral_uplift == Decimal(0)

    def test_deferral_leaves_the_protected_slice_intact(self) -> None:
        """Deferral changes the uplift fraction, never the slices.

        The uplift applies to the whole rate at claim — protected
        slice included — but applying it is the engine's job.
        """
        record = record_of(forecast="250.00", protected="20.00", deferral="1")
        entitlement = scheme().entitlement(record, DOB_SPA_67, TODAY)
        assert entitlement.annual_amount == Money(Decimal("230.00") * WEEKS)
        assert entitlement.cpi_uprated_annual_amount == Money(Decimal("20.00") * WEEKS)
        expected = Decimal("0.01") * (Decimal(52) / Decimal(9))
        assert entitlement.deferral_uplift == expected


class TestRateResolution:
    """The full rate comes from the tax year containing ``today`` (§5.3)."""

    def test_past_the_shipped_files_the_rate_steps_forward(self) -> None:
        """A 2033 run start gets seven whole April upratings.

        The extension carries the rate untouched (§5.3), so the scheme
        itself steps it forward — one whole uprating per intervening
        tax year at the triple-lock proxy rate max(2% + 0.5%, 2.5%).
        """
        record = record_of(ni_start=POST_2016_NI_START, qualifying=35)
        future_today = date(2033, 8, 2)
        entitlement = scheme().entitlement(record, DOB_SPA_67, future_today)
        stepped = FULL_WEEKLY * (Decimal(35) / Decimal(35)) * Decimal("1.025") ** 7
        assert entitlement.annual_amount == Money(stepped * WEEKS)

    def test_past_the_shipped_files_without_an_uprating_policy_fails(self) -> None:
        """Stepping needs the uprating policy; stale rates are refused."""
        extension_only = UkStatePensionScheme.from_shipped_data(
            future_years_extension(default_assumption_set())
        )
        record = record_of(ni_start=POST_2016_NI_START, qualifying=35)
        future_today = date(2033, 8, 2)
        with pytest.raises(UkStatePensionError, match="uprating policy"):
            extension_only.entitlement(record, DOB_SPA_67, future_today)

    def test_a_forecast_needs_no_rate_data(self) -> None:
        """The forecast is the fact: no shipped rate or stepping involved."""
        bare = UkStatePensionScheme.from_shipped_data()
        record = record_of(forecast="230.25")
        entitlement = bare.entitlement(record, DOB_SPA_67, date(2033, 8, 2))
        assert entitlement.annual_amount == Money(Decimal("230.25") * WEEKS)

    def test_without_an_extension_a_future_today_fails(self) -> None:
        """Past shipped coverage, no extension means no answer."""
        bare = UkStatePensionScheme.from_shipped_data()
        record = record_of(ni_start=POST_2016_NI_START, qualifying=35)
        future_today = date(2033, 8, 2)
        with pytest.raises(ValueError, match="no shipped tax-year data"):
            bare.entitlement(record, DOB_SPA_67, future_today)
