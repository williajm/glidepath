"""Future-year extension tests (issue 2.5; planning §5.3, §7).

The acceptance criterion: ``policy.tax.future_years`` drives
extrapolation past the last shipped data file. Golden figures are
hand-worked from the shipped 2026/27 file with 2% assumed CPI.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from glidepath.core import AssumptionKey, Money, Period, Rate, TaxInput
from glidepath.regions.uk import (
    RUK_RESIDENCY,
    SCOTLAND_RESIDENCY,
    DataFileError,
    FutureYearsExtension,
    FutureYearsMode,
    FutureYearsPolicy,
    IncomeTaxSchedule,
    TaxBand,
    TaxYearFile,
    UkTaxError,
    UkTaxSystem,
    extend_tax_year,
    load_default_assumptions,
    load_tax_year,
)

CPI = Rate(Decimal("0.02"))

SCOTLAND_VALUE = {
    "lower_bands_frozen_until_tax_year": "2026/27",
    "upper_bands_frozen_until_tax_year": "2028/29",
}


@pytest.fixture(scope="module", name="base")
def base_fixture() -> TaxYearFile:
    """The last shipped tax-year file (2026/27)."""
    return load_tax_year(2026)


@pytest.fixture(scope="module", name="default_policy")
def default_policy_fixture() -> FutureYearsPolicy:
    """The policy parsed from the shipped default assumptions."""
    default = load_default_assumptions().get(AssumptionKey.POLICY_TAX_FUTURE_YEARS)
    return FutureYearsPolicy.from_assumption_value(default.value)


# --- policy parsing ---------------------------------------------------------


def test_shipped_default_parses(default_policy: FutureYearsPolicy) -> None:
    """The shipped default is frozen to 2030/31, then CPI-indexed (§7).

    The devolved Scottish band groups carry their own freeze ends: the
    lower bands hold only through the shipped 2026/27 year, the
    Higher/Advanced/Top group through the announced 2028/29 commitment.
    """
    assert default_policy.mode is FutureYearsMode.FROZEN_THEN_CPI_INDEXED
    assert default_policy.frozen_until_start_year == 2030
    assert default_policy.scotland is not None
    assert default_policy.scotland.lower_frozen_until_start_year == 2026
    assert default_policy.scotland.upper_frozen_until_start_year == 2028


def test_frozen_needs_no_freeze_end() -> None:
    """A bare frozen policy parses without ``frozen_until_tax_year``."""
    policy = FutureYearsPolicy.from_assumption_value({"mode": "frozen"})
    assert policy.mode is FutureYearsMode.FROZEN
    assert policy.frozen_until_start_year is None


@pytest.mark.parametrize(
    ("value", "problem"),
    [
        (Decimal("0.02"), "expected a table value"),
        ({}, "missing required key 'mode'"),
        ({"mode": Decimal(1)}, "expected a string"),
        ({"mode": "sideways"}, "unknown mode"),
        # Index-immediately is deliberately not a mode: it could contradict
        # a legislated freeze (planning §6). Use a lapsed freeze end instead.
        ({"mode": "cpi_indexed"}, "unknown mode"),
        ({"mode": "frozen", "extra": "x"}, "unknown keys: extra"),
        (
            {"mode": "frozen_then_cpi_indexed", "frozen_until_tax_year": 2030},
            "'YYYY/YY' string",
        ),
        (
            {"mode": "frozen_then_cpi_indexed", "frozen_until_tax_year": "2030-31"},
            "not 'YYYY/YY'",
        ),
        (
            {"mode": "frozen_then_cpi_indexed", "frozen_until_tax_year": "2030/32"},
            "suffix is not start",
        ),
        ({"mode": "frozen_then_cpi_indexed"}, "requires frozen_until_tax_year"),
        (
            {"mode": "frozen", "frozen_until_tax_year": "2030/31"},
            "does not take frozen_until_tax_year",
        ),
        (
            {"mode": "frozen_then_cpi_indexed", "frozen_until_tax_year": "2030/31"},
            "requires a scotland table",
        ),
        (
            {"mode": "frozen", "scotland": dict(SCOTLAND_VALUE)},
            "does not take a scotland table",
        ),
        (
            {
                "mode": "frozen_then_cpi_indexed",
                "frozen_until_tax_year": "2030/31",
                "scotland": "everything frozen",
            },
            "scotland: expected a table value",
        ),
        (
            {
                "mode": "frozen_then_cpi_indexed",
                "frozen_until_tax_year": "2030/31",
                "scotland": {"upper_bands_frozen_until_tax_year": "2028/29"},
            },
            "missing required key 'lower_bands_frozen_until_tax_year'",
        ),
        (
            {
                "mode": "frozen_then_cpi_indexed",
                "frozen_until_tax_year": "2030/31",
                "scotland": {**SCOTLAND_VALUE, "extra": "x"},
            },
            "unknown keys: extra",
        ),
        (
            {
                "mode": "frozen_then_cpi_indexed",
                "frozen_until_tax_year": "2030/31",
                "scotland": {
                    **SCOTLAND_VALUE,
                    "lower_bands_frozen_until_tax_year": "2026-27",
                },
            },
            "not 'YYYY/YY'",
        ),
        (
            {
                "mode": "frozen_then_cpi_indexed",
                "frozen_until_tax_year": "2030/31",
                "scotland": {
                    **SCOTLAND_VALUE,
                    "upper_bands_frozen_until_tax_year": 2028,
                },
            },
            "'YYYY/YY' string",
        ),
    ],
)
def test_invalid_policy_values_are_rejected(value: object, problem: str) -> None:
    """Malformed ``policy.tax.future_years`` values fail with context."""
    with pytest.raises(DataFileError, match=problem):
        FutureYearsPolicy.from_assumption_value(value)  # type: ignore[arg-type]


def test_direct_construction_enforces_the_same_invariants() -> None:
    """The freeze end is required exactly when the mode uses one."""
    with pytest.raises(DataFileError, match="requires frozen_until_tax_year"):
        FutureYearsPolicy(mode=FutureYearsMode.FROZEN_THEN_CPI_INDEXED)


def test_direct_construction_requires_the_scotland_table() -> None:
    """Indexing without a devolved Scottish policy is a construction error."""
    with pytest.raises(DataFileError, match="requires a scotland table"):
        FutureYearsPolicy(
            mode=FutureYearsMode.FROZEN_THEN_CPI_INDEXED, frozen_until_start_year=2030
        )


def test_mode_must_be_an_enum_member() -> None:
    """A bare string would dodge the identity checks and silently index."""
    with pytest.raises(DataFileError, match="must be a FutureYearsMode member"):
        FutureYearsPolicy(mode="frozen")  # type: ignore[arg-type]


def test_cpi_at_or_below_minus_one_is_rejected(
    default_policy: FutureYearsPolicy,
) -> None:
    """A growth factor of zero or below can never index money figures."""
    collapse = Rate(Decimal(-1))
    with pytest.raises(DataFileError, match="greater than -100%"):
        FutureYearsExtension(policy=default_policy, cpi=collapse)


def test_extend_tax_year_validates_the_cpi_itself(
    base: TaxYearFile, default_policy: FutureYearsPolicy
) -> None:
    """The exported function rejects an unindexable CPI directly too."""
    # -300%: an even step count would give a positive factor and
    # plausible-looking (but nonsense) figures if this were allowed.
    inverted = Rate(Decimal(-3))
    with pytest.raises(DataFileError, match="greater than -100%"):
        extend_tax_year(base, 2031, policy=default_policy, cpi=inverted)


# --- extension mechanics ----------------------------------------------------


def test_synthesized_meta(base: TaxYearFile, default_policy: FutureYearsPolicy) -> None:
    """The synthesized year gets its own label and dates, base provenance."""
    extended = extend_tax_year(base, 2040, policy=default_policy, cpi=CPI)
    assert extended.meta.tax_year == "2040/41"
    assert extended.meta.start_date == date(2040, 4, 6)
    assert extended.meta.end_date == date(2041, 4, 5)
    assert extended.meta.verified_on == base.meta.verified_on
    assert extended.meta.sources == base.meta.sources


@pytest.mark.parametrize("target", [2026, 2025])
def test_target_must_be_after_the_base_year(
    base: TaxYearFile, default_policy: FutureYearsPolicy, target: int
) -> None:
    """Extension only reaches forward; shipped years use shipped files."""
    with pytest.raises(ValueError, match="not after the last shipped"):
        extend_tax_year(base, target, policy=default_policy, cpi=CPI)


def test_frozen_mode_carries_every_figure_forward(base: TaxYearFile) -> None:
    """Frozen-indefinitely never indexes, however distant the target."""
    policy = FutureYearsPolicy(mode=FutureYearsMode.FROZEN)
    extended = extend_tax_year(base, 2060, policy=policy, cpi=CPI)
    assert extended.income_tax_ruk == base.income_tax_ruk
    assert extended.income_tax_scotland == base.income_tax_scotland
    assert extended.pension == base.pension
    assert extended.isa == base.isa
    assert extended.state_pension == base.state_pension


def test_default_policy_is_frozen_through_2030_31(
    base: TaxYearFile, default_policy: FutureYearsPolicy
) -> None:
    """Inside the legislated freeze the rUK/reserved figures match the base."""
    extended = extend_tax_year(base, 2030, policy=default_policy, cpi=CPI)
    assert extended.meta.tax_year == "2030/31"
    assert extended.income_tax_ruk == base.income_tax_ruk
    assert extended.pension == base.pension


def test_scottish_thresholds_move_inside_the_ruk_freeze(
    base: TaxYearFile, default_policy: FutureYearsPolicy
) -> None:
    """The rUK freeze never governs the devolved Scottish band uppers.

    2030/31: the reserved PA and taper hold (the legislated freeze),
    but the lower Scottish uppers get four CPI steps past the shipped
    2026/27 year and the Higher/Advanced/Top uppers two steps past the
    announced 2028/29 commitment.
    """
    extended = extend_tax_year(base, 2030, policy=default_policy, cpi=CPI)
    scotland = extended.income_tax_scotland
    assert scotland.personal_allowance == Money(Decimal(12570))
    assert scotland.pa_taper_threshold == Money(Decimal(100000))
    assert [band.upper for band in scotland.bands] == [
        Money(Decimal(4294)),  # 3,967 x 1.02^4 = 4,294.01
        Money(Decimal(18354)),  # 16,956 x 1.02^4 = 18,353.72
        Money(Decimal(33655)),  # 31,092 x 1.02^4 = 33,654.98
        Money(Decimal(64952)),  # 62,430 x 1.02^2 = 64,952.17
        Money(Decimal(130196)),  # 125,140 x 1.02^2 = 130,195.66
        None,
    ]


def test_first_indexed_year_figures(
    base: TaxYearFile, default_policy: FutureYearsPolicy
) -> None:
    """2031/32 figures are the base scaled once by 1.02, whole-pound rounded."""
    extended = extend_tax_year(base, 2031, policy=default_policy, cpi=CPI)
    ruk = extended.income_tax_ruk
    assert ruk.personal_allowance == Money(Decimal(12821))  # 12,821.40 down
    assert ruk.pa_taper_threshold == Money(Decimal(102000))
    assert [band.upper for band in ruk.bands] == [
        Money(Decimal(38454)),
        Money(Decimal(127643)),  # 127,642.80 up
        None,
    ]
    scotland = extended.income_tax_scotland
    assert scotland.personal_allowance == Money(Decimal(12821))  # reserved: 1 step
    assert scotland.pa_taper_threshold == Money(Decimal(102000))
    assert [band.upper for band in scotland.bands] == [
        Money(Decimal(4380)),  # 3,967 x 1.02^5 = 4,379.89 (5 steps past 2026/27)
        Money(Decimal(18721)),  # 16,956 x 1.02^5 = 18,720.79
        Money(Decimal(34328)),  # 31,092 x 1.02^5 = 34,328.08
        Money(Decimal(66251)),  # 62,430 x 1.02^3 = 66,251.22 (3 steps past 2028/29)
        Money(Decimal(132800)),  # 125,140 x 1.02^3 = 132,799.57
        None,
    ]
    pension = extended.pension
    assert pension.annual_allowance == Money(Decimal(61200))
    assert pension.aa_taper_threshold_income == Money(Decimal(204000))
    assert pension.aa_taper_adjusted_income == Money(Decimal(265200))
    assert pension.aa_taper_floor == Money(Decimal(10200))
    assert pension.mpaa == Money(Decimal(10200))
    assert pension.member_relief_basic_amount == Money(Decimal(3672))
    assert pension.member_relief_max_age == 75  # ages never index
    # 273,640.50 is a tie: half-even rounds to the even pound.
    assert pension.lump_sum_allowance == Money(Decimal(273640))
    assert pension.lump_sum_death_benefit_allowance == Money(Decimal(1094562))
    assert extended.isa.annual_allowance == Money(Decimal(20400))
    assert extended.isa.lisa_allowance == Money(Decimal(4080))
    savings = extended.savings
    assert savings.starting_rate_limit == Money(Decimal(5100))
    assert savings.psa_basic == Money(Decimal(1020))
    assert savings.psa_higher == Money(Decimal(510))
    assert savings.psa_additional == Money(Decimal(0))  # zero indexes to zero
    assert extended.dividend.allowance == Money(Decimal(510))


def test_rates_never_extrapolate(
    base: TaxYearFile, default_policy: FutureYearsPolicy
) -> None:
    """Indexation moves thresholds only; every rate matches the base year."""
    extended = extend_tax_year(base, 2031, policy=default_policy, cpi=CPI)
    base_rates = [band.rate for band in base.income_tax_ruk.bands]
    assert [band.rate for band in extended.income_tax_ruk.bands] == base_rates
    base_scottish_rates = [band.rate for band in base.income_tax_scotland.bands]
    scottish_rates = [band.rate for band in extended.income_tax_scotland.bands]
    assert scottish_rates == base_scottish_rates
    assert extended.income_tax_ruk.pa_taper_rate == base.income_tax_ruk.pa_taper_rate
    assert extended.pension.aa_taper_rate == base.pension.aa_taper_rate
    assert extended.pension.relief_at_source_rate == base.pension.relief_at_source_rate
    assert (
        extended.pension.tax_free_lump_sum_fraction
        == base.pension.tax_free_lump_sum_fraction
    )
    assert extended.isa.lisa_bonus_rate == base.isa.lisa_bonus_rate
    assert extended.isa.lisa_withdrawal_charge == base.isa.lisa_withdrawal_charge
    assert extended.dividend.rates == base.dividend.rates


def test_state_pension_is_never_extrapolated(
    base: TaxYearFile, default_policy: FutureYearsPolicy
) -> None:
    """State pension uprating belongs to its own §7 assumption, not this."""
    extended = extend_tax_year(base, 2035, policy=default_policy, cpi=CPI)
    assert extended.state_pension == base.state_pension


def test_indexation_compounds_once_from_the_base_year(
    base: TaxYearFile, default_policy: FutureYearsPolicy
) -> None:
    """2035/36 gets five steps in one go: 12,570 x 1.02^5 = 13,878.30."""
    extended = extend_tax_year(base, 2035, policy=default_policy, cpi=CPI)
    assert extended.income_tax_ruk.personal_allowance == Money(Decimal(13878))


def test_lapsed_freeze_end_indexes_from_the_base_year(base: TaxYearFile) -> None:
    """A freeze that ended at or before the base year is pure indexation.

    This is how post-freeze indexation is expressed once the legislated
    freeze lapses — there is deliberately no index-immediately mode.
    """
    policy = FutureYearsPolicy.from_assumption_value(
        {
            "mode": "frozen_then_cpi_indexed",
            "frozen_until_tax_year": "2020/21",
            "scotland": dict(SCOTLAND_VALUE),
        }
    )
    extended = extend_tax_year(base, 2028, policy=policy, cpi=CPI)
    # Two steps from 2026/27: 12,570 x 1.02^2 = 13,077.83 -> 13,078.
    assert extended.income_tax_ruk.personal_allowance == Money(Decimal(13078))


def test_scottish_extension_needs_the_higher_band_anchor(
    base: TaxYearFile, default_policy: FutureYearsPolicy
) -> None:
    """A Scottish ladder without a higher band cannot group its uppers."""
    scotland = base.income_tax_scotland
    schedule = IncomeTaxSchedule(
        personal_allowance=scotland.personal_allowance,
        pa_taper_threshold=scotland.pa_taper_threshold,
        pa_taper_rate=scotland.pa_taper_rate,
        bands=(
            TaxBand(
                name="basic", rate=Rate(Decimal("0.20")), upper=Money(Decimal(16956))
            ),
            TaxBand(name="top", rate=Rate(Decimal("0.48")), upper=None),
        ),
    )
    lopsided = replace(base, income_tax_scotland=schedule)
    with pytest.raises(DataFileError, match="no band named 'higher'"):
        extend_tax_year(lopsided, 2031, policy=default_policy, cpi=CPI)


# --- UkTaxSystem integration ------------------------------------------------


@pytest.fixture(scope="module", name="system")
def system_fixture(default_policy: FutureYearsPolicy) -> UkTaxSystem:
    """A shipped-data system with the default extension configured."""
    extension = FutureYearsExtension(policy=default_policy, cpi=CPI)
    return UkTaxSystem.from_shipped_data(future_years=extension)


def ruk_income(amount: str) -> TaxInput:
    """Gross non-savings income for an rUK taxpayer."""
    return TaxInput(residency=RUK_RESIDENCY, non_savings_income=Money(Decimal(amount)))


def test_assessment_in_an_indexed_future_year(system: UkTaxSystem) -> None:
    """2031/32 at 60,000: PA 12,821; 38,454 at 20% + 8,725 at 40%."""
    year_2031_32 = Period(start=date(2031, 4, 6), end=date(2032, 4, 5))
    result = system.assess(year_2031_32, ruk_income("60000"))
    assert result.tax_free_allowance == Money(Decimal(12821))
    assert result.tax_due == Money(Decimal("11180.80"))


def test_assessment_in_a_frozen_future_year(system: UkTaxSystem) -> None:
    """Inside the freeze a future year assesses exactly like the base year."""
    year_2030_31 = Period(start=date(2030, 4, 6), end=date(2031, 4, 5))
    base_year = Period(start=date(2026, 4, 6), end=date(2027, 4, 5))
    income = ruk_income("60000")
    assert system.assess(year_2030_31, income) == system.assess(base_year, income)


def test_scottish_assessment_in_a_partially_frozen_year(system: UkTaxSystem) -> None:
    """2030/31 at 50,000: reserved PA frozen, Scottish uppers indexed.

    Hand-worked from the group-indexed ladder (4,294 / 18,354 / 33,655
    / 64,952 / 130,196): 815.86 + 2,812.00 + 3,213.21 + 1,585.50.
    """
    year_2030_31 = Period(start=date(2030, 4, 6), end=date(2031, 4, 5))
    scottish = TaxInput(
        residency=SCOTLAND_RESIDENCY, non_savings_income=Money(Decimal(50000))
    )
    result = system.assess(year_2030_31, scottish)
    assert result.tax_free_allowance == Money(Decimal(12570))
    assert result.tax_due == Money(Decimal("8426.57"))


def test_extension_never_reaches_backwards(system: UkTaxSystem) -> None:
    """Years before the first shipped file still fail with an extension."""
    year_2025_26 = Period(start=date(2025, 4, 6), end=date(2026, 4, 5))
    income = ruk_income("60000")
    with pytest.raises(UkTaxError, match="only reaches past"):
        system.assess(year_2025_26, income)


def test_period_spanning_synthesized_years_is_rejected(system: UkTaxSystem) -> None:
    """A period crossing 6 April fails in synthesized years too."""
    spanning = Period(start=date(2031, 3, 1), end=date(2031, 6, 30))
    income = ruk_income("60000")
    with pytest.raises(UkTaxError, match="extends beyond"):
        system.assess(spanning, income)
