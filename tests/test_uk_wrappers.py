"""Tests for the UK wrapper ruleset (issue 3.1).

Acceptance criterion: tax treatment in/during/out resolved per wrapper
kind — pensions EET with the data file's tax-free lump-sum fraction,
ISAs TEE — plus limits, relief mechanics, and NMPA-gated access.
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
    ISA_KIND,
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

DOB_OVER_NMPA = date(1970, 1, 1)  # 56 when 2026/27 starts: access open
DOB_UNDER_NMPA = date(2000, 1, 1)  # 26 when 2026/27 starts: access shut
DOB_CAUGHT_BY_STEP = date(1972, 6, 1)  # 55 before the 2028 step, 57 in 2029

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


def test_isa_is_tee(ruleset: UkWrapperRuleset) -> None:
    """ISA money: taxed in, tax-free growth, tax-free out."""
    treatment = ruleset.tax_treatment(ISA_KIND, TAX_YEAR_2026_27)
    assert treatment.contributions is ContributionTaxTreatment.FROM_TAXED_INCOME
    assert treatment.growth is GrowthTaxTreatment.TAX_FREE
    assert treatment.withdrawals is WithdrawalTaxTreatment.TAX_FREE
    assert treatment.tax_free_fraction is None


def test_isa_contribution_limit_comes_from_data(ruleset: UkWrapperRuleset) -> None:
    """The ISA cap is the year's annual allowance from the data file."""
    limit = ruleset.annual_contribution_limit(ISA_KIND, TAX_YEAR_2026_27)
    assert limit == load_tax_year(2026).isa.annual_allowance


def test_sub_period_limit_is_the_full_year_figure(ruleset: UkWrapperRuleset) -> None:
    """Apportionment over part-years is the consumer's concern (3.2)."""
    part = Period(start=date(2026, 4, 6), end=date(2026, 12, 31))
    full = ruleset.annual_contribution_limit(ISA_KIND, TAX_YEAR_2026_27)
    assert ruleset.annual_contribution_limit(ISA_KIND, part) == full


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
    assert ruleset.annual_contribution_limit(kind, TAX_YEAR_2026_27) is None


def test_relief_mechanics_per_kind(ruleset: UkWrapperRuleset) -> None:
    """Workplace DC may use either mechanic; SIPPs RAS; ISAs none."""
    both = frozenset({ReliefMechanic.RELIEF_AT_SOURCE, ReliefMechanic.NET_PAY})
    assert ruleset.permitted_relief_mechanics(WORKPLACE_DC_KIND) == both
    assert ruleset.permitted_relief_mechanics(SIPP_KIND) == frozenset(
        {ReliefMechanic.RELIEF_AT_SOURCE}
    )
    assert ruleset.permitted_relief_mechanics(ISA_KIND) == frozenset()


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


def test_isa_access_has_no_age_gate(ruleset: UkWrapperRuleset) -> None:
    """ISAs are accessible at any age."""
    assert ruleset.is_access_open(ISA_KIND, DOB_UNDER_NMPA, TAX_YEAR_2026_27)


def test_unknown_kind_rejected_by_tax_treatment(ruleset: UkWrapperRuleset) -> None:
    """An unknown kind is an error, never a default treatment."""
    with pytest.raises(UkWrapperError, match="unknown UK wrapper kind"):
        ruleset.tax_treatment(UNKNOWN_KIND, TAX_YEAR_2026_27)


def test_unknown_kind_rejected_by_contribution_limit(
    ruleset: UkWrapperRuleset,
) -> None:
    """An unknown kind is an error, never an uncapped contribution."""
    with pytest.raises(UkWrapperError, match="unknown UK wrapper kind"):
        ruleset.annual_contribution_limit(UNKNOWN_KIND, TAX_YEAR_2026_27)


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
    limit = ruleset.annual_contribution_limit(ISA_KIND, TAX_YEAR_2028_29)
    assert limit == load_tax_year(2026).isa.annual_allowance


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
