"""Golden tests for UK relief mechanics and pension allowances (3.2, 3.3).

Relief mechanics hand-worked from the gov.uk worked examples (pension
tax relief: relief at source grosses up net contributions by 20%; net
pay deducts before tax; no/low earners keep the £3,600-gross basic
amount via relief at source only). Allowance figures from planning §6:
AA £60,000; taper threshold £200,000 / adjusted £260,000, -£1 per £2
rounded down to the pound, floor £10,000; MPAA £10,000; alternative
annual allowance = AA - MPAA = £50,000 (HS345).
"""

from datetime import date
from decimal import Decimal

import pytest

from glidepath.core import (
    ContributionRuleset,
    Money,
    Period,
    ReliefMechanic,
)
from glidepath.regions.uk import (
    AnnualAllowanceAssessment,
    PensionRules,
    UkContributionError,
    UkContributionRuleset,
    adjusted_income,
    assess_annual_allowance,
    is_mpaa_active,
    load_tax_year,
    tapered_annual_allowance,
    threshold_income,
)

TAX_YEAR_2026_27 = Period(start=date(2026, 4, 6), end=date(2027, 4, 5))


def money(amount: str) -> Money:
    """Build ``Money`` from a decimal string."""
    return Money(Decimal(amount))


@pytest.fixture(scope="module", name="rules")
def rules_fixture() -> UkContributionRuleset:
    """A contribution ruleset over the shipped data files."""
    return UkContributionRuleset.from_shipped_data()


@pytest.fixture(scope="module", name="pension")
def pension_fixture() -> PensionRules:
    """The shipped 2026/27 pension rules."""
    return load_tax_year(2026).pension


# --- relief mechanics (issue 3.2) -------------------------------------------


def test_relief_at_source_grosses_up(rules: UkContributionRuleset) -> None:
    """gov.uk worked example: £8,000 cash becomes £10,000 in the pot."""
    outcome = rules.member_contribution(
        gross=money("10000"),
        relevant_earnings=money("60000"),
        mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
        period=TAX_YEAR_2026_27,
    )
    assert outcome.gross_to_pot == money("10000")
    assert outcome.member_cash_cost == money("8000")
    assert outcome.provider_relief == money("2000")
    assert outcome.taxable_pay_deduction == money("0")
    assert outcome.assessment_relief_gross == money("10000")
    assert outcome.unrelieved_excess == money("0")


def test_net_pay_deducts_before_tax(rules: UkContributionRuleset) -> None:
    """Net pay: the gross amount leaves pay pre-tax; nothing at source."""
    outcome = rules.member_contribution(
        gross=money("10000"),
        relevant_earnings=money("60000"),
        mechanic=ReliefMechanic.NET_PAY,
        period=TAX_YEAR_2026_27,
    )
    assert outcome.gross_to_pot == money("10000")
    assert outcome.member_cash_cost == money("10000")
    assert outcome.provider_relief == money("0")
    assert outcome.taxable_pay_deduction == money("10000")
    assert outcome.assessment_relief_gross == money("0")


def test_no_mechanic_is_plain_taxed_cash(rules: UkContributionRuleset) -> None:
    """An ISA-style contribution: no relief, no limit, no assessment."""
    outcome = rules.member_contribution(
        gross=money("5000"),
        relevant_earnings=money("0"),
        mechanic=None,
        period=TAX_YEAR_2026_27,
    )
    assert outcome.gross_to_pot == money("5000")
    assert outcome.member_cash_cost == money("5000")
    assert outcome.provider_relief == money("0")
    assert outcome.unrelieved_excess == money("0")


def test_no_earner_keeps_the_basic_amount_via_ras(
    rules: UkContributionRuleset,
) -> None:
    """gov.uk: with no earnings, £2,880 net grosses up to £3,600."""
    outcome = rules.member_contribution(
        gross=money("3600"),
        relevant_earnings=money("0"),
        mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
        period=TAX_YEAR_2026_27,
    )
    assert outcome.gross_to_pot == money("3600")
    assert outcome.member_cash_cost == money("2880")
    assert outcome.provider_relief == money("720")
    assert outcome.unrelieved_excess == money("0")


def test_ras_clips_above_the_basic_amount_for_no_earners(
    rules: UkContributionRuleset,
) -> None:
    """A no-earner asking for more than £3,600 gross is clipped to it."""
    outcome = rules.member_contribution(
        gross=money("5000"),
        relevant_earnings=money("0"),
        mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
        period=TAX_YEAR_2026_27,
    )
    assert outcome.gross_to_pot == money("3600")
    assert outcome.unrelieved_excess == money("1400")


def test_ras_relief_limited_to_earnings_above_basic_amount(
    rules: UkContributionRuleset,
) -> None:
    """Relief caps at 100% of relevant earnings once above the floor."""
    outcome = rules.member_contribution(
        gross=money("25000"),
        relevant_earnings=money("20000"),
        mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
        period=TAX_YEAR_2026_27,
    )
    assert outcome.gross_to_pot == money("20000")
    assert outcome.member_cash_cost == money("16000")
    assert outcome.provider_relief == money("4000")
    assert outcome.unrelieved_excess == money("5000")


def test_net_pay_gets_no_basic_amount(rules: UkContributionRuleset) -> None:
    """The £3,600 floor is relief-at-source only: net pay needs earnings."""
    outcome = rules.member_contribution(
        gross=money("3600"),
        relevant_earnings=money("0"),
        mechanic=ReliefMechanic.NET_PAY,
        period=TAX_YEAR_2026_27,
    )
    assert outcome.gross_to_pot == money("0")
    assert outcome.unrelieved_excess == money("3600")


def test_net_pay_cannot_exceed_pay(rules: UkContributionRuleset) -> None:
    """A net-pay deduction is limited to the pay it comes out of."""
    outcome = rules.member_contribution(
        gross=money("30000"),
        relevant_earnings=money("25000"),
        mechanic=ReliefMechanic.NET_PAY,
        period=TAX_YEAR_2026_27,
    )
    assert outcome.gross_to_pot == money("25000")
    assert outcome.unrelieved_excess == money("5000")


def test_member_contribution_rejects_negative_gross(
    rules: UkContributionRuleset,
) -> None:
    """A negative contribution is a construction-time input error."""
    negative = money("-1")
    earnings = money("10000")
    with pytest.raises(UkContributionError, match="gross"):
        rules.member_contribution(
            gross=negative,
            relevant_earnings=earnings,
            mechanic=None,
            period=TAX_YEAR_2026_27,
        )


def test_uncovered_period_is_rejected(rules: UkContributionRuleset) -> None:
    """Without an extension, periods outside shipped data fail loudly."""
    uncovered = Period(start=date(2027, 4, 6), end=date(2028, 4, 5))
    gross = money("100")
    earnings = money("100")
    with pytest.raises(UkContributionError, match="no shipped tax-year data"):
        rules.member_contribution(
            gross=gross,
            relevant_earnings=earnings,
            mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
            period=uncovered,
        )


def test_empty_ruleset_is_rejected() -> None:
    """A ruleset needs at least one tax-year file."""
    with pytest.raises(UkContributionError, match="at least one"):
        UkContributionRuleset(tax_years=())


def test_ruleset_satisfies_core_protocol(rules: UkContributionRuleset) -> None:
    """``UkContributionRuleset`` is usable via the core protocol."""
    protocol_typed: ContributionRuleset = rules
    outcome = protocol_typed.member_contribution(
        gross=money("100"),
        relevant_earnings=money("0"),
        mechanic=None,
        period=TAX_YEAR_2026_27,
    )
    assert outcome.gross_to_pot == money("100")


# --- taper incomes (issue 3.3) ----------------------------------------------


def test_threshold_income_deducts_both_member_routes() -> None:
    """Both net-pay and RAS gross amounts come off threshold income."""
    result = threshold_income(
        total_income=money("210000"),
        net_pay_contributions=money("5000"),
        relief_at_source_gross=money("6000"),
    )
    assert result == money("199000")


def test_threshold_income_floors_at_zero() -> None:
    """Contributions above income cannot make the measure negative."""
    result = threshold_income(
        total_income=money("3000"),
        net_pay_contributions=money("0"),
        relief_at_source_gross=money("3600"),
    )
    assert result == money("0")


def test_adjusted_income_adds_employer_contributions() -> None:
    """Adjusted income keeps member amounts and adds employer input."""
    result = adjusted_income(
        total_income=money("210000"), employer_contributions=money("30000")
    )
    assert result == money("240000")


def test_taper_income_helpers_reject_negatives() -> None:
    """Negative income components are input errors."""
    negative = money("-1")
    with pytest.raises(UkContributionError, match="total_income"):
        adjusted_income(total_income=negative, employer_contributions=negative)


# --- tapered annual allowance (issue 3.3) -----------------------------------


@pytest.mark.parametrize(
    ("threshold", "adjusted", "expected"),
    [
        ("200000", "300000", "60000"),  # threshold income at the limit: no taper
        ("210000", "260000", "60000"),  # adjusted income at the limit: no taper
        ("210000", "280000", "50000"),  # 20,000 over: -10,000
        ("210000", "300000", "40000"),  # 40,000 over: -20,000
        ("210000", "360000", "10000"),  # -50,000 lands exactly on the floor
        ("210000", "500000", "10000"),  # beyond maximum taper: floor holds
        ("210000", "260001", "60000"),  # 50p reduction rounds down to nil
        ("210000", "260003", "59999"),  # £1.50 reduction rounds down to £1
    ],
)
def test_tapered_annual_allowance_goldens(
    pension: PensionRules, threshold: str, adjusted: str, expected: str
) -> None:
    """Taper arithmetic matches the PTM057100 rules (issue 3.3 criterion)."""
    result = tapered_annual_allowance(
        pension, threshold=money(threshold), adjusted=money(adjusted)
    )
    assert result == money(expected)


# --- MPAA activation (issue 3.3) --------------------------------------------


@pytest.mark.parametrize(
    ("triggered_on", "active"),
    [
        (None, False),  # never flexibly accessed
        (date(2024, 6, 1), True),  # before the period: persists
        (date(2026, 10, 1), True),  # inside the period: conservative
        (date(2027, 6, 1), False),  # not yet triggered
    ],
)
def test_mpaa_activation(triggered_on: date | None, *, active: bool) -> None:
    """The MPAA flips on first flexible access and persists thereafter."""
    assert is_mpaa_active(triggered_on, TAX_YEAR_2026_27) is active


# --- annual allowance assessment (issue 3.3) --------------------------------


def test_within_the_allowance_has_no_excess(pension: PensionRules) -> None:
    """Employer plus member inputs inside the AA leave nothing chargeable."""
    assessment = assess_annual_allowance(
        pension,
        annual_allowance=pension.annual_allowance,
        money_purchase_inputs=money("30000"),
        other_inputs=money("0"),
        mpaa_active=False,
    )
    assert assessment.chargeable_excess == money("0")
    assert assessment.alternative_annual_allowance is None
    assert assessment.money_purchase_excess == money("0")


def test_aa_measures_total_pension_input_amounts(pension: PensionRules) -> None:
    """The AA measures member + employer inputs (issue 3.3 criterion)."""
    assessment = assess_annual_allowance(
        pension,
        annual_allowance=pension.annual_allowance,
        money_purchase_inputs=money("70000"),  # e.g. 40,000 member + 30,000 employer
        other_inputs=money("0"),
        mpaa_active=False,
    )
    assert assessment.chargeable_excess == money("10000")


def test_alternative_allowance_is_aa_minus_mpaa(pension: PensionRules) -> None:
    """The verified §9.8 figure: £60,000 - £10,000 leaves £50,000 for DB."""
    assessment = assess_annual_allowance(
        pension,
        annual_allowance=pension.annual_allowance,
        money_purchase_inputs=money("8000"),
        other_inputs=money("0"),
        mpaa_active=True,
    )
    assert assessment.alternative_annual_allowance == money("50000")
    assert assessment.money_purchase_excess == money("0")
    assert assessment.chargeable_excess == money("0")


def test_mpaa_excess_is_chargeable_below_the_aa(pension: PensionRules) -> None:
    """£25,000 of DC input after flexible access charges £15,000."""
    assessment = assess_annual_allowance(
        pension,
        annual_allowance=pension.annual_allowance,
        money_purchase_inputs=money("25000"),
        other_inputs=money("0"),
        mpaa_active=True,
    )
    assert assessment.money_purchase_excess == money("15000")
    assert assessment.chargeable_excess == money("15000")


def test_db_accrual_uses_the_alternative_allowance(pension: PensionRules) -> None:
    """DC over the MPAA plus DB over the alternative AA both charge."""
    assessment = assess_annual_allowance(
        pension,
        annual_allowance=pension.annual_allowance,
        money_purchase_inputs=money("15000"),
        other_inputs=money("55000"),
        mpaa_active=True,
    )
    # The alternative computation, 5,000 over the MPAA plus 5,000 of DB
    # over the alternative allowance, ties the default 70,000 - 60,000.
    assert assessment.chargeable_excess == money("10000")


def test_charge_is_the_greater_of_default_and_alternative(
    pension: PensionRules,
) -> None:
    """FA 2004 s227ZA: the chargeable amount is the greater computation."""
    assessment = assess_annual_allowance(
        pension,
        annual_allowance=pension.annual_allowance,
        money_purchase_inputs=money("25000"),
        other_inputs=money("40000"),
        mpaa_active=True,
    )
    # Alternative: 15,000 + max(40,000 - 50,000, 0) = 15,000 beats the
    # default 65,000 - 60,000 = 5,000.
    assert assessment.chargeable_excess == money("15000")
    higher_db = assess_annual_allowance(
        pension,
        annual_allowance=pension.annual_allowance,
        money_purchase_inputs=money("9000"),
        other_inputs=money("70000"),
        mpaa_active=True,
    )
    # DC within the MPAA: only the default computation applies —
    # 79,000 - 60,000 = 19,000.
    assert higher_db.chargeable_excess == money("19000")


def test_max_taper_leaves_no_alternative_allowance(pension: PensionRules) -> None:
    """At the taper floor the alternative allowance is nil (HS345)."""
    floored = tapered_annual_allowance(
        pension, threshold=money("210000"), adjusted=money("500000")
    )
    assessment = assess_annual_allowance(
        pension,
        annual_allowance=floored,
        money_purchase_inputs=money("12000"),
        other_inputs=money("5000"),
        mpaa_active=True,
    )
    assert assessment.alternative_annual_allowance == money("0")
    # Alternative: 2,000 + 5,000 = 7,000; default: 17,000 - 10,000 = 7,000.
    assert assessment.chargeable_excess == money("7000")


def test_assessment_requires_alternative_exactly_when_mpaa_active() -> None:
    """The result type cannot mis-state the MPAA position."""
    zero = money("0")
    allowance = money("60000")
    with pytest.raises(UkContributionError, match="alternative_annual_allowance"):
        AnnualAllowanceAssessment(
            annual_allowance=allowance,
            mpaa_active=True,
            money_purchase_excess=zero,
            alternative_annual_allowance=None,
            chargeable_excess=zero,
        )


def test_assessment_rejects_negative_inputs(pension: PensionRules) -> None:
    """Negative pension input amounts are input errors."""
    negative = money("-1")
    zero = money("0")
    with pytest.raises(UkContributionError, match="money_purchase_inputs"):
        assess_annual_allowance(
            pension,
            annual_allowance=zero,
            money_purchase_inputs=negative,
            other_inputs=zero,
            mpaa_active=False,
        )
