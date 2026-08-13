"""Property-based tests over the UK tax assessment (issue #201).

The golden suite (``test_uk_tax.py``) pins hand-worked figures for
single years; these properties assert the structural identities that
must hold across the whole income space, every shipped tax year, and
both residencies:

- the per-band lines sum to the assessed tax, and their widths account
  for every taxable pound exactly once (nil rates consume band width,
  planning §6);
- the assessment stays between zero and the gross income assessed;
- more non-savings income never means less tax — through the personal
  allowance taper's 60% zone and HMRC's whole-pound and penny
  roundings;
- relief-at-source contributions never increase the assessment and
  never shrink the personal allowance (band extension plus the
  adjusted-net-income deduction, planning §6);
- the annual-allowance charge lines account for exactly the chargeable
  excess (FA 2004 s227B top-slicing).
"""

from datetime import date
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from glidepath.core import Money, Period, TaxInput, TaxResidencyId
from glidepath.regions.uk import (
    RUK_RESIDENCY,
    SCOTLAND_RESIDENCY,
    UkTaxSystem,
    available_tax_years,
)

SYSTEM = UkTaxSystem.from_shipped_data()

TAX_YEAR_PERIODS = tuple(
    Period(start=date(year, 4, 6), end=date(year + 1, 4, 5))
    for year in available_tax_years()
)

ZERO = Money(Decimal(0))

AA_CHARGE_PREFIX = "aa_charge_"

incomes = st.decimals(
    min_value=0, max_value=250_000, places=2, allow_nan=False, allow_infinity=False
)
ras_amounts = st.decimals(
    min_value=0, max_value=60_000, places=2, allow_nan=False, allow_infinity=False
)
excess_amounts = st.decimals(
    min_value=0, max_value=150_000, places=2, allow_nan=False, allow_infinity=False
)
periods = st.sampled_from(TAX_YEAR_PERIODS)
residencies = st.sampled_from((RUK_RESIDENCY, SCOTLAND_RESIDENCY))


def tax_input_of(
    residency: TaxResidencyId,
    non_savings: Decimal,
    savings: Decimal = Decimal(0),
    dividends: Decimal = Decimal(0),
    ras: Decimal = Decimal(0),
) -> TaxInput:
    """Categorised gross income for one assessment."""
    return TaxInput(
        residency=residency,
        non_savings_income=Money(non_savings),
        savings_income=Money(savings),
        dividend_income=Money(dividends),
        relief_at_source_contributions=Money(ras),
    )


class TestAssessmentProperties:
    """Identities every assessment must satisfy (issue #201)."""

    @given(
        period=periods,
        residency=residencies,
        non_savings=incomes,
        savings=incomes,
        dividends=incomes,
        ras=ras_amounts,
    )
    @settings(max_examples=200, deadline=None)
    def test_lines_account_for_the_assessment(
        self,
        *,
        period: Period,
        residency: TaxResidencyId,
        non_savings: Decimal,
        savings: Decimal,
        dividends: Decimal,
        ras: Decimal,
    ) -> None:
        """Line taxes sum to the due; line widths sum to the taxable.

        The width identity is the stacking rule of planning §6: the
        starting rate, PSA, and dividend allowance are nil *rates*, so
        their lines still consume band width — every taxable pound
        appears on exactly one line.
        """
        result = SYSTEM.assess(
            period, tax_input_of(residency, non_savings, savings, dividends, ras)
        )
        lines_tax = sum((line.tax for line in result.lines), start=ZERO)
        assert result.tax_due == lines_tax
        lines_width = sum((line.taxed for line in result.lines), start=ZERO)
        assert result.taxable_income == lines_width
        assert result.tax_due >= ZERO
        assert result.tax_due <= Money(non_savings + savings + dividends)

    @given(
        period=periods,
        residency=residencies,
        first=incomes,
        second=incomes,
        ras=ras_amounts,
    )
    @settings(max_examples=200, deadline=None)
    def test_more_income_never_means_less_tax(
        self,
        period: Period,
        residency: TaxResidencyId,
        first: Decimal,
        second: Decimal,
        ras: Decimal,
    ) -> None:
        """The assessment is monotone in non-savings income.

        Every band's width weakly grows with income (the tapered
        allowance only shrinks), so HMRC's per-band round-down cannot
        push the total backwards — the 60% taper zone included.
        """
        lower, higher = sorted((first, second))
        lower_tax = SYSTEM.assess(
            period, tax_input_of(residency, lower, ras=ras)
        ).tax_due
        higher_tax = SYSTEM.assess(
            period, tax_input_of(residency, higher, ras=ras)
        ).tax_due
        assert higher_tax >= lower_tax

    @given(
        period=periods,
        residency=residencies,
        non_savings=incomes,
        savings=incomes,
        dividends=incomes,
        ras=ras_amounts,
    )
    @settings(max_examples=200, deadline=None)
    def test_relief_at_source_never_increases_the_assessment(
        self,
        *,
        period: Period,
        residency: TaxResidencyId,
        non_savings: Decimal,
        savings: Decimal,
        dividends: Decimal,
        ras: Decimal,
    ) -> None:
        """A relief-at-source gross can only reduce tax, never add it.

        HMRC's mechanism (planning §6): the basic limit and every
        limit above it extend by the gross, and adjusted net income —
        the taper measure — deducts it, so the allowance can only grow
        and every taxed pound lands in the same or a lower band.
        """
        base = SYSTEM.assess(
            period, tax_input_of(residency, non_savings, savings, dividends)
        )
        relieved = SYSTEM.assess(
            period, tax_input_of(residency, non_savings, savings, dividends, ras)
        )
        assert relieved.tax_due <= base.tax_due
        assert relieved.tax_free_allowance >= base.tax_free_allowance


class TestAnnualAllowanceChargeProperties:
    """The s227B charge ladder over generated positions (issue #201)."""

    @given(
        period=periods,
        residency=residencies,
        non_savings=incomes,
        ras=ras_amounts,
        excess=excess_amounts,
    )
    @settings(max_examples=200, deadline=None)
    def test_charge_lines_account_for_the_excess(
        self,
        period: Period,
        residency: TaxResidencyId,
        non_savings: Decimal,
        ras: Decimal,
        excess: Decimal,
    ) -> None:
        """The charge lines' widths sum to exactly the excess.

        FA 2004 s227B: the chargeable amount stacks whole as the top
        slice of the taxpayer's income — the unbounded top band means
        no part of it can fall off the ladder — and each line is a
        prefixed, non-negative slice charged below 100%.
        """
        tax_input = tax_input_of(residency, non_savings, ras=ras)
        lines = SYSTEM.annual_allowance_charge(period, tax_input, Money(excess))
        lines_width = sum((line.taxed for line in lines), start=ZERO)
        assert lines_width == Money(excess)
        charge = sum((line.tax for line in lines), start=ZERO)
        assert charge >= ZERO
        assert charge <= Money(excess)
        assert all(line.band.startswith(AA_CHARGE_PREFIX) for line in lines)
