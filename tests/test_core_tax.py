"""Invariant tests for the generic tax boundary types (issue 2.3)."""

from decimal import Decimal

import pytest

from glidepath.core import Money, Rate, TaxInput, TaxLine, TaxResidencyId, TaxResult

RESIDENCY = TaxResidencyId("test.region")


def money(amount: str) -> Money:
    """Build ``Money`` from a decimal string."""
    return Money(Decimal(amount))


def make_line(taxed: str = "100", tax: str = "20") -> TaxLine:
    """Build a valid 20% tax line."""
    return TaxLine(
        band="basic", rate=Rate(Decimal("0.20")), taxed=money(taxed), tax=money(tax)
    )


def test_tax_input_accepts_zero_income() -> None:
    """Zero income is a valid assessment input."""
    tax_input = TaxInput(residency=RESIDENCY, non_savings_income=money("0"))
    assert tax_input.non_savings_income == money("0")


def test_tax_input_rejects_negative_income() -> None:
    """Negative gross income is a construction error."""
    with pytest.raises(ValueError, match="non-negative"):
        TaxInput(residency=RESIDENCY, non_savings_income=money("-1"))


def test_tax_line_rejects_empty_band_name() -> None:
    """Band labels are region-supplied and must not be empty."""
    with pytest.raises(ValueError, match="band"):
        TaxLine(
            band="", rate=Rate(Decimal("0.20")), taxed=money("1"), tax=money("0.20")
        )


@pytest.mark.parametrize(
    ("taxed", "tax"),
    [("-1", "0"), ("1", "-0.20")],
)
def test_tax_line_rejects_negative_amounts(taxed: str, tax: str) -> None:
    """Taxed amounts and tax charged must be non-negative."""
    with pytest.raises(ValueError, match="non-negative"):
        TaxLine(
            band="basic", rate=Rate(Decimal("0.20")), taxed=money(taxed), tax=money(tax)
        )


def test_tax_result_accepts_consistent_breakdown() -> None:
    """A result whose total equals the sum of its lines constructs."""
    lines = (make_line(), make_line(taxed="50", tax="10"))
    result = TaxResult(
        tax_due=money("30"),
        taxable_income=money("150"),
        tax_free_allowance=money("10"),
        lines=lines,
    )
    assert result.tax_due == money("30")


def test_tax_result_rejects_mismatched_total() -> None:
    """``tax_due`` must equal the sum of the line taxes."""
    with pytest.raises(ValueError, match="sum of its lines"):
        TaxResult(
            tax_due=money("21"),
            taxable_income=money("100"),
            tax_free_allowance=money("0"),
            lines=(make_line(),),
        )


def test_tax_result_rejects_negative_amounts() -> None:
    """Taxable income and allowance must be non-negative."""
    with pytest.raises(ValueError, match="non-negative"):
        TaxResult(
            tax_due=money("0"),
            taxable_income=money("-1"),
            tax_free_allowance=money("0"),
            lines=(),
        )


def test_empty_breakdown_means_zero_tax() -> None:
    """No lines is valid only with zero tax due."""
    result = TaxResult(
        tax_due=money("0"),
        taxable_income=money("0"),
        tax_free_allowance=money("5"),
        lines=(),
    )
    assert result.tax_due == money("0")
