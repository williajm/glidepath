"""Display formatting tests (§4.7: Decimal→display lives in the app layer)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.app import format_money, format_percent, format_recorded, format_value
from glidepath.core import Money, Sex


class TestFormatMoney:
    """Money renders quantized, thousands-separated, sign before symbol."""

    @pytest.mark.parametrize(
        ("amount", "expected"),
        [
            ("0", "£0.00"),
            ("1234.5", "£1,234.50"),
            ("1234567.891", "£1,234,567.89"),
            ("-42.5", "-£42.50"),
        ],
    )
    def test_amounts(self, amount: str, expected: str) -> None:
        """Pounds and pence, exactly two decimal places."""
        assert format_money(Money(Decimal(amount))) == expected


class TestFormatPercent:
    """Fractions render as one-decimal-place percentages (9.13)."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("0", "0.0%"),
            ("1", "100.0%"),
            ("0.95", "95.0%"),
            ("0.005", "0.5%"),
            ("0.6666", "66.7%"),
        ],
    )
    def test_fractions(self, value: str, expected: str) -> None:
        """The success-rate readout convention."""
        assert format_percent(Decimal(value)) == expected


class TestFormatValue:
    """The generic dispatch covers every wrapped value type."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (Money(Decimal(2500)), "£2,500.00"),
            (date(1984, 5, 20), "1984-05-20"),
            (datetime(2026, 8, 1, 12, 0, tzinfo=UTC), "2026-08-01"),
            (Decimal("0.05"), "0.05"),
            (61, "61"),
            ("triple_lock", "triple_lock"),
            (Sex.FEMALE, "Female"),
            ({"a": 1, "b": 2}, "a=1; b=2"),
            ({"rule": "cpi", "floor": Decimal("0.025")}, "rule=cpi; floor=0.025"),
        ],
    )
    def test_values(self, value: object, expected: str) -> None:
        """Each value type has one canonical rendering."""
        assert format_value(value) == expected

    def test_long_structured_values_are_truncated(self) -> None:
        """A big table renders compactly, ending in an ellipsis."""
        table = {f"knot_{index}": Decimal(index) for index in range(40)}
        rendered = format_value(table)
        assert len(rendered) <= 120
        assert rendered.endswith("…")


class TestFormatRecorded:
    """Recorded-on timestamps show as their calendar date."""

    def test_recorded_shows_the_date(self) -> None:
        """The time of day is provenance noise for display."""
        assert format_recorded(datetime(2026, 8, 3, 23, 59, tzinfo=UTC)) == "2026-08-03"
