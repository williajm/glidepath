"""Golden UK income tax tests against hand-worked examples (issues 2.3, 9.1).

Figures hand-worked from the 2026/27 rules verified in planning §6:
personal allowance £12,570 tapering to £0 across £100,000-£125,140
(both regimes); rUK basic 20% on the first £37,700 of taxable income,
higher 40% to £125,140, additional 45% above; Scottish starter 19% to
£3,967 of taxable income, basic 20% to £16,956, intermediate 21% to
£31,092, higher 42% to £62,430, advanced 45% to £125,140, top 48%
above.
"""

from datetime import date
from decimal import Decimal

import pytest

from glidepath.core import (
    HouseholdAssessment,
    Money,
    Period,
    Rate,
    TaxInput,
    TaxLine,
    TaxResidencyId,
    TaxResult,
    TaxSystem,
)
from glidepath.regions.uk import (
    MARRIAGE_ALLOWANCE_BAND,
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


def categorised_income(
    residency: TaxResidencyId, non_savings: str, savings: str, dividends: str
) -> TaxInput:
    """Categorised gross income for the savings/dividend layers (9.2)."""
    return TaxInput(
        residency=residency,
        non_savings_income=Money(Decimal(non_savings)),
        savings_income=Money(Decimal(savings)),
        dividend_income=Money(Decimal(dividends)),
    )


def test_savings_only_breakdown(system: UkTaxSystem) -> None:
    """£20,000 of interest alone: PA, starting rate, PSA, then 20%.

    Taxable savings 7,430; the starting rate covers 5,000 (nothing
    eats it), the basic PSA 1,000, and the last 1,430 is charged at
    the basic rate: £286.00.
    """
    result = system.assess(
        TAX_YEAR_2026_27, categorised_income(RUK_RESIDENCY, "0", "20000", "0")
    )
    assert result.taxable_income == Money(Decimal(7430))
    assert [(line.band, line.taxed, line.tax) for line in result.lines] == [
        ("savings_starting_rate", Money(Decimal(5000)), Money(Decimal(0))),
        ("savings_nil_rate", Money(Decimal(1000)), Money(Decimal(0))),
        ("savings_basic", Money(Decimal(1430)), Money(Decimal("286.00"))),
    ]
    assert result.tax_due == Money(Decimal("286.00"))


def test_earnings_eat_the_starting_rate_for_savings(system: UkTaxSystem) -> None:
    """£30,000 pay + £3,000 interest: no starting rate, PSA then 20%.

    Non-savings taxable income (17,430) exceeds the £5,000 limit, so
    the starting rate is gone; the PSA still covers 1,000 and the
    remaining 2,000 pays 20%: 3,486.00 + 400.00.
    """
    result = system.assess(
        TAX_YEAR_2026_27, categorised_income(RUK_RESIDENCY, "30000", "3000", "0")
    )
    assert [(line.band, line.tax) for line in result.lines] == [
        ("basic", Money(Decimal("3486.00"))),
        ("savings_nil_rate", Money(Decimal(0))),
        ("savings_basic", Money(Decimal("400.00"))),
    ]
    assert result.tax_due == Money(Decimal("3886.00"))


def test_higher_rate_halves_the_psa(system: UkTaxSystem) -> None:
    """£60,000 pay + £1,000 interest: PSA 500, remainder at 40%."""
    result = system.assess(
        TAX_YEAR_2026_27, categorised_income(RUK_RESIDENCY, "60000", "1000", "0")
    )
    assert [(line.band, line.taxed) for line in result.lines[-2:]] == [
        ("savings_nil_rate", Money(Decimal(500))),
        ("savings_higher", Money(Decimal(500))),
    ]
    assert result.tax_due == Money(Decimal("11632.00"))


def test_additional_rate_has_no_psa(system: UkTaxSystem) -> None:
    """£130,000 pay + £1,000 interest: PSA nil, all interest at 45%."""
    result = system.assess(
        TAX_YEAR_2026_27, categorised_income(RUK_RESIDENCY, "130000", "1000", "0")
    )
    assert result.lines[-1].band == "savings_additional"
    assert result.lines[-1].taxed == Money(Decimal(1000))
    assert result.tax_due == Money(Decimal("45153.00"))


@pytest.mark.parametrize(
    ("savings", "psa", "expected_tax"),
    [
        ("1999.99", "1000", "7339.99"),  # taxable 37,699.99: a penny inside basic
        ("2000", "1000", "7340.00"),  # taxable exactly 37,700: basic tier holds
        ("2000.01", "500", "7440.00"),  # first penny beyond: PSA halves to 500
    ],
)
def test_psa_tier_at_the_basic_band_edge(
    system: UkTaxSystem, savings: str, psa: str, expected_tax: str
) -> None:
    """The PSA tier is decided exactly at the £37,700 basic limit.

    Pay of £48,270 leaves taxable pay of 35,700, so the savings push
    total taxable income across the limit penny by penny. The £1,000
    basic tier applies up to and including exactly £37,700; the first
    penny beyond drops to the £500 higher tier, costing £100 (the 500
    no longer nil-rated, at 20%).
    """
    result = system.assess(
        TAX_YEAR_2026_27, categorised_income(RUK_RESIDENCY, "48270", savings, "0")
    )
    nil_rates = [line.taxed for line in result.lines if line.band == "savings_nil_rate"]
    assert nil_rates == [Money(Decimal(psa))]
    assert result.tax_due == Money(Decimal(expected_tax))


@pytest.mark.parametrize(
    ("savings", "psa", "expected_tax"),
    [
        ("1999.99", "500", "42315.59"),  # taxable 125,138.99: PA held at £1
        ("2000", "500", "42316.00"),  # taxable exactly 125,140: higher tier holds
        ("2000.01", None, "42516.00"),  # first penny beyond: no PSA at all
    ],
)
def test_psa_tier_at_the_additional_rate_edge(
    system: UkTaxSystem, savings: str, psa: str | None, expected_tax: str
) -> None:
    """The PSA vanishes exactly one penny past the £125,140 higher limit.

    Pay of £123,140 with £2,000 of savings makes total taxable income
    exactly £125,140 (the taper leaves the allowance at exactly nil):
    the £500 higher tier still applies at the limit itself; the first
    penny beyond drops to the nil additional tier, costing £200 (the
    500 no longer nil-rated, at 40%). A penny below, the taper
    reduction rounds down so the allowance is £1 and taxable income is
    125,138.99 — still the higher tier.
    """
    result = system.assess(
        TAX_YEAR_2026_27, categorised_income(RUK_RESIDENCY, "123140", savings, "0")
    )
    expected_nil = [] if psa is None else [Money(Decimal(psa))]
    nil_rates = [line.taxed for line in result.lines if line.band == "savings_nil_rate"]
    assert nil_rates == expected_nil
    assert result.tax_due == Money(Decimal(expected_tax))


@pytest.mark.parametrize(
    ("income", "starting", "expected_tax"),
    [
        ("17569.99", "0.01", "1099.98"),  # taxable pay 4,999.99: 1p of headroom
        ("17570", None, "1100.00"),  # taxable pay exactly 5,000: headroom nil
        ("17570.01", None, "1100.00"),  # a penny past: still nil, never negative
    ],
)
def test_starting_rate_headroom_at_the_5000_limit(
    system: UkTaxSystem, income: str, starting: str | None, expected_tax: str
) -> None:
    """The starting rate for savings dies exactly at £5,000 of pay.

    Taxable non-savings income eats the £5,000 starting-rate limit £1
    per £1: at exactly £5,000 (pay of £17,570) the headroom is exactly
    nil and the line disappears; a penny of pay less leaves exactly 1p
    of starting rate; a penny more must clamp at nil, never go
    negative. The £1,500 of interest then takes the £1,000 basic PSA
    and pays 20% on the rest.
    """
    result = system.assess(
        TAX_YEAR_2026_27, categorised_income(RUK_RESIDENCY, income, "1500", "0")
    )
    expected_lines = [] if starting is None else [Money(Decimal(starting))]
    starting_lines = [
        line.taxed for line in result.lines if line.band == "savings_starting_rate"
    ]
    assert starting_lines == expected_lines
    assert result.tax_due == Money(Decimal(expected_tax))


def test_dividends_stack_above_pay(system: UkTaxSystem) -> None:
    """£20,000 pay + £5,000 dividends: £500 nil, 10.75% on the rest."""
    result = system.assess(
        TAX_YEAR_2026_27, categorised_income(RUK_RESIDENCY, "20000", "0", "5000")
    )
    assert [(line.band, line.taxed, line.tax) for line in result.lines] == [
        ("basic", Money(Decimal(7430)), Money(Decimal("1486.00"))),
        ("dividend_nil_rate", Money(Decimal(500)), Money(Decimal(0))),
        ("dividend_ordinary", Money(Decimal(4500)), Money(Decimal("483.75"))),
    ]
    assert result.tax_due == Money(Decimal("1969.75"))


def test_dividend_nil_rate_consumes_band_width(system: UkTaxSystem) -> None:
    """At the basic-band top, the £500 nil rate pushes the rest to 35.75%.

    Pay of £50,270 fills the basic band exactly. The dividend
    allowance is a nil rate, not a deduction: it occupies the first
    £500 above the band, so the remaining £1,500 is charged at the
    upper dividend rate, never the ordinary one.
    """
    result = system.assess(
        TAX_YEAR_2026_27, categorised_income(RUK_RESIDENCY, "50270", "0", "2000")
    )
    assert [(line.band, line.taxed, line.tax) for line in result.lines[-2:]] == [
        ("dividend_nil_rate", Money(Decimal(500)), Money(Decimal(0))),
        ("dividend_upper", Money(Decimal(1500)), Money(Decimal("536.25"))),
    ]
    assert result.tax_due == Money(Decimal("8076.25"))


def test_leftover_allowance_covers_savings_then_dividends(
    system: UkTaxSystem,
) -> None:
    """£10,000 pay + £4,000 interest + £2,000 dividends.

    The PA covers all the pay and 2,570 of the interest; the taxable
    1,430 of interest sits inside the starting rate; dividends keep
    their 500 nil rate and pay 10.75% on 1,500: £161.25.
    """
    result = system.assess(
        TAX_YEAR_2026_27, categorised_income(RUK_RESIDENCY, "10000", "4000", "2000")
    )
    assert result.taxable_income == Money(Decimal(3430))
    assert [(line.band, line.taxed) for line in result.lines] == [
        ("savings_starting_rate", Money(Decimal(1430))),
        ("dividend_nil_rate", Money(Decimal(500))),
        ("dividend_ordinary", Money(Decimal(1500))),
    ]
    assert result.tax_due == Money(Decimal("161.25"))


def test_savings_and_dividends_feed_the_pa_taper(system: UkTaxSystem) -> None:
    """£95,000 pay + £10,000 dividends: ANI 105,000 tapers the PA.

    The reduction is 2,500, so the PA is 10,070 and taxable pay is
    84,930 (26,432.00 of tax); dividends add 500 nil and 9,500 at the
    upper rate (3,396.25).
    """
    result = system.assess(
        TAX_YEAR_2026_27, categorised_income(RUK_RESIDENCY, "95000", "0", "10000")
    )
    assert result.tax_free_allowance == Money(Decimal(10070))
    assert result.tax_due == Money(Decimal("29828.25"))


def test_scottish_savings_use_ruk_bands(system: UkTaxSystem) -> None:
    """Scottish pay uses Scottish bands; interest stacks on rUK bands.

    Pay of £50,000 leaves 37,430 taxable: 19/20/21/42% through the
    Scottish ladder (8,982.05). The £1,000 of interest keeps a 500
    higher-tier PSA (total taxable 38,430 crosses the rUK basic
    limit), and the taxed 500 lands past the rUK basic limit at 40% —
    never a Scottish rate.
    """
    result = system.assess(
        TAX_YEAR_2026_27, categorised_income(SCOTLAND_RESIDENCY, "50000", "1000", "0")
    )
    assert [line.band for line in result.lines] == [
        "starter",
        "basic",
        "intermediate",
        "higher",
        "savings_nil_rate",
        "savings_higher",
    ]
    assert result.lines[-1].rate == Rate(Decimal("0.40"))
    assert result.lines[-1].tax == Money(Decimal("200.00"))
    assert result.tax_due == Money(Decimal("9182.05"))


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


def scot_income(amount: str, ras_gross: str = "0") -> TaxInput:
    """Gross non-savings income for a Scottish taxpayer."""
    return TaxInput(
        residency=SCOTLAND_RESIDENCY,
        non_savings_income=Money(Decimal(amount)),
        relief_at_source_contributions=Money(Decimal(ras_gross)),
    )


@pytest.mark.parametrize(
    ("income", "expected_tax"),
    [
        ("0", "0"),  # no income
        ("10000", "0"),  # below the personal allowance
        ("12570", "0"),  # exactly the personal allowance
        ("16537", "753.73"),  # top of the starter band: 19% of 3,967
        ("16538", "753.93"),  # first pound at the Scottish basic 20%
        ("25000", "2446.33"),  # 753.73 + 20% of 8,463
        ("29526", "3351.53"),  # top of the basic band: taxable 16,956
        ("43662", "6320.09"),  # top of the intermediate band: taxable 31,092
        ("50000", "8982.05"),  # 6,320.09 + 42% of 6,338
        ("75000", "19482.05"),  # top of the higher band: taxable 62,430
        ("75001", "19482.50"),  # first pound at the advanced 45%
        ("100000", "30732.05"),  # taper threshold: PA still intact
        ("110000", "37482.05"),  # PA 7,570, taxable 102,430
        ("125140", "47701.55"),  # PA fully tapered to nil
        ("125141", "47702.03"),  # first pound at the top 48%
        ("150000", "59634.35"),  # 47,701.55 + 48% of 24,860
    ],
)
def test_golden_scottish_assessment(
    system: UkTaxSystem, income: str, expected_tax: str
) -> None:
    """Total tax matches the hand-worked Scottish figure (roadmap 9.1)."""
    result = system.assess(TAX_YEAR_2026_27, scot_income(income))
    assert result.tax_due == Money(Decimal(expected_tax))


def test_scottish_breakdown_at_50000(system: UkTaxSystem) -> None:
    """The Scottish band-by-band breakdown matches the hand-worked ladder."""
    result = system.assess(TAX_YEAR_2026_27, scot_income("50000"))
    assert result.tax_free_allowance == Money(Decimal(12570))
    assert result.taxable_income == Money(Decimal(37430))
    assert [line.band for line in result.lines] == [
        "starter",
        "basic",
        "intermediate",
        "higher",
    ]
    assert [line.taxed for line in result.lines] == [
        Money(Decimal(3967)),
        Money(Decimal(12989)),
        Money(Decimal(14136)),
        Money(Decimal(6338)),
    ]
    assert [line.tax for line in result.lines] == [
        Money(Decimal("753.73")),
        Money(Decimal("2597.80")),
        Money(Decimal("2968.56")),
        Money(Decimal("2661.96")),
    ]


def test_scottish_and_ruk_regimes_diverge(system: UkTaxSystem) -> None:
    """The same income assesses differently under the two schedules."""
    scottish = system.assess(TAX_YEAR_2026_27, scot_income("50000"))
    ruk = system.assess(TAX_YEAR_2026_27, ruk_income("50000"))
    assert scottish.tax_due - ruk.tax_due == Money(Decimal("1496.05"))


def test_scottish_ras_leaves_the_starter_limit_fixed(system: UkTaxSystem) -> None:
    """RAS extends the basic limit and above; the starter limit never moves.

    FA 2004 s192 as applied to Scottish taxpayers (SI 2018/459): a
    £10,000 gross contribution at £50,000 income moves the basic,
    intermediate and later limits up by £10,000, but the starter band
    still holds exactly £3,967.
    """
    result = system.assess(TAX_YEAR_2026_27, scot_income("50000", ras_gross="10000"))
    assert result.tax_due == Money(Decimal("7551.07"))
    assert [line.band for line in result.lines] == ["starter", "basic", "intermediate"]
    assert [line.taxed for line in result.lines] == [
        Money(Decimal(3967)),  # unmoved: limits below basic are never extended
        Money(Decimal(22989)),  # basic limit 16,956 + 10,000, less the starter
        Money(Decimal(10474)),
    ]


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


# --- annual-allowance charge (roadmap 3.3) ----------------------------------


def test_aa_charge_stacks_from_total_taxable_income(system: UkTaxSystem) -> None:
    """£40,000 income, £20,000 excess: the charge straddles the bands.

    Taxable income is 27,430, so 10,270 of the excess fills the rest
    of the basic band at 20% (2,054.00) and the remaining 9,730 falls
    to the higher band at 40% (3,892.00) — FA 2004 s227B's top-slice
    positioning.
    """
    lines = system.annual_allowance_charge(
        TAX_YEAR_2026_27, ruk_income("40000"), Money(Decimal(20000))
    )
    assert [line.band for line in lines] == ["aa_charge_basic", "aa_charge_higher"]
    assert [line.taxed for line in lines] == [
        Money(Decimal(10270)),
        Money(Decimal(9730)),
    ]
    assert [line.tax for line in lines] == [
        Money(Decimal("2054.00")),
        Money(Decimal("3892.00")),
    ]


def test_aa_charge_never_moves_the_personal_allowance(system: UkTaxSystem) -> None:
    """The excess is a charge, not income: no personal-allowance taper.

    £90,000 income leaves taxable income at 77,430; a £20,000 excess
    prices wholly in the higher band — 8,000.00 — even though £110,000
    of *income* would have tapered the allowance and cost more.
    """
    lines = system.annual_allowance_charge(
        TAX_YEAR_2026_27, ruk_income("90000"), Money(Decimal(20000))
    )
    total = sum((line.tax for line in lines), start=Money(Decimal(0)))
    assert [line.band for line in lines] == ["aa_charge_higher"]
    assert total == Money(Decimal("8000.00"))


def test_aa_charge_uses_relief_extended_bands(system: UkTaxSystem) -> None:
    """A relief-at-source gross extends the charge's rate limits too.

    FA 2004 s192(4) extends the basic rate limit for all income-tax
    purposes: with a £5,000 gross the basic limit is 42,700, so
    15,270 of the excess stays at 20% (3,054.00) and 4,730 reaches
    40% (1,892.00).
    """
    with_relief = TaxInput(
        residency=RUK_RESIDENCY,
        non_savings_income=Money(Decimal(40000)),
        relief_at_source_contributions=Money(Decimal(5000)),
    )
    lines = system.annual_allowance_charge(
        TAX_YEAR_2026_27, with_relief, Money(Decimal(20000))
    )
    assert [line.tax for line in lines] == [
        Money(Decimal("3054.00")),
        Money(Decimal("1892.00")),
    ]


def test_aa_charge_zero_excess_prices_nothing(system: UkTaxSystem) -> None:
    """No excess, no lines."""
    lines = system.annual_allowance_charge(
        TAX_YEAR_2026_27, ruk_income("40000"), Money(Decimal(0))
    )
    assert lines == ()


def test_scottish_aa_charge_uses_scottish_bands(system: UkTaxSystem) -> None:
    """A Scottish taxpayer's charge prices at the Scottish rates.

    With no income the excess starts at the foot of the Scottish
    ladder: £1,000 wholly in the 19% starter band — 190.00.
    """
    scottish = TaxInput(
        residency=SCOTLAND_RESIDENCY, non_savings_income=Money(Decimal(0))
    )
    lines = system.annual_allowance_charge(
        TAX_YEAR_2026_27, scottish, Money(Decimal(1000))
    )
    assert [line.band for line in lines] == ["aa_charge_starter"]
    assert lines[0].tax == Money(Decimal("190.00"))


# --- marriage allowance (roadmap 9.32; planning §4.11, §6 "Couples") ---------


def household_assessment(
    system: UkTaxSystem,
    income: str,
    residency: TaxResidencyId = RUK_RESIDENCY,
    savings: str = "0",
) -> HouseholdAssessment:
    """One person's completed 2026/27 assessment at ``income``."""
    tax_input = TaxInput(
        residency=residency,
        non_savings_income=Money(Decimal(income)),
        savings_income=Money(Decimal(savings)),
    )
    return HouseholdAssessment(
        tax_input=tax_input, result=system.assess(TAX_YEAR_2026_27, tax_input)
    )


class TestMarriageAllowance:
    """The §4.11 household adjustment applies the ITA 2007 s55B reducer."""

    def test_eligible_couple_gets_the_full_reducer(self, system: UkTaxSystem) -> None:
        """No-income transferor, basic-rate recipient: £252 off.

        50,000 gross is 37,430 taxable at 20% — 7,486.00 — and the
        reducer is 20% of the £1,260 transferable amount.
        """
        transferor = household_assessment(system, "0")
        recipient = household_assessment(system, "50000")
        adjusted = system.adjust_household(TAX_YEAR_2026_27, (transferor, recipient))
        assert adjusted[0] is transferor.result
        assert adjusted[1].tax_due == Money(Decimal("7234.00"))
        reducer = adjusted[1].lines[-1]
        assert reducer.band == MARRIAGE_ALLOWANCE_BAND
        assert reducer.tax == Money(Decimal("-252.00"))
        assert reducer.taxed == Money(Decimal(0))
        assert reducer.rate == Rate(Decimal("0.20"))

    def test_direction_is_picked_automatically(self, system: UkTaxSystem) -> None:
        """Swapping the couple's order moves the reducer, not the result."""
        recipient = household_assessment(system, "50000")
        transferor = household_assessment(system, "0")
        adjusted = system.adjust_household(TAX_YEAR_2026_27, (recipient, transferor))
        assert adjusted[0].tax_due == Money(Decimal("7234.00"))
        assert adjusted[1] is transferor.result

    def test_higher_rate_recipient_is_ineligible(self, system: UkTaxSystem) -> None:
        """A recipient liable at 40% keeps their unadjusted assessment."""
        transferor = household_assessment(system, "0")
        recipient = household_assessment(system, "60000")
        adjusted = system.adjust_household(TAX_YEAR_2026_27, (transferor, recipient))
        assert adjusted == (transferor.result, recipient.result)

    def test_transferor_above_the_allowance_is_ineligible(
        self, system: UkTaxSystem
    ) -> None:
        """Both partners with taxable income: nothing transfers."""
        low = household_assessment(system, "13000")
        high = household_assessment(system, "50000")
        adjusted = system.adjust_household(TAX_YEAR_2026_27, (low, high))
        assert adjusted == (low.result, high.result)

    def test_scottish_recipient_qualifies_to_the_intermediate_rate(
        self, system: UkTaxSystem
    ) -> None:
        """40,000 in Scotland tops out at 21% — eligible, at the UK 20%.

        Starter 3,967 at 19% (753.73), basic 12,989 at 20% (2,597.80),
        intermediate 10,474 at 21% (2,199.54): 5,551.07 before the
        reducer.
        """
        transferor = household_assessment(system, "0")
        recipient = household_assessment(system, "40000", SCOTLAND_RESIDENCY)
        adjusted = system.adjust_household(TAX_YEAR_2026_27, (transferor, recipient))
        assert adjusted[1].tax_due == Money(Decimal("5299.07"))
        assert adjusted[1].lines[-1].tax == Money(Decimal("-252.00"))

    def test_scottish_higher_rate_recipient_is_ineligible(
        self, system: UkTaxSystem
    ) -> None:
        """A Scottish recipient at 42% keeps their unadjusted assessment."""
        transferor = household_assessment(system, "0")
        recipient = household_assessment(system, "60000", SCOTLAND_RESIDENCY)
        adjusted = system.adjust_household(TAX_YEAR_2026_27, (transferor, recipient))
        assert adjusted == (transferor.result, recipient.result)

    def test_nil_rated_savings_keep_the_recipient_eligible(
        self, system: UkTaxSystem
    ) -> None:
        """PSA-nil savings are a qualifying rate (ITA 2007 s55B(2)(b))."""
        transferor = household_assessment(system, "0")
        recipient = household_assessment(system, "20000", savings="500")
        adjusted = system.adjust_household(TAX_YEAR_2026_27, (transferor, recipient))
        assert adjusted[1].lines[-1].band == MARRIAGE_ALLOWANCE_BAND
        assert adjusted[1].tax_due == Money(Decimal("1234.00"))

    def test_reducer_caps_at_the_liability(self, system: UkTaxSystem) -> None:
        """13,000 gross owes 86.00 — the reducer stops there, never refunds."""
        transferor = household_assessment(system, "0")
        recipient = household_assessment(system, "13000")
        adjusted = system.adjust_household(TAX_YEAR_2026_27, (transferor, recipient))
        assert adjusted[1].tax_due == Money(Decimal(0))
        assert adjusted[1].lines[-1].tax == Money(Decimal("-86.00"))

    def test_no_liability_leaves_the_couple_unchanged(
        self, system: UkTaxSystem
    ) -> None:
        """Both inside their allowances: a claim would reduce nothing."""
        first = household_assessment(system, "0")
        second = household_assessment(system, "10000")
        adjusted = system.adjust_household(TAX_YEAR_2026_27, (first, second))
        assert adjusted == (first.result, second.result)

    def test_a_single_assessment_passes_through(self, system: UkTaxSystem) -> None:
        """The adjustment only exists between two partners."""
        only = household_assessment(system, "50000")
        adjusted = system.adjust_household(TAX_YEAR_2026_27, (only,))
        assert adjusted == (only.result,)

    def test_annual_allowance_charge_lines_stay_outside(
        self, system: UkTaxSystem
    ) -> None:
        """The s55B reducer lands before the s23 step-7 AA charge.

        The charge's lines neither disqualify the recipient nor lift
        the cap: eligibility and the liability read from the income-tax
        lines alone.
        """
        transferor = household_assessment(system, "0")
        basic = household_assessment(system, "20000")
        charged = TaxLine(
            band="aa_charge_higher",
            rate=Rate(Decimal("0.40")),
            taxed=Money(Decimal(1000)),
            tax=Money(Decimal("400.00")),
        )
        recipient = HouseholdAssessment(
            tax_input=basic.tax_input,
            result=TaxResult(
                tax_due=basic.result.tax_due + charged.tax,
                taxable_income=basic.result.taxable_income,
                tax_free_allowance=basic.result.tax_free_allowance,
                lines=(*basic.result.lines, charged),
            ),
        )
        adjusted = system.adjust_household(TAX_YEAR_2026_27, (transferor, recipient))
        # 20,000 gross owes 1,486.00; with the 400.00 charge on top the
        # reducer still takes the full 252.00: 1,634.00.
        assert adjusted[1].tax_due == Money(Decimal("1634.00"))
        assert adjusted[1].lines[-1].band == MARRIAGE_ALLOWANCE_BAND
