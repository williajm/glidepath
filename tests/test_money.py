"""Tests for the Money/Rate rounding policy (planning §4.6, issue 1.1)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from glidepath.core.money import Money, Rate

amounts = st.decimals(
    min_value=Decimal("-1e12"),
    max_value=Decimal("1e12"),
    allow_nan=False,
    allow_infinity=False,
)


@given(amount=amounts)
def test_quantized_amount_has_penny_exponent(amount: Decimal) -> None:
    """Quantization always lands on exactly two decimal places."""
    assert Money(amount).quantized().amount.as_tuple().exponent == -2


@given(amount=amounts)
def test_quantized_is_idempotent(amount: Decimal) -> None:
    """Quantizing twice equals quantizing once."""
    once = Money(amount).quantized()
    assert once.quantized() == once


@given(amount=amounts)
def test_quantized_moves_at_most_half_a_penny(amount: Decimal) -> None:
    """Rounding to the nearest penny never moves the value more than 0.005."""
    quantized = Money(amount).quantized()
    assert (quantized.amount - amount).copy_abs() <= Decimal("0.005")


@given(amount=amounts)
def test_quantized_result_is_penny_exact(amount: Decimal) -> None:
    """A quantized amount reports itself penny-exact."""
    assert Money(amount).quantized().is_penny_exact


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.005", "0.00"),  # tie rounds to even (0)
        ("0.015", "0.02"),  # tie rounds to even (2)
        ("0.025", "0.02"),  # tie rounds to even (2)
        ("0.035", "0.04"),  # tie rounds to even (4)
        ("-0.005", "0.00"),
        ("-0.015", "-0.02"),
        ("2.675", "2.68"),
        ("1.004", "1.00"),
        ("1.006", "1.01"),
    ],
)
def test_ties_round_half_even(raw: str, expected: str) -> None:
    """Ledger rounding is banker's rounding, not half-up (planning §4.6)."""
    assert Money(Decimal(raw)).quantized() == Money(Decimal(expected))


@given(amount=amounts)
def test_quantized_zero_is_never_signed(amount: Decimal) -> None:
    """A zero ledger write has the one unsigned representation.

    A sub-penny negative residual would otherwise quantize to
    ``Decimal("-0.00")`` — numerically zero, but serialized with a
    minus sign into reports and golden outputs.
    """
    quantized = Money(amount).quantized()
    if quantized.amount == 0:
        assert not quantized.amount.is_signed()


def test_negative_residual_quantizes_to_unsigned_zero() -> None:
    """A tiny negative residual rounds to plain ``0.00``, not ``-0.00``."""
    assert str(Money(Decimal("-0.0001")).quantized().amount) == "0.00"


@given(left=amounts, right=amounts)
def test_addition_and_subtraction_are_exact(left: Decimal, right: Decimal) -> None:
    """Intermediate arithmetic never rounds (planning §4.6)."""
    total = Money(left) + Money(right)
    assert total.amount == left + right
    difference = Money(left) - Money(right)
    assert difference.amount == left - right


def test_multiplication_stays_unquantized() -> None:
    """Scaling by a factor keeps sub-penny precision until a ledger write."""
    scaled = Money(Decimal(100)) * Decimal("0.0333")
    assert scaled.amount == Decimal("3.3300")
    assert Decimal("0.0333") * Money(Decimal(100)) == scaled


def test_negation_and_ordering() -> None:
    """Money negates exactly and orders by amount."""
    assert -Money(Decimal("1.50")) == Money(Decimal("-1.50"))
    assert Money(Decimal(1)) < Money(Decimal(2))


def test_sub_penny_amount_is_not_penny_exact() -> None:
    """Sub-penny precision is detectable so ledger writes can assert on it."""
    assert not Money(Decimal("1.005")).is_penny_exact
    assert Money(Decimal("1.5")).is_penny_exact


def test_money_rejects_float() -> None:
    """Money is Decimal, never float (repo rule)."""
    with pytest.raises(TypeError, match="must be Decimal"):
        Money(0.1)  # type: ignore[arg-type]


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
def test_money_rejects_non_finite(raw: str) -> None:
    """NaN and infinities are invalid amounts."""
    amount = Decimal(raw)
    with pytest.raises(ValueError, match="must be finite"):
        Money(amount)


def test_rate_growth_factor_and_of() -> None:
    """A rate exposes its growth factor and scales money exactly."""
    rate = Rate(Decimal("0.05"))
    assert rate.growth_factor == Decimal("1.05")
    assert rate.of(Money(Decimal(200))) == Money(Decimal("10.00"))


def test_rate_rejects_float_and_non_finite() -> None:
    """Rates are Decimal and finite, like money."""
    with pytest.raises(TypeError, match="must be Decimal"):
        Rate(0.05)  # type: ignore[arg-type]
    nan = Decimal("NaN")
    with pytest.raises(ValueError, match="must be finite"):
        Rate(nan)
