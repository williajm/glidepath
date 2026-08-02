"""Typed shapes of the UK region data files (planning §5.3).

Every UK policy figure lives in a TOML data file under
``glidepath/regions/uk/data/`` — never in code (guard-tested). The strict
loader (:mod:`glidepath.regions.uk.loader`) parses those files into the
frozen dataclasses defined here. Cross-field invariants (band ordering,
SPA band contiguity, assumption-key completeness) are enforced in
``__post_init__`` so any instance that exists is valid.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING, NoReturn

from glidepath.core import AssumptionKey, Money, Rate

if TYPE_CHECKING:
    from collections.abc import Mapping
    from decimal import Decimal

SCHEMA_VERSION = 1
"""The data-file schema version this code understands."""

_TAX_YEAR_FORMAT = re.compile(r"\d{4}/\d{2}")
_MONTHS_PER_YEAR = 12
_TAX_YEAR_START = (4, 6)  # UK tax years run 6 April - 5 April.
_TAX_YEAR_END = (4, 5)


class DataFileError(ValueError):
    """A UK region data file failed strict validation (planning §5.3)."""


def _fail(context: str, problem: str) -> NoReturn:
    """Raise a :class:`DataFileError` locating ``problem`` at ``context``."""
    msg = f"{context}: {problem}"
    raise DataFileError(msg)


def tax_year_label(start_year: int) -> str:
    """The ``YYYY/YY`` label of the tax year starting 6 April ``start_year``."""
    return f"{start_year}/{(start_year + 1) % 100:02d}"


def tax_year_start(start_year: int) -> date:
    """6 April of ``start_year``: the first day of that tax year."""
    month, day = _TAX_YEAR_START
    return date(start_year, month, day)


def tax_year_end(start_year: int) -> date:
    """5 April of the following year: the last day of that tax year."""
    month, day = _TAX_YEAR_END
    return date(start_year + 1, month, day)


def tax_year_start_year(day: date) -> int:
    """The start year of the UK tax year containing ``day``."""
    return day.year if day >= tax_year_start(day.year) else day.year - 1


def parse_tax_year_label(label: str, context: str) -> int:
    """Parse a ``YYYY/YY`` label into its start year, validating the suffix."""
    if not _TAX_YEAR_FORMAT.fullmatch(label):
        _fail(context, f"tax_year {label!r} is not 'YYYY/YY'")
    start_year = int(label[:4])
    if int(label[5:]) != (start_year + 1) % 100:
        _fail(context, f"tax_year {label!r} suffix is not start+1")
    return start_year


def _require_sources(sources: tuple[str, ...], owner: str) -> None:
    """Every data file must cite at least one source (planning §5.3)."""
    if not sources:
        _fail(owner, "meta.sources must list at least one source")


def _require_schema_version(version: int, owner: str) -> None:
    """Reject files written against a different schema version."""
    if version != SCHEMA_VERSION:
        _fail(owner, f"schema_version {version} is not supported ({SCHEMA_VERSION})")


def _require_dob_order(dob_from: date | None, dob_to: date | None, owner: str) -> None:
    """A band's date-of-birth range must not be inverted."""
    if dob_from is not None and dob_to is not None and dob_from > dob_to:
        _fail(owner, f"dob_from {dob_from} is after dob_to {dob_to}")


@dataclass(frozen=True, slots=True)
class FileMeta:
    """The mandatory ``[meta]`` table of a non-tax-year data file."""

    verified_on: date
    sources: tuple[str, ...]

    def __post_init__(self) -> None:
        """Require at least one source."""
        _require_sources(self.sources, "FileMeta")


@dataclass(frozen=True, slots=True)
class TaxYearMeta:
    """The mandatory ``[meta]`` table of a tax-year file."""

    tax_year: str
    start_date: date
    end_date: date
    verified_on: date
    sources: tuple[str, ...]

    def __post_init__(self) -> None:
        """Require a coherent UK tax-year label and date range."""
        _require_sources(self.sources, "TaxYearMeta")
        start_year = parse_tax_year_label(self.tax_year, "TaxYearMeta")
        if self.start_date != tax_year_start(start_year):
            _fail("TaxYearMeta", "start_date must be 6 April of the start year")
        if self.end_date != tax_year_end(start_year):
            _fail("TaxYearMeta", "end_date must be 5 April of the following year")


@dataclass(frozen=True, slots=True)
class TaxBand:
    """One income-tax band: width is taxable income above the allowance."""

    name: str
    rate: Rate
    upper: Money | None
    """Cumulative taxable-income upper bound; ``None`` = unbounded top band."""


@dataclass(frozen=True, slots=True)
class IncomeTaxSchedule:
    """Personal allowance, taper, and ascending bands for one regime."""

    personal_allowance: Money
    pa_taper_threshold: Money
    pa_taper_rate: Rate
    bands: tuple[TaxBand, ...]

    def __post_init__(self) -> None:
        """Require ascending bands with exactly one unbounded top band."""
        owner = "IncomeTaxSchedule"
        if not self.bands:
            _fail(owner, "at least one tax band is required")
        previous_upper: Money | None = None
        last_index = len(self.bands) - 1
        for index, band in enumerate(self.bands):
            if (band.upper is None) != (index == last_index):
                _fail(owner, "exactly the last band must omit 'upper'")
            if band.upper is None:
                continue
            if previous_upper is not None and band.upper <= previous_upper:
                _fail(owner, "band uppers must be strictly increasing")
            previous_upper = band.upper


@dataclass(frozen=True, slots=True)
class PensionRules:
    """Pension allowances and relief parameters for one tax year."""

    annual_allowance: Money
    aa_taper_threshold_income: Money
    aa_taper_adjusted_income: Money
    aa_taper_rate: Rate
    aa_taper_floor: Money
    mpaa: Money
    member_relief_basic_amount: Money
    """Relief floor for low/no earners, available via relief at source only."""
    member_relief_max_age: int
    """No relief on contributions from this age on (FA 2004 s188(3)(a))."""
    relief_at_source_rate: Rate
    tax_free_lump_sum_fraction: Rate
    lump_sum_allowance: Money
    lump_sum_death_benefit_allowance: Money

    def __post_init__(self) -> None:
        """Require a positive relief age limit."""
        if self.member_relief_max_age <= 0:
            _fail("PensionRules", "member_relief_max_age must be positive")


@dataclass(frozen=True, slots=True)
class IsaRules:
    """ISA and LISA allowances and rates for one tax year."""

    annual_allowance: Money
    lisa_allowance: Money
    lisa_bonus_rate: Rate
    lisa_withdrawal_charge: Rate


@dataclass(frozen=True, slots=True)
class StatePensionRules:
    """New state pension rate and qualifying-year rules for one tax year."""

    new_full_weekly: Money
    qualifying_years_full: int
    qualifying_years_min: int

    def __post_init__(self) -> None:
        """Require sane qualifying-year counts."""
        if not 0 < self.qualifying_years_min <= self.qualifying_years_full:
            _fail("StatePensionRules", "qualifying years must satisfy 0 < min <= full")


@dataclass(frozen=True, slots=True)
class TaxYearFile:
    """One fully validated ``tax_year_YYYY_YY.toml`` data file."""

    schema_version: int
    meta: TaxYearMeta
    income_tax_ruk: IncomeTaxSchedule
    income_tax_scotland: IncomeTaxSchedule
    pension: PensionRules
    isa: IsaRules
    state_pension: StatePensionRules

    def __post_init__(self) -> None:
        """Reject unsupported schema versions."""
        _require_schema_version(self.schema_version, "TaxYearFile")


@dataclass(frozen=True, slots=True)
class NmpaStep:
    """One effective-dated normal-minimum-pension-age value."""

    age: int
    effective_from: date | None
    """``None`` marks the baseline step already in force."""

    def __post_init__(self) -> None:
        """Require a positive age."""
        if self.age <= 0:
            _fail("NmpaStep", "age must be positive")


@dataclass(frozen=True, slots=True)
class SpaAgeBand:
    """A DOB band whose state pension age is a years-and-months age."""

    dob_from: date | None
    dob_to: date | None
    years: int
    months: int

    def __post_init__(self) -> None:
        """Require a plausible age and an ordered DOB range."""
        owner = "SpaAgeBand"
        _require_dob_order(self.dob_from, self.dob_to, owner)
        if self.years <= 0:
            _fail(owner, "years must be positive")
        if not 0 <= self.months < _MONTHS_PER_YEAR:
            _fail(owner, "months must be between 0 and 11")


@dataclass(frozen=True, slots=True)
class SpaDateBand:
    """A DOB band whose members reach state pension age on a fixed date."""

    dob_from: date | None
    dob_to: date | None
    reaches_on: date

    def __post_init__(self) -> None:
        """Require an ordered DOB range."""
        _require_dob_order(self.dob_from, self.dob_to, "SpaDateBand")


type SpaBand = SpaAgeBand | SpaDateBand
"""One row of the state-pension-age timetable (age-based or date-based)."""


@dataclass(frozen=True, slots=True)
class LisaAges:
    """LISA age gates: open 18-39, contribute to 50, access at 60 (§6)."""

    open_age_min: int
    open_age_max: int
    contribute_until_age: int
    access_age: int

    def __post_init__(self) -> None:
        """Require the gates in ascending order."""
        ordered = (
            0
            < self.open_age_min
            <= self.open_age_max
            <= self.contribute_until_age
            <= self.access_age
        )
        if not ordered:
            _fail(
                "LisaAges",
                "ages must satisfy 0 < open_min <= open_max <= contribute <= access",
            )


@dataclass(frozen=True, slots=True)
class StatePensionDeferral:
    """State pension deferral increment (post-2016 rules)."""

    increment_rate: Rate
    per_weeks: int

    def __post_init__(self) -> None:
        """Require a positive increment earned over a positive interval."""
        owner = "StatePensionDeferral"
        if self.increment_rate.value <= 0:
            _fail(owner, "increment_rate must be positive")
        if self.per_weeks <= 0:
            _fail(owner, "per_weeks must be positive")


def _validate_nmpa_steps(steps: tuple[NmpaStep, ...]) -> None:
    """The first step is the baseline; later steps are strictly dated."""
    owner = "AgeRulesFile.nmpa"
    if not steps:
        _fail(owner, "at least one step is required")
    if steps[0].effective_from is not None:
        _fail(owner, "the first step is the baseline and must omit effective_from")
    previous: date | None = None
    for step in steps[1:]:
        if step.effective_from is None:
            _fail(owner, "only the first step may omit effective_from")
        if previous is not None and step.effective_from <= previous:
            _fail(owner, "steps must be in ascending effective_from order")
        previous = step.effective_from


def _validate_spa_bands(bands: tuple[SpaBand, ...]) -> None:
    """SPA bands must tile the covered DOB range with no gaps or overlaps.

    The first band may set ``dob_from``: coverage starts there, and age
    logic must reject earlier dates of birth rather than guess. The last
    band is always open-ended (future births).
    """
    owner = "AgeRulesFile.state_pension_age"
    if not bands:
        _fail(owner, "at least one band is required")
    if bands[-1].dob_to is not None:
        _fail(owner, "the last band must omit dob_to (open-ended)")
    for previous, current in pairwise(bands):
        if previous.dob_to is None:
            _fail(owner, "only the last band may omit dob_to")
        if current.dob_from is None:
            _fail(owner, "only the first band may omit dob_from")
        if current.dob_from != previous.dob_to + timedelta(days=1):
            _fail(
                owner,
                f"bands must be contiguous: {current.dob_from} does not follow"
                f" {previous.dob_to}",
            )


@dataclass(frozen=True, slots=True)
class AgeRulesFile:
    """The fully validated ``age_rules.toml`` data file."""

    schema_version: int
    meta: FileMeta
    nmpa: tuple[NmpaStep, ...]
    spa_bands: tuple[SpaBand, ...]
    lisa: LisaAges
    deferral: StatePensionDeferral

    def __post_init__(self) -> None:
        """Validate the version, the NMPA schedule, and SPA band tiling."""
        _require_schema_version(self.schema_version, "AgeRulesFile")
        _validate_nmpa_steps(self.nmpa)
        _validate_spa_bands(self.spa_bands)


type AssumptionValue = Decimal | int | str | Mapping[str, AssumptionValue]
"""A shipped default: a number, an enum-like tag, or a structured table."""


@dataclass(frozen=True, slots=True)
class AssumptionDefault:
    """One shipped default assumption with the basis it rests on (§7)."""

    key: AssumptionKey
    value: AssumptionValue
    basis: str


def _validate_assumption_keys(defaults: tuple[AssumptionDefault, ...]) -> None:
    """The file must define every :class:`AssumptionKey` exactly once."""
    owner = "AssumptionsFile"
    seen = [entry.key for entry in defaults]
    duplicates = sorted({key.value for key in seen if seen.count(key) > 1})
    if duplicates:
        _fail(owner, f"duplicate assumption keys: {', '.join(duplicates)}")
    missing = sorted(key.value for key in AssumptionKey if key not in seen)
    if missing:
        _fail(owner, f"missing assumption keys: {', '.join(missing)}")


@dataclass(frozen=True, slots=True)
class AssumptionsFile:
    """The fully validated ``assumptions_default.toml`` data file."""

    schema_version: int
    meta: FileMeta
    defaults: tuple[AssumptionDefault, ...]

    def __post_init__(self) -> None:
        """Validate the version and assumption-key completeness."""
        _require_schema_version(self.schema_version, "AssumptionsFile")
        _validate_assumption_keys(self.defaults)

    def get(self, key: AssumptionKey) -> AssumptionDefault:
        """Return the shipped default for ``key`` (always present)."""
        by_key = {entry.key: entry for entry in self.defaults}
        return by_key[key]
