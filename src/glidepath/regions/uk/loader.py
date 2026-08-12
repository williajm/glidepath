"""Strict TOML loader for the UK region data files (planning §5.3).

``importlib.resources`` + stdlib ``tomllib`` only. Loading is strict:

- money and rates are TOML **strings** parsed to ``Decimal`` — a bare
  float (or int) in a money position is a load error;
- every file declares ``schema_version`` and carries a mandatory
  ``[meta]`` table with ``verified_on`` + ``sources``;
- unknown keys anywhere are load errors.

Failures raise :class:`~glidepath.regions.uk.schema.DataFileError` with a
``file.section.key`` context string.
"""

import hashlib
import re
import tomllib
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from importlib import resources
from typing import TYPE_CHECKING, NoReturn

from glidepath.core import AssumptionKey, HistoricalSeries, HistoricalYear, Money, Rate
from glidepath.regions.uk.schema import (
    SCHEMA_VERSION,
    AgeRulesFile,
    AssumptionDefault,
    AssumptionsFile,
    DataFileError,
    DeathBenefitAges,
    DividendRate,
    DividendRules,
    FileMeta,
    FrozenTable,
    IncomeTaxSchedule,
    IsaRules,
    LisaAges,
    MarriageAllowanceRules,
    NmpaStep,
    PensionRules,
    ReturnsHistoryFile,
    SavingsRules,
    SpaAgeBand,
    SpaBand,
    SpaDateBand,
    StatePensionDeferral,
    TaxBand,
    TaxYearFile,
    TaxYearMeta,
    tax_year_label,
)

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable

    from glidepath.regions.uk.schema import AssumptionValue

AGE_RULES_FILENAME = "age_rules.toml"
ASSUMPTIONS_FILENAME = "assumptions_default.toml"
RETURNS_HISTORY_FILENAME = "returns_history.toml"

_DATA_ANCHOR = "glidepath.regions.uk"
_TAX_YEAR_FILE = re.compile(r"tax_year_(\d{4})_(\d{2})\.toml")
_MUST_NOT_BE_EMPTY = "must not be empty"


def _fail(context: str, problem: str) -> NoReturn:
    """Raise a :class:`DataFileError` locating ``problem`` at ``context``."""
    msg = f"{context}: {problem}"
    raise DataFileError(msg)


class _Table:
    """Strict view over one TOML table: every key must be consumed."""

    __slots__ = ("_context", "_entries")

    def __init__(self, raw: object, context: str) -> None:
        """Wrap ``raw`` if it is a TOML table, else fail."""
        if not isinstance(raw, dict):
            _fail(context, f"expected a TOML table, got {type(raw).__name__}")
        self._entries: dict[str, object] = dict(raw)
        self._context = context

    def take(self, key: str) -> object:
        """Remove and return the required ``key``."""
        if key not in self._entries:
            _fail(self._context, f"missing required key {key!r}")
        return self._entries.pop(key)

    def take_optional(self, key: str) -> object | None:
        """Remove and return ``key``, or ``None`` if absent."""
        return self._entries.pop(key, None)

    def finish(self) -> None:
        """Fail if any key was never consumed (unknown keys)."""
        if self._entries:
            unknown = ", ".join(sorted(self._entries))
            _fail(self._context, f"unknown keys: {unknown}")


def _decimal_string(raw: object, context: str) -> Decimal:
    """Parse a money/rate figure, which must be a TOML string (§5.3)."""
    if isinstance(raw, float):
        _fail(context, 'float-typed number; write money and rates as strings ("0.25")')
    if not isinstance(raw, str):
        type_name = type(raw).__name__
        _fail(context, f"money and rates must be TOML strings, got {type_name}")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        _fail(context, f"not a valid decimal number: {raw!r}")
    if not value.is_finite():
        _fail(context, "number must be finite")
    return value


def _money(raw: object, context: str) -> Money:
    """Parse a non-negative monetary amount."""
    value = _decimal_string(raw, context)
    if value < 0:
        _fail(context, "money amounts must be non-negative")
    return Money(value)


def _fraction(raw: object, context: str) -> Rate:
    """Parse a rate that must lie in [0, 1]."""
    value = _decimal_string(raw, context)
    if not Decimal(0) <= value <= Decimal(1):
        _fail(context, "rates must be fractions between 0 and 1")
    return Rate(value)


def _integer(raw: object, context: str, *, minimum: int) -> int:
    """Parse an integer of at least ``minimum`` (bools rejected)."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        _fail(context, f"expected an integer, got {type(raw).__name__}")
    if raw < minimum:
        _fail(context, f"must be at least {minimum}")
    return raw


def _plain_date(raw: object, context: str) -> date:
    """Parse a TOML local date (a datetime with a time part is an error)."""
    if isinstance(raw, datetime):
        _fail(context, "expected a calendar date without a time part")
    if not isinstance(raw, date):
        _fail(context, f"expected a TOML date, got {type(raw).__name__}")
    return raw


def _optional_date(raw: object | None, context: str) -> date | None:
    """Parse an optional TOML local date."""
    return None if raw is None else _plain_date(raw, context)


def _string(raw: object, context: str) -> str:
    """Parse a non-empty string."""
    if not isinstance(raw, str):
        _fail(context, f"expected a string, got {type(raw).__name__}")
    if not raw:
        _fail(context, _MUST_NOT_BE_EMPTY)
    return raw


def _array(raw: object, context: str) -> list[object]:
    """Parse a non-empty TOML array."""
    if not isinstance(raw, list):
        _fail(context, f"expected an array, got {type(raw).__name__}")
    if not raw:
        _fail(context, _MUST_NOT_BE_EMPTY)
    return raw


def _sources(raw: object, context: str) -> tuple[str, ...]:
    """Parse the mandatory list of https source URLs."""
    entries = _array(raw, context)
    result: list[str] = []
    for index, entry in enumerate(entries):
        url = _string(entry, f"{context}[{index}]")
        if not url.startswith("https://"):
            _fail(f"{context}[{index}]", "sources must be https:// URLs")
        result.append(url)
    return tuple(result)


def _load_document(text: str, context: str) -> _Table:
    """Parse ``text`` as TOML into a strict root table."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        _fail(context, f"invalid TOML: {error}")
    return _Table(document, context)


def _take_schema_version(root: _Table, context: str) -> int:
    """Consume ``schema_version``; unsupported versions fail before parsing."""
    version = _integer(
        root.take("schema_version"), f"{context}.schema_version", minimum=1
    )
    if version != SCHEMA_VERSION:
        _fail(
            f"{context}.schema_version",
            f"schema_version {version} is not supported ({SCHEMA_VERSION})",
        )
    return version


def _parse_file_meta(raw: object, context: str) -> FileMeta:
    """Parse the ``[meta]`` table of a non-tax-year file."""
    table = _Table(raw, context)
    verified_on = _plain_date(table.take("verified_on"), f"{context}.verified_on")
    sources = _sources(table.take("sources"), f"{context}.sources")
    table.finish()
    return FileMeta(verified_on=verified_on, sources=sources)


def _parse_tax_year_meta(raw: object, context: str) -> TaxYearMeta:
    """Parse the ``[meta]`` table of a tax-year file."""
    table = _Table(raw, context)
    meta = TaxYearMeta(
        tax_year=_string(table.take("tax_year"), f"{context}.tax_year"),
        start_date=_plain_date(table.take("start_date"), f"{context}.start_date"),
        end_date=_plain_date(table.take("end_date"), f"{context}.end_date"),
        verified_on=_plain_date(table.take("verified_on"), f"{context}.verified_on"),
        sources=_sources(table.take("sources"), f"{context}.sources"),
    )
    table.finish()
    return meta


def _parse_band(raw: object, context: str) -> TaxBand:
    """Parse one income-tax band."""
    table = _Table(raw, context)
    name = _string(table.take("name"), f"{context}.name")
    rate = _fraction(table.take("rate"), f"{context}.rate")
    upper_raw = table.take_optional("upper")
    upper = None if upper_raw is None else _money(upper_raw, f"{context}.upper")
    table.finish()
    return TaxBand(name=name, rate=rate, upper=upper)


def _parse_income_tax_schedule(raw: object, context: str) -> IncomeTaxSchedule:
    """Parse one regime's allowance, taper, and band table."""
    table = _Table(raw, context)
    personal_allowance = _money(
        table.take("personal_allowance"), f"{context}.personal_allowance"
    )
    pa_taper_threshold = _money(
        table.take("pa_taper_threshold"), f"{context}.pa_taper_threshold"
    )
    pa_taper_rate = _fraction(table.take("pa_taper_rate"), f"{context}.pa_taper_rate")
    bands_raw = _array(table.take("bands"), f"{context}.bands")
    bands = tuple(
        _parse_band(item, f"{context}.bands[{index}]")
        for index, item in enumerate(bands_raw)
    )
    table.finish()
    return IncomeTaxSchedule(
        personal_allowance=personal_allowance,
        pa_taper_threshold=pa_taper_threshold,
        pa_taper_rate=pa_taper_rate,
        bands=bands,
    )


def _parse_pension(raw: object, context: str) -> PensionRules:
    """Parse the ``[pension]`` table."""
    table = _Table(raw, context)
    rules = PensionRules(
        annual_allowance=_money(
            table.take("annual_allowance"), f"{context}.annual_allowance"
        ),
        aa_taper_threshold_income=_money(
            table.take("aa_taper_threshold_income"),
            f"{context}.aa_taper_threshold_income",
        ),
        aa_taper_adjusted_income=_money(
            table.take("aa_taper_adjusted_income"),
            f"{context}.aa_taper_adjusted_income",
        ),
        aa_taper_rate=_fraction(
            table.take("aa_taper_rate"), f"{context}.aa_taper_rate"
        ),
        aa_taper_floor=_money(
            table.take("aa_taper_floor"), f"{context}.aa_taper_floor"
        ),
        mpaa=_money(table.take("mpaa"), f"{context}.mpaa"),
        aa_carry_forward_years=_integer(
            table.take("aa_carry_forward_years"),
            f"{context}.aa_carry_forward_years",
            minimum=0,
        ),
        scheme_pays_min_charge=_money(
            table.take("scheme_pays_min_charge"),
            f"{context}.scheme_pays_min_charge",
        ),
        member_relief_basic_amount=_money(
            table.take("member_relief_basic_amount"),
            f"{context}.member_relief_basic_amount",
        ),
        member_relief_max_age=_integer(
            table.take("member_relief_max_age"),
            f"{context}.member_relief_max_age",
            minimum=1,
        ),
        relief_at_source_rate=_fraction(
            table.take("relief_at_source_rate"), f"{context}.relief_at_source_rate"
        ),
        tax_free_lump_sum_fraction=_fraction(
            table.take("tax_free_lump_sum_fraction"),
            f"{context}.tax_free_lump_sum_fraction",
        ),
        lump_sum_allowance=_money(
            table.take("lump_sum_allowance"), f"{context}.lump_sum_allowance"
        ),
        lump_sum_death_benefit_allowance=_money(
            table.take("lump_sum_death_benefit_allowance"),
            f"{context}.lump_sum_death_benefit_allowance",
        ),
        db_valuation_factor=_integer(
            table.take("db_valuation_factor"),
            f"{context}.db_valuation_factor",
            minimum=1,
        ),
    )
    table.finish()
    return rules


def _parse_isa(raw: object, context: str) -> IsaRules:
    """Parse the ``[isa]`` table."""
    table = _Table(raw, context)
    rules = IsaRules(
        annual_allowance=_money(
            table.take("annual_allowance"), f"{context}.annual_allowance"
        ),
        lisa_allowance=_money(
            table.take("lisa_allowance"), f"{context}.lisa_allowance"
        ),
        lisa_bonus_rate=_fraction(
            table.take("lisa_bonus_rate"), f"{context}.lisa_bonus_rate"
        ),
        lisa_withdrawal_charge=_fraction(
            table.take("lisa_withdrawal_charge"), f"{context}.lisa_withdrawal_charge"
        ),
    )
    table.finish()
    return rules


def _parse_savings(raw: object, context: str) -> SavingsRules:
    """Parse the ``[savings]`` table."""
    table = _Table(raw, context)
    rules = SavingsRules(
        starting_rate_limit=_money(
            table.take("starting_rate_limit"), f"{context}.starting_rate_limit"
        ),
        psa_basic=_money(table.take("psa_basic"), f"{context}.psa_basic"),
        psa_higher=_money(table.take("psa_higher"), f"{context}.psa_higher"),
        psa_additional=_money(
            table.take("psa_additional"), f"{context}.psa_additional"
        ),
    )
    table.finish()
    return rules


def _parse_dividend_rate(raw: object, context: str) -> DividendRate:
    """Parse one entry of the ``dividend.rates`` array."""
    table = _Table(raw, context)
    name = _string(table.take("name"), f"{context}.name")
    rate = _fraction(table.take("rate"), f"{context}.rate")
    table.finish()
    return DividendRate(name=name, rate=rate)


def _parse_dividend(raw: object, context: str) -> DividendRules:
    """Parse the ``[dividend]`` table."""
    table = _Table(raw, context)
    allowance = _money(table.take("allowance"), f"{context}.allowance")
    rates_raw = _array(table.take("rates"), f"{context}.rates")
    rates = tuple(
        _parse_dividend_rate(item, f"{context}.rates[{index}]")
        for index, item in enumerate(rates_raw)
    )
    table.finish()
    return DividendRules(allowance=allowance, rates=rates)


def _parse_marriage_allowance(raw: object, context: str) -> MarriageAllowanceRules:
    """Parse the ``[marriage_allowance]`` table."""
    table = _Table(raw, context)
    rules = MarriageAllowanceRules(
        transferable_amount=_money(
            table.take("transferable_amount"), f"{context}.transferable_amount"
        ),
        recipient_top_band_ruk=_string(
            table.take("recipient_top_band_ruk"),
            f"{context}.recipient_top_band_ruk",
        ),
        recipient_top_band_scotland=_string(
            table.take("recipient_top_band_scotland"),
            f"{context}.recipient_top_band_scotland",
        ),
    )
    table.finish()
    return rules


def parse_tax_year(text: str, *, context: str = "<tax-year data>") -> TaxYearFile:
    """Parse and strictly validate one tax-year TOML document."""
    root = _load_document(text, context)
    schema_version = _take_schema_version(root, context)
    meta = _parse_tax_year_meta(root.take("meta"), f"{context}.meta")
    income_tax = _Table(root.take("income_tax"), f"{context}.income_tax")
    ruk = _parse_income_tax_schedule(
        income_tax.take("ruk"), f"{context}.income_tax.ruk"
    )
    scotland = _parse_income_tax_schedule(
        income_tax.take("scotland"), f"{context}.income_tax.scotland"
    )
    income_tax.finish()
    pension = _parse_pension(root.take("pension"), f"{context}.pension")
    isa = _parse_isa(root.take("isa"), f"{context}.isa")
    savings = _parse_savings(root.take("savings"), f"{context}.savings")
    dividend = _parse_dividend(root.take("dividend"), f"{context}.dividend")
    marriage_allowance = _parse_marriage_allowance(
        root.take("marriage_allowance"), f"{context}.marriage_allowance"
    )
    root.finish()
    return TaxYearFile(
        schema_version=schema_version,
        meta=meta,
        income_tax_ruk=ruk,
        income_tax_scotland=scotland,
        pension=pension,
        isa=isa,
        savings=savings,
        dividend=dividend,
        marriage_allowance=marriage_allowance,
    )


def _parse_nmpa_step(raw: object, context: str) -> NmpaStep:
    """Parse one effective-dated NMPA step."""
    table = _Table(raw, context)
    age = _integer(table.take("age"), f"{context}.age", minimum=1)
    effective_from = _optional_date(
        table.take_optional("effective_from"), f"{context}.effective_from"
    )
    table.finish()
    return NmpaStep(age=age, effective_from=effective_from)


def _parse_spa_band(raw: object, context: str) -> SpaBand:
    """Parse one SPA timetable band (age-based xor date-based)."""
    table = _Table(raw, context)
    dob_from = _optional_date(table.take_optional("dob_from"), f"{context}.dob_from")
    dob_to = _optional_date(table.take_optional("dob_to"), f"{context}.dob_to")
    age_raw = table.take_optional("age")
    reaches_raw = table.take_optional("reaches_on")
    table.finish()
    if (age_raw is None) == (reaches_raw is None):
        _fail(context, "exactly one of 'age' and 'reaches_on' is required")
    if age_raw is None:
        reaches_on = _plain_date(reaches_raw, f"{context}.reaches_on")
        return SpaDateBand(dob_from=dob_from, dob_to=dob_to, reaches_on=reaches_on)
    age_table = _Table(age_raw, f"{context}.age")
    years = _integer(age_table.take("years"), f"{context}.age.years", minimum=1)
    months_raw = age_table.take_optional("months")
    months = 0
    if months_raw is not None:
        months = _integer(months_raw, f"{context}.age.months", minimum=0)
    age_table.finish()
    return SpaAgeBand(dob_from=dob_from, dob_to=dob_to, years=years, months=months)


def _parse_lisa(raw: object, context: str) -> LisaAges:
    """Parse the ``[lisa]`` age gates."""
    table = _Table(raw, context)

    def gate(key: str) -> int:
        """Take one required age gate."""
        return _integer(table.take(key), f"{context}.{key}", minimum=1)

    ages = LisaAges(
        open_age_min=gate("open_age_min"),
        open_age_max=gate("open_age_max"),
        contribute_until_age=gate("contribute_until_age"),
        access_age=gate("access_age"),
    )
    table.finish()
    return ages


def _parse_death_benefits(raw: object, context: str) -> DeathBenefitAges:
    """Parse the ``[death_benefits]`` table."""
    table = _Table(raw, context)
    ages = DeathBenefitAges(
        income_tax_free_before_age=_integer(
            table.take("income_tax_free_before_age"),
            f"{context}.income_tax_free_before_age",
            minimum=1,
        )
    )
    table.finish()
    return ages


def _parse_deferral(raw: object, context: str) -> StatePensionDeferral:
    """Parse the ``[state_pension_deferral]`` table."""
    table = _Table(raw, context)
    deferral = StatePensionDeferral(
        increment_rate=_fraction(
            table.take("increment_rate"), f"{context}.increment_rate"
        ),
        per_weeks=_integer(table.take("per_weeks"), f"{context}.per_weeks", minimum=1),
    )
    table.finish()
    return deferral


def parse_age_rules(text: str, *, context: str = "<age-rules data>") -> AgeRulesFile:
    """Parse and strictly validate an age-rules TOML document."""
    root = _load_document(text, context)
    schema_version = _take_schema_version(root, context)
    meta = _parse_file_meta(root.take("meta"), f"{context}.meta")
    nmpa_table = _Table(root.take("nmpa"), f"{context}.nmpa")
    steps_raw = _array(nmpa_table.take("steps"), f"{context}.nmpa.steps")
    nmpa = tuple(
        _parse_nmpa_step(item, f"{context}.nmpa.steps[{index}]")
        for index, item in enumerate(steps_raw)
    )
    nmpa_table.finish()
    spa_table = _Table(root.take("state_pension_age"), f"{context}.state_pension_age")
    bands_raw = _array(spa_table.take("bands"), f"{context}.state_pension_age.bands")
    spa_bands = tuple(
        _parse_spa_band(item, f"{context}.state_pension_age.bands[{index}]")
        for index, item in enumerate(bands_raw)
    )
    spa_table.finish()
    lisa = _parse_lisa(root.take("lisa"), f"{context}.lisa")
    deferral = _parse_deferral(
        root.take("state_pension_deferral"), f"{context}.state_pension_deferral"
    )
    death_benefits = _parse_death_benefits(
        root.take("death_benefits"), f"{context}.death_benefits"
    )
    root.finish()
    return AgeRulesFile(
        schema_version=schema_version,
        meta=meta,
        nmpa=nmpa,
        spa_bands=spa_bands,
        lisa=lisa,
        deferral=deferral,
        death_benefits=death_benefits,
    )


def _signed_rate(raw: object, context: str) -> Decimal:
    """Parse an annual rate that may be negative but must exceed -100%.

    Historical returns and inflation are frequently negative and can
    exceed +100%, so neither ``_fraction`` nor ``_money`` fits; -100%
    or worse can never be recomposed into a real rate
    (:mod:`glidepath.core.backtest`).
    """
    value = _decimal_string(raw, context)
    if value <= Decimal(-1):
        _fail(context, "rates must be greater than -1 (-100%)")
    return value


def _parse_history_year(raw: object, context: str) -> HistoricalYear:
    """Parse one observed year of the return series."""
    table = _Table(raw, context)
    year = _integer(table.take("year"), f"{context}.year", minimum=1)
    equity = _signed_rate(table.take("equity"), f"{context}.equity")
    bonds = _signed_rate(table.take("bonds"), f"{context}.bonds")
    cash = _signed_rate(table.take("cash"), f"{context}.cash")
    cpi = _signed_rate(table.take("cpi"), f"{context}.cpi")
    table.finish()
    return HistoricalYear(year=year, equity=equity, bonds=bonds, cash=cash, cpi=cpi)


def parse_returns_history(
    text: str, *, context: str = "<returns-history data>"
) -> ReturnsHistoryFile:
    """Parse and strictly validate a returns-history TOML document."""
    root = _load_document(text, context)
    schema_version = _take_schema_version(root, context)
    meta = _parse_file_meta(root.take("meta"), f"{context}.meta")
    returns_table = _Table(root.take("returns"), f"{context}.returns")
    series_context = f"{context}.returns.series"
    entries_raw = _array(returns_table.take("series"), series_context)
    years = tuple(
        _parse_history_year(item, f"{series_context}[{index}]")
        for index, item in enumerate(entries_raw)
    )
    returns_table.finish()
    root.finish()
    try:
        series = HistoricalSeries(years=years)
    except ValueError as error:
        _fail(series_context, str(error))
    return ReturnsHistoryFile(schema_version=schema_version, meta=meta, series=series)


def _assumption_value(raw: object, context: str) -> AssumptionValue:
    """Parse a default's value: Decimal string, integer, tag, or table."""
    if isinstance(raw, bool):
        _fail(context, "booleans are not valid assumption values")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        _fail(context, 'float-typed number; write numeric values as strings ("0.02")')
    if isinstance(raw, str):
        return _decimal_or_tag(raw, context)
    if isinstance(raw, dict):
        if not raw:
            _fail(context, _MUST_NOT_BE_EMPTY)
        items: dict[str, AssumptionValue] = {
            key: _assumption_value(item, f"{context}.{key}")
            for key, item in raw.items()
        }
        return FrozenTable(items)
    _fail(context, f"unsupported value type: {type(raw).__name__}")


def _decimal_or_tag(raw: str, context: str) -> Decimal | str:
    """Parse a string value: numeric strings become ``Decimal``."""
    if not raw:
        _fail(context, _MUST_NOT_BE_EMPTY)
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return raw
    if not value.is_finite():
        _fail(context, "number must be finite")
    return value


def _parse_assumption(raw: object, context: str) -> AssumptionDefault:
    """Parse one ``[[assumption]]`` entry."""
    table = _Table(raw, context)
    key_text = _string(table.take("key"), f"{context}.key")
    try:
        key = AssumptionKey(key_text)
    except ValueError:
        _fail(f"{context}.key", f"unknown assumption key {key_text!r}")
    value = _assumption_value(table.take("value"), f"{context}.value")
    basis = _string(table.take("basis"), f"{context}.basis")
    table.finish()
    return AssumptionDefault(key=key, value=value, basis=basis)


def parse_default_assumptions(
    text: str, *, context: str = "<assumptions data>"
) -> AssumptionsFile:
    """Parse and strictly validate a default-assumptions TOML document."""
    root = _load_document(text, context)
    schema_version = _take_schema_version(root, context)
    meta = _parse_file_meta(root.take("meta"), f"{context}.meta")
    entries = _array(root.take("assumption"), f"{context}.assumption")
    defaults = tuple(
        _parse_assumption(item, f"{context}.assumption[{index}]")
        for index, item in enumerate(entries)
    )
    root.finish()
    return AssumptionsFile(schema_version=schema_version, meta=meta, defaults=defaults)


def _data_directory() -> Traversable:
    """The packaged ``data/`` directory of the UK region."""
    return resources.files(_DATA_ANCHOR).joinpath("data")


def _read_data_file(filename: str) -> str:
    """Read one shipped data file as UTF-8 text."""
    target = _data_directory().joinpath(filename)
    try:
        return target.read_text(encoding="utf-8")
    except OSError:
        _fail(filename, "no such shipped UK data file")


def tax_year_filename(start_year: int) -> str:
    """The canonical data filename for the tax year starting ``start_year``."""
    return f"tax_year_{start_year}_{(start_year + 1) % 100:02d}.toml"


def data_file_digest(filename: str) -> str:
    """A short SHA-256 digest of one shipped data file's text.

    Part of the region data version (planning §4.6): ``verified_on``
    dates alone cannot tell two same-day revisions of a file apart, so
    the content digest makes the version string change whenever the
    data does.
    """
    text = _read_data_file(filename)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def available_tax_years() -> tuple[int, ...]:
    """Start years of every shipped ``tax_year_*.toml``, ascending."""
    years: list[int] = []
    for entry in _data_directory().iterdir():
        match = _TAX_YEAR_FILE.fullmatch(entry.name)
        if match is None:
            continue
        start_year = int(match.group(1))
        if int(match.group(2)) == (start_year + 1) % 100:
            years.append(start_year)
    return tuple(sorted(years))


def load_tax_year(start_year: int) -> TaxYearFile:
    """Load the shipped file for the tax year starting 6 April ``start_year``."""
    filename = tax_year_filename(start_year)
    parsed = parse_tax_year(_read_data_file(filename), context=filename)
    expected = tax_year_label(start_year)
    if parsed.meta.tax_year != expected:
        claimed = parsed.meta.tax_year
        _fail(filename, f"file claims tax year {claimed!r}, not {expected!r}")
    return parsed


def load_age_rules() -> AgeRulesFile:
    """Load the shipped ``age_rules.toml``."""
    return parse_age_rules(
        _read_data_file(AGE_RULES_FILENAME), context=AGE_RULES_FILENAME
    )


def load_default_assumptions() -> AssumptionsFile:
    """Load the shipped ``assumptions_default.toml``."""
    return parse_default_assumptions(
        _read_data_file(ASSUMPTIONS_FILENAME), context=ASSUMPTIONS_FILENAME
    )


def load_returns_history() -> ReturnsHistoryFile:
    """Load the shipped ``returns_history.toml``."""
    return parse_returns_history(
        _read_data_file(RETURNS_HISTORY_FILENAME), context=RETURNS_HISTORY_FILENAME
    )
