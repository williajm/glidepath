"""The shipped UK data files load and match planning §6 (issue 2.2)."""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from glidepath.core import AssumptionKey, Money, Rate
from glidepath.regions.uk import (
    SpaAgeBand,
    SpaDateBand,
    available_tax_years,
    load_age_rules,
    load_default_assumptions,
    load_tax_year,
)


def test_available_tax_years_lists_2026() -> None:
    """2026/27 is the only shipped tax year so far."""
    assert available_tax_years() == (2026,)


def test_tax_year_2026_27_meta() -> None:
    """The meta block carries the verified dates and https sources."""
    meta = load_tax_year(2026).meta
    assert meta.tax_year == "2026/27"
    assert meta.start_date == date(2026, 4, 6)
    assert meta.end_date == date(2027, 4, 5)
    assert meta.verified_on == date(2026, 8, 4)
    assert meta.sources
    assert all(source.startswith("https://") for source in meta.sources)


def test_ruk_income_tax_figures() -> None:
    """RUK schedule matches §6: PA 12,570; 20/40/45 at 37,700/125,140."""
    ruk = load_tax_year(2026).income_tax_ruk
    assert ruk.personal_allowance == Money(Decimal(12570))
    assert ruk.pa_taper_threshold == Money(Decimal(100000))
    assert ruk.pa_taper_rate == Rate(Decimal("0.5"))
    assert [band.name for band in ruk.bands] == ["basic", "higher", "additional"]
    assert [band.rate.value for band in ruk.bands] == [
        Decimal("0.20"),
        Decimal("0.40"),
        Decimal("0.45"),
    ]
    assert ruk.bands[0].upper == Money(Decimal(37700))
    assert ruk.bands[1].upper == Money(Decimal(125140))
    assert ruk.bands[2].upper is None


def test_scotland_income_tax_figures() -> None:
    """Scottish schedule matches §6: six bands, 19% to 48%."""
    scotland = load_tax_year(2026).income_tax_scotland
    assert scotland.personal_allowance == Money(Decimal(12570))
    assert [band.name for band in scotland.bands] == [
        "starter",
        "basic",
        "intermediate",
        "higher",
        "advanced",
        "top",
    ]
    assert [band.rate.value for band in scotland.bands] == [
        Decimal("0.19"),
        Decimal("0.20"),
        Decimal("0.21"),
        Decimal("0.42"),
        Decimal("0.45"),
        Decimal("0.48"),
    ]
    uppers = [band.upper for band in scotland.bands]
    assert uppers[:5] == [
        Money(Decimal(3967)),
        Money(Decimal(16956)),
        Money(Decimal(31092)),
        Money(Decimal(62430)),
        Money(Decimal(125140)),
    ]
    assert uppers[5] is None


def test_pension_allowances() -> None:
    """Pension figures match §6 (AA, taper, MPAA, relief, LSA, LSDBA)."""
    pension = load_tax_year(2026).pension
    assert pension.annual_allowance == Money(Decimal(60000))
    assert pension.aa_taper_threshold_income == Money(Decimal(200000))
    assert pension.aa_taper_adjusted_income == Money(Decimal(260000))
    assert pension.aa_taper_rate == Rate(Decimal("0.5"))
    assert pension.aa_taper_floor == Money(Decimal(10000))
    assert pension.mpaa == Money(Decimal(10000))
    assert pension.aa_carry_forward_years == 3
    assert pension.member_relief_basic_amount == Money(Decimal(3600))
    assert pension.member_relief_max_age == 75
    assert pension.relief_at_source_rate == Rate(Decimal("0.20"))
    assert pension.tax_free_lump_sum_fraction == Rate(Decimal("0.25"))
    assert pension.lump_sum_allowance == Money(Decimal(268275))
    assert pension.lump_sum_death_benefit_allowance == Money(Decimal(1073100))


def test_isa_allowances() -> None:
    """ISA figures match §6 (20,000 overall; LISA 4,000 at 25%)."""
    isa = load_tax_year(2026).isa
    assert isa.annual_allowance == Money(Decimal(20000))
    assert isa.lisa_allowance == Money(Decimal(4000))
    assert isa.lisa_bonus_rate == Rate(Decimal("0.25"))
    assert isa.lisa_withdrawal_charge == Rate(Decimal("0.25"))


def test_savings_nil_rates() -> None:
    """Savings figures match §6 (starting rate £5,000; PSA 1,000/500/0)."""
    savings = load_tax_year(2026).savings
    assert savings.starting_rate_limit == Money(Decimal(5000))
    assert savings.psa_basic == Money(Decimal(1000))
    assert savings.psa_higher == Money(Decimal(500))
    assert savings.psa_additional == Money(Decimal(0))


def test_dividend_allowance_and_rates() -> None:
    """Dividend figures match §6 (£500; 10.75/35.75/39.35 in force)."""
    dividend = load_tax_year(2026).dividend
    assert dividend.allowance == Money(Decimal(500))
    assert [entry.name for entry in dividend.rates] == [
        "ordinary",
        "upper",
        "additional",
    ]
    assert [entry.rate.value for entry in dividend.rates] == [
        Decimal("0.1075"),
        Decimal("0.3575"),
        Decimal("0.3935"),
    ]


def test_state_pension_figures() -> None:
    """State pension figures match §6 (£241.30/week; 35 full, 10 min)."""
    state_pension = load_tax_year(2026).state_pension
    assert state_pension.new_full_weekly == Money(Decimal("241.30"))
    assert state_pension.qualifying_years_full == 35
    assert state_pension.qualifying_years_min == 10


def test_nmpa_schedule() -> None:
    """NMPA is 55 now and 57 from 6 April 2028."""
    nmpa = load_age_rules().nmpa
    assert [(step.age, step.effective_from) for step in nmpa] == [
        (55, None),
        (57, date(2028, 4, 6)),
    ]


def test_spa_timetable_shape_and_endpoints() -> None:
    """The SPA table mirrors the gov.uk timetable (verified 2026-08-02)."""
    bands = load_age_rules().spa_bands
    assert len(bands) == 26

    first = bands[0]
    assert isinstance(first, SpaAgeBand)
    assert first.dob_from == date(1954, 10, 6)
    assert first.dob_to == date(1960, 4, 5)
    assert (first.years, first.months) == (66, 0)

    second = bands[1]
    assert isinstance(second, SpaAgeBand)
    assert second.dob_from == date(1960, 4, 6)
    assert second.dob_to == date(1960, 5, 5)
    assert (second.years, second.months) == (66, 1)

    to_67 = bands[12]
    assert isinstance(to_67, SpaAgeBand)
    assert to_67.dob_from == date(1961, 3, 6)
    assert to_67.dob_to == date(1977, 4, 5)
    assert (to_67.years, to_67.months) == (67, 0)

    first_dated = bands[13]
    assert isinstance(first_dated, SpaDateBand)
    assert first_dated.dob_from == date(1977, 4, 6)
    assert first_dated.dob_to == date(1977, 5, 5)
    assert first_dated.reaches_on == date(2044, 5, 6)

    last_dated = bands[24]
    assert isinstance(last_dated, SpaDateBand)
    assert last_dated.dob_from == date(1978, 3, 6)
    assert last_dated.dob_to == date(1978, 4, 5)
    assert last_dated.reaches_on == date(2046, 3, 6)

    last = bands[25]
    assert isinstance(last, SpaAgeBand)
    assert last.dob_from == date(1978, 4, 6)
    assert last.dob_to is None
    assert (last.years, last.months) == (68, 0)


def test_spa_monthly_bands_step_one_month_at_a_time() -> None:
    """The 66→67 rise adds one month of SPA per DOB month."""
    bands = load_age_rules().spa_bands[1:12]
    for offset, band in enumerate(bands, start=1):
        assert isinstance(band, SpaAgeBand)
        assert (band.years, band.months) == (66, offset)


def test_lisa_ages_and_deferral() -> None:
    """LISA gates are 18/39/50/60; deferral is 1% per 9 weeks."""
    rules = load_age_rules()
    assert rules.lisa.open_age_min == 18
    assert rules.lisa.open_age_max == 39
    assert rules.lisa.contribute_until_age == 50
    assert rules.lisa.access_age == 60
    assert rules.deferral.increment_rate == Rate(Decimal("0.01"))
    assert rules.deferral.per_weeks == 9


def test_new_state_pension_system_start() -> None:
    """The new state pension began 6 April 2016 (the derivation gate)."""
    rules = load_age_rules()
    assert rules.new_state_pension.system_start == date(2016, 4, 6)


def test_default_assumptions_cover_every_key() -> None:
    """The defaults file defines every AssumptionKey exactly once."""
    file = load_default_assumptions()
    assert file.meta.verified_on == date(2026, 8, 4)
    assert {entry.key for entry in file.defaults} == set(AssumptionKey)


def test_default_assumption_spot_values() -> None:
    """Numeric defaults land as Decimal; structured defaults as mappings."""
    file = load_default_assumptions()
    assert file.get(AssumptionKey.INFLATION_CPI).value == Decimal("0.02")
    assert file.get(AssumptionKey.RETURNS_EQUITY_REAL).value == Decimal("0.04")
    assert file.get(AssumptionKey.RETURNS_CASH_REAL).value == Decimal("-0.005")
    assert file.get(AssumptionKey.HORIZON_PLANNING_AGE).value == 95

    shape = file.get(AssumptionKey.GLIDEPATH_DEFAULT_SHAPE).value
    assert isinstance(shape, Mapping)
    assert set(shape) == {
        "equity_start",
        "derisk_years_before_retirement",
        "equity_at_retirement",
        "transition",
        "in_drawdown",
    }
    assert shape["equity_start"] == Decimal("0.80")
    assert shape["equity_at_retirement"] == Decimal("0.40")
    assert shape["derisk_years_before_retirement"] == 15
    assert shape["transition"] == "linear"
    assert shape["in_drawdown"] == "hold"

    uprating = file.get(AssumptionKey.POLICY_STATE_PENSION_UPRATING).value
    assert isinstance(uprating, Mapping)
    assert set(uprating) == {"rule", "floor", "deterministic_cpi_margin"}
    assert uprating["rule"] == "triple_lock"
    assert uprating["floor"] == Decimal("0.025")
    assert uprating["deterministic_cpi_margin"] == Decimal("0.005")

    future_years = file.get(AssumptionKey.POLICY_TAX_FUTURE_YEARS).value
    assert isinstance(future_years, Mapping)
    assert set(future_years) == {"mode", "frozen_until_tax_year", "scotland"}
    assert future_years["mode"] == "frozen_then_cpi_indexed"
    assert future_years["frozen_until_tax_year"] == "2030/31"
    scotland = future_years["scotland"]
    assert isinstance(scotland, Mapping)
    assert scotland["lower_bands_frozen_until_tax_year"] == "2026/27"
    assert scotland["upper_bands_frozen_until_tax_year"] == "2028/29"


def test_every_default_states_a_basis() -> None:
    """Each shipped default carries the basis it rests on (§7)."""
    file = load_default_assumptions()
    assert all(entry.basis for entry in file.defaults)
