"""Asset allocation, fees, and growth application (roadmap 3.4; planning §5.2).

Implements steps 6 and 7 of the §5.2 operation order for one wrapper and
one period:

- **Fees** (step 6): platform + fund annual percentages applied to the
  *average* balance — the mean of the opening balance and the balance
  after the period's flows — approximating intra-year accrual acceptably
  at annual resolution. A fee can never take more than the account
  holds.
- **Growth** (step 7): the period's asset-class returns applied to the
  post-fee balance through the wrapper's asset allocation. Fees before
  growth is part of the spec, so :func:`apply_fees_and_growth` performs
  both in that order by construction.

Asset classes are the three the assumption catalogue prices (planning
§7: ``returns.equity.real``, ``returns.bonds.real``, ``returns.cash.real``)
— an economic vocabulary, not a region one. All amounts stay unquantized;
the ledger rounds at period close (step 8, planning §4.6).
"""

from dataclasses import dataclass
from decimal import Decimal

from glidepath.core.money import Money, Rate

_ZERO = Decimal(0)
_ONE = Decimal(1)
_HALF = Decimal("0.5")
_ZERO_MONEY = Money(_ZERO)
_MINUS_ONE = Decimal(-1)


@dataclass(frozen=True, slots=True)
class AssetAllocation:
    """Portfolio weights over the three priced asset classes.

    Weights are exact ``Decimal`` fractions that must sum to exactly 1 —
    an allocation is a complete description of where a balance sits, not
    a preference ranking.
    """

    equity: Decimal
    bonds: Decimal
    cash: Decimal = _ZERO

    def __post_init__(self) -> None:
        """Require weights in [0, 1] summing to exactly 1."""
        weights = (self.equity, self.bonds, self.cash)
        if any(not _ZERO <= weight <= _ONE for weight in weights):
            msg = "AssetAllocation weights must lie between 0 and 1"
            raise ValueError(msg)
        if sum(weights) != _ONE:
            msg = "AssetAllocation weights must sum to exactly 1"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class AssetReturns:
    """One period's nominal return per asset class.

    Rates may be negative (a loss) but never below -100%: a balance
    cannot lose more than itself.
    """

    equity: Rate
    bonds: Rate
    cash: Rate

    def __post_init__(self) -> None:
        """Reject returns below -100%."""
        rates = (self.equity, self.bonds, self.cash)
        if any(rate.value < _MINUS_ONE for rate in rates):
            msg = "AssetReturns rates must be at least -1 (a total loss)"
            raise ValueError(msg)

    def portfolio_growth_factor(self, allocation: AssetAllocation) -> Decimal:
        """The allocation-weighted growth multiplier for one period."""
        return (
            allocation.equity * self.equity.growth_factor
            + allocation.bonds * self.bonds.growth_factor
            + allocation.cash * self.cash.growth_factor
        )


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """One wrapper's annual percentage fees (planning §5.1).

    ``platform`` is the platform/provider charge, ``fund`` the fund OCF;
    both are annual fractions of the balance, charged together on the
    period's average balance (§5.2 step 6).
    """

    platform: Rate
    fund: Rate

    def __post_init__(self) -> None:
        """Require each fee rate to be a fraction in [0, 1]."""
        rates = (self.platform, self.fund)
        if any(not _ZERO <= rate.value <= _ONE for rate in rates):
            msg = "FeeSchedule rates must lie between 0 and 1"
            raise ValueError(msg)

    @property
    def total_rate(self) -> Rate:
        """Platform and fund combined, as one annual rate."""
        return Rate(self.platform.value + self.fund.value)


def period_fee(
    opening: Money,
    after_flows: Money,
    fees: FeeSchedule,
    year_fraction: Decimal = _ONE,
) -> Money:
    """The period's fee: the total rate on the average balance (§5.2 step 6).

    The average balance is the mean of the opening balance and the
    balance after the period's flows (contributions and withdrawals,
    steps 2-4). ``year_fraction`` scales the annual rate linearly for a
    partial first/last period (the roadmap-4.6 convention of planning
    §5.2); a whole period passes 1. The fee is capped at ``after_flows``
    — a provider cannot charge more than the account holds.

    Raises:
        ValueError: If either balance is negative, or ``year_fraction``
            lies outside [0, 1].
    """
    if opening < _ZERO_MONEY or after_flows < _ZERO_MONEY:
        msg = "balances must be non-negative"
        raise ValueError(msg)
    if not _ZERO <= year_fraction <= _ONE:
        msg = "year_fraction must lie between 0 and 1"
        raise ValueError(msg)
    average = (opening + after_flows) * _HALF
    return min(fees.total_rate.of(average) * year_fraction, after_flows)


@dataclass(frozen=True, slots=True)
class FeesAndGrowthOutcome:
    """Steps 6 and 7 of §5.2 resolved for one wrapper and one period.

    ``fee`` is the charge taken (step 6), ``growth`` the return earned on
    the post-fee balance (step 7, negative in a down period), and
    ``closing`` the resulting balance — all unquantized; the ledger
    rounds at period close (step 8).
    """

    fee: Money
    growth: Money
    closing: Money


def apply_fees_and_growth(
    opening: Money,
    after_flows: Money,
    fees: FeeSchedule,
    allocation: AssetAllocation,
    returns: AssetReturns,
) -> FeesAndGrowthOutcome:
    """Apply one period's fees then growth, in the §5.2 operation order.

    The fee (step 6) comes off before returns apply (step 7), so growth
    compounds only the post-fee balance — the order is enforced by
    construction, not by caller discipline.

    Raises:
        ValueError: If either balance is negative.
    """
    fee = period_fee(opening, after_flows, fees)
    post_fee = after_flows - fee
    factor = returns.portfolio_growth_factor(allocation)
    growth = Money(post_fee.amount * (factor - _ONE))
    return FeesAndGrowthOutcome(fee=fee, growth=growth, closing=post_fee + growth)
