"""Golden rUK income tax tests against hand-worked HMRC examples (issue 2.3).

Figures hand-worked from the 2026/27 rules verified in planning §6:
personal allowance £12,570 tapering to £0 across £100,000-£125,140;
basic 20% on the first £37,700 of taxable income, higher 40% to
£125,140, additional 45% above.
"""

from datetime import date
from decimal import Decimal

import pytest

from glidepath.core import Money, Period, TaxInput, TaxResidencyId, TaxSystem
from glidepath.regions.uk import (
    RUK_RESIDENCY,
    SCOTLAND_RESIDENCY,
    UkTaxError,
    UkTaxSystem,
    load_tax_year,
)

TAX_YEAR_2026_27 = Period(start=date(2026, 4, 6), end=date(2027, 4, 5))


@pytest.fixture(scope="module", name="system")
def system_fixture() -> UkTaxSystem:
    """A tax system over the shipped data files."""
    return UkTaxSystem.from_shipped_data()


def ruk_income(amount: str) -> TaxInput:
    """Gross non-savings income for an rUK taxpayer."""
    return TaxInput(residency=RUK_RESIDENCY, non_savings_income=Money(Decimal(amount)))


@pytest.mark.parametrize(
    ("income", "expected_tax"),
    [
        ("0", "0"),  # no income
        ("10000", "0"),  # below the personal allowance
        ("12570", "0"),  # exactly the personal allowance
        ("20000", "1486.00"),  # 20% of 7,430
        ("50270", "7540.00"),  # top of the basic band: 20% of 37,700
        ("50271", "7540.40"),  # first pound at 40%
        ("60000", "11432.00"),  # 7,540 + 40% of 9,730
        ("100000", "27432.00"),  # taper threshold: PA still intact
        ("100001", "27432.40"),  # reduction rounds down to nil: PA held at 12,570
        ("100002", "27433.20"),  # first full 2 of excess: PA 12,569, taxable +3
        ("110000", "33432.00"),  # PA 7,570, taxable 102,430
        ("125140", "42516.00"),  # PA fully tapered to nil
        ("125141", "42516.45"),  # first pound at 45%
        ("150000", "53703.00"),  # 42,516 + 45% of 24,860
    ],
)
def test_golden_ruk_assessment(
    system: UkTaxSystem, income: str, expected_tax: str
) -> None:
    """Total tax matches the hand-worked HMRC figure."""
    result = system.assess(TAX_YEAR_2026_27, ruk_income(income))
    assert result.tax_due == Money(Decimal(expected_tax))


def test_sixty_percent_zone(system: UkTaxSystem) -> None:
    """The taper makes £100k-£110k cost 60%: £6,000 tax on £10,000."""
    at_threshold = system.assess(TAX_YEAR_2026_27, ruk_income("100000"))
    above = system.assess(TAX_YEAR_2026_27, ruk_income("110000"))
    assert above.tax_due - at_threshold.tax_due == Money(Decimal(6000))


def test_breakdown_at_60000(system: UkTaxSystem) -> None:
    """The band-by-band breakdown matches the hand-worked computation."""
    result = system.assess(TAX_YEAR_2026_27, ruk_income("60000"))
    assert result.tax_free_allowance == Money(Decimal(12570))
    assert result.taxable_income == Money(Decimal(47430))
    assert [line.band for line in result.lines] == ["basic", "higher"]
    assert [line.taxed for line in result.lines] == [
        Money(Decimal(37700)),
        Money(Decimal(9730)),
    ]
    assert [line.tax for line in result.lines] == [
        Money(Decimal("7540.00")),
        Money(Decimal("3892.00")),
    ]


def test_below_allowance_has_no_lines(system: UkTaxSystem) -> None:
    """Income inside the allowance produces an empty breakdown."""
    result = system.assess(TAX_YEAR_2026_27, ruk_income("10000"))
    assert result.lines == ()
    assert result.taxable_income == Money(Decimal(0))
    assert result.tax_free_allowance == Money(Decimal(12570))


@pytest.mark.parametrize(
    ("income", "allowance"),
    [
        ("100001", "12570"),  # excess 1: reduction rounds down to nil
        ("100002", "12569"),  # excess 2: first whole-pound step
        ("100003", "12569"),  # excess 3: reduction still 1
        ("100004", "12568"),  # excess 4: next step
    ],
)
def test_taper_steps_in_whole_pounds(
    system: UkTaxSystem, income: str, allowance: str
) -> None:
    """HMRC rounds the reduction down and the allowance up to whole pounds."""
    result = system.assess(TAX_YEAR_2026_27, ruk_income(income))
    assert result.tax_free_allowance == Money(Decimal(allowance))


def test_band_tax_rounds_down_to_the_penny(system: UkTaxSystem) -> None:
    """20% of 3p taxable is 0.6p: HMRC rounds each band's tax down to nil."""
    result = system.assess(TAX_YEAR_2026_27, ruk_income("12570.03"))
    assert result.taxable_income == Money(Decimal("0.03"))
    assert result.tax_due == Money(Decimal(0))
    assert [line.tax for line in result.lines] == [Money(Decimal(0))]


def ruk_income_with_ras(amount: str, ras_gross: str) -> TaxInput:
    """Gross income plus gross relief-at-source pension contributions."""
    return TaxInput(
        residency=RUK_RESIDENCY,
        non_savings_income=Money(Decimal(amount)),
        relief_at_source_contributions=Money(Decimal(ras_gross)),
    )


def test_ras_extends_the_basic_rate_band(system: UkTaxSystem) -> None:
    """HMRC mechanism: £10,000 gross RAS moves the 40% threshold up.

    At £60,000 income the whole £47,430 taxable now fits inside the
    extended basic band (£47,700), so the assessment grants a further
    20% on the £9,730 that had sat in the higher band.
    """
    result = system.assess(TAX_YEAR_2026_27, ruk_income_with_ras("60000", "10000"))
    assert result.tax_due == Money(Decimal("9486.00"))
    assert [line.band for line in result.lines] == ["basic"]
    without = system.assess(TAX_YEAR_2026_27, ruk_income("60000"))
    assert without.tax_due - result.tax_due == Money(Decimal("1946.00"))


def test_ras_restores_the_tapered_allowance(system: UkTaxSystem) -> None:
    """Adjusted net income deducts gross RAS: £110,000 keeps the full PA."""
    result = system.assess(TAX_YEAR_2026_27, ruk_income_with_ras("110000", "10000"))
    assert result.tax_free_allowance == Money(Decimal(12570))
    assert result.taxable_income == Money(Decimal(97430))
    assert [line.taxed for line in result.lines] == [
        Money(Decimal(47700)),  # basic band extended by the gross contribution
        Money(Decimal(49730)),
    ]
    assert result.tax_due == Money(Decimal("29432.00"))


def test_sub_period_within_tax_year(system: UkTaxSystem) -> None:
    """A period inside one tax year assesses under that year's rules."""
    part_year = Period(start=date(2026, 4, 6), end=date(2026, 12, 31))
    full = system.assess(TAX_YEAR_2026_27, ruk_income("60000"))
    part = system.assess(part_year, ruk_income("60000"))
    assert part == full


def test_period_spanning_tax_years_is_rejected(system: UkTaxSystem) -> None:
    """A period crossing 6 April cannot be assessed."""
    spanning = Period(start=date(2027, 3, 1), end=date(2027, 6, 30))
    income = ruk_income("60000")
    with pytest.raises(UkTaxError, match="extends beyond"):
        system.assess(spanning, income)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2025, 4, 6), date(2026, 4, 5)),  # before the first shipped year
        (date(2027, 4, 6), date(2028, 4, 5)),  # after the last shipped year
    ],
)
def test_uncovered_period_is_rejected(
    system: UkTaxSystem, start: date, end: date
) -> None:
    """Without a future-years extension, periods outside shipped data fail."""
    uncovered = Period(start=start, end=end)
    income = ruk_income("60000")
    with pytest.raises(UkTaxError, match="no shipped tax-year data"):
        system.assess(uncovered, income)


def test_scottish_residency_not_yet_active(system: UkTaxSystem) -> None:
    """Scottish bands ship in data but activate in roadmap 9.1."""
    scottish = TaxInput(
        residency=SCOTLAND_RESIDENCY, non_savings_income=Money(Decimal(60000))
    )
    with pytest.raises(UkTaxError, match=r"9\.1"):
        system.assess(TAX_YEAR_2026_27, scottish)


def test_unknown_residency_is_rejected(system: UkTaxSystem) -> None:
    """A residency id the UK region does not define is an error."""
    unknown = TaxInput(
        residency=TaxResidencyId("uk.mars"), non_savings_income=Money(Decimal(1))
    )
    with pytest.raises(UkTaxError, match="unknown UK tax residency"):
        system.assess(TAX_YEAR_2026_27, unknown)


def test_empty_system_is_rejected() -> None:
    """A system needs at least one tax-year file."""
    with pytest.raises(UkTaxError, match="at least one"):
        UkTaxSystem(tax_years=())


def test_overlapping_years_are_rejected() -> None:
    """Duplicate or out-of-order year files are a construction error."""
    year = load_tax_year(2026)
    with pytest.raises(UkTaxError, match="ascending"):
        UkTaxSystem(tax_years=(year, year))


def test_uk_system_satisfies_core_protocol(system: UkTaxSystem) -> None:
    """``UkTaxSystem`` is usable wherever the core protocol is expected."""
    protocol_typed: TaxSystem = system
    result = protocol_typed.assess(TAX_YEAR_2026_27, ruk_income("20000"))
    assert result.tax_due == Money(Decimal("1486.00"))
