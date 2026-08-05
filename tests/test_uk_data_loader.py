"""Strict-loader tests for the UK region data files (issue 2.1).

The acceptance criterion of planning §5.3: unknown keys, missing meta,
or float-typed money are load errors. Each test mutates a known-valid
TOML document and asserts the load fails with a pointed error.
"""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from glidepath.core import AssumptionKey, Money, Rate
from glidepath.regions.uk import (
    DataFileError,
    SpaAgeBand,
    SpaDateBand,
    loader,
    parse_age_rules,
    parse_default_assumptions,
    parse_tax_year,
    tax_year_filename,
)

TAX_HEADER = "schema_version = 2\n"
TAX_META = """
[meta]
tax_year = "2026/27"
start_date = 2026-04-06
end_date = 2027-04-05
verified_on = 2026-08-01
sources = ["https://example.test/tax"]
"""
TAX_RUK = """
[income_tax.ruk]
personal_allowance = "12570"
pa_taper_threshold = "100000"
pa_taper_rate = "0.5"
bands = [
  { name = "basic", rate = "0.20", upper = "37700" },
  { name = "higher", rate = "0.40", upper = "125140" },
  { name = "additional", rate = "0.45" },
]
"""
TAX_SCOTLAND = """
[income_tax.scotland]
personal_allowance = "12570"
pa_taper_threshold = "100000"
pa_taper_rate = "0.5"
bands = [
  { name = "starter", rate = "0.19", upper = "3967" },
  { name = "basic", rate = "0.20", upper = "16956" },
  { name = "top", rate = "0.48" },
]
"""
TAX_PENSION = """
[pension]
annual_allowance = "60000"
aa_taper_threshold_income = "200000"
aa_taper_adjusted_income = "260000"
aa_taper_rate = "0.5"
aa_taper_floor = "10000"
mpaa = "10000"
aa_carry_forward_years = 3
member_relief_basic_amount = "3600"
member_relief_max_age = 75
relief_at_source_rate = "0.20"
tax_free_lump_sum_fraction = "0.25"
lump_sum_allowance = "268275"
lump_sum_death_benefit_allowance = "1073100"
db_valuation_factor = 16
"""
TAX_ISA = """
[isa]
annual_allowance = "20000"
lisa_allowance = "4000"
lisa_bonus_rate = "0.25"
lisa_withdrawal_charge = "0.25"
"""
TAX_SAVINGS = """
[savings]
starting_rate_limit = "5000"
psa_basic = "1000"
psa_higher = "500"
psa_additional = "0"
"""
TAX_DIVIDEND = """
[dividend]
allowance = "500"
rates = [
  { name = "ordinary", rate = "0.1075" },
  { name = "upper", rate = "0.3575" },
  { name = "additional", rate = "0.3935" },
]
"""
VALID_TAX_YEAR = (
    TAX_HEADER
    + TAX_META
    + TAX_RUK
    + TAX_SCOTLAND
    + TAX_PENSION
    + TAX_ISA
    + TAX_SAVINGS
    + TAX_DIVIDEND
)

VALID_AGE_RULES = """
schema_version = 2

[meta]
verified_on = 2026-08-02
sources = ["https://example.test/ages"]

[nmpa]
steps = [
  { age = 55 },
  { age = 57, effective_from = 2028-04-06 },
]

[state_pension_age]
bands = [
  { dob_to = 1960-04-05, age = { years = 66 } },
  { dob_from = 1960-04-06, dob_to = 1960-05-05, age = { years = 66, months = 1 } },
  { dob_from = 1960-05-06, dob_to = 1977-04-05, age = { years = 67 } },
  { dob_from = 1977-04-06, dob_to = 1978-04-05, reaches_on = 2044-05-06 },
  { dob_from = 1978-04-06, age = { years = 68 } },
]

[lisa]
open_age_min = 18
open_age_max = 39
contribute_until_age = 50
access_age = 60

[state_pension_deferral]
increment_rate = "0.01"
per_weeks = 9
"""

ASSUMPTIONS_HEADER = """schema_version = 2

[meta]
verified_on = 2026-08-01
sources = ["https://example.test/assumptions"]

"""


def _assumption_entry(key: str) -> str:
    """One minimal ``[[assumption]]`` entry for ``key``."""
    return f'[[assumption]]\nkey = "{key}"\nvalue = "0.1"\nbasis = "test basis"\n\n'


VALID_ASSUMPTIONS = ASSUMPTIONS_HEADER + "".join(
    _assumption_entry(key.value) for key in AssumptionKey
)


def _mutated(base: str, old: str, new: str) -> str:
    """Return ``base`` with every ``old`` swapped for ``new`` (must exist)."""
    assert old in base, f"fixture drift: {old!r} not found"
    return base.replace(old, new)


# --- tax-year documents ------------------------------------------------------


def test_valid_tax_year_parses_into_typed_values() -> None:
    """The fixture round-trips into Money/Rate typed dataclasses."""
    file = parse_tax_year(VALID_TAX_YEAR)
    assert file.meta.start_date == date(2026, 4, 6)
    assert file.income_tax_ruk.personal_allowance == Money(Decimal(12570))
    assert file.income_tax_ruk.bands[-1].upper is None
    assert file.income_tax_scotland.bands[0].rate == Rate(Decimal("0.19"))
    assert file.pension.mpaa == Money(Decimal(10000))
    assert file.savings.starting_rate_limit == Money(Decimal(5000))
    assert file.dividend.rates[0].rate == Rate(Decimal("0.1075"))


def test_descending_psa_tier_order_is_enforced_at_load() -> None:
    """PSA tiers must descend basic >= higher >= additional."""
    broken = _mutated(VALID_TAX_YEAR, 'psa_higher = "500"', 'psa_higher = "2000"')
    with pytest.raises(DataFileError, match="basic >= higher >= additional"):
        parse_tax_year(broken)


def test_misaligned_dividend_rates_are_an_error() -> None:
    """Dividend rates must align one-to-one with the rUK bands."""
    broken = _mutated(VALID_TAX_YEAR, '  { name = "ordinary", rate = "0.1075" },\n', "")
    with pytest.raises(DataFileError, match="align one-to-one with the rUK bands"):
        parse_tax_year(broken)


def test_invalid_toml_is_a_load_error() -> None:
    """Broken TOML syntax fails with file context."""
    with pytest.raises(DataFileError, match="invalid TOML"):
        parse_tax_year("not = = toml")


def test_missing_schema_version_is_an_error() -> None:
    """schema_version is mandatory."""
    broken = _mutated(VALID_TAX_YEAR, "schema_version = 2\n", "")
    with pytest.raises(DataFileError, match="missing required key 'schema_version'"):
        parse_tax_year(broken)


def test_unsupported_schema_version_is_an_error() -> None:
    """Version 3 files must not load into version 2 code."""
    broken = _mutated(VALID_TAX_YEAR, "schema_version = 2", "schema_version = 3")
    with pytest.raises(DataFileError, match="schema_version 3 is not supported"):
        parse_tax_year(broken)


def test_string_schema_version_is_an_error() -> None:
    """schema_version must be an integer."""
    broken = _mutated(VALID_TAX_YEAR, "schema_version = 2", 'schema_version = "2"')
    with pytest.raises(DataFileError, match="expected an integer"):
        parse_tax_year(broken)


def test_missing_meta_is_an_error() -> None:
    """The [meta] table is mandatory (§5.3 acceptance criterion)."""
    broken = _mutated(VALID_TAX_YEAR, TAX_META, "\n")
    with pytest.raises(DataFileError, match="missing required key 'meta'"):
        parse_tax_year(broken)


def test_missing_meta_key_is_an_error() -> None:
    """Every meta field is required."""
    broken = _mutated(VALID_TAX_YEAR, "start_date = 2026-04-06\n", "")
    with pytest.raises(DataFileError, match="missing required key 'start_date'"):
        parse_tax_year(broken)


def test_unknown_top_level_key_is_an_error() -> None:
    """Unknown keys anywhere are load errors."""
    broken = _mutated(
        VALID_TAX_YEAR, "schema_version = 2\n", "schema_version = 2\nmystery = 1\n"
    )
    with pytest.raises(DataFileError, match="unknown keys: mystery"):
        parse_tax_year(broken)


def test_unknown_section_key_is_an_error() -> None:
    """Unknown keys inside a section are load errors."""
    broken = _mutated(VALID_TAX_YEAR, "[pension]\n", '[pension]\nmystery = "1"\n')
    with pytest.raises(DataFileError, match="pension: unknown keys: mystery"):
        parse_tax_year(broken)


def test_unknown_band_key_is_an_error() -> None:
    """Unknown keys inside an array-of-tables item are load errors."""
    broken = _mutated(
        VALID_TAX_YEAR,
        '{ name = "basic", rate = "0.20", upper = "37700" }',
        '{ name = "basic", rate = "0.20", upper = "37700", extra = "1" }',
    )
    with pytest.raises(DataFileError, match="unknown keys: extra"):
        parse_tax_year(broken)


def test_negative_carry_forward_window_is_an_error() -> None:
    """The carry-forward window cannot be negative."""
    broken = _mutated(
        VALID_TAX_YEAR, "aa_carry_forward_years = 3", "aa_carry_forward_years = -1"
    )
    with pytest.raises(DataFileError, match="aa_carry_forward_years: must be at least"):
        parse_tax_year(broken)


def test_float_typed_money_is_an_error() -> None:
    """Bare floats in money positions are load errors (§5.3)."""
    broken = _mutated(
        VALID_TAX_YEAR, 'personal_allowance = "12570"', "personal_allowance = 12570.0"
    )
    with pytest.raises(DataFileError, match="float-typed number"):
        parse_tax_year(broken)


def test_int_typed_money_is_an_error() -> None:
    """Money must be a TOML string, not an integer."""
    broken = _mutated(
        VALID_TAX_YEAR, 'personal_allowance = "12570"', "personal_allowance = 12570"
    )
    with pytest.raises(DataFileError, match="must be TOML strings, got int"):
        parse_tax_year(broken)


def test_bool_typed_rate_is_an_error() -> None:
    """Booleans are not rates."""
    broken = _mutated(VALID_TAX_YEAR, 'aa_taper_rate = "0.5"', "aa_taper_rate = true")
    with pytest.raises(DataFileError, match="got bool"):
        parse_tax_year(broken)


def test_unparseable_decimal_is_an_error() -> None:
    """Money strings must parse as decimals."""
    broken = _mutated(VALID_TAX_YEAR, 'mpaa = "10000"', 'mpaa = "ten grand"')
    with pytest.raises(DataFileError, match="not a valid decimal number"):
        parse_tax_year(broken)


def test_non_finite_decimal_is_an_error() -> None:
    """NaN and infinities are rejected."""
    broken = _mutated(VALID_TAX_YEAR, 'mpaa = "10000"', 'mpaa = "NaN"')
    with pytest.raises(DataFileError, match="must be finite"):
        parse_tax_year(broken)


def test_negative_money_is_an_error() -> None:
    """Allowances cannot be negative."""
    broken = _mutated(VALID_TAX_YEAR, 'mpaa = "10000"', 'mpaa = "-1"')
    with pytest.raises(DataFileError, match="non-negative"):
        parse_tax_year(broken)


def test_rate_above_one_is_an_error() -> None:
    """Rates are fractions in [0, 1]."""
    broken = _mutated(VALID_TAX_YEAR, 'aa_taper_rate = "0.5"', 'aa_taper_rate = "1.5"')
    with pytest.raises(DataFileError, match="between 0 and 1"):
        parse_tax_year(broken)


def test_missing_section_key_is_an_error() -> None:
    """Every figure in a section is required."""
    broken = _mutated(VALID_TAX_YEAR, 'mpaa = "10000"\n', "")
    with pytest.raises(DataFileError, match="missing required key 'mpaa'"):
        parse_tax_year(broken)


def test_bounded_top_band_is_an_error() -> None:
    """The last band must be unbounded."""
    broken = _mutated(
        VALID_TAX_YEAR,
        '{ name = "additional", rate = "0.45" }',
        '{ name = "additional", rate = "0.45", upper = "999999" }',
    )
    with pytest.raises(DataFileError, match="exactly the last band"):
        parse_tax_year(broken)


def test_non_increasing_band_uppers_are_an_error() -> None:
    """Band uppers must strictly increase."""
    broken = _mutated(VALID_TAX_YEAR, 'upper = "125140"', 'upper = "37700"')
    with pytest.raises(DataFileError, match="strictly increasing"):
        parse_tax_year(broken)


def test_datetime_in_date_position_is_an_error() -> None:
    """Dates must be calendar dates without a time part."""
    broken = _mutated(
        VALID_TAX_YEAR, "start_date = 2026-04-06", "start_date = 2026-04-06T00:00:00Z"
    )
    with pytest.raises(DataFileError, match="without a time part"):
        parse_tax_year(broken)


def test_non_https_source_is_an_error() -> None:
    """Sources must be https URLs."""
    broken = _mutated(
        VALID_TAX_YEAR, '"https://example.test/tax"', '"http://example.test/tax"'
    )
    with pytest.raises(DataFileError, match="https:// URLs"):
        parse_tax_year(broken)


def test_empty_sources_are_an_error() -> None:
    """At least one source is required."""
    broken = _mutated(VALID_TAX_YEAR, '["https://example.test/tax"]', "[]")
    with pytest.raises(DataFileError, match="must not be empty"):
        parse_tax_year(broken)


# --- age-rules documents -----------------------------------------------------


def test_valid_age_rules_parse() -> None:
    """The fixture parses into typed NMPA, SPA, LISA and deferral rules."""
    rules = parse_age_rules(VALID_AGE_RULES)
    assert rules.nmpa[0].age == 55
    assert rules.nmpa[1].effective_from == date(2028, 4, 6)
    first_band = rules.spa_bands[0]
    assert isinstance(first_band, SpaAgeBand)
    assert first_band.months == 0
    date_band = rules.spa_bands[3]
    assert isinstance(date_band, SpaDateBand)
    assert date_band.reaches_on == date(2044, 5, 6)
    assert rules.lisa.access_age == 60
    assert rules.deferral.per_weeks == 9


def test_spa_band_with_age_and_date_is_an_error() -> None:
    """A band must be age-based xor date-based."""
    broken = _mutated(
        VALID_AGE_RULES,
        "{ dob_from = 1978-04-06, age = { years = 68 } }",
        "{ dob_from = 1978-04-06, age = { years = 68 }, reaches_on = 2046-04-06 }",
    )
    with pytest.raises(DataFileError, match="exactly one of 'age' and 'reaches_on'"):
        parse_age_rules(broken)


def test_spa_band_with_neither_age_nor_date_is_an_error() -> None:
    """A band without an SPA value is invalid."""
    broken = _mutated(
        VALID_AGE_RULES,
        "{ dob_from = 1978-04-06, age = { years = 68 } }",
        "{ dob_from = 1978-04-06 }",
    )
    with pytest.raises(DataFileError, match="exactly one of 'age' and 'reaches_on'"):
        parse_age_rules(broken)


def test_spa_band_month_overflow_is_an_error() -> None:
    """Months above 11 are rejected."""
    broken = _mutated(
        VALID_AGE_RULES,
        "age = { years = 66, months = 1 }",
        "age = { years = 66, months = 12 }",
    )
    with pytest.raises(DataFileError, match="months must be between 0 and 11"):
        parse_age_rules(broken)


def test_spa_band_gap_is_an_error() -> None:
    """Bands must tile dates of birth contiguously."""
    broken = _mutated(VALID_AGE_RULES, "dob_from = 1960-04-06", "dob_from = 1960-04-07")
    with pytest.raises(DataFileError, match="must be contiguous"):
        parse_age_rules(broken)


def test_spa_first_band_may_be_bounded() -> None:
    """A bounded first band marks where SPA coverage starts."""
    bounded = _mutated(
        VALID_AGE_RULES,
        "{ dob_to = 1960-04-05, age = { years = 66 } }",
        "{ dob_from = 1954-10-06, dob_to = 1960-04-05, age = { years = 66 } }",
    )
    first = parse_age_rules(bounded).spa_bands[0]
    assert isinstance(first, SpaAgeBand)
    assert first.dob_from == date(1954, 10, 6)


def test_spa_last_band_must_be_open_ended() -> None:
    """The last band covers everyone born after the timetable."""
    broken = _mutated(
        VALID_AGE_RULES,
        "{ dob_from = 1978-04-06, age = { years = 68 } }",
        "{ dob_from = 1978-04-06, dob_to = 2000-01-01, age = { years = 68 } }",
    )
    with pytest.raises(DataFileError, match="last band must omit dob_to"):
        parse_age_rules(broken)


def test_nmpa_baseline_step_must_be_undated() -> None:
    """The first NMPA step is the baseline already in force."""
    broken = _mutated(
        VALID_AGE_RULES, "{ age = 55 }", "{ age = 55, effective_from = 2020-01-01 }"
    )
    with pytest.raises(DataFileError, match="baseline and must omit"):
        parse_age_rules(broken)


def test_nmpa_steps_must_ascend() -> None:
    """Later NMPA steps must have ascending effective dates."""
    duplicate_date_step = "\n  { age = 58, effective_from = 2028-04-06 },"
    broken = _mutated(
        VALID_AGE_RULES,
        "{ age = 57, effective_from = 2028-04-06 },",
        "{ age = 57, effective_from = 2028-04-06 }," + duplicate_date_step,
    )
    with pytest.raises(DataFileError, match="ascending effective_from"):
        parse_age_rules(broken)


def test_lisa_age_order_is_enforced_at_load() -> None:
    """Age-gate ordering violations surface as load errors."""
    broken = _mutated(VALID_AGE_RULES, "access_age = 60", "access_age = 10")
    with pytest.raises(DataFileError, match="ages must satisfy"):
        parse_age_rules(broken)


def test_deferral_zero_weeks_is_an_error() -> None:
    """The deferral increment interval must be positive."""
    broken = _mutated(VALID_AGE_RULES, "per_weeks = 9", "per_weeks = 0")
    with pytest.raises(DataFileError, match="at least 1"):
        parse_age_rules(broken)


# --- assumptions documents ---------------------------------------------------


def test_valid_assumptions_parse_completely() -> None:
    """Every AssumptionKey is present and numeric strings become Decimal."""
    file = parse_default_assumptions(VALID_ASSUMPTIONS)
    assert len(file.defaults) == len(AssumptionKey)
    assert file.get(AssumptionKey.INFLATION_CPI).value == Decimal("0.1")


def test_structured_assumption_values_parse_recursively() -> None:
    """Inline tables become mappings of typed leaves."""
    entry = _assumption_entry("glidepath.default_shape")
    structured = entry.replace(
        'value = "0.1"',
        'value = { equity_start = "0.80", years = 15, mode = "linear" }',
    )
    document = _mutated(VALID_ASSUMPTIONS, entry, structured)
    value = (
        parse_default_assumptions(document)
        .get(AssumptionKey.GLIDEPATH_DEFAULT_SHAPE)
        .value
    )
    assert isinstance(value, Mapping)
    assert value["equity_start"] == Decimal("0.80")
    assert value["years"] == 15
    assert value["mode"] == "linear"


def test_unknown_assumption_key_is_an_error() -> None:
    """Keys outside AssumptionKey are load errors."""
    broken = _mutated(
        VALID_ASSUMPTIONS, 'key = "inflation.cpi"', 'key = "inflation.rpix"'
    )
    with pytest.raises(DataFileError, match="unknown assumption key"):
        parse_default_assumptions(broken)


def test_duplicate_assumption_key_is_an_error() -> None:
    """A key defined twice is a load error."""
    broken = VALID_ASSUMPTIONS + _assumption_entry("inflation.cpi")
    with pytest.raises(DataFileError, match="duplicate assumption keys"):
        parse_default_assumptions(broken)


def test_missing_assumption_key_is_an_error() -> None:
    """The file must mirror §7 completely."""
    broken = _mutated(VALID_ASSUMPTIONS, _assumption_entry("inflation.cpi"), "")
    with pytest.raises(DataFileError, match=r"missing assumption keys: inflation\.cpi"):
        parse_default_assumptions(broken)


def test_float_assumption_value_is_an_error() -> None:
    """Floats are banned in assumption values too."""
    broken = _mutated(VALID_ASSUMPTIONS, 'value = "0.1"', "value = 0.1")
    with pytest.raises(DataFileError, match="float-typed number"):
        parse_default_assumptions(broken)


def test_bool_assumption_value_is_an_error() -> None:
    """Booleans are not assumption values."""
    broken = _mutated(VALID_ASSUMPTIONS, 'value = "0.1"', "value = true")
    with pytest.raises(DataFileError, match="booleans are not valid"):
        parse_default_assumptions(broken)


def test_array_assumption_value_is_an_error() -> None:
    """Arrays are not assumption values."""
    broken = _mutated(VALID_ASSUMPTIONS, 'value = "0.1"', 'value = ["0.1"]')
    with pytest.raises(DataFileError, match="unsupported value type: list"):
        parse_default_assumptions(broken)


def test_empty_string_assumption_value_is_an_error() -> None:
    """Empty values are meaningless."""
    broken = _mutated(VALID_ASSUMPTIONS, 'value = "0.1"', 'value = ""')
    with pytest.raises(DataFileError, match="must not be empty"):
        parse_default_assumptions(broken)


def test_non_finite_assumption_value_is_an_error() -> None:
    """NaN strings are rejected, not kept as tags."""
    broken = _mutated(VALID_ASSUMPTIONS, 'value = "0.1"', 'value = "NaN"')
    with pytest.raises(DataFileError, match="must be finite"):
        parse_default_assumptions(broken)


def test_empty_table_assumption_value_is_an_error() -> None:
    """Empty inline tables are rejected."""
    broken = _mutated(VALID_ASSUMPTIONS, 'value = "0.1"', "value = {}")
    with pytest.raises(DataFileError, match="must not be empty"):
        parse_default_assumptions(broken)


def test_nested_float_assumption_value_is_an_error() -> None:
    """Floats are rejected inside structured values as well."""
    broken = _mutated(VALID_ASSUMPTIONS, 'value = "0.1"', "value = { x = 0.1 }")
    with pytest.raises(DataFileError, match="float-typed number"):
        parse_default_assumptions(broken)


def test_empty_basis_is_an_error() -> None:
    """Every default must state the basis it rests on."""
    broken = _mutated(VALID_ASSUMPTIONS, 'basis = "test basis"', 'basis = ""')
    with pytest.raises(DataFileError, match="must not be empty"):
        parse_default_assumptions(broken)


def test_missing_basis_is_an_error() -> None:
    """The basis field is mandatory."""
    broken = _mutated(VALID_ASSUMPTIONS, 'basis = "test basis"\n', "")
    with pytest.raises(DataFileError, match="missing required key 'basis'"):
        parse_default_assumptions(broken)


# --- packaged-file access ----------------------------------------------------


def test_tax_year_filename_formats_century_rollover() -> None:
    """The YY suffix wraps correctly across a century boundary."""
    assert tax_year_filename(2026) == "tax_year_2026_27.toml"
    assert tax_year_filename(1999) == "tax_year_1999_00.toml"


def test_load_missing_tax_year_is_an_error() -> None:
    """Asking for an unshipped year fails cleanly."""
    with pytest.raises(DataFileError, match="no such shipped UK data file"):
        loader.load_tax_year(1999)


def test_load_tax_year_rejects_mismatched_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file whose meta disagrees with its filename must not load."""
    (tmp_path / "tax_year_2027_28.toml").write_text(VALID_TAX_YEAR, encoding="utf-8")
    monkeypatch.setattr(loader, "_data_directory", lambda: tmp_path)
    with pytest.raises(DataFileError, match="claims tax year"):
        loader.load_tax_year(2027)


def test_available_tax_years_matches_shipped_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only canonically named tax-year files are listed, in ascending order."""
    for name in (
        "tax_year_2026_27.toml",
        "tax_year_2025_26.toml",
        "age_rules.toml",
        "tax_year_bad.toml",
        "tax_year_2026_99.toml",
    ):
        (tmp_path / name).write_text("", encoding="utf-8")
    monkeypatch.setattr(loader, "_data_directory", lambda: tmp_path)
    assert loader.available_tax_years() == (2025, 2026)
