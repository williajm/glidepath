"""Tests for the UK state pension scheme (issues 4.3 and #97; planning §5.1, §6).

The official DWP forecast is the fact and the only route to an amount
(§5.1). Deferral expectations are hand-derived from the shipped
``age_rules.toml``: +1% per 9 whole weeks deferred.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.core import Decision, Fact, Money, StatePensionRecord
from glidepath.regions.uk import UkStatePensionError, UkStatePensionScheme

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)

DOB_SPA_67 = date(1970, 6, 15)  # SPA 67 band: reaches SPA on 2037-06-15

WEEKS = Decimal(52)


def scheme() -> UkStatePensionScheme:
    """The scheme over the shipped age-rules data."""
    return UkStatePensionScheme.from_shipped_data()


def money_fact(amount: str) -> Fact[Money]:
    """A user-stated weekly amount."""
    return Fact(value=Money(Decimal(amount)), as_of=AS_OF, recorded_on=RECORDED)


def record_of(
    *,
    forecast: str | None = None,
    protected: str | None = None,
    deferral: str = "0",
) -> StatePensionRecord:
    """A state pension record built from compact test parameters."""
    return StatePensionRecord(
        forecast_weekly_amount=None if forecast is None else money_fact(forecast),
        protected_payment=None if protected is None else money_fact(protected),
        deferral_years=Decision(value=Decimal(deferral), recorded_on=RECORDED),
    )


class TestForecastIsTheOnlyRoute:
    """The official forecast is the fact and the only route (§5.1)."""

    def test_forecast_sets_the_amount(self) -> None:
        """The entitlement is the forecast, annualised at 52 weeks."""
        record = record_of(forecast="230.25")
        entitlement = scheme().entitlement(record, DOB_SPA_67)
        assert entitlement.annual_amount == Money(Decimal("230.25") * WEEKS)
        assert entitlement.cpi_uprated_annual_amount == Money(Decimal(0))

    def test_protected_payment_splits_into_the_cpi_slice(self) -> None:
        """The protected slice uprates by CPI only, so it is split out."""
        record = record_of(forecast="250.00", protected="20.00")
        entitlement = scheme().entitlement(record, DOB_SPA_67)
        assert entitlement.annual_amount == Money(Decimal("230.00") * WEEKS)
        assert entitlement.cpi_uprated_annual_amount == Money(Decimal("20.00") * WEEKS)

    def test_start_date_is_the_spa_date(self) -> None:
        """Without deferral the entitlement starts exactly at SPA."""
        record = record_of(forecast="230.25")
        entitlement = scheme().entitlement(record, DOB_SPA_67)
        assert entitlement.start_date == date(2037, 6, 15)

    def test_a_record_without_a_forecast_is_refused(self) -> None:
        """No forecast, no answer — the demand names where to get one.

        A pre-#97 plan that relied on the retired qualifying-years
        derivation migrates to exactly this record shape, so the
        message is the migration's upgrade path too.
        """
        uk = scheme()
        record = record_of(deferral="1")
        with pytest.raises(UkStatePensionError, match="check-state-pension"):
            uk.entitlement(record, DOB_SPA_67)


class TestDeferral:
    """Deferral shifts the start and earns per-week increments (§6)."""

    def test_one_year_deferral_accrues_per_whole_week(self) -> None:
        """52 whole weeks earn 52 ninths of 1% — just under 5.8% (§6).

        The uplift is returned as a fraction: it applies to the rate
        payable at claim, which only the engine knows (upratings earned
        during deferment included).
        """
        record = record_of(forecast="241.30", deferral="1")
        entitlement = scheme().entitlement(record, DOB_SPA_67)
        assert entitlement.start_date == date(2038, 6, 15)
        assert entitlement.annual_amount == Money(Decimal("241.30") * WEEKS)
        assert entitlement.cpi_uprated_annual_amount == Money(Decimal(0))
        expected = Decimal("0.01") * (Decimal(52) / Decimal(9))
        assert entitlement.deferral_uplift == expected

    def test_three_month_deferral_counts_thirteen_weeks(self) -> None:
        """92 days are 13 whole weeks: past the 9-week minimum, 13/9%."""
        record = record_of(forecast="241.30", deferral="0.25")
        entitlement = scheme().entitlement(record, DOB_SPA_67)
        assert entitlement.start_date == date(2037, 9, 15)
        assert (date(2037, 9, 15) - date(2037, 6, 15)).days // 7 == 13
        expected = Decimal("0.01") * (Decimal(13) / Decimal(9))
        assert entitlement.deferral_uplift == expected

    def test_no_deferral_earns_no_uplift(self) -> None:
        """Zero weeks is under the 9-week statutory minimum."""
        record = record_of(forecast="241.30")
        entitlement = scheme().entitlement(record, DOB_SPA_67)
        assert entitlement.deferral_uplift == Decimal(0)

    def test_deferral_leaves_the_protected_slice_intact(self) -> None:
        """Deferral changes the uplift fraction, never the slices.

        The uplift applies to the whole rate at claim — protected
        slice included — but applying it is the engine's job.
        """
        record = record_of(forecast="250.00", protected="20.00", deferral="1")
        entitlement = scheme().entitlement(record, DOB_SPA_67)
        assert entitlement.annual_amount == Money(Decimal("230.00") * WEEKS)
        assert entitlement.cpi_uprated_annual_amount == Money(Decimal("20.00") * WEEKS)
        expected = Decimal("0.01") * (Decimal(52) / Decimal(9))
        assert entitlement.deferral_uplift == expected
