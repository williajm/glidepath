"""Facts entry forms (roadmap 8.2; planning §1, §4.7, §5.1).

The form a shell renders to capture every §5.1 fact — DOB, balances
with ``as_of`` dates, contributions, DB scheme parameters, the NI
record / state pension forecast, and the pre-existing access facts —
plus the decisions a projectable plan needs (retirement age,
contribution and commutation choices). The shell binds
:class:`FactsFormViewModel` to widgets and returns raw text via
:class:`FactsFormData`; parsing back into `Fact`/`Decision`-wrapped
domain objects happens here, so validation and messages stay
UI-agnostic.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum, auto
from typing import TYPE_CHECKING

from glidepath.core import (
    AssetAllocation,
    AssumptionKey,
    ContributionSchedule,
    DBPension,
    Decision,
    EntityId,
    Fact,
    FactorTable,
    Household,
    Money,
    Person,
    Rate,
    ReliefMechanic,
    RevaluationBasis,
    RevaluationReference,
    Sex,
    SpendingPlan,
    StatePensionRecord,
    TaxResidencyId,
    Wrapper,
    WrapperKindId,
    new_entity_id,
    validate_household_v1,
)
from glidepath.regions.uk import (
    CASH_KIND,
    GIA_KIND,
    ISA_KIND,
    LISA_KIND,
    RUK_RESIDENCY,
    SCOTLAND_RESIDENCY,
    SIPP_KIND,
    WORKPLACE_DC_KIND,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class FieldKind(Enum):
    """How a shell should render one form field."""

    TEXT = auto()
    """A free-text entry; parsing happens in the app layer."""
    CHOICE = auto()
    """A pick-one list of :class:`ChoiceOption` entries."""


@dataclass(frozen=True)
class ChoiceOption:
    """One selectable option: the stable value and its display label."""

    value: str
    label: str


@dataclass(frozen=True)
class FieldSpec:
    """One form field a shell renders and returns raw text for."""

    key: str
    label: str
    kind: FieldKind = FieldKind.TEXT
    hint: str = ""
    required: bool = False
    choices: tuple[ChoiceOption, ...] = ()


@dataclass(frozen=True)
class SectionSpec:
    """One titled group of fields; repeatable sections list entities."""

    key: str
    title: str
    description: str
    fields: tuple[FieldSpec, ...]
    repeatable: bool = False
    add_label: str = ""
    remove_label: str = ""


@dataclass(frozen=True)
class FactsFormViewModel:
    """The whole facts entry screen (roadmap 8.2)."""

    title: str
    intro: str
    person: SectionSpec
    spending: SectionSpec
    state_pension: SectionSpec
    wrapper: SectionSpec
    db_pension: SectionSpec
    submit_label: str

    @property
    def sections(self) -> tuple[SectionSpec, ...]:
        """Every section, singular and repeatable alike."""
        return (
            self.person,
            self.spending,
            self.state_pension,
            self.wrapper,
            self.db_pension,
        )


@dataclass(frozen=True)
class FactsFormData:
    """Raw text captured by a shell, keyed exactly like the specs."""

    person: Mapping[str, str] = field(default_factory=dict)
    spending: Mapping[str, str] = field(default_factory=dict)
    state_pension: Mapping[str, str] = field(default_factory=dict)
    wrappers: tuple[Mapping[str, str], ...] = ()
    db_pensions: tuple[Mapping[str, str], ...] = ()


@dataclass(frozen=True)
class FormError:
    """One rejected field (or section, when ``field_key`` is empty)."""

    section: str
    index: int | None
    field_key: str
    message: str


@dataclass(frozen=True)
class FactsFormResult:
    """A parsed household, or the errors preventing one."""

    household: Household | None
    errors: tuple[FormError, ...]


_REQUIRED_MESSAGE = "this field is required"
_DATE_MESSAGE = "enter a date as YYYY-MM-DD"
_MONEY_MESSAGE = "enter an amount of money, e.g. 1250 or 1250.50"
_INT_MESSAGE = "enter a whole number"
_DECIMAL_MESSAGE = "enter a plain number, e.g. 0.05"
_CHOICE_MESSAGE = "pick one of the listed options"
_FACTORS_MESSAGE = "enter age:factor pairs separated by commas, e.g. 60:0.75, 65:1"
_CONTRIBUTIONS_NEED_EMPLOYEE = (
    "enter the employee contribution (0 is fine) to record contributions"
)

_AS_OF_HINT = "YYYY-MM-DD; blank means today"

_SELECT_OPTION = ChoiceOption(value="", label="Select…")
"""The blank first option of every required choice: a real value must
be picked, never pre-selected (planning §1 — facts are stated, not
guessed)."""

_SEXES: Mapping[str, Sex] = {"female": Sex.FEMALE, "male": Sex.MALE}
_RESIDENCIES: Mapping[str, TaxResidencyId] = {
    str(RUK_RESIDENCY): RUK_RESIDENCY,
    str(SCOTLAND_RESIDENCY): SCOTLAND_RESIDENCY,
}
_WRAPPER_KINDS: Mapping[str, WrapperKindId] = {
    str(WORKPLACE_DC_KIND): WORKPLACE_DC_KIND,
    str(SIPP_KIND): SIPP_KIND,
    str(ISA_KIND): ISA_KIND,
    str(LISA_KIND): LISA_KIND,
    str(GIA_KIND): GIA_KIND,
    str(CASH_KIND): CASH_KIND,
}
_PENSION_KINDS = frozenset({WORKPLACE_DC_KIND, SIPP_KIND})
"""Kinds whose wrappers may carry a crystallised (drawdown) balance."""
_CASH_ALLOCATION = AssetAllocation(equity=Decimal(0), bonds=Decimal(0), cash=Decimal(1))
"""A cash account holds cash — never the glide path (roadmap 9.2)."""
_RELIEF_MECHANICS: Mapping[str, ReliefMechanic] = {
    "relief_at_source": ReliefMechanic.RELIEF_AT_SOURCE,
    "net_pay": ReliefMechanic.NET_PAY,
}
_ESCALATIONS: Mapping[str, AssumptionKey] = {
    "earnings": AssumptionKey.EARNINGS_GROWTH_REAL,
}
_REVALUATION_REFERENCES: Mapping[str, RevaluationReference] = {
    "cpi": RevaluationReference.CPI,
    "fixed": RevaluationReference.FIXED,
    "none": RevaluationReference.NONE,
}


@dataclass
class _FormContext:
    """Shared parsing state for one submission."""

    recorded_on: datetime
    default_as_of: date
    errors: list[FormError]


class _SectionReader:
    """Parses one section instance's raw values, accumulating errors."""

    def __init__(
        self,
        context: _FormContext,
        section: str,
        values: Mapping[str, str],
        index: int | None = None,
    ) -> None:
        """Bind the reader to one section instance's raw values."""
        self._context = context
        self._section = section
        self._values = values
        self._index = index
        self._failed = False

    @property
    def ok(self) -> bool:
        """Whether every value read so far parsed cleanly."""
        return not self._failed

    @property
    def recorded_on(self) -> datetime:
        """The submission's provenance timestamp."""
        return self._context.recorded_on

    def error(self, field_key: str, message: str) -> None:
        """Record a parse failure against ``field_key``."""
        self._failed = True
        self._context.errors.append(
            FormError(self._section, self._index, field_key, message)
        )

    def raw(self, field_key: str) -> str:
        """The stripped raw text for ``field_key`` (empty when absent)."""
        return self._values.get(field_key, "").strip()

    def any_entered(self) -> bool:
        """Whether the user typed anything at all into this section."""
        return any(value.strip() for value in self._values.values())

    def date_value(self, field_key: str, *, required: bool = False) -> date | None:
        """An ISO date, or ``None`` when blank or unparsable."""
        text = self.raw(field_key)
        if not text:
            if required:
                self.error(field_key, _REQUIRED_MESSAGE)
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            self.error(field_key, _DATE_MESSAGE)
            return None

    def as_of(self, field_key: str) -> date:
        """An ``as_of`` date, defaulting to the submission day when blank."""
        parsed = self.date_value(field_key)
        return self._context.default_as_of if parsed is None else parsed

    def money(self, field_key: str, *, required: bool = False) -> Money | None:
        """A money amount, tolerating a currency symbol and separators."""
        text = self.raw(field_key)
        if not text:
            if required:
                self.error(field_key, _REQUIRED_MESSAGE)
            return None
        cleaned = text.replace("£", "").replace(",", "").strip()
        try:
            amount = Decimal(cleaned)
        except InvalidOperation:
            self.error(field_key, _MONEY_MESSAGE)
            return None
        if not amount.is_finite():
            self.error(field_key, _MONEY_MESSAGE)
            return None
        return Money(amount)

    def int_value(self, field_key: str, *, required: bool = False) -> int | None:
        """A whole number."""
        text = self.raw(field_key)
        if not text:
            if required:
                self.error(field_key, _REQUIRED_MESSAGE)
            return None
        try:
            return int(text, 10)
        except ValueError:
            self.error(field_key, _INT_MESSAGE)
            return None

    def decimal_value(
        self, field_key: str, *, required: bool = False
    ) -> Decimal | None:
        """A plain (finite) decimal number."""
        text = self.raw(field_key)
        if not text:
            if required:
                self.error(field_key, _REQUIRED_MESSAGE)
            return None
        try:
            value = Decimal(text)
        except InvalidOperation:
            self.error(field_key, _DECIMAL_MESSAGE)
            return None
        if not value.is_finite():
            self.error(field_key, _DECIMAL_MESSAGE)
            return None
        return value

    def choice[T](self, field_key: str, options: Mapping[str, T]) -> T | None:
        """The domain value behind a choice field, ``None`` when blank."""
        text = self.raw(field_key)
        if not text:
            return None
        if text not in options:
            self.error(field_key, _CHOICE_MESSAGE)
            return None
        return options[text]

    def factor_table(self, field_key: str) -> FactorTable | None:
        """An age→factor table from ``age:factor`` comma-separated pairs."""
        text = self.raw(field_key)
        if not text:
            return None
        factors: dict[int, Decimal] = {}
        for chunk in text.split(","):
            age_text, sep, factor_text = chunk.partition(":")
            if not sep:
                self.error(field_key, _FACTORS_MESSAGE)
                return None
            try:
                age = int(age_text.strip(), 10)
                factor = Decimal(factor_text.strip())
            except ValueError, InvalidOperation:
                self.error(field_key, _FACTORS_MESSAGE)
                return None
            if not factor.is_finite():
                self.error(field_key, _FACTORS_MESSAGE)
                return None
            factors[age] = factor
        try:
            return FactorTable(factors=factors)
        except ValueError as exc:
            self.error(field_key, str(exc))
            return None

    def fact_of[T](self, value: T | None, as_of_field: str) -> Fact[T] | None:
        """Wrap a parsed value as a fact dated by ``as_of_field``."""
        if value is None:
            return None
        return Fact(
            value=value, as_of=self.as_of(as_of_field), recorded_on=self.recorded_on
        )

    def decision_of[T](self, value: T | None) -> Decision[T] | None:
        """Wrap a parsed value as a decision recorded at submission time."""
        if value is None:
            return None
        return Decision(value=value, recorded_on=self.recorded_on)


def _spending_from(reader: _SectionReader) -> SpendingPlan | None:
    """The spending plan, or ``None`` when the section is blank."""
    spending_fact = reader.fact_of(
        reader.money("annual_spending_real"), "annual_spending_real_as_of"
    )
    if spending_fact is None:
        return None
    try:
        return SpendingPlan(annual_spending_real=spending_fact)
    except ValueError as exc:
        reader.error("", str(exc))
        return None


def _state_pension_from(reader: _SectionReader) -> StatePensionRecord | None:
    """The state pension record, or ``None`` when wholly blank (§5.1)."""
    if not reader.any_entered():
        return None
    forecast = reader.fact_of(reader.money("forecast_weekly_amount"), "forecast_as_of")
    protected = reader.fact_of(reader.money("protected_payment"), "forecast_as_of")
    ni_start = reader.fact_of(reader.date_value("ni_record_start"), "ni_as_of")
    qualifying = reader.fact_of(reader.int_value("qualifying_years"), "ni_as_of")
    extra_years = reader.int_value("planned_extra_years")
    deferral = reader.decimal_value("deferral_years")
    if not reader.ok:
        return None
    try:
        return StatePensionRecord(
            forecast_weekly_amount=forecast,
            protected_payment=protected,
            ni_record_start=ni_start,
            qualifying_years=qualifying,
            planned_extra_years=Decision(
                value=0 if extra_years is None else extra_years,
                recorded_on=reader.recorded_on,
            ),
            deferral_years=Decision(
                value=Decimal(0) if deferral is None else deferral,
                recorded_on=reader.recorded_on,
            ),
        )
    except ValueError as exc:
        reader.error("", str(exc))
        return None


def _contributions_from(reader: _SectionReader) -> ContributionSchedule | None:
    """A wrapper's contribution schedule, or ``None`` when untouched."""
    employee = reader.money("employee_contribution")
    employer = reader.fact_of(
        reader.money("employer_contribution"), "contributions_as_of"
    )
    relief = reader.choice("relief_mechanic", _RELIEF_MECHANICS)
    escalation = reader.choice("escalation", _ESCALATIONS)
    if employee is None:
        if employer is not None or relief is not None or escalation is not None:
            reader.error("employee_contribution", _CONTRIBUTIONS_NEED_EMPLOYEE)
        return None
    try:
        return ContributionSchedule(
            employee_amount=Decision(value=employee, recorded_on=reader.recorded_on),
            employer_amount=employer,
            relief_mechanic=relief,
            escalation=escalation,
        )
    except ValueError as exc:
        reader.error("", str(exc))
        return None


def _wrapper_from(reader: _SectionReader, entity_id: EntityId) -> Wrapper | None:
    """One savings wrapper from its section values.

    A crystallised balance is a pension concept — funds already
    designated to drawdown — so any other kind rejects it here (the
    engine enforces the same invariant, planning §5.1): accepting one
    on an age-gated kind would let money bypass its access gate. A
    cash account holds cash: its allocation is fixed at 100% cash
    rather than following the glide path (roadmap 9.2).
    """
    kind = reader.choice("kind", _WRAPPER_KINDS)
    if kind is None:
        reader.error("kind", _REQUIRED_MESSAGE)
    balance = reader.fact_of(reader.money("balance", required=True), "balances_as_of")
    crystallised = reader.fact_of(
        reader.money("crystallised_balance"), "balances_as_of"
    )
    if kind is not None and kind not in _PENSION_KINDS and crystallised is not None:
        reader.error(
            "crystallised_balance",
            "Only pension wrappers hold a crystallised balance — leave blank.",
        )
    contributions = _contributions_from(reader)
    if kind is None or balance is None or not reader.ok:
        return None
    try:
        return Wrapper(
            id=entity_id,
            kind=kind,
            balance=balance,
            crystallised_balance=crystallised,
            contributions=contributions,
            allocation=_CASH_ALLOCATION if kind == CASH_KIND else None,
        )
    except ValueError as exc:
        reader.error("", str(exc))
        return None


def _db_pension_from(reader: _SectionReader, entity_id: EntityId) -> DBPension | None:
    """One deferred DB entitlement from its section values.

    The statement date doubles as the ``as_of`` for the scheme facts it
    dates (planning §5.1: accrued pension is "at date of leaving /
    statement").
    """
    accrued = reader.money("accrued_annual_pension", required=True)
    statement = reader.date_value("statement_date", required=True)
    npa = reader.int_value("normal_pension_age", required=True)
    reference = reader.choice("revaluation_reference", _REVALUATION_REFERENCES)
    cap = reader.decimal_value("revaluation_cap")
    fixed_rate = reader.decimal_value("revaluation_fixed_rate")
    factors = reader.factor_table("early_late_factors")
    commutation = reader.decimal_value("commutation_factor")
    taken_at = reader.int_value("taken_at_age")
    fraction = reader.decimal_value("commuted_fraction")
    if reference is None:
        reader.error("revaluation_reference", _REQUIRED_MESSAGE)
        return None
    if accrued is None or statement is None or npa is None or not reader.ok:
        return None
    recorded = reader.recorded_on
    try:
        return DBPension(
            id=entity_id,
            accrued_annual_pension=Fact(
                value=accrued, as_of=statement, recorded_on=recorded
            ),
            statement_date=statement,
            normal_pension_age=Fact(value=npa, as_of=statement, recorded_on=recorded),
            revaluation_basis=RevaluationBasis(
                reference=reference,
                cap=None if cap is None else Rate(cap),
                fixed_rate=None if fixed_rate is None else Rate(fixed_rate),
            ),
            early_late_factors=(
                FactorTable(factors={}) if factors is None else factors
            ),
            commuted_fraction=Decision(
                value=Decimal(0) if fraction is None else fraction,
                recorded_on=recorded,
            ),
            commutation_factor=(
                None
                if commutation is None
                else Fact(value=commutation, as_of=statement, recorded_on=recorded)
            ),
            taken_at_age=reader.decision_of(taken_at),
        )
    except ValueError as exc:
        reader.error("", str(exc))
        return None


def _person_from(
    reader: _SectionReader,
    wrappers: tuple[Wrapper, ...],
    db_pensions: tuple[DBPension, ...],
    state_pension: StatePensionRecord | None,
    entity_id: EntityId,
) -> Person | None:
    """The (v1 single) person from the person section plus sub-entities."""
    dob = reader.fact_of(
        reader.date_value("date_of_birth", required=True), "date_of_birth_as_of"
    )
    sex_value = reader.choice("sex_for_longevity", _SEXES)
    residency = reader.choice("tax_residency", _RESIDENCIES)
    if residency is None:
        reader.error("tax_residency", _REQUIRED_MESSAGE)
    target = reader.int_value("target_retirement_age", required=True)
    employment = reader.fact_of(
        reader.money("employment_income"), "employment_income_as_of"
    )
    mpaa = reader.fact_of(reader.date_value("mpaa_triggered_on"), "mpaa_as_of")
    lsa = reader.fact_of(reader.money("lsa_used"), "lsa_as_of")
    if dob is None or target is None or residency is None or not reader.ok:
        return None
    try:
        return Person(
            id=entity_id,
            date_of_birth=dob,
            target_retirement_age=Decision(
                value=target, recorded_on=reader.recorded_on
            ),
            tax_residency=residency,
            sex_for_longevity=(
                None
                if sex_value is None
                else Fact(
                    value=sex_value,
                    as_of=reader.as_of("sex_as_of"),
                    recorded_on=reader.recorded_on,
                )
            ),
            employment_income=employment,
            mpaa_triggered_on=mpaa,
            lsa_used=lsa,
            wrappers=wrappers,
            db_pensions=db_pensions,
            state_pension=state_pension,
        )
    except ValueError as exc:
        reader.error("", str(exc))
        return None


def _kept_id(ids: tuple[EntityId, ...], index: int) -> EntityId:
    """The prior entity id at ``index``, or a fresh one past the end."""
    if index < len(ids):
        return ids[index]
    return new_entity_id()


def parse_facts_form(
    data: FactsFormData,
    *,
    recorded_on: datetime,
    today: date,
    previous: Household | None = None,
) -> FactsFormResult:
    """Parse a submission into a v1 household, or the errors preventing one.

    Every fact is stamped with the submission's ``recorded_on`` and the
    ``as_of`` date the user entered (defaulting to ``today``), so
    provenance is complete at entry time (planning §1). ``today`` is
    the caller's civil date — the same one the projection will run
    with, never derived from the UTC ``recorded_on`` timestamp: around
    midnight the two calendars disagree, and a blank ``as_of``
    defaulted to the UTC date could sit a day after the run's
    ``today``, which §4.8 rejects as future-dated.

    ``previous`` is the household a re-submission replaces: the person
    and each wrapper and DB pension reuse the prior id at their form
    position, so scenario overrides targeting them by stable id (§4.3)
    survive a facts edit instead of orphaning. Sections beyond the
    prior plan's count mint fresh ids.
    """
    context = _FormContext(
        recorded_on=recorded_on,
        default_as_of=today,
        errors=[],
    )
    prior = previous.persons[0] if previous is not None and previous.persons else None
    prior_wrapper_ids = tuple(
        wrapper.id for wrapper in (prior.wrappers if prior is not None else ())
    )
    prior_pension_ids = tuple(
        pension.id for pension in (prior.db_pensions if prior is not None else ())
    )
    spending = _spending_from(_SectionReader(context, "spending", data.spending))
    state_pension = _state_pension_from(
        _SectionReader(context, "state_pension", data.state_pension)
    )
    wrappers = tuple(
        wrapper
        for index, values in enumerate(data.wrappers)
        if (
            wrapper := _wrapper_from(
                _SectionReader(context, "wrapper", values, index=index),
                _kept_id(prior_wrapper_ids, index),
            )
        )
        is not None
    )
    db_pensions = tuple(
        pension
        for index, values in enumerate(data.db_pensions)
        if (
            pension := _db_pension_from(
                _SectionReader(context, "db_pension", values, index=index),
                _kept_id(prior_pension_ids, index),
            )
        )
        is not None
    )
    person = _person_from(
        _SectionReader(context, "person", data.person),
        wrappers,
        db_pensions,
        state_pension,
        prior.id if prior is not None else new_entity_id(),
    )
    if context.errors or person is None:
        return FactsFormResult(household=None, errors=tuple(context.errors))
    try:
        household = Household(persons=(person,), spending=spending)
        validate_household_v1(household)
    except ValueError as exc:
        return FactsFormResult(
            household=None, errors=(FormError("person", None, "", str(exc)),)
        )
    return FactsFormResult(household=household, errors=())


def format_form_errors(form: FactsFormViewModel, errors: Sequence[FormError]) -> str:
    """Render form errors as one line per problem, labelled for humans."""
    labels = {
        (section.key, spec.key): spec.label
        for section in form.sections
        for spec in section.fields
    }
    titles = {section.key: section.title for section in form.sections}
    lines = []
    for error in errors:
        where = titles.get(error.section, error.section)
        if error.index is not None:
            where = f"{where} {error.index + 1}"
        label = labels.get((error.section, error.field_key))
        if label is None:
            lines.append(f"{where}: {error.message}")
        else:
            lines.append(f"{where} — {label}: {error.message}")
    return "\n".join(lines)


def _as_of_field(key: str, label: str) -> FieldSpec:
    """A standard ``as_of`` companion field."""
    return FieldSpec(key=key, label=label, hint=_AS_OF_HINT)


def _person_section() -> SectionSpec:
    """The person section: identity, income, and pre-existing access facts."""
    return SectionSpec(
        key="person",
        title="About you",
        description=(
            "Facts you state about yourself, plus your target retirement "
            "age — a choice, and the anchor the whole projection swings on."
        ),
        fields=(
            FieldSpec(
                key="date_of_birth",
                label="Date of birth",
                hint="YYYY-MM-DD, e.g. 1980-04-12",
                required=True,
            ),
            _as_of_field("date_of_birth_as_of", "Date of birth stated as of"),
            FieldSpec(
                key="sex_for_longevity",
                label="Sex (longevity default only)",
                kind=FieldKind.CHOICE,
                choices=(
                    ChoiceOption(value="", label="Not stated"),
                    ChoiceOption(value="female", label="Female"),
                    ChoiceOption(value="male", label="Male"),
                ),
            ),
            _as_of_field("sex_as_of", "Sex stated as of"),
            FieldSpec(
                key="tax_residency",
                label="Tax residency",
                kind=FieldKind.CHOICE,
                required=True,
                choices=(
                    _SELECT_OPTION,
                    ChoiceOption(
                        value=str(RUK_RESIDENCY),
                        label="England, Wales or Northern Ireland",
                    ),
                    ChoiceOption(
                        value=str(SCOTLAND_RESIDENCY),
                        label="Scotland",
                    ),
                ),
            ),
            FieldSpec(
                key="employment_income",
                label="Employment income (gross, per year)",
                hint="e.g. 52000",
            ),
            _as_of_field("employment_income_as_of", "Income as of"),
            FieldSpec(
                key="target_retirement_age",
                label="Target retirement age (your choice)",
                hint="e.g. 62",
                required=True,
            ),
            FieldSpec(
                key="mpaa_triggered_on",
                label="MPAA triggered on",
                hint="YYYY-MM-DD; blank if you have never flexibly accessed a pension",
            ),
            _as_of_field("mpaa_as_of", "MPAA fact as of"),
            FieldSpec(
                key="lsa_used",
                label="Lump sum allowance already used",
                hint="blank if none",
            ),
            _as_of_field("lsa_as_of", "LSA fact as of"),
        ),
    )


def _spending_section() -> SectionSpec:
    """The household spending section."""
    return SectionSpec(
        key="spending",
        title="Household spending",
        description=(
            "Your annual spending need in today's money, after tax. Blank "
            "means spending is not modelled yet."
        ),
        fields=(
            FieldSpec(
                key="annual_spending_real",
                label="Annual spending (today's money, net)",
                hint="e.g. 28000",
            ),
            _as_of_field("annual_spending_real_as_of", "Spending as of"),
        ),
    )


def _state_pension_section() -> SectionSpec:
    """The state pension / NI record section."""
    return SectionSpec(
        key="state_pension",
        title="State pension",
        description=(
            "An official DWP forecast wins when you have one. Without a "
            "forecast, the NI record derivation applies only to records "
            "starting after 5 April 2016 — earlier records need the "
            "forecast. Leave the whole section blank to skip the state "
            "pension."
        ),
        fields=(
            FieldSpec(
                key="forecast_weekly_amount",
                label="Forecast weekly amount",
                hint="from your DWP forecast, e.g. 230.25",
            ),
            FieldSpec(
                key="protected_payment",
                label="Protected payment (part of the forecast)",
                hint="blank if none; uprates by CPI only",
            ),
            _as_of_field("forecast_as_of", "Forecast as of"),
            FieldSpec(
                key="ni_record_start",
                label="NI record started",
                hint="YYYY-MM-DD",
            ),
            FieldSpec(
                key="qualifying_years",
                label="Qualifying years so far",
                hint="from your NI record, e.g. 18",
            ),
            _as_of_field("ni_as_of", "NI record as of"),
            FieldSpec(
                key="planned_extra_years",
                label="Further years you plan to accrue (your choice)",
                hint="blank means none",
            ),
            FieldSpec(
                key="deferral_years",
                label="Years you plan to defer claiming (your choice)",
                hint="whole months, e.g. 1.25; blank means none",
            ),
        ),
    )


def _wrapper_section() -> SectionSpec:
    """The repeatable savings-wrapper section."""
    return SectionSpec(
        key="wrapper",
        title="Savings wrapper",
        description=(
            "One pension, ISA, or taxable account. Balances are facts "
            "from a statement; contributions are your choices plus your "
            "employer's terms. A balance dated a whole month or more "
            "before today is rolled forward to today at the assumed "
            "return — contributions in the gap are not added, so "
            "restate the balance if your statement is old."
        ),
        repeatable=True,
        add_label="Add wrapper",
        remove_label="Remove this wrapper",
        fields=(
            FieldSpec(
                key="kind",
                label="Kind",
                kind=FieldKind.CHOICE,
                required=True,
                choices=(
                    _SELECT_OPTION,
                    ChoiceOption(
                        value=str(WORKPLACE_DC_KIND), label="Workplace DC pension"
                    ),
                    ChoiceOption(value=str(SIPP_KIND), label="SIPP"),
                    ChoiceOption(value=str(ISA_KIND), label="Stocks & shares ISA"),
                    ChoiceOption(value=str(LISA_KIND), label="Lifetime ISA"),
                    ChoiceOption(
                        value=str(GIA_KIND), label="General investment account"
                    ),
                    ChoiceOption(value=str(CASH_KIND), label="Cash savings"),
                ),
            ),
            FieldSpec(
                key="balance",
                label="Balance (pensions: uncrystallised)",
                hint="e.g. 45000",
                required=True,
            ),
            FieldSpec(
                key="crystallised_balance",
                label="Crystallised balance (already in drawdown)",
                hint="pensions only; blank if none",
            ),
            _as_of_field("balances_as_of", "Balances as of"),
            FieldSpec(
                key="employee_contribution",
                label="Your contribution (gross, per year — your choice)",
                hint="e.g. 6000; blank if none",
            ),
            FieldSpec(
                key="employer_contribution",
                label="Employer contribution (per year)",
                hint="from your employment terms; blank if none",
            ),
            _as_of_field("contributions_as_of", "Contributions as of"),
            FieldSpec(
                key="relief_mechanic",
                label="Tax relief mechanic",
                kind=FieldKind.CHOICE,
                choices=(
                    ChoiceOption(
                        value="",
                        label="None (ISAs, LISAs, GIA, cash; pensions must pick one)",
                    ),
                    ChoiceOption(value="relief_at_source", label="Relief at source"),
                    ChoiceOption(value="net_pay", label="Net pay"),
                ),
            ),
            FieldSpec(
                key="escalation",
                label="Contribution escalation",
                kind=FieldKind.CHOICE,
                choices=(
                    ChoiceOption(value="", label="Fixed amount"),
                    ChoiceOption(value="earnings", label="Grows with earnings"),
                ),
            ),
        ),
    )


def _db_pension_section() -> SectionSpec:
    """The repeatable deferred-DB-pension section."""
    return SectionSpec(
        key="db_pension",
        title="Defined benefit pension (deferred)",
        description=(
            "Scheme parameters are facts from your deferred benefit "
            "statement — schemes vary too much to guess. The statement "
            "date dates every scheme fact."
        ),
        repeatable=True,
        add_label="Add DB pension",
        remove_label="Remove this DB pension",
        fields=(
            FieldSpec(
                key="accrued_annual_pension",
                label="Accrued annual pension",
                hint="per year at the statement date, e.g. 8500",
                required=True,
            ),
            FieldSpec(
                key="statement_date",
                label="Statement date (dates the scheme facts)",
                hint="YYYY-MM-DD",
                required=True,
            ),
            FieldSpec(
                key="normal_pension_age",
                label="Normal pension age",
                hint="e.g. 65",
                required=True,
            ),
            FieldSpec(
                key="revaluation_reference",
                label="Revaluation basis",
                kind=FieldKind.CHOICE,
                required=True,
                choices=(
                    _SELECT_OPTION,
                    ChoiceOption(value="cpi", label="CPI (optionally capped)"),
                    ChoiceOption(value="fixed", label="Fixed annual rate"),
                    ChoiceOption(value="none", label="No revaluation"),
                ),
            ),
            FieldSpec(
                key="revaluation_cap",
                label="CPI cap (annual, as a fraction)",
                hint="e.g. 0.05 for CPI capped at 5%; blank if uncapped",
            ),
            FieldSpec(
                key="revaluation_fixed_rate",
                label="Fixed rate (annual, as a fraction)",
                hint="fixed basis only, e.g. 0.03",
            ),
            FieldSpec(
                key="early_late_factors",
                label="Early/late retirement factors",
                hint=_FACTORS_MESSAGE.removeprefix("enter "),
            ),
            FieldSpec(
                key="commutation_factor",
                label="Commutation factor (£ lump sum per £1 pension)",
                hint="e.g. 12; blank if not commuting",
            ),
            FieldSpec(
                key="taken_at_age",
                label="Take benefits at age (your choice)",
                hint="blank means the normal pension age",
            ),
            FieldSpec(
                key="commuted_fraction",
                label="Fraction commuted to lump sum (your choice)",
                hint="0 to 1, e.g. 0.25; blank means none",
            ),
        ),
    )


def build_facts_form_view_model() -> FactsFormViewModel:
    """Assemble the facts entry screen (roadmap 8.2).

    The acceptance criterion is that every §5.1 fact is enterable with
    its ``as_of`` date; the guard is ``tests/test_app_forms.py``'s
    coverage sweep over the §5.1 fact list.
    """
    return FactsFormViewModel(
        title="Your plan's facts",
        intro=(
            "Everything here is either a fact you state or a choice you "
            "make — never a guess. Dates default to today when blank; "
            "estimates and defaults live in the assumptions inspector."
        ),
        person=_person_section(),
        spending=_spending_section(),
        state_pension=_state_pension_section(),
        wrapper=_wrapper_section(),
        db_pension=_db_pension_section(),
        submit_label="Save facts and project",
    )
