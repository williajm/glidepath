"""Tests for the UK state pension scheme (4.3, #97, 11.5; planning §4.13, §5.1).

The official DWP forecast is the only fact route to an amount (§5.1);
a record without one takes the shipped full rate (§4.13). Deferral
expectations are hand-derived from the shipped ``age_rules.toml``:
+1% per 9 whole weeks deferred.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from factories import money_fact
from glidepath.core import Decision, Money, StatePensionRate, StatePensionRecord
from glidepath.regions.uk import UkAgeRules, UkStatePensionError, UkStatePensionScheme
from glidepath.regions.uk.loader import load_tax_year

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

DOB_SPA_67 = date(1970, 6, 15)  # SPA 67 band: reaches SPA on 2037-06-15

WEEKS = Decimal(52)


def scheme() -> UkStatePensionScheme:
    """The scheme over the shipped age-rules data."""
    return UkStatePensionScheme.from_shipped_data()


def record_of(
    *,
    forecast: str | None = None,
    protected: str | None = None,
    deferral: str | Decimal = "0",
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

    def test_a_record_without_a_forecast_takes_no_forecast_entitlement(self) -> None:
        """A forecast-less record is answered by the full-rate route only."""
        uk = scheme()
        record = record_of(deferral="1")
        with pytest.raises(UkStatePensionError, match="takes the full rate"):
            uk.entitlement(record, DOB_SPA_67)


def rate_of(weekly: str, rates_from: date, label: str) -> StatePensionRate:
    """A published full rate built from compact test parameters."""
    return StatePensionRate(
        weekly=Money(Decimal(weekly)), rates_from=rates_from, label=label
    )


class TestFullRateEntitlement:
    """A record without a forecast takes the shipped full rate (§4.13)."""

    def test_shipped_rate_is_the_tax_year_files(self) -> None:
        """The scheme reads each shipped file's published rate."""
        shipped = load_tax_year(2026)
        [rate] = scheme().full_rates
        assert rate.weekly == shipped.state_pension.full_weekly_rate
        assert rate.rates_from == shipped.meta.start_date
        assert rate.label == shipped.meta.tax_year

    def test_amount_is_the_full_rate_from_spa(self) -> None:
        """The full rate, annualised at 52 weeks, from the SPA date."""
        uk = scheme()
        answer = uk.full_rate_entitlement(record_of(), DOB_SPA_67, date(2026, 11, 27))
        weekly = uk.full_rates[0].weekly
        assert answer.rate == uk.full_rates[0]
        assert answer.entitlement.annual_amount == weekly * WEEKS
        assert answer.entitlement.cpi_uprated_annual_amount == Money(Decimal(0))
        assert answer.entitlement.start_date == date(2037, 6, 15)
        assert answer.entitlement.deferral_uplift == 0

    def test_deferral_shifts_the_start_and_earns_the_uplift(self) -> None:
        """Deferral applies to the assumed rate exactly as to a forecast."""
        uk = scheme()
        record = record_of(deferral="1")
        answer = uk.full_rate_entitlement(record, DOB_SPA_67, date(2026, 11, 27))
        forecast = uk.entitlement(record_of(forecast="100", deferral="1"), DOB_SPA_67)
        assert answer.entitlement.start_date == forecast.start_date
        assert answer.entitlement.deferral_uplift == forecast.deferral_uplift

    def test_latest_rate_starting_on_or_before_the_date_wins(self) -> None:
        """A later year's rate applies from its own start date."""
        older = rate_of("100.00", date(2026, 4, 6), "2026/27")
        newer = rate_of("110.00", date(2027, 4, 6), "2027/28")
        uk = UkStatePensionScheme(
            ages=UkAgeRules.from_shipped_data(), full_rates=(older, newer)
        )
        assert (
            uk.full_rate_entitlement(record_of(), DOB_SPA_67, date(2027, 4, 5)).rate
            == older
        )
        assert (
            uk.full_rate_entitlement(record_of(), DOB_SPA_67, date(2027, 4, 6)).rate
            == newer
        )

    def test_a_date_before_every_shipped_rate_is_refused(self) -> None:
        """No published rate yet: the message points to the forecast."""
        uk = scheme()
        record = record_of()
        before = date(2020, 1, 1)
        with pytest.raises(UkStatePensionError, match="check-state-pension"):
            uk.full_rate_entitlement(record, DOB_SPA_67, before)


class _CliffProbe(UkStatePensionScheme):
    """The scheme with its uplift computation exposed for exact day gaps.

    Whole-month deferrals can never land on exactly nine weeks (two
    calendar months span at most 62 days, three at least 89), so the
    statutory 9-week cliff is pinned through the scheme's own uplift
    computation over crafted dates.
    """

    def uplift_after_days(self, days: int) -> Decimal:
        """The uplift fraction for a start ``days`` after SPA."""
        spa = date(2037, 6, 15)
        return self._deferral_uplift(spa, spa + timedelta(days=days))


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

    def test_two_month_deferral_is_eight_weeks_and_earns_nothing(self) -> None:
        """61 days are 8 whole weeks: under the 9-week statutory minimum.

        The shortest deferral that earns anything through the
        whole-month API is 3 months; 2 months sits just under the
        cliff and must yield exactly nil, not a part increment.
        """
        record = record_of(forecast="241.30", deferral=Decimal(2) / Decimal(12))
        entitlement = scheme().entitlement(record, DOB_SPA_67)
        assert entitlement.start_date == date(2037, 8, 15)
        assert (date(2037, 8, 15) - date(2037, 6, 15)).days // 7 == 8
        assert entitlement.deferral_uplift == Decimal(0)

    def test_eight_whole_weeks_earn_nothing(self) -> None:
        """62 days are 8 whole weeks: one day short of the cliff, nil."""
        probe = _CliffProbe(ages=UkAgeRules.from_shipped_data())
        uplift = probe.uplift_after_days(62)
        assert uplift == Decimal(0)

    @pytest.mark.parametrize(
        ("days", "weeks"),
        [
            (63, 9),  # exactly nine whole weeks: the first increment vests
            (69, 9),  # nine weeks six days: part-weeks never count
            (70, 10),  # ten whole weeks: one further ninth of 1%
        ],
    )
    def test_nine_whole_weeks_vest_the_first_increment(
        self, days: int, weeks: int
    ) -> None:
        """The increment vests at exactly 9 whole weeks and steps weekly."""
        probe = _CliffProbe(ages=UkAgeRules.from_shipped_data())
        uplift = probe.uplift_after_days(days)
        expected = Decimal("0.01") * (Decimal(weeks) / Decimal(9))
        assert uplift == expected

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
