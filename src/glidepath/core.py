"""Core financial primitives for the glidepath engine.

Walking-skeleton module: proves the toolchain (ruff ALL, mypy --strict,
pytest + coverage) end to end. Money is always ``Decimal``, never float.
"""

from decimal import Decimal


def project_balance(balance: Decimal, annual_rate: Decimal, years: int) -> Decimal:
    """Project a balance forward under compound annual growth.

    Args:
        balance: Starting balance in currency units.
        annual_rate: Annual growth rate as a fraction (e.g. ``Decimal("0.05")``).
        years: Whole years to project forward; must be non-negative.

    Returns:
        The balance after ``years`` years of compounding.

    Raises:
        ValueError: If ``years`` is negative.
    """
    if years < 0:
        msg = "years must be non-negative"
        raise ValueError(msg)
    return balance * (Decimal(1) + annual_rate) ** years
