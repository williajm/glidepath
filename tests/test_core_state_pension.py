"""Tests for the core state pension model (issue 4.3, planning §5.1)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import MappingProxyType

import pytest

from glidepath.core import (
    Decision,
    EngineError,
    Fact,
    Money,
    StatePensionEntitlement,
    StatePensionRecord,
    StatePensionUprating,
    UpratingRule,
    deferral_months,
)

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)

TRIPLE_LOCK_VALUE = MappingProxyType(
    {
        "rule": "triple_lock",
        "floor": Decimal("0.025"),
        "deterministic_cpi_margin": Decimal("0.005"),
    }
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


class TestStatePensionRecordValidation:
    """Internally inconsistent records fail at construction."""

    def test_negative_forecast_is_rejected(self) -> None:
        """The forecast is a non-negative fact."""
        with pytest.raises(ValueError, match="forecast_weekly_amount"):
            record_of(forecast="-1")

    def test_protected_payment_without_a_forecast_is_rejected(self) -> None:
        """A protected payment is a slice of the official forecast."""
        with pytest.raises(ValueError, match="protected_payment"):
            record_of(protected="10", qualifying=35)

    def test_protected_payment_beyond_the_forecast_is_rejected(self) -> None:
        """The slice cannot exceed the forecast it comes from."""
        with pytest.raises(ValueError, match="protected_payment"):
            record_of(forecast="200", protected="250")

    def test_negative_qualifying_years_are_rejected(self) -> None:
        """A negative NI year count is a data error."""
        with pytest.raises(ValueError, match="qualifying_years"):
            record_of(qualifying=-1)

    def test_negative_planned_extra_years_are_rejected(self) -> None:
        """Years still to accrue cannot be negative."""
        with pytest.raises(ValueError, match="planned_extra_years"):
            record_of(forecast="200", extra_years=-1)

    def test_negative_deferral_is_rejected(self) -> None:
        """Deferral cannot run backwards."""
        with pytest.raises(ValueError, match="deferral_years"):
            record_of(forecast="200", deferral="-0.5")

    def test_deferral_must_be_whole_months(self) -> None:
        """A third of a year is not a whole number of months."""
        with pytest.raises(ValueError, match="whole number of months"):
            record_of(forecast="200", deferral="0.3")

    def test_deferral_months_converts_years_to_months(self) -> None:
        """1.75 years of deferral is exactly 21 months."""
        assert deferral_months(record_of(forecast="200", deferral="1.75")) == 21


class TestStatePensionEntitlement:
    """The region's answer is validated at construction."""

    def test_negative_annual_amount_is_rejected(self) -> None:
        """A negative entitlement is a region bug."""
        negative = Money(Decimal(-1))
        zero = Money(Decimal(0))
        start = date(2037, 6, 15)
        with pytest.raises(ValueError, match="annual_amount"):
            StatePensionEntitlement(
                start_date=start,
                annual_amount=negative,
                cpi_uprated_annual_amount=zero,
            )

    def test_negative_cpi_slice_is_rejected(self) -> None:
        """The CPI-only slice is non-negative too."""
        negative = Money(Decimal(-1))
        zero = Money(Decimal(0))
        start = date(2037, 6, 15)
        with pytest.raises(ValueError, match="cpi_uprated_annual_amount"):
            StatePensionEntitlement(
                start_date=start,
                annual_amount=zero,
                cpi_uprated_annual_amount=negative,
            )


class TestStatePensionUprating:
    """Parsing and applying the ``policy.state_pension.uprating`` value."""

    def test_parses_the_shipped_triple_lock_table(self) -> None:
        """The default table becomes a triple-lock proxy with parameters."""
        uprating = StatePensionUprating.from_assumption_value(TRIPLE_LOCK_VALUE)
        assert uprating.rule is UpratingRule.TRIPLE_LOCK
        assert uprating.floor == Decimal("0.025")
        assert uprating.cpi_margin == Decimal("0.005")

    def test_parses_the_bare_cpi_tag(self) -> None:
        """The alternative scenario value is the plain string ``"cpi"``."""
        uprating = StatePensionUprating.from_assumption_value("cpi")
        assert uprating.rule is UpratingRule.CPI

    def test_parses_a_cpi_rule_table(self) -> None:
        """A table form of the CPI rule needs no parameters."""
        uprating = StatePensionUprating.from_assumption_value({"rule": "cpi"})
        assert uprating.rule is UpratingRule.CPI

    def test_triple_lock_rate_is_cpi_plus_margin_with_a_floor(self) -> None:
        """max(CPI + margin, floor): the deterministic proxy of §7."""
        uprating = StatePensionUprating.from_assumption_value(TRIPLE_LOCK_VALUE)
        assert uprating.annual_rate(Decimal("0.02")) == Decimal("0.025")
        assert uprating.annual_rate(Decimal("0.04")) == Decimal("0.045")
        assert uprating.annual_rate(Decimal("0.01")) == Decimal("0.025")

    def test_cpi_rule_tracks_cpi(self) -> None:
        """The CPI rule uprates by CPI, floor-free."""
        uprating = StatePensionUprating.from_assumption_value("cpi")
        assert uprating.annual_rate(Decimal("0.02")) == Decimal("0.02")

    def test_unknown_rule_is_rejected(self) -> None:
        """A rule outside the known set fails loudly."""
        with pytest.raises(EngineError, match="unknown rule"):
            StatePensionUprating.from_assumption_value("double_lock")

    def test_unknown_table_keys_are_rejected(self) -> None:
        """Extra keys in the table are configuration errors."""
        value = {"rule": "cpi", "surprise": Decimal(1)}
        with pytest.raises(EngineError, match="unknown keys"):
            StatePensionUprating.from_assumption_value(value)

    def test_triple_lock_requires_its_parameters(self) -> None:
        """The proxy cannot run without a floor and a margin."""
        value = {"rule": "triple_lock"}
        with pytest.raises(EngineError, match="floor and deterministic_cpi_margin"):
            StatePensionUprating.from_assumption_value(value)

    def test_parameters_on_the_cpi_rule_are_rejected(self) -> None:
        """A floor on the CPI rule is contradictory."""
        value = {"rule": "cpi", "floor": Decimal("0.025")}
        with pytest.raises(EngineError, match="floor and deterministic_cpi_margin"):
            StatePensionUprating.from_assumption_value(value)

    def test_non_table_values_are_rejected(self) -> None:
        """A number is not an uprating policy."""
        value = Decimal("0.025")
        with pytest.raises(EngineError, match="rule tag or table"):
            StatePensionUprating.from_assumption_value(value)

    def test_non_string_rule_is_rejected(self) -> None:
        """The rule key must hold a string tag."""
        value = {"rule": Decimal(1)}
        with pytest.raises(EngineError, match="expected a string tag"):
            StatePensionUprating.from_assumption_value(value)

    def test_non_decimal_parameter_is_rejected(self) -> None:
        """Numeric parameters must be Decimal (never float)."""
        value = {
            "rule": "triple_lock",
            "floor": 0.025,
            "deterministic_cpi_margin": 0.005,
        }
        with pytest.raises(EngineError, match="expected a Decimal"):
            StatePensionUprating.from_assumption_value(value)
