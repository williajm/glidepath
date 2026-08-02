"""Tests for the Wrapper entity and wrapper-boundary types (issue 3.1)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.core import (
    ContributionTaxTreatment,
    Fact,
    GrowthTaxTreatment,
    Money,
    Rate,
    WithdrawalTaxTreatment,
    Wrapper,
    WrapperKindId,
    WrapperTaxTreatment,
    new_entity_id,
)

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
KIND = WrapperKindId("test.dc")


def money_fact(amount: str) -> Fact[Money]:
    """A user-stated balance fact."""
    return Fact(
        value=Money(Decimal(amount)), as_of=date(2026, 8, 1), recorded_on=RECORDED
    )


def test_wrapper_holds_balances_as_facts() -> None:
    """Balances are user-stated facts; crystallised defaults to None."""
    wrapper = Wrapper(id=new_entity_id(), kind=KIND, balance=money_fact("125000"))
    assert wrapper.balance.value == Money(Decimal(125000))
    assert wrapper.crystallised_balance is None


def test_wrapper_carries_crystallised_balance() -> None:
    """An already-in-drawdown user is modellable (planning §5.1)."""
    wrapper = Wrapper(
        id=new_entity_id(),
        kind=KIND,
        balance=money_fact("0"),
        crystallised_balance=money_fact("80000"),
    )
    assert wrapper.crystallised_balance is not None
    assert wrapper.crystallised_balance.value == Money(Decimal(80000))


def test_wrapper_rejects_negative_balance() -> None:
    """A negative balance is a data-entry error, not a plan."""
    wrapper_id = new_entity_id()
    negative = money_fact("-1")
    with pytest.raises(ValueError, match="balance must be non-negative"):
        Wrapper(id=wrapper_id, kind=KIND, balance=negative)


def test_wrapper_rejects_negative_crystallised_balance() -> None:
    """The crystallised balance is bounded the same way."""
    wrapper_id = new_entity_id()
    balance = money_fact("1000")
    negative = money_fact("-1")
    with pytest.raises(ValueError, match="crystallised_balance must be non-negative"):
        Wrapper(
            id=wrapper_id,
            kind=KIND,
            balance=balance,
            crystallised_balance=negative,
        )


def test_partially_tax_free_treatment_carries_its_fraction() -> None:
    """The EET shape: relieved in, free growth, fraction free on the way out."""
    treatment = WrapperTaxTreatment(
        contributions=ContributionTaxTreatment.TAX_RELIEVED,
        growth=GrowthTaxTreatment.TAX_FREE,
        withdrawals=WithdrawalTaxTreatment.PARTIALLY_TAX_FREE,
        tax_free_fraction=Rate(Decimal("0.25")),
    )
    assert treatment.tax_free_fraction == Rate(Decimal("0.25"))


def test_partially_tax_free_treatment_requires_a_fraction() -> None:
    """PARTIALLY_TAX_FREE without a fraction is unanswerable."""
    with pytest.raises(ValueError, match="require a tax_free_fraction"):
        WrapperTaxTreatment(
            contributions=ContributionTaxTreatment.TAX_RELIEVED,
            growth=GrowthTaxTreatment.TAX_FREE,
            withdrawals=WithdrawalTaxTreatment.PARTIALLY_TAX_FREE,
        )


def test_fully_tax_free_treatment_rejects_a_fraction() -> None:
    """A fraction on a non-partial treatment is a contradiction."""
    fraction = Rate(Decimal("0.25"))
    with pytest.raises(ValueError, match="do not take a tax_free_fraction"):
        WrapperTaxTreatment(
            contributions=ContributionTaxTreatment.FROM_TAXED_INCOME,
            growth=GrowthTaxTreatment.TAX_FREE,
            withdrawals=WithdrawalTaxTreatment.TAX_FREE,
            tax_free_fraction=fraction,
        )


@pytest.mark.parametrize("fraction", ["0", "1", "-0.1", "1.5"])
def test_tax_free_fraction_must_be_a_proper_fraction(fraction: str) -> None:
    """0 or 1 must be stated as TAX_FREE / TAXABLE_INCOME instead."""
    rate = Rate(Decimal(fraction))
    with pytest.raises(ValueError, match="strictly between 0 and 1"):
        WrapperTaxTreatment(
            contributions=ContributionTaxTreatment.TAX_RELIEVED,
            growth=GrowthTaxTreatment.TAX_FREE,
            withdrawals=WithdrawalTaxTreatment.PARTIALLY_TAX_FREE,
            tax_free_fraction=rate,
        )
