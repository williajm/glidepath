"""Tests for glidepath.core."""

from decimal import Decimal

import pytest

from glidepath.core import project_balance


def test_project_balance_compounds_annually() -> None:
    """A balance grows by the annual rate, compounded per year."""
    result = project_balance(Decimal(1000), Decimal("0.05"), 2)
    assert result == Decimal("1102.50")


def test_project_balance_zero_years_is_identity() -> None:
    """Zero years of growth returns the starting balance unchanged."""
    assert project_balance(Decimal(500), Decimal("0.10"), 0) == Decimal(500)


def test_project_balance_rejects_negative_years() -> None:
    """Negative projection horizons are invalid."""
    with pytest.raises(ValueError, match="non-negative"):
        project_balance(Decimal(1), Decimal(0), -1)
