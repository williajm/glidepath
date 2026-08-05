"""Golden tests for UK relief mechanics and pension allowances (3.2, 3.3, 9.5).

Relief mechanics hand-worked from the gov.uk worked examples (pension
tax relief: relief at source grosses up net contributions by 20%; net
pay deducts before tax; no/low earners keep the £3,600-gross basic
amount via relief at source only). Allowance figures from planning §6:
AA £60,000; taper threshold £200,000 / adjusted £260,000, -£1 per £2
rounded down to the pound, floor £10,000; MPAA £10,000; alternative
annual allowance = AA - MPAA = £50,000 (HS345). Carry-forward per the
gov.uk guidance (verified 2026-08-04): unused AA from the previous 3
tax years, drawn earliest first, only from scheme-membership years,
never topping up the MPAA.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from glidepath.core import (
    AnnualAllowanceMeasurement,
    ContributionRuleset,
    DbArrangementInput,
    MemberContributionRequest,
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
    apply_carry_forward,
    assess_annual_allowance,
    carry_forward_generated,
    db_pension_input_amount,
    is_mpaa_active,
    load_tax_year,
    roll_carry_forward,
    tapered_annual_allowance,
    threshold_income,
)

TAX_YEAR_2026_27 = Period(start=date(2026, 4, 6), end=date(2027, 4, 5))
DOB_1980 = date(1980, 1, 1)  # well under the relief age limit throughout


def money(amount: str) -> Money:
    """Build ``Money`` from a decimal string."""
    return Money(Decimal(amount))


def request(
    gross: str,
    earnings: str,
    mechanic: ReliefMechanic | None,
    already_relieved: str = "0",
    date_of_birth: date = DOB_1980,
) -> MemberContributionRequest:
    """Build a contribution request for the standard test member."""
    return MemberContributionRequest(
        gross=money(gross),
        relevant_earnings=money(earnings),
        date_of_birth=date_of_birth,
        mechanic=mechanic,
        already_relieved_gross=money(already_relieved),
    )


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
        request("10000", "60000", ReliefMechanic.RELIEF_AT_SOURCE), TAX_YEAR_2026_27
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
        request("10000", "60000", ReliefMechanic.NET_PAY), TAX_YEAR_2026_27
    )
    assert outcome.gross_to_pot == money("10000")
    assert outcome.member_cash_cost == money("10000")
    assert outcome.provider_relief == money("0")
    assert outcome.taxable_pay_deduction == money("10000")
    assert outcome.assessment_relief_gross == money("0")


def test_no_mechanic_is_plain_taxed_cash(rules: UkContributionRuleset) -> None:
    """An ISA-style contribution: no relief, no limit, no assessment."""
    outcome = rules.member_contribution(request("5000", "0", None), TAX_YEAR_2026_27)
    assert outcome.gross_to_pot == money("5000")
    assert outcome.member_cash_cost == money("5000")
    assert outcome.provider_relief == money("0")
    assert outcome.unrelieved_excess == money("0")


def test_no_earner_keeps_the_basic_amount_via_ras(
    rules: UkContributionRuleset,
) -> None:
    """gov.uk: with no earnings, £2,880 net grosses up to £3,600."""
    outcome = rules.member_contribution(
        request("3600", "0", ReliefMechanic.RELIEF_AT_SOURCE), TAX_YEAR_2026_27
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
        request("5000", "0", ReliefMechanic.RELIEF_AT_SOURCE), TAX_YEAR_2026_27
    )
    assert outcome.gross_to_pot == money("3600")
    assert outcome.unrelieved_excess == money("1400")


def test_ras_relief_limited_to_earnings_above_basic_amount(
    rules: UkContributionRuleset,
) -> None:
    """Relief caps at 100% of relevant earnings once above the floor."""
    outcome = rules.member_contribution(
        request("25000", "20000", ReliefMechanic.RELIEF_AT_SOURCE), TAX_YEAR_2026_27
    )
    assert outcome.gross_to_pot == money("20000")
    assert outcome.member_cash_cost == money("16000")
    assert outcome.provider_relief == money("4000")
    assert outcome.unrelieved_excess == money("5000")


def test_net_pay_gets_no_basic_amount(rules: UkContributionRuleset) -> None:
    """The £3,600 floor is relief-at-source only: net pay needs earnings."""
    outcome = rules.member_contribution(
        request("3600", "0", ReliefMechanic.NET_PAY), TAX_YEAR_2026_27
    )
    assert outcome.gross_to_pot == money("0")
    assert outcome.unrelieved_excess == money("3600")


def test_net_pay_cannot_exceed_pay(rules: UkContributionRuleset) -> None:
    """A net-pay deduction is limited to the pay it comes out of."""
    outcome = rules.member_contribution(
        request("30000", "25000", ReliefMechanic.NET_PAY), TAX_YEAR_2026_27
    )
    assert outcome.gross_to_pot == money("25000")
    assert outcome.unrelieved_excess == money("5000")


def test_relief_limit_is_shared_across_wrappers(
    rules: UkContributionRuleset,
) -> None:
    """PTM044220: the earnings limit aggregates over every scheme.

    With £60,000 earnings, a £40,000 SIPP contribution after £40,000
    already relieved elsewhere leaves only £20,000 of headroom.
    """
    outcome = rules.member_contribution(
        request(
            "40000",
            "60000",
            ReliefMechanic.RELIEF_AT_SOURCE,
            already_relieved="40000",
        ),
        TAX_YEAR_2026_27,
    )
    assert outcome.gross_to_pot == money("20000")
    assert outcome.provider_relief == money("4000")
    assert outcome.unrelieved_excess == money("20000")


def test_basic_amount_is_not_repeated_per_wrapper(
    rules: UkContributionRuleset,
) -> None:
    """A no-earner who used the £3,600 floor gets nothing in a second pot."""
    outcome = rules.member_contribution(
        request(
            "3600",
            "0",
            ReliefMechanic.RELIEF_AT_SOURCE,
            already_relieved="3600",
        ),
        TAX_YEAR_2026_27,
    )
    assert outcome.gross_to_pot == money("0")
    assert outcome.unrelieved_excess == money("3600")


def test_net_pay_headroom_is_reduced_by_prior_relief(
    rules: UkContributionRuleset,
) -> None:
    """The aggregate limit spans mechanics: prior relief shrinks net pay too."""
    outcome = rules.member_contribution(
        request("10000", "12000", ReliefMechanic.NET_PAY, already_relieved="8000"),
        TAX_YEAR_2026_27,
    )
    assert outcome.gross_to_pot == money("4000")
    assert outcome.unrelieved_excess == money("6000")


def test_no_relief_from_age_75(rules: UkContributionRuleset) -> None:
    """FA 2004 s188(3)(a): contributions from 75 are never relievable."""
    born_1950 = date(1950, 1, 1)  # 76 throughout 2026/27
    outcome = rules.member_contribution(
        request("3600", "0", ReliefMechanic.RELIEF_AT_SOURCE, date_of_birth=born_1950),
        TAX_YEAR_2026_27,
    )
    assert outcome.gross_to_pot == money("0")
    assert outcome.provider_relief == money("0")
    assert outcome.unrelieved_excess == money("3600")


def test_relief_stops_in_the_period_of_the_75th_birthday(
    rules: UkContributionRuleset,
) -> None:
    """A mid-period 75th birthday shuts relief for the whole period.

    Conservative at annual resolution (§4.1): contributions after the
    birthday could not be relieved, so the model grants none. The
    period before is unaffected.
    """
    born_1952 = date(1952, 1, 1)  # turns 75 on 1 January 2027, mid tax year
    denied = rules.member_contribution(
        request(
            "10000",
            "60000",
            ReliefMechanic.NET_PAY,
            date_of_birth=born_1952,
        ),
        TAX_YEAR_2026_27,
    )
    assert denied.gross_to_pot == money("0")
    assert denied.unrelieved_excess == money("10000")


def test_request_rejects_negative_gross() -> None:
    """A negative contribution is a construction-time input error."""
    negative = money("-1")
    earnings = money("10000")
    zero = money("0")
    with pytest.raises(ValueError, match="non-negative"):
        MemberContributionRequest(
            gross=negative,
            relevant_earnings=earnings,
            date_of_birth=DOB_1980,
            mechanic=None,
            already_relieved_gross=zero,
        )


@pytest.mark.parametrize(
    "mechanic", [ReliefMechanic.RELIEF_AT_SOURCE, None], ids=["ras", "no-relief"]
)
def test_uncovered_period_is_rejected(
    rules: UkContributionRuleset, mechanic: ReliefMechanic | None
) -> None:
    """Periods outside shipped data fail loudly on every mechanic path."""
    uncovered = Period(start=date(2027, 4, 6), end=date(2028, 4, 5))
    contribution = request("100", "100", mechanic)
    with pytest.raises(UkContributionError, match="no shipped tax-year data"):
        rules.member_contribution(contribution, uncovered)


def test_empty_ruleset_is_rejected() -> None:
    """A ruleset needs at least one tax-year file."""
    with pytest.raises(UkContributionError, match="at least one"):
        UkContributionRuleset(tax_years=())


def test_ruleset_satisfies_core_protocol(rules: UkContributionRuleset) -> None:
    """``UkContributionRuleset`` is usable via the core protocol."""
    protocol_typed: ContributionRuleset = rules
    outcome = protocol_typed.member_contribution(
        request("100", "0", None), TAX_YEAR_2026_27
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


def test_adjusted_income_adds_employer_pension_inputs() -> None:
    """Adjusted income keeps member amounts and adds employer-funded input.

    The input covers DC employer contributions and DB pension input
    net of member contributions alike (PTM057100): £220,000 income
    with £60,000 of DB input gives £280,000 adjusted income.
    """
    result = adjusted_income(
        total_income=money("220000"), employer_pension_inputs=money("60000")
    )
    assert result == money("280000")


def test_taper_income_helpers_reject_negatives() -> None:
    """Negative income components are input errors."""
    negative = money("-1")
    with pytest.raises(UkContributionError, match="total_income"):
        adjusted_income(total_income=negative, employer_pension_inputs=negative)


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
            money_purchase_inputs=zero,
            other_inputs=zero,
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


# --- AA carry-forward (issue 9.5) -------------------------------------------


def assessed(
    pension: PensionRules,
    money_purchase: str,
    other: str = "0",
    *,
    mpaa_active: bool = False,
    annual_allowance: Money | None = None,
) -> AnnualAllowanceAssessment:
    """Assess one year against the (possibly overridden) allowance."""
    allowance = (
        annual_allowance if annual_allowance is not None else pension.annual_allowance
    )
    return assess_annual_allowance(
        pension,
        annual_allowance=allowance,
        money_purchase_inputs=money(money_purchase),
        other_inputs=money(other),
        mpaa_active=mpaa_active,
    )


def test_carry_forward_covers_the_excess(pension: PensionRules) -> None:
    """£70,000 of inputs against £60,000 draws £10,000 from the pool."""
    assessment = assessed(pension, "70000")
    pool = (money("5000"), money("5000"), money("5000"))
    outcome = apply_carry_forward(pension, assessment, pool)
    assert outcome.chargeable_excess == money("0")
    assert outcome.used == (money("5000"), money("5000"), money("0"))
    assert outcome.remaining == (money("0"), money("0"), money("5000"))


def test_carry_forward_draws_earliest_years_first(pension: PensionRules) -> None:
    """A £5,000 excess takes all of year one, then part of year two."""
    assessment = assessed(pension, "65000")
    outcome = apply_carry_forward(pension, assessment, (money("4000"), money("3000")))
    assert outcome.chargeable_excess == money("0")
    assert outcome.used == (money("4000"), money("1000"))
    assert outcome.remaining == (money("0"), money("2000"))


def test_insufficient_carry_forward_leaves_a_charge(pension: PensionRules) -> None:
    """Whatever the pool cannot cover stays chargeable."""
    assessment = assessed(pension, "70000")
    outcome = apply_carry_forward(pension, assessment, (money("4000"),))
    assert outcome.chargeable_excess == money("6000")
    assert outcome.remaining == (money("0"),)


def test_no_excess_leaves_the_pool_untouched(pension: PensionRules) -> None:
    """Carry-forward is drawn only to the extent an excess needs it."""
    assessment = assessed(pension, "30000")
    outcome = apply_carry_forward(pension, assessment, (money("10000"),))
    assert outcome.chargeable_excess == money("0")
    assert outcome.used == (money("0"),)
    assert outcome.remaining == (money("10000"),)


def test_carry_forward_never_offsets_the_mpaa_excess(pension: PensionRules) -> None:
    """The money-purchase excess is a floor no pool can reach (HS345)."""
    assessment = assessed(pension, "25000", mpaa_active=True)
    outcome = apply_carry_forward(pension, assessment, (money("20000"),))
    assert outcome.chargeable_excess == money("15000")
    assert outcome.used == (money("0"),)


def test_carry_forward_tops_up_the_alternative_allowance(
    pension: PensionRules,
) -> None:
    """DB input over the alternative AA is offsettable; the MP excess is not."""
    assessment = assessed(pension, "15000", "55000", mpaa_active=True)
    # Before: alternative 5,000 + 5,000 ties default 70,000 - 60,000.
    # The pool lifts both computations down to the 5,000 MP floor.
    outcome = apply_carry_forward(pension, assessment, (money("10000"),))
    assert outcome.chargeable_excess == money("5000")
    assert outcome.used == (money("5000"),)


def test_carry_forward_rejects_a_pool_beyond_the_window(
    pension: PensionRules,
) -> None:
    """More than aa_carry_forward_years entries is an input error."""
    assessment = assessed(pension, "0")
    pool = (money("1"), money("1"), money("1"), money("1"))
    with pytest.raises(UkContributionError, match="previous 3 tax years"):
        apply_carry_forward(pension, assessment, pool)


def test_carry_forward_rejects_negative_entries(pension: PensionRules) -> None:
    """A negative pool entry is an input error."""
    assessment = assessed(pension, "0")
    pool = (money("-1"),)
    with pytest.raises(UkContributionError, match="carry_forward"):
        apply_carry_forward(pension, assessment, pool)


def test_unused_allowance_survives_for_future_years(pension: PensionRules) -> None:
    """£20,000 of inputs against £60,000 leaves £40,000 to carry."""
    assessment = assessed(pension, "20000")
    assert carry_forward_generated(assessment, scheme_member=True) == money("40000")


def test_only_unused_alternative_allowance_carries_when_mpaa_exceeded(
    pension: PensionRules,
) -> None:
    """Unused MPAA headroom never carries forward (HS345)."""
    assessment = assessed(pension, "15000", "20000", mpaa_active=True)
    # Money-purchase inputs over the MPAA: 50,000 alternative
    # allowance less 20,000 of DB input carries, nothing more.
    assert carry_forward_generated(assessment, scheme_member=True) == money("30000")


def test_within_the_mpaa_the_default_basis_generates(pension: PensionRules) -> None:
    """MP inputs inside the MPAA keep the normal AA basis (PTM056510)."""
    assessment = assessed(pension, "5000", "20000", mpaa_active=True)
    # No money-purchase excess, so the full 60,000 allowance less the
    # 25,000 total inputs carries despite the trigger.
    assert carry_forward_generated(assessment, scheme_member=True) == money("35000")


def test_a_year_without_scheme_membership_generates_nothing(
    pension: PensionRules,
) -> None:
    """No registered-scheme membership, no carry-forward from that year."""
    assessment = assessed(pension, "0")
    assert carry_forward_generated(assessment, scheme_member=False) == money("0")


def test_a_tapered_year_carries_its_tapered_headroom(pension: PensionRules) -> None:
    """Unused allowance from a tapered year is measured off the tapered AA."""
    tapered = tapered_annual_allowance(
        pension, threshold=money("210000"), adjusted=money("300000")
    )
    assessment = assessed(pension, "10000", annual_allowance=tapered)
    # The £40,000 tapered allowance less £10,000 of inputs.
    assert carry_forward_generated(assessment, scheme_member=True) == money("30000")


def test_rolling_the_pool_expires_the_oldest_year(pension: PensionRules) -> None:
    """A full window drops its earliest entry as the new year joins."""
    remaining = (money("0"), money("2000"), money("5000"))
    rolled = roll_carry_forward(pension, remaining, money("40000"))
    assert rolled == (money("2000"), money("5000"), money("40000"))


def test_rolling_a_short_pool_keeps_every_year(pension: PensionRules) -> None:
    """Years still inside the window survive the roll."""
    rolled = roll_carry_forward(pension, (money("3000"),), money("1000"))
    assert rolled == (money("3000"), money("1000"))


def test_a_zero_year_window_rolls_to_an_empty_pool(pension: PensionRules) -> None:
    """A regime without carry-forward keeps no pool at all."""
    no_carry = replace(pension, aa_carry_forward_years=0)
    rolled = roll_carry_forward(no_carry, (), money("5000"))
    assert rolled == ()


def test_rolling_rejects_a_pool_beyond_the_window(pension: PensionRules) -> None:
    """The rolled pool is validated like the applied one."""
    pool = (money("1"), money("1"), money("1"), money("1"))
    generated = money("0")
    with pytest.raises(UkContributionError, match="previous 3 tax years"):
        roll_carry_forward(pension, pool, generated)


def test_unused_allowance_expires_after_three_years(pension: PensionRules) -> None:
    """The gov.uk 3-year rule end-to-end: year-one headroom lapses unused."""
    opening = assessed(pension, "20000")  # generates 40,000
    pool = roll_carry_forward(
        pension, (), carry_forward_generated(opening, scheme_member=True)
    )
    for _ in range(3):  # three exactly-full years use none of it
        assessment = assessed(pension, "60000")
        outcome = apply_carry_forward(pension, assessment, pool)
        pool = roll_carry_forward(
            pension,
            outcome.remaining,
            carry_forward_generated(assessment, scheme_member=True),
        )
    over = apply_carry_forward(pension, assessed(pension, "70000"), pool)
    assert over.chargeable_excess == money("10000")


# --- DB pension input amount (issue 9.6) ------------------------------------


def test_db_input_is_sixteen_times_the_pension_growth(
    pension: PensionRules,
) -> None:
    """PTM053301: £1,000 of new pension with flat CPI is a £16,000 input."""
    amount = db_pension_input_amount(
        pension,
        opening_annual=money("10000"),
        closing_annual=money("11000"),
        cpi=Decimal(0),
    )
    assert amount == money("16000")


def test_db_input_uprates_the_opening_value_by_cpi(pension: PensionRules) -> None:
    """s235: only growth beyond CPI counts.

    Opening £10,000 uprated by 3% is £10,300; closing £11,000 leaves
    £700 x 16 = £11,200.
    """
    amount = db_pension_input_amount(
        pension,
        opening_annual=money("10000"),
        closing_annual=money("11000"),
        cpi=Decimal("0.03"),
    )
    assert amount == money("11200")


def test_db_input_is_nil_when_revaluation_stays_within_cpi(
    pension: PensionRules,
) -> None:
    """A deferred arrangement tracking CPI generates no input amount."""
    amount = db_pension_input_amount(
        pension,
        opening_annual=money("10000"),
        closing_annual=money("10300"),
        cpi=Decimal("0.03"),
    )
    assert amount == money("0")


def test_db_input_negative_difference_is_nil(pension: PensionRules) -> None:
    """PTM053301: a shrinking value is nil, never negative."""
    amount = db_pension_input_amount(
        pension,
        opening_annual=money("10000"),
        closing_annual=money("9000"),
        cpi=Decimal(0),
    )
    assert amount == money("0")


def test_db_input_deflation_never_shrinks_the_opening_value(
    pension: PensionRules,
) -> None:
    """Deflation leaves the opening value alone: the s235 uplift is a floor."""
    amount = db_pension_input_amount(
        pension,
        opening_annual=money("10000"),
        closing_annual=money("10500"),
        cpi=Decimal("-0.02"),
    )
    assert amount == money("8000")


def test_db_input_first_year_measures_the_whole_pension(
    pension: PensionRules,
) -> None:
    """PTM053301: a nil opening value makes the whole closing value count."""
    amount = db_pension_input_amount(
        pension,
        opening_annual=money("0"),
        closing_annual=money("700"),
        cpi=Decimal("0.03"),
    )
    assert amount == money("11200")


def test_db_input_rejects_negative_amounts(pension: PensionRules) -> None:
    """Negative pension amounts are caller errors."""
    opening = money("-1")
    closing = money("0")
    cpi = Decimal(0)
    with pytest.raises(UkContributionError, match="opening_annual"):
        db_pension_input_amount(
            pension, opening_annual=opening, closing_annual=closing, cpi=cpi
        )


# --- the composed annual_allowance measurement (issue #116) ------------------


def measurement_of(
    *,
    member: str = "0",
    employer: str = "0",
    db: tuple[DbArrangementInput, ...] = (),
    total_income: str = "50000",
    net_pay: str = "0",
    ras: str = "0",
    cpi: str = "0",
    mpaa_triggered_on: date | None = None,
    scheme_member: bool = True,
    carry_forward: tuple[str, ...] = (),
) -> AnnualAllowanceMeasurement:
    """One period's measurement with hand-set inputs."""
    return AnnualAllowanceMeasurement(
        member_money_purchase=money(member),
        employer_money_purchase=money(employer),
        db_arrangements=db,
        total_income=money(total_income),
        net_pay_contributions=money(net_pay),
        relief_at_source_gross=money(ras),
        cpi=Decimal(cpi),
        mpaa_triggered_on=mpaa_triggered_on,
        scheme_member=scheme_member,
        carry_forward=tuple(money(entry) for entry in carry_forward),
    )


def test_inputs_within_the_allowance_generate_carry_forward(
    rules: UkContributionRuleset,
) -> None:
    """£20,000 of inputs against the £60,000 AA: no excess, £40,000 carried."""
    outcome = rules.annual_allowance(
        measurement_of(member="15000", employer="5000"), TAX_YEAR_2026_27
    )
    assert outcome.chargeable_excess == money("0")
    assert outcome.carry_forward == (money("40000"),)


def test_excess_above_the_allowance_is_chargeable(
    rules: UkContributionRuleset,
) -> None:
    """£70,000 of inputs: £10,000 chargeable, nothing carried forward."""
    outcome = rules.annual_allowance(
        measurement_of(member="50000", employer="20000", total_income="80000"),
        TAX_YEAR_2026_27,
    )
    assert outcome.chargeable_excess == money("10000")
    assert outcome.carry_forward == (money("0"),)


def test_taper_reduces_the_allowance(rules: UkContributionRuleset) -> None:
    """£300,000 income tapers the AA to £40,000: £60,000 in leaves £20,000.

    Threshold income is 240,000 (60,000 relief-at-source deducted) and
    adjusted income 300,000 — 40,000 over the limit, so the allowance
    drops by 20,000.
    """
    outcome = rules.annual_allowance(
        measurement_of(member="60000", total_income="300000", ras="60000"),
        TAX_YEAR_2026_27,
    )
    assert outcome.chargeable_excess == money("20000")


def test_mpaa_floors_the_excess_against_carry_forward(
    rules: UkContributionRuleset,
) -> None:
    """Post-trigger money-purchase inputs over the MPAA resist the pool.

    £20,000 after flexible access exceeds the £10,000 MPAA by £10,000
    — a floor no carry-forward can offset (HS345), however much the
    pool holds.
    """
    outcome = rules.annual_allowance(
        measurement_of(
            member="20000",
            mpaa_triggered_on=date(2025, 1, 1),
            carry_forward=("50000",),
        ),
        TAX_YEAR_2026_27,
    )
    assert outcome.chargeable_excess == money("10000")


def test_a_trigger_after_the_period_leaves_the_full_allowance(
    rules: UkContributionRuleset,
) -> None:
    """Inputs made before the trigger period measure against the full AA."""
    outcome = rules.annual_allowance(
        measurement_of(member="20000", mpaa_triggered_on=date(2027, 6, 1)),
        TAX_YEAR_2026_27,
    )
    assert outcome.chargeable_excess == money("0")
    assert outcome.carry_forward == (money("40000"),)


def test_carry_forward_offsets_earliest_first(
    rules: UkContributionRuleset,
) -> None:
    """A £10,000 excess draws £5,000 then £3,000, leaving £2,000 charged."""
    outcome = rules.annual_allowance(
        measurement_of(
            member="70000", total_income="80000", carry_forward=("5000", "3000")
        ),
        TAX_YEAR_2026_27,
    )
    assert outcome.chargeable_excess == money("2000")
    assert outcome.carry_forward == (money("0"), money("0"), money("0"))


def test_db_arrangements_value_into_pension_inputs(
    rules: UkContributionRuleset,
) -> None:
    """A DB input of 16 x (11,000 - 10,000 x 1.02) joins the measure.

    The £12,800 DB amount on top of £50,000 money purchase makes
    £62,800 of pension inputs — £2,800 over the allowance.
    """
    arrangement = DbArrangementInput(
        opening_annual=money("10000"), closing_annual=money("11000")
    )
    outcome = rules.annual_allowance(
        measurement_of(member="50000", db=(arrangement,), cpi="0.02"),
        TAX_YEAR_2026_27,
    )
    assert outcome.chargeable_excess == money("2800")


def test_no_scheme_membership_generates_no_carry_forward(
    rules: UkContributionRuleset,
) -> None:
    """A year without membership carries nothing, however unused the AA."""
    outcome = rules.annual_allowance(
        measurement_of(scheme_member=False), TAX_YEAR_2026_27
    )
    assert outcome.chargeable_excess == money("0")
    assert outcome.carry_forward == (money("0"),)


def test_a_pool_beyond_a_shrunken_window_expires_oldest_first(
    rules: UkContributionRuleset,
) -> None:
    """A pool longer than the year's window keeps its most recent years.

    Only possible if a data file ever shrinks the window between
    years; the four-entry pool trims to its three most recent, so the
    40,000 oldest year expires and 5,000 + 3,000 + 0 set against the
    10,000 excess leaves 2,000 charged — never a failed run.
    """
    outcome = rules.annual_allowance(
        measurement_of(
            member="70000",
            total_income="80000",
            carry_forward=("40000", "5000", "3000", "0"),
        ),
        TAX_YEAR_2026_27,
    )
    assert outcome.chargeable_excess == money("2000")
    assert outcome.carry_forward == (money("0"), money("0"), money("0"))
