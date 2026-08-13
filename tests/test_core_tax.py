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
    negative = money("-1")
    with pytest.raises(ValueError, match="non-negative"):
        TaxInput(residency=RESIDENCY, non_savings_income=negative)


def test_tax_input_relief_at_source_defaults_to_zero() -> None:
    """With no contributions stated, nothing extends the assessment."""
    tax_input = TaxInput(residency=RESIDENCY, non_savings_income=money("100"))
    assert tax_input.relief_at_source_contributions == money("0")


def test_tax_input_rejects_negative_relief_at_source() -> None:
    """Negative relief-at-source contributions are a construction error."""
    income = money("100")
    negative = money("-1")
    with pytest.raises(ValueError, match="relief_at_source"):
        TaxInput(
            residency=RESIDENCY,
            non_savings_income=income,
            relief_at_source_contributions=negative,
        )


def test_tax_line_rejects_empty_band_name() -> None:
    """Band labels are region-supplied and must not be empty."""
    rate = Rate(Decimal("0.20"))
    taxed = money("1")
    tax = money("0.20")
    with pytest.raises(ValueError, match="band"):
        TaxLine(band="", rate=rate, taxed=taxed, tax=tax)


@pytest.mark.parametrize(
    ("taxed", "tax", "message"),
    [("-1", "0", "non-negative"), ("1", "-0.20", "reducer")],
)
def test_tax_line_rejects_negative_amounts(taxed: str, tax: str, message: str) -> None:
    """Negative taxed income never; negative tax only on a reducer line."""
    rate = Rate(Decimal("0.20"))
    taxed_amount = money(taxed)
    tax_amount = money(tax)
    with pytest.raises(ValueError, match=message):
        TaxLine(band="basic", rate=rate, taxed=taxed_amount, tax=tax_amount)


def test_tax_line_accepts_a_no_income_reducer() -> None:
    """A statutory tax reducer: negative tax on a line covering no income."""
    line = TaxLine(
        band="marriage_allowance",
        rate=Rate(Decimal("0.20")),
        taxed=money("0"),
        tax=money("-252"),
    )
    assert line.tax == money("-252")


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
    mismatched_total = money("21")
    taxable = money("100")
    allowance = money("0")
    lines = (make_line(),)
    with pytest.raises(ValueError, match="sum of its lines"):
        TaxResult(
            tax_due=mismatched_total,
            taxable_income=taxable,
            tax_free_allowance=allowance,
            lines=lines,
        )


def test_tax_result_rejects_negative_amounts() -> None:
    """Taxable income and allowance must be non-negative."""
    zero = money("0")
    negative = money("-1")
    with pytest.raises(ValueError, match="non-negative"):
        TaxResult(
            tax_due=zero,
            taxable_income=negative,
            tax_free_allowance=zero,
            lines=(),
        )


def test_tax_result_rejects_negative_tax_due() -> None:
    """A reducer may zero the liability but never turn it negative."""
    negative = money("-1")
    taxable = money("100")
    allowance = money("0")
    with pytest.raises(ValueError, match="tax_due must be non-negative"):
        TaxResult(
            tax_due=negative,
            taxable_income=taxable,
            tax_free_allowance=allowance,
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
