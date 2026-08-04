"""Tests for the UK wrapper ruleset (issues 3.1, 9.2).

Acceptance criterion: tax treatment in/during/out resolved per wrapper
kind — pensions EET with the data file's tax-free lump-sum fraction,
ISAs/LISAs TEE, GIA/cash taxable as growth arises — plus contribution
terms (shared allowance groups, the LISA bonus and window), relief
mechanics, and age-gated access (NMPA; LISA at 60).
"""

from datetime import date
from decimal import Decimal

import pytest

from glidepath.core import (
    ContributionTaxTreatment,
    GrowthTaxTreatment,
    Period,
    Rate,
    ReliefMechanic,
    WithdrawalTaxTreatment,
    WrapperKindId,
    WrapperRuleset,
)
from glidepath.regions.uk import (
    CASH_KIND,
    GIA_KIND,
    ISA_ALLOWANCE_GROUP,
    ISA_KIND,
    LISA_ALLOWANCE_GROUP,
    LISA_KIND,
    SIPP_KIND,
    WORKPLACE_DC_KIND,
    FutureYearsExtension,
    FutureYearsMode,
    FutureYearsPolicy,
    UkAgeRules,
    UkWrapperError,
    UkWrapperRuleset,
    load_tax_year,
)

TAX_YEAR_2026_27 = Period(start=date(2026, 4, 6), end=date(2027, 4, 5))
TAX_YEAR_2028_29 = Period(start=date(2028, 4, 6), end=date(2029, 4, 5))
TAX_YEAR_2030_31 = Period(start=date(2030, 4, 6), end=date(2031, 4, 5))

UNKNOWN_KIND = WrapperKindId("uk.mattress")
PENSION_KINDS = [WORKPLACE_DC_KIND, SIPP_KIND]
TAXABLE_KINDS = [GIA_KIND, CASH_KIND]

DOB_OVER_NMPA = date(1970, 1, 1)  # 56 when 2026/27 starts: access open
DOB_UNDER_NMPA = date(2000, 1, 1)  # 26 when 2026/27 starts: access shut
DOB_CAUGHT_BY_STEP = date(1972, 6, 1)  # 55 before the 2028 step, 57 in 2029
DOB_OVER_LISA_ACCESS = date(1965, 1, 1)  # 61 when 2026/27 starts
DOB_TURNS_50_MID_YEAR = date(1976, 10, 6)  # 50 on 2026-10-06: window halves
DOB_OVER_50 = date(1970, 1, 1)  # contribution window closed

FROZEN_EXTENSION = FutureYearsExtension(
    policy=FutureYearsPolicy(mode=FutureYearsMode.FROZEN), cpi=Rate(Decimal("0.02"))
)


@pytest.fixture(scope="module", name="ruleset")
def ruleset_fixture() -> UkWrapperRuleset:
    """A ruleset over the shipped data files."""
    return UkWrapperRuleset.from_shipped_data()


@pytest.mark.parametrize("kind", PENSION_KINDS)
def test_pension_kinds_are_eet_with_the_data_fraction(
    ruleset: UkWrapperRuleset, kind: WrapperKindId
) -> None:
    """Pension money: relieved in, tax-free growth, partially free out."""
    treatment = ruleset.tax_treatment(kind, TAX_YEAR_2026_27)
    assert treatment.contributions is ContributionTaxTreatment.TAX_RELIEVED
    assert treatment.growth is GrowthTaxTreatment.TAX_FREE
    assert treatment.withdrawals is WithdrawalTaxTreatment.PARTIALLY_TAX_FREE
    assert (
        treatment.tax_free_fraction
        == load_tax_year(2026).pension.tax_free_lump_sum_fraction
    )


@pytest.mark.parametrize("kind", [ISA_KIND, LISA_KIND])
def test_isa_kinds_are_tee(ruleset: UkWrapperRuleset, kind: WrapperKindId) -> None:
    """ISA/LISA money: taxed in, tax-free growth, tax-free out."""
    treatment = ruleset.tax_treatment(kind, TAX_YEAR_2026_27)
    assert treatment.contributions is ContributionTaxTreatment.FROM_TAXED_INCOME
    assert treatment.growth is GrowthTaxTreatment.TAX_FREE
    assert treatment.withdrawals is WithdrawalTaxTreatment.TAX_FREE
    assert treatment.tax_free_fraction is None


@pytest.mark.parametrize("kind", TAXABLE_KINDS)
def test_taxable_kinds_are_taxed_as_growth_arises(
    ruleset: UkWrapperRuleset, kind: WrapperKindId
) -> None:
    """GIA/cash money: taxed in, taxable growth, tax-free out."""
    treatment = ruleset.tax_treatment(kind, TAX_YEAR_2026_27)
    assert treatment.contributions is ContributionTaxTreatment.FROM_TAXED_INCOME
    assert treatment.growth is GrowthTaxTreatment.TAXABLE
    assert treatment.withdrawals is WithdrawalTaxTreatment.TAX_FREE
    assert treatment.tax_free_fraction is None


def test_isa_contribution_cap_comes_from_data(ruleset: UkWrapperRuleset) -> None:
    """The ISA cap is the year's annual allowance under the shared group."""
    terms = ruleset.contribution_terms(ISA_KIND, DOB_UNDER_NMPA, TAX_YEAR_2026_27)
    (cap,) = terms.caps
    assert cap.group == ISA_ALLOWANCE_GROUP
    assert cap.limit == load_tax_year(2026).isa.annual_allowance
    assert terms.bonus_rate is None
    assert terms.window_fraction == Decimal(1)


def test_lisa_terms_carry_sub_cap_bonus_and_window(
    ruleset: UkWrapperRuleset,
) -> None:
    """A LISA consumes its own allowance and the overall ISA allowance."""
    terms = ruleset.contribution_terms(LISA_KIND, DOB_UNDER_NMPA, TAX_YEAR_2026_27)
    year = load_tax_year(2026)
    assert [(cap.group, cap.limit) for cap in terms.caps] == [
        (LISA_ALLOWANCE_GROUP, year.isa.lisa_allowance),
        (ISA_ALLOWANCE_GROUP, year.isa.annual_allowance),
    ]
    assert terms.bonus_rate == year.isa.lisa_bonus_rate
    assert terms.window_fraction == Decimal(1)


def test_lisa_window_halves_in_the_50th_birthday_year(
    ruleset: UkWrapperRuleset,
) -> None:
    """The contribution window ends at 50, pro-rated by whole months."""
    terms = ruleset.contribution_terms(
        LISA_KIND, DOB_TURNS_50_MID_YEAR, TAX_YEAR_2026_27
    )
    assert terms.window_fraction == Decimal(1) / Decimal(2)


def test_lisa_window_is_closed_past_50(ruleset: UkWrapperRuleset) -> None:
    """No LISA contributions (or bonus) once the window has closed."""
    terms = ruleset.contribution_terms(LISA_KIND, DOB_OVER_50, TAX_YEAR_2026_27)
    assert terms.window_fraction == Decimal(0)


@pytest.mark.parametrize("kind", TAXABLE_KINDS)
def test_taxable_kinds_are_uncapped(
    ruleset: UkWrapperRuleset, kind: WrapperKindId
) -> None:
    """GIA and cash contributions meet no allowance machinery."""
    terms = ruleset.contribution_terms(kind, DOB_UNDER_NMPA, TAX_YEAR_2026_27)
    assert terms.caps == ()
    assert terms.bonus_rate is None
    assert terms.window_fraction == Decimal(1)


def test_sub_period_cap_is_the_full_year_figure(ruleset: UkWrapperRuleset) -> None:
    """Apportionment over part-years is the consumer's concern (3.2)."""
    part = Period(start=date(2026, 4, 6), end=date(2026, 12, 31))
    full = ruleset.contribution_terms(ISA_KIND, DOB_UNDER_NMPA, TAX_YEAR_2026_27)
    assert ruleset.contribution_terms(ISA_KIND, DOB_UNDER_NMPA, part) == full


def test_lump_sum_allowance_comes_from_data(ruleset: UkWrapperRuleset) -> None:
    """The LSA is the year's figure from the data file (roadmap 5.2)."""
    allowance = ruleset.lump_sum_allowance(TAX_YEAR_2026_27)
    assert allowance == load_tax_year(2026).pension.lump_sum_allowance


def test_lump_sum_allowance_outside_coverage_fails_loudly(
    ruleset: UkWrapperRuleset,
) -> None:
    """A query outside the shipped years never answers from the wrong year."""
    uncovered = Period(start=date(1999, 4, 6), end=date(2000, 4, 5))
    with pytest.raises(UkWrapperError, match="no shipped tax-year data"):
        ruleset.lump_sum_allowance(uncovered)


@pytest.mark.parametrize("kind", PENSION_KINDS)
def test_pension_kinds_have_no_per_kind_cap(
    ruleset: UkWrapperRuleset, kind: WrapperKindId
) -> None:
    """The annual allowance is a cross-pension measure (roadmap 3.3)."""
    terms = ruleset.contribution_terms(kind, DOB_UNDER_NMPA, TAX_YEAR_2026_27)
    assert terms.caps == ()


def test_relief_mechanics_per_kind(ruleset: UkWrapperRuleset) -> None:
    """Workplace DC may use either mechanic; SIPPs RAS; savings kinds none."""
    both = frozenset({ReliefMechanic.RELIEF_AT_SOURCE, ReliefMechanic.NET_PAY})
    assert ruleset.permitted_relief_mechanics(WORKPLACE_DC_KIND) == both
    assert ruleset.permitted_relief_mechanics(SIPP_KIND) == frozenset(
        {ReliefMechanic.RELIEF_AT_SOURCE}
    )
    for kind in (ISA_KIND, LISA_KIND, GIA_KIND, CASH_KIND):
        assert ruleset.permitted_relief_mechanics(kind) == frozenset()


@pytest.mark.parametrize("kind", PENSION_KINDS)
def test_pension_access_follows_the_nmpa_gate(
    ruleset: UkWrapperRuleset, kind: WrapperKindId
) -> None:
    """Pension access is open only once the NMPA is attained (§4.1)."""
    assert ruleset.is_access_open(kind, DOB_OVER_NMPA, TAX_YEAR_2026_27)
    assert not ruleset.is_access_open(kind, DOB_UNDER_NMPA, TAX_YEAR_2026_27)


def test_the_2028_step_catches_the_55_to_57_cohort(
    ruleset: UkWrapperRuleset,
) -> None:
    """Old enough under NMPA 55, not under 57: shut until 57 is attained."""
    dob = DOB_CAUGHT_BY_STEP
    assert not ruleset.is_access_open(SIPP_KIND, dob, TAX_YEAR_2028_29)
    assert ruleset.is_access_open(SIPP_KIND, dob, TAX_YEAR_2030_31)


@pytest.mark.parametrize("kind", [ISA_KIND, GIA_KIND, CASH_KIND])
def test_ungated_kinds_have_no_age_gate(
    ruleset: UkWrapperRuleset, kind: WrapperKindId
) -> None:
    """ISAs, GIAs and cash are accessible at any age."""
    assert ruleset.is_access_open(kind, DOB_UNDER_NMPA, TAX_YEAR_2026_27)


def test_lisa_access_gates_at_60(ruleset: UkWrapperRuleset) -> None:
    """Charge-free LISA access opens at 60 (§4.1 gate convention)."""
    assert ruleset.is_access_open(LISA_KIND, DOB_OVER_LISA_ACCESS, TAX_YEAR_2026_27)
    assert not ruleset.is_access_open(LISA_KIND, DOB_OVER_NMPA, TAX_YEAR_2026_27)


def test_unknown_kind_rejected_by_tax_treatment(ruleset: UkWrapperRuleset) -> None:
    """An unknown kind is an error, never a default treatment."""
    with pytest.raises(UkWrapperError, match="unknown UK wrapper kind"):
        ruleset.tax_treatment(UNKNOWN_KIND, TAX_YEAR_2026_27)


def test_unknown_kind_rejected_by_contribution_terms(
    ruleset: UkWrapperRuleset,
) -> None:
    """An unknown kind is an error, never an uncapped contribution."""
    with pytest.raises(UkWrapperError, match="unknown UK wrapper kind"):
        ruleset.contribution_terms(UNKNOWN_KIND, DOB_UNDER_NMPA, TAX_YEAR_2026_27)


def test_unknown_kind_rejected_by_relief_mechanics(
    ruleset: UkWrapperRuleset,
) -> None:
    """An unknown kind is an error, never relief-free."""
    with pytest.raises(UkWrapperError, match="unknown UK wrapper kind"):
        ruleset.permitted_relief_mechanics(UNKNOWN_KIND)


def test_unknown_kind_rejected_by_access_gate(ruleset: UkWrapperRuleset) -> None:
    """An unknown kind is an error, never an open gate."""
    with pytest.raises(UkWrapperError, match="unknown UK wrapper kind"):
        ruleset.is_access_open(UNKNOWN_KIND, DOB_OVER_NMPA, TAX_YEAR_2026_27)


def test_figure_queries_fail_outside_data_coverage(
    ruleset: UkWrapperRuleset,
) -> None:
    """Even the ISA's year-independent treatment resolves its tax year."""
    uncovered = Period(start=date(1999, 4, 6), end=date(2000, 4, 5))
    with pytest.raises(UkWrapperError, match="no shipped tax-year data"):
        ruleset.tax_treatment(ISA_KIND, uncovered)


def test_extension_supplies_future_year_figures() -> None:
    """With a future-years extension, later periods resolve (frozen here)."""
    ruleset = UkWrapperRuleset.from_shipped_data(future_years=FROZEN_EXTENSION)
    terms = ruleset.contribution_terms(ISA_KIND, DOB_UNDER_NMPA, TAX_YEAR_2028_29)
    assert terms.caps[0].limit == load_tax_year(2026).isa.annual_allowance


def test_empty_ruleset_is_rejected() -> None:
    """A ruleset needs at least one tax-year file."""
    ages = UkAgeRules.from_shipped_data()
    with pytest.raises(UkWrapperError, match="at least one"):
        UkWrapperRuleset(tax_years=(), ages=ages)


def test_overlapping_years_are_rejected() -> None:
    """Duplicate or out-of-order year files are a construction error."""
    year = load_tax_year(2026)
    ages = UkAgeRules.from_shipped_data()
    with pytest.raises(UkWrapperError, match="ascending"):
        UkWrapperRuleset(tax_years=(year, year), ages=ages)


def test_uk_ruleset_satisfies_core_protocol(ruleset: UkWrapperRuleset) -> None:
    """``UkWrapperRuleset`` is usable wherever the core protocol is expected."""
    protocol_typed: WrapperRuleset = ruleset
    assert protocol_typed.is_access_open(ISA_KIND, DOB_UNDER_NMPA, TAX_YEAR_2026_27)
