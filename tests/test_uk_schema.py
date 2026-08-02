"""Direct invariant tests for the UK data-file dataclasses (issue 2.1)."""

from datetime import date
from decimal import Decimal

import pytest

from glidepath.core import AssumptionKey, Money, Rate
from glidepath.regions.uk import (
    AssumptionDefault,
    AssumptionsFile,
    DataFileError,
    FileMeta,
    IncomeTaxSchedule,
    LisaAges,
    NmpaStep,
    SpaAgeBand,
    StatePensionDeferral,
    StatePensionRules,
    TaxBand,
    TaxYearMeta,
)

START_2026 = date(2026, 4, 6)
END_2027 = date(2027, 4, 5)
VERIFIED = date(2026, 8, 1)
SOURCES = ("https://example.test/source",)
FILE_META = FileMeta(verified_on=VERIFIED, sources=SOURCES)


def make_tax_year_meta(
    tax_year: str = "2026/27",
    start_date: date = START_2026,
    end_date: date = END_2027,
) -> TaxYearMeta:
    """Build a tax-year meta with valid defaults."""
    return TaxYearMeta(
        tax_year=tax_year,
        start_date=start_date,
        end_date=end_date,
        verified_on=VERIFIED,
        sources=SOURCES,
    )


def make_schedule(bands: tuple[TaxBand, ...]) -> IncomeTaxSchedule:
    """Build a schedule around ``bands`` with valid allowance fields."""
    return IncomeTaxSchedule(
        personal_allowance=Money(Decimal(10)),
        pa_taper_threshold=Money(Decimal(100)),
        pa_taper_rate=Rate(Decimal("0.5")),
        bands=bands,
    )


def make_assumption(key: AssumptionKey) -> AssumptionDefault:
    """Build a minimal default assumption for ``key``."""
    return AssumptionDefault(key=key, value=Decimal("0.1"), basis="test basis")


def test_tax_year_meta_accepts_coherent_values() -> None:
    """The §5.3 example meta shape is valid."""
    assert make_tax_year_meta().tax_year == "2026/27"


def test_tax_year_meta_rejects_bad_label_format() -> None:
    """The tax-year label must be 'YYYY/YY'."""
    with pytest.raises(DataFileError, match="not 'YYYY/YY'"):
        make_tax_year_meta(tax_year="2026-27")


def test_tax_year_meta_rejects_wrong_suffix() -> None:
    """The label suffix must be the start year plus one."""
    with pytest.raises(DataFileError, match="suffix is not start"):
        make_tax_year_meta(tax_year="2026/29")


def test_tax_year_meta_rejects_wrong_start_date() -> None:
    """UK tax years start on 6 April."""
    with pytest.raises(DataFileError, match="6 April"):
        make_tax_year_meta(start_date=date(2026, 4, 7))


def test_tax_year_meta_rejects_wrong_end_date() -> None:
    """UK tax years end on 5 April of the following year."""
    with pytest.raises(DataFileError, match="5 April"):
        make_tax_year_meta(end_date=date(2027, 4, 6))


def test_file_meta_requires_sources() -> None:
    """Every data file must cite at least one source."""
    with pytest.raises(DataFileError, match="at least one source"):
        FileMeta(verified_on=VERIFIED, sources=())


def test_schedule_requires_at_least_one_band() -> None:
    """An empty band table is invalid."""
    with pytest.raises(DataFileError, match="at least one tax band"):
        make_schedule(())


def test_schedule_requires_unbounded_top_band() -> None:
    """Exactly the last band must omit its upper bound."""
    bounded = TaxBand(name="basic", rate=Rate(Decimal("0.2")), upper=Money(Decimal(5)))
    with pytest.raises(DataFileError, match="exactly the last band"):
        make_schedule((bounded, bounded))


def test_schedule_requires_increasing_uppers() -> None:
    """Band uppers must be strictly increasing."""
    bands = (
        TaxBand(name="a", rate=Rate(Decimal("0.2")), upper=Money(Decimal(50))),
        TaxBand(name="b", rate=Rate(Decimal("0.4")), upper=Money(Decimal(50))),
        TaxBand(name="c", rate=Rate(Decimal("0.45")), upper=None),
    )
    with pytest.raises(DataFileError, match="strictly increasing"):
        make_schedule(bands)


def test_state_pension_rules_reject_min_above_full() -> None:
    """Qualifying-year counts must satisfy 0 < min <= full."""
    with pytest.raises(DataFileError, match="min <= full"):
        StatePensionRules(
            new_full_weekly=Money(Decimal(200)),
            qualifying_years_full=35,
            qualifying_years_min=36,
        )


def test_nmpa_step_rejects_non_positive_age() -> None:
    """An NMPA age of zero is nonsense."""
    with pytest.raises(DataFileError, match="age must be positive"):
        NmpaStep(age=0, effective_from=None)


def test_spa_age_band_rejects_month_overflow() -> None:
    """Months are 0-11; a twelfth month is a year."""
    with pytest.raises(DataFileError, match="months must be between 0 and 11"):
        SpaAgeBand(dob_from=None, dob_to=None, years=66, months=12)


def test_spa_age_band_rejects_non_positive_years() -> None:
    """A zero-year pension age is nonsense."""
    with pytest.raises(DataFileError, match="years must be positive"):
        SpaAgeBand(dob_from=None, dob_to=None, years=0, months=0)


def test_spa_age_band_rejects_inverted_dob_range() -> None:
    """dob_from must not fall after dob_to."""
    with pytest.raises(DataFileError, match="is after"):
        SpaAgeBand(
            dob_from=date(1961, 1, 1),
            dob_to=date(1960, 1, 1),
            years=66,
            months=0,
        )


def test_lisa_ages_must_ascend() -> None:
    """The LISA age gates must be in ascending order."""
    with pytest.raises(DataFileError, match="ages must satisfy"):
        LisaAges(
            open_age_min=18,
            open_age_max=39,
            contribute_until_age=50,
            access_age=40,
        )


def test_deferral_rejects_zero_rate() -> None:
    """A deferral increment of zero is invalid."""
    with pytest.raises(DataFileError, match="increment_rate must be positive"):
        StatePensionDeferral(increment_rate=Rate(Decimal(0)), per_weeks=9)


def test_deferral_rejects_zero_weeks() -> None:
    """The increment interval must be positive."""
    with pytest.raises(DataFileError, match="per_weeks must be positive"):
        StatePensionDeferral(increment_rate=Rate(Decimal("0.01")), per_weeks=0)


def test_assumptions_file_rejects_duplicate_keys() -> None:
    """Each assumption key may appear only once."""
    entries = tuple(make_assumption(key) for key in AssumptionKey)
    duplicated = (*entries, make_assumption(AssumptionKey.INFLATION_CPI))
    duplicate_message = r"duplicate assumption keys: inflation\.cpi"
    with pytest.raises(DataFileError, match=duplicate_message):
        AssumptionsFile(schema_version=1, meta=FILE_META, defaults=duplicated)


def test_assumptions_file_rejects_missing_keys() -> None:
    """Every AssumptionKey must be present (completeness)."""
    entries = tuple(make_assumption(key) for key in AssumptionKey)
    with pytest.raises(DataFileError, match="missing assumption keys"):
        AssumptionsFile(schema_version=1, meta=FILE_META, defaults=entries[:-1])


def test_assumptions_file_rejects_wrong_schema_version() -> None:
    """Files written against another schema version must not load."""
    entries = tuple(make_assumption(key) for key in AssumptionKey)
    with pytest.raises(DataFileError, match="is not supported"):
        AssumptionsFile(schema_version=2, meta=FILE_META, defaults=entries)


def test_assumptions_file_get_returns_entry() -> None:
    """``get`` resolves a key to its shipped default."""
    entries = tuple(make_assumption(key) for key in AssumptionKey)
    file = AssumptionsFile(schema_version=1, meta=FILE_META, defaults=entries)
    assert file.get(AssumptionKey.INFLATION_CPI).value == Decimal("0.1")
