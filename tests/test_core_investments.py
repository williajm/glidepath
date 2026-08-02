"""Tests for fees and growth application (issue 3.4; planning §5.2 steps 6-7)."""

from decimal import Decimal

import pytest

from glidepath.core import (
    AssetAllocation,
    AssetReturns,
    FeeSchedule,
    Money,
    Rate,
    apply_fees_and_growth,
    period_fee,
)

TYPICAL_FEES = FeeSchedule(
    platform=Rate(Decimal("0.0025")), fund=Rate(Decimal("0.0015"))
)
SIXTY_FORTY = AssetAllocation(equity=Decimal("0.6"), bonds=Decimal("0.4"))
RETURNS = AssetReturns(
    equity=Rate(Decimal("0.05")), bonds=Rate(Decimal("0.01")), cash=Rate(Decimal(0))
)


def test_allocation_accepts_weights_summing_to_one() -> None:
    """A complete three-class split is a valid allocation."""
    allocation = AssetAllocation(
        equity=Decimal("0.5"), bonds=Decimal("0.3"), cash=Decimal("0.2")
    )
    assert allocation.equity + allocation.bonds + allocation.cash == 1


def test_allocation_rejects_weights_not_summing_to_one() -> None:
    """An incomplete split describes no portfolio."""
    with pytest.raises(ValueError, match="must sum to exactly 1"):
        AssetAllocation(equity=Decimal("0.5"), bonds=Decimal("0.3"))


def test_allocation_rejects_negative_weights() -> None:
    """Short positions are not modelled."""
    with pytest.raises(ValueError, match="between 0 and 1"):
        AssetAllocation(equity=Decimal("1.2"), bonds=Decimal("-0.2"))


def test_returns_reject_losses_beyond_everything() -> None:
    """A balance cannot lose more than itself."""
    total_loss = Rate(Decimal(-1))
    below = Rate(Decimal("-1.01"))
    with pytest.raises(ValueError, match="at least -1"):
        AssetReturns(equity=below, bonds=total_loss, cash=total_loss)


def test_portfolio_growth_factor_weights_each_class() -> None:
    """60/40 at +5%/+1% grows by the weighted factor 1.034."""
    assert RETURNS.portfolio_growth_factor(SIXTY_FORTY) == Decimal("1.034")


def test_fee_schedule_combines_platform_and_fund() -> None:
    """The total rate is the sum of the two annual percentages."""
    assert TYPICAL_FEES.total_rate == Rate(Decimal("0.0040"))


def test_fee_schedule_rejects_rates_outside_the_unit_interval() -> None:
    """A negative fee is not a fee."""
    fund = Rate(Decimal("0.0015"))
    with pytest.raises(ValueError, match="between 0 and 1"):
        FeeSchedule(platform=Rate(Decimal("-0.001")), fund=fund)


def test_period_fee_charges_the_average_balance() -> None:
    """0.40% on the mean of 100k opening and 110k post-flow is 420."""
    fee = period_fee(Money(Decimal(100000)), Money(Decimal(110000)), TYPICAL_FEES)
    assert fee == Money(Decimal("420.00"))


def test_period_fee_cannot_exceed_the_available_balance() -> None:
    """A provider cannot take more than the account holds."""
    fee = period_fee(Money(Decimal(1000000)), Money(Decimal(100)), TYPICAL_FEES)
    assert fee == Money(Decimal(100))


def test_period_fee_rejects_negative_balances() -> None:
    """Negative balances violate the wrapper invariant upstream."""
    negative = Money(Decimal(-1))
    positive = Money(Decimal(100))
    with pytest.raises(ValueError, match="must be non-negative"):
        period_fee(negative, positive, TYPICAL_FEES)


def test_fees_apply_before_growth_per_the_operation_order() -> None:
    """The §5.2 golden numbers: fee 420 off 110k, then 3.4% growth.

    Fee = 0.40% x mean(100000, 110000) = 420; growth applies to the
    post-fee 109,580 at the 60/40 weighted factor 1.034, earning
    3,725.72 — growth-first would close on 113,312.52 instead, so these
    exact figures pin the operation order.
    """
    outcome = apply_fees_and_growth(
        opening=Money(Decimal(100000)),
        after_flows=Money(Decimal(110000)),
        fees=TYPICAL_FEES,
        allocation=SIXTY_FORTY,
        returns=RETURNS,
    )
    assert outcome.fee == Money(Decimal("420.00"))
    assert outcome.growth == Money(Decimal("3725.72"))
    assert outcome.closing == Money(Decimal("113305.72"))


def test_growth_can_be_negative_in_a_down_period() -> None:
    """A losing year shrinks the closing balance below the post-fee one."""
    down = AssetReturns(
        equity=Rate(Decimal("-0.20")),
        bonds=Rate(Decimal("-0.05")),
        cash=Rate(Decimal(0)),
    )
    outcome = apply_fees_and_growth(
        opening=Money(Decimal(100000)),
        after_flows=Money(Decimal(100000)),
        fees=TYPICAL_FEES,
        allocation=SIXTY_FORTY,
        returns=down,
    )
    assert outcome.fee == Money(Decimal("400.00"))
    assert outcome.growth == Money(Decimal("-13944.00"))
    assert outcome.closing == Money(Decimal("85656.00"))


def test_outcome_amounts_stay_unquantized() -> None:
    """Ledger rounding happens at period close, not here (planning §4.6)."""
    fees = FeeSchedule(platform=Rate(Decimal("0.00333")), fund=Rate(Decimal(0)))
    outcome = apply_fees_and_growth(
        opening=Money(Decimal(100001)),
        after_flows=Money(Decimal(100000)),
        fees=fees,
        allocation=SIXTY_FORTY,
        returns=RETURNS,
    )
    assert outcome.fee == Money(Decimal("333.001665"))
    assert not outcome.closing.is_penny_exact
