"""Money and Rate value types implementing the engine rounding policy.

Policy (docs/planning.md §4.6): all monetary arithmetic is exact
``Decimal`` — never float. Intermediate values stay unquantized; money is
quantized to whole pennies with ``ROUND_HALF_EVEN`` at every ledger write
via :meth:`Money.quantized`. Rates and factors are never quantized.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

_PENNY = Decimal("0.01")


def _require_finite_decimal(value: Decimal, field_name: str) -> None:
    """Reject non-Decimal and non-finite amounts at construction time."""
    if not isinstance(value, Decimal):
        # Reachable from untyped callers (decoded plan JSON, region TOML)
        # even though the annotation lets mypy prove the branch dead.
        msg = f"{field_name} must be Decimal, got {type(value).__name__}"  # type: ignore[unreachable]
        raise TypeError(msg)
    if not value.is_finite():
        msg = f"{field_name} must be finite"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True, order=True)
class Money:
    """An exact monetary amount in the plan's single currency.

    ``amount`` may carry more precision than a penny between operations;
    ledger writes call :meth:`quantized` (planning §4.6).
    """

    amount: Decimal

    def __post_init__(self) -> None:
        """Reject non-Decimal and non-finite amounts."""
        _require_finite_decimal(self.amount, "Money.amount")

    def __add__(self, other: Money) -> Money:
        """Return the exact (unquantized) sum."""
        return Money(self.amount + other.amount)

    def __sub__(self, other: Money) -> Money:
        """Return the exact (unquantized) difference."""
        return Money(self.amount - other.amount)

    def __neg__(self) -> Money:
        """Return the amount negated."""
        return Money(-self.amount)

    def __mul__(self, factor: Decimal) -> Money:
        """Scale by a ``Decimal`` factor; the result stays unquantized."""
        return Money(self.amount * factor)

    def __rmul__(self, factor: Decimal) -> Money:
        """Support ``Decimal * Money``."""
        return Money(factor * self.amount)

    def quantized(self) -> Money:
        """Round to whole pennies with banker's rounding.

        This is the ledger-write rounding step of planning §4.6:
        ``ROUND_HALF_EVEN`` to two decimal places. Intermediate arithmetic
        must stay exact; only ledger writes round. A sub-penny negative
        residual quantizes to ``Decimal("-0.00")`` — numerically zero but
        serialized with a minus sign — so zero is normalized to the one
        positive representation.

        Returns:
            A new ``Money`` whose amount has exponent ``-2``.
        """
        rounded = self.amount.quantize(_PENNY, rounding=ROUND_HALF_EVEN)
        if rounded == 0:
            rounded = rounded.copy_abs()
        return Money(rounded)

    @property
    def is_penny_exact(self) -> bool:
        """Whether the amount is representable in whole pennies."""
        return self.amount == self.amount.quantize(_PENNY, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True, slots=True, order=True)
class Rate:
    """An annual rate expressed as an exact fraction; never quantized.

    ``Rate(Decimal("0.05"))`` is 5% per year (planning §4.6: rates and
    factors carry full precision end to end).
    """

    value: Decimal

    def __post_init__(self) -> None:
        """Reject non-Decimal and non-finite rates."""
        _require_finite_decimal(self.value, "Rate.value")

    @property
    def growth_factor(self) -> Decimal:
        """``1 + value``: the multiplier for one period's growth."""
        return Decimal(1) + self.value

    def of(self, money: Money) -> Money:
        """Return ``money`` scaled by this rate, e.g. one year's interest.

        The result is unquantized; callers round at ledger writes.
        """
        return Money(money.amount * self.value)
