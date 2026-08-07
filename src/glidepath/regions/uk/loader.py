"""Strict TOML loader for the UK region data files (planning §5.3).

``importlib.resources`` + stdlib ``tomllib`` for the parsing; pydantic
wire models for the validation. Loading is strict:

- money and rates are TOML **strings** parsed to ``Decimal`` — a bare
  float (or int) in a money position is a load error;
- every file declares ``schema_version`` and carries a mandatory
  ``[meta]`` table with ``verified_on`` + ``sources``;
- unknown keys anywhere are load errors (``extra="forbid"``).

Each wire model builds its schema dataclass inside an after-validator,
so the §5.3 policy invariants (band ordering, SPA tiling, NMPA
baseline) surface as validation errors carrying the precise location.
Failures raise :class:`~glidepath.regions.uk.schema.DataFileError`
with a ``file.section.key`` context string.
"""

import hashlib
import re
import tomllib
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from importlib import resources
from typing import TYPE_CHECKING, Annotated, NoReturn, Self, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    PlainValidator,
    PrivateAttr,
    ValidationError,
    model_validator,
)

from glidepath.core import AssumptionKey, HistoricalSeries, HistoricalYear, Money, Rate
from glidepath.regions.uk.schema import (
    SCHEMA_VERSION,
    AgeRulesFile,
    AssumptionDefault,
    AssumptionsFile,
    AssumptionValue,
    DataFileError,
    DividendRate,
    DividendRules,
    FileMeta,
    FrozenTable,
    IncomeTaxSchedule,
    IsaRules,
    LisaAges,
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


def _error_message(error: ValidationError, context: str) -> str:
    """One context-prefixed message from a validation failure.

    The first error's location renders as the ``file.section.key``
    context string of planning §5.3; pydantic's ``Value error, ``
    prefix is stripped so the loader's messages read unchanged.
    """
    details = error.errors()
    first = details[0]
    path = context + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in first["loc"]
    )
    message = first["msg"].removeprefix("Value error, ")
    suffix = f" (and {len(details) - 1} more)" if len(details) > 1 else ""
    return f"{path}: {message}{suffix}"


def _decimal_string(raw: object) -> Decimal:
    """Parse a money/rate figure, which must be a TOML string (§5.3)."""
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, float):
        msg = 'float-typed number; write money and rates as strings ("0.25")'
        raise DataFileError(msg)
    if not isinstance(raw, str):
        msg = f"money and rates must be TOML strings, got {type(raw).__name__}"
        raise DataFileError(msg)
    try:
        value = Decimal(raw)
    except InvalidOperation:
        msg = f"not a valid decimal number: {raw!r}"
        raise DataFileError(msg) from None
    if not value.is_finite():
        msg = "number must be finite"
        raise DataFileError(msg)
    return value


def _money_amount(raw: object) -> Money:
    """Parse a non-negative monetary amount."""
    if isinstance(raw, Money):
        return raw
    value = _decimal_string(raw)
    if value < 0:
        msg = "money amounts must be non-negative"
        raise DataFileError(msg)
    return Money(value)


def _fraction_rate(raw: object) -> Rate:
    """Parse a rate that must lie in [0, 1]."""
    if isinstance(raw, Rate):
        return raw
    value = _decimal_string(raw)
    if not Decimal(0) <= value <= Decimal(1):
        msg = "rates must be fractions between 0 and 1"
        raise DataFileError(msg)
    return Rate(value)


def _signed_rate(raw: object) -> Decimal:
    """Parse an annual rate that may be negative but must exceed -100%.

    Historical returns and inflation are frequently negative and can
    exceed +100%, so neither the fraction nor the money rule fits;
    -100% or worse can never be recomposed into a real rate
    (:mod:`glidepath.core.backtest`).
    """
    value = _decimal_string(raw)
    if value <= Decimal(-1):
        msg = "rates must be greater than -1 (-100%)"
        raise DataFileError(msg)
    return value


def _integer(raw: object, *, minimum: int) -> int:
    """Parse an integer of at least ``minimum`` (bools rejected)."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        msg = f"expected an integer, got {type(raw).__name__}"
        raise DataFileError(msg)
    if raw < minimum:
        msg = f"must be at least {minimum}"
        raise DataFileError(msg)
    return raw


def _positive_int(raw: object) -> int:
    """Parse an integer of at least one."""
    return _integer(raw, minimum=1)


def _non_negative_int(raw: object) -> int:
    """Parse an integer of at least zero."""
    return _integer(raw, minimum=0)


def _schema_version_value(raw: object) -> int:
    """Consume ``schema_version``; unsupported versions fail early."""
    version = _integer(raw, minimum=1)
    if version != SCHEMA_VERSION:
        msg = f"schema_version {version} is not supported ({SCHEMA_VERSION})"
        raise DataFileError(msg)
    return version


def _plain_date(raw: object) -> date:
    """Parse a TOML local date (a datetime with a time part is an error)."""
    if isinstance(raw, datetime):
        msg = "expected a calendar date without a time part"
        raise DataFileError(msg)
    if not isinstance(raw, date):
        msg = f"expected a TOML date, got {type(raw).__name__}"
        raise DataFileError(msg)
    return raw


def _non_empty_str(raw: object) -> str:
    """Parse a non-empty string."""
    if not isinstance(raw, str):
        msg = f"expected a string, got {type(raw).__name__}"
        raise DataFileError(msg)
    if not raw:
        raise DataFileError(_MUST_NOT_BE_EMPTY)
    return raw


def _source_url(raw: object) -> str:
    """Parse one https source URL."""
    url = _non_empty_str(raw)
    if not url.startswith("https://"):
        msg = "sources must be https:// URLs"
        raise DataFileError(msg)
    return url


def _non_empty[T](value: tuple[T, ...]) -> tuple[T, ...]:
    """Require a non-empty array."""
    if not value:
        raise DataFileError(_MUST_NOT_BE_EMPTY)
    return value


def _assumption_value(raw: object) -> AssumptionValue:
    """Parse a default's value: Decimal string, integer, tag, or table."""
    if isinstance(raw, bool):
        msg = "booleans are not valid assumption values"
        raise DataFileError(msg)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        msg = 'float-typed number; write numeric values as strings ("0.02")'
        raise DataFileError(msg)
    if isinstance(raw, str):
        return _decimal_or_tag(raw)
    if isinstance(raw, FrozenTable | dict):
        if not raw:
            raise DataFileError(_MUST_NOT_BE_EMPTY)
        return FrozenTable({key: _assumption_value(item) for key, item in raw.items()})
    msg = f"unsupported value type: {type(raw).__name__}"
    raise DataFileError(msg)


def _decimal_or_tag(raw: str) -> Decimal | str:
    """Parse a string value: numeric strings become ``Decimal``."""
    if not raw:
        raise DataFileError(_MUST_NOT_BE_EMPTY)
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return raw
    if not value.is_finite():
        msg = "number must be finite"
        raise DataFileError(msg)
    return value


def _uk_assumption_key(raw: object) -> AssumptionKey:
    """Parse a dotted assumption key from the stable catalogue."""
    if isinstance(raw, AssumptionKey):
        return raw
    text = _non_empty_str(raw)
    try:
        return AssumptionKey(text)
    except ValueError:
        msg = f"unknown assumption key {text!r}"
        raise DataFileError(msg) from None


TomlDecimal = Annotated[Decimal, PlainValidator(_decimal_string)]
TomlMoney = Annotated[Money, PlainValidator(_money_amount)]
TomlFraction = Annotated[Rate, PlainValidator(_fraction_rate)]
TomlSignedRate = Annotated[Decimal, PlainValidator(_signed_rate)]
PositiveInt = Annotated[int, PlainValidator(_positive_int)]
NonNegativeInt = Annotated[int, PlainValidator(_non_negative_int)]
SchemaVersionField = Annotated[int, PlainValidator(_schema_version_value)]
TomlDate = Annotated[date, PlainValidator(_plain_date)]
NonEmptyStr = Annotated[str, PlainValidator(_non_empty_str)]
SourceUrl = Annotated[str, PlainValidator(_source_url)]
SourcesField = Annotated[tuple[SourceUrl, ...], AfterValidator(_non_empty)]
AssumptionValueField = Annotated[object, PlainValidator(_assumption_value)]
"""Typed ``object`` on the wire: pydantic cannot lazily resolve the
recursive PEP 695 ``AssumptionValue`` alias, so the validator carries
the real type and the entry model casts at the domain boundary."""
UkAssumptionKeyField = Annotated[AssumptionKey, PlainValidator(_uk_assumption_key)]


class _WireTable(BaseModel):
    """Base wire model: unknown keys rejected."""

    model_config = ConfigDict(extra="forbid")


class WireFileMeta(_WireTable):
    """The ``[meta]`` table of a non-tax-year file."""

    verified_on: TomlDate
    sources: SourcesField

    _domain: FileMeta = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the schema meta record."""
        self._domain = FileMeta(verified_on=self.verified_on, sources=self.sources)
        return self

    @property
    def domain(self) -> FileMeta:
        """The validated schema meta record."""
        return self._domain


class WireTaxYearMeta(_WireTable):
    """The ``[meta]`` table of a tax-year file."""

    tax_year: NonEmptyStr
    start_date: TomlDate
    end_date: TomlDate
    verified_on: TomlDate
    sources: SourcesField

    _domain: TaxYearMeta = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the schema meta record, surfacing its invariants."""
        self._domain = TaxYearMeta(
            tax_year=self.tax_year,
            start_date=self.start_date,
            end_date=self.end_date,
            verified_on=self.verified_on,
            sources=self.sources,
        )
        return self

    @property
    def domain(self) -> TaxYearMeta:
        """The validated schema meta record."""
        return self._domain


class WireTaxBand(_WireTable):
    """One income-tax band."""

    name: NonEmptyStr
    rate: TomlFraction
    upper: TomlMoney | None = None

    _domain: TaxBand = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the schema band, surfacing its invariants."""
        self._domain = TaxBand(name=self.name, rate=self.rate, upper=self.upper)
        return self

    @property
    def domain(self) -> TaxBand:
        """The validated schema band."""
        return self._domain


class WireIncomeTaxSchedule(_WireTable):
    """One regime's allowance, taper, and band table."""

    personal_allowance: TomlMoney
    pa_taper_threshold: TomlMoney
    pa_taper_rate: TomlFraction
    bands: Annotated[tuple[WireTaxBand, ...], AfterValidator(_non_empty)]

    _domain: IncomeTaxSchedule = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the schema schedule, surfacing its invariants."""
        self._domain = IncomeTaxSchedule(
            personal_allowance=self.personal_allowance,
            pa_taper_threshold=self.pa_taper_threshold,
            pa_taper_rate=self.pa_taper_rate,
            bands=tuple(band.domain for band in self.bands),
        )
        return self

    @property
    def domain(self) -> IncomeTaxSchedule:
        """The validated schema schedule."""
        return self._domain


class WireIncomeTax(_WireTable):
    """The ``[income_tax]`` table: one schedule per regime."""

    ruk: WireIncomeTaxSchedule
    scotland: WireIncomeTaxSchedule


class WirePension(_WireTable):
    """The ``[pension]`` table."""

    annual_allowance: TomlMoney
    aa_taper_threshold_income: TomlMoney
    aa_taper_adjusted_income: TomlMoney
    aa_taper_rate: TomlFraction
    aa_taper_floor: TomlMoney
    mpaa: TomlMoney
    aa_carry_forward_years: NonNegativeInt
    scheme_pays_min_charge: TomlMoney
    member_relief_basic_amount: TomlMoney
    member_relief_max_age: PositiveInt
    relief_at_source_rate: TomlFraction
    tax_free_lump_sum_fraction: TomlFraction
    lump_sum_allowance: TomlMoney
    lump_sum_death_benefit_allowance: TomlMoney
    db_valuation_factor: PositiveInt

    _domain: PensionRules = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the schema pension rules, surfacing their invariants."""
        self._domain = PensionRules(
            annual_allowance=self.annual_allowance,
            aa_taper_threshold_income=self.aa_taper_threshold_income,
            aa_taper_adjusted_income=self.aa_taper_adjusted_income,
            aa_taper_rate=self.aa_taper_rate,
            aa_taper_floor=self.aa_taper_floor,
            mpaa=self.mpaa,
            aa_carry_forward_years=self.aa_carry_forward_years,
            scheme_pays_min_charge=self.scheme_pays_min_charge,
            member_relief_basic_amount=self.member_relief_basic_amount,
            member_relief_max_age=self.member_relief_max_age,
            relief_at_source_rate=self.relief_at_source_rate,
            tax_free_lump_sum_fraction=self.tax_free_lump_sum_fraction,
            lump_sum_allowance=self.lump_sum_allowance,
            lump_sum_death_benefit_allowance=self.lump_sum_death_benefit_allowance,
            db_valuation_factor=self.db_valuation_factor,
        )
        return self

    @property
    def domain(self) -> PensionRules:
        """The validated schema pension rules."""
        return self._domain


class WireIsa(_WireTable):
    """The ``[isa]`` table."""

    annual_allowance: TomlMoney
    lisa_allowance: TomlMoney
    lisa_bonus_rate: TomlFraction
    lisa_withdrawal_charge: TomlFraction

    _domain: IsaRules = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the schema ISA rules, surfacing their invariants."""
        self._domain = IsaRules(
            annual_allowance=self.annual_allowance,
            lisa_allowance=self.lisa_allowance,
            lisa_bonus_rate=self.lisa_bonus_rate,
            lisa_withdrawal_charge=self.lisa_withdrawal_charge,
        )
        return self

    @property
    def domain(self) -> IsaRules:
        """The validated schema ISA rules."""
        return self._domain


class WireSavings(_WireTable):
    """The ``[savings]`` table."""

    starting_rate_limit: TomlMoney
    psa_basic: TomlMoney
    psa_higher: TomlMoney
    psa_additional: TomlMoney

    _domain: SavingsRules = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the schema savings rules, surfacing their invariants."""
        self._domain = SavingsRules(
            starting_rate_limit=self.starting_rate_limit,
            psa_basic=self.psa_basic,
            psa_higher=self.psa_higher,
            psa_additional=self.psa_additional,
        )
        return self

    @property
    def domain(self) -> SavingsRules:
        """The validated schema savings rules."""
        return self._domain


class WireDividendRate(_WireTable):
    """One entry of the ``dividend.rates`` array."""

    name: NonEmptyStr
    rate: TomlFraction

    _domain: DividendRate = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the schema dividend rate."""
        self._domain = DividendRate(name=self.name, rate=self.rate)
        return self

    @property
    def domain(self) -> DividendRate:
        """The validated schema dividend rate."""
        return self._domain


class WireDividend(_WireTable):
    """The ``[dividend]`` table."""

    allowance: TomlMoney
    rates: Annotated[tuple[WireDividendRate, ...], AfterValidator(_non_empty)]

    _domain: DividendRules = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the schema dividend rules, surfacing their invariants."""
        self._domain = DividendRules(
            allowance=self.allowance,
            rates=tuple(rate.domain for rate in self.rates),
        )
        return self

    @property
    def domain(self) -> DividendRules:
        """The validated schema dividend rules."""
        return self._domain


class WireTaxYearDoc(_WireTable):
    """One whole tax-year TOML document."""

    schema_version: SchemaVersionField
    meta: WireTaxYearMeta
    income_tax: WireIncomeTax
    pension: WirePension
    isa: WireIsa
    savings: WireSavings
    dividend: WireDividend

    _domain: TaxYearFile = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the schema file, surfacing the §5.3 invariants."""
        self._domain = TaxYearFile(
            schema_version=self.schema_version,
            meta=self.meta.domain,
            income_tax_ruk=self.income_tax.ruk.domain,
            income_tax_scotland=self.income_tax.scotland.domain,
            pension=self.pension.domain,
            isa=self.isa.domain,
            savings=self.savings.domain,
            dividend=self.dividend.domain,
        )
        return self

    @property
    def domain(self) -> TaxYearFile:
        """The validated schema file."""
        return self._domain


class WireNmpaStep(_WireTable):
    """One effective-dated NMPA step."""

    age: PositiveInt
    effective_from: TomlDate | None = None

    _domain: NmpaStep = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the schema step, surfacing its invariants."""
        self._domain = NmpaStep(age=self.age, effective_from=self.effective_from)
        return self

    @property
    def domain(self) -> NmpaStep:
        """The validated schema step."""
        return self._domain


class WireNmpa(_WireTable):
    """The ``[nmpa]`` table."""

    steps: Annotated[tuple[WireNmpaStep, ...], AfterValidator(_non_empty)]


class WireSpaAge(_WireTable):
    """The years-and-months age of an age-based SPA band."""

    years: PositiveInt
    months: NonNegativeInt = 0


class WireSpaBand(_WireTable):
    """One SPA timetable band (age-based xor date-based)."""

    dob_from: TomlDate | None = None
    dob_to: TomlDate | None = None
    age: WireSpaAge | None = None
    reaches_on: TomlDate | None = None

    _domain: SpaBand = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Require exactly one band form and construct it."""
        age = self.age
        reaches_on = self.reaches_on
        if age is not None and reaches_on is None:
            self._domain = SpaAgeBand(
                dob_from=self.dob_from,
                dob_to=self.dob_to,
                years=age.years,
                months=age.months,
            )
        elif reaches_on is not None and age is None:
            self._domain = SpaDateBand(
                dob_from=self.dob_from, dob_to=self.dob_to, reaches_on=reaches_on
            )
        else:
            msg = "exactly one of 'age' and 'reaches_on' is required"
            raise DataFileError(msg)
        return self

    @property
    def domain(self) -> SpaBand:
        """The validated schema band."""
        return self._domain


class WireSpa(_WireTable):
    """The ``[state_pension_age]`` table."""

    bands: Annotated[tuple[WireSpaBand, ...], AfterValidator(_non_empty)]


class WireLisa(_WireTable):
    """The ``[lisa]`` age gates."""

    open_age_min: PositiveInt
    open_age_max: PositiveInt
    contribute_until_age: PositiveInt
    access_age: PositiveInt

    _domain: LisaAges = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the schema age gates, surfacing their invariants."""
        self._domain = LisaAges(
            open_age_min=self.open_age_min,
            open_age_max=self.open_age_max,
            contribute_until_age=self.contribute_until_age,
            access_age=self.access_age,
        )
        return self

    @property
    def domain(self) -> LisaAges:
        """The validated schema age gates."""
        return self._domain


class WireDeferral(_WireTable):
    """The ``[state_pension_deferral]`` table."""

    increment_rate: TomlFraction
    per_weeks: PositiveInt

    _domain: StatePensionDeferral = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the schema deferral terms."""
        self._domain = StatePensionDeferral(
            increment_rate=self.increment_rate, per_weeks=self.per_weeks
        )
        return self

    @property
    def domain(self) -> StatePensionDeferral:
        """The validated schema deferral terms."""
        return self._domain


class WireAgeRulesDoc(_WireTable):
    """One whole age-rules TOML document."""

    schema_version: SchemaVersionField
    meta: WireFileMeta
    nmpa: WireNmpa
    state_pension_age: WireSpa
    lisa: WireLisa
    state_pension_deferral: WireDeferral

    _domain: AgeRulesFile = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the schema file, surfacing the §5.3 invariants."""
        self._domain = AgeRulesFile(
            schema_version=self.schema_version,
            meta=self.meta.domain,
            nmpa=tuple(step.domain for step in self.nmpa.steps),
            spa_bands=tuple(band.domain for band in self.state_pension_age.bands),
            lisa=self.lisa.domain,
            deferral=self.state_pension_deferral.domain,
        )
        return self

    @property
    def domain(self) -> AgeRulesFile:
        """The validated schema file."""
        return self._domain


class WireHistoryYear(_WireTable):
    """One observed year of the return series."""

    year: PositiveInt
    equity: TomlSignedRate
    bonds: TomlSignedRate
    cash: TomlSignedRate
    cpi: TomlSignedRate

    _domain: HistoricalYear = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the core historical year, surfacing its invariants."""
        self._domain = HistoricalYear(
            year=self.year,
            equity=self.equity,
            bonds=self.bonds,
            cash=self.cash,
            cpi=self.cpi,
        )
        return self

    @property
    def domain(self) -> HistoricalYear:
        """The validated core historical year."""
        return self._domain


class WireReturns(_WireTable):
    """The ``[returns]`` table holding the observed series."""

    series: Annotated[tuple[WireHistoryYear, ...], AfterValidator(_non_empty)]

    _series: HistoricalSeries = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the core series, surfacing its own invariants."""
        self._series = HistoricalSeries(
            years=tuple(year.domain for year in self.series)
        )
        return self

    @property
    def domain(self) -> HistoricalSeries:
        """The validated core series."""
        return self._series


class WireReturnsDoc(_WireTable):
    """One whole returns-history TOML document."""

    schema_version: SchemaVersionField
    meta: WireFileMeta
    returns: WireReturns

    _domain: ReturnsHistoryFile = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the schema file, surfacing the §5.3 invariants."""
        self._domain = ReturnsHistoryFile(
            schema_version=self.schema_version,
            meta=self.meta.domain,
            series=self.returns.domain,
        )
        return self

    @property
    def domain(self) -> ReturnsHistoryFile:
        """The validated schema file."""
        return self._domain


class WireAssumptionEntry(_WireTable):
    """One ``[[assumption]]`` entry."""

    key: UkAssumptionKeyField
    value: AssumptionValueField
    basis: NonEmptyStr

    _domain: AssumptionDefault = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the schema default, surfacing its invariants."""
        self._domain = AssumptionDefault(
            key=self.key, value=cast("AssumptionValue", self.value), basis=self.basis
        )
        return self

    @property
    def domain(self) -> AssumptionDefault:
        """The validated schema default."""
        return self._domain


class WireAssumptionsDoc(_WireTable):
    """One whole default-assumptions TOML document."""

    schema_version: SchemaVersionField
    meta: WireFileMeta
    assumption: Annotated[tuple[WireAssumptionEntry, ...], AfterValidator(_non_empty)]

    _domain: AssumptionsFile = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the schema file, surfacing the §5.3 invariants."""
        self._domain = AssumptionsFile(
            schema_version=self.schema_version,
            meta=self.meta.domain,
            defaults=tuple(entry.domain for entry in self.assumption),
        )
        return self

    @property
    def domain(self) -> AssumptionsFile:
        """The validated schema file."""
        return self._domain


def _load_toml(text: str, context: str) -> dict[str, object]:
    """Parse ``text`` as TOML into a root table."""
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        _fail(context, f"invalid TOML: {error}")


def _validated[M: BaseModel](model: type[M], text: str, context: str) -> M:
    """Validate a TOML document through one wire model."""
    raw = _load_toml(text, context)
    try:
        return model.model_validate(raw)
    except ValidationError as error:
        raise DataFileError(_error_message(error, context)) from error


def parse_tax_year(text: str, *, context: str = "<tax-year data>") -> TaxYearFile:
    """Parse and strictly validate one tax-year TOML document."""
    return _validated(WireTaxYearDoc, text, context).domain


def parse_age_rules(text: str, *, context: str = "<age-rules data>") -> AgeRulesFile:
    """Parse and strictly validate an age-rules TOML document."""
    return _validated(WireAgeRulesDoc, text, context).domain


def parse_returns_history(
    text: str, *, context: str = "<returns-history data>"
) -> ReturnsHistoryFile:
    """Parse and strictly validate a returns-history TOML document."""
    return _validated(WireReturnsDoc, text, context).domain


def parse_default_assumptions(
    text: str, *, context: str = "<assumptions data>"
) -> AssumptionsFile:
    """Parse and strictly validate a default-assumptions TOML document."""
    return _validated(WireAssumptionsDoc, text, context).domain


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
