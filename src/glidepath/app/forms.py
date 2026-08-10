"""Facts entry forms (roadmap 8.2; planning §1, §4.7, §5.1).

The form a shell renders to capture every §5.1 fact — DOB, balances
with ``as_of`` dates, contributions, DB scheme parameters, the state
pension forecast, and the pre-existing access facts — plus the
decisions a projectable plan needs (retirement age,
contribution and commutation choices, planned annuity purchases). The
shell binds
:class:`FactsFormViewModel` to widgets and returns raw text via
:class:`FactsFormData`; parsing back into `Fact`/`Decision`-wrapped
domain objects happens here, so validation and messages stay
UI-agnostic.
"""

from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum, auto
from itertools import chain
from typing import TYPE_CHECKING, Any, Final

from glidepath.core import (
    AnnuityBasis,
    AnnuityPurchase,
    AnnuityType,
    AssetAllocation,
    AssumptionKey,
    ContributionSchedule,
    DBActiveMembership,
    DBPension,
    Decision,
    EntityId,
    Fact,
    FactorTable,
    Household,
    LifeStage,
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
    DATE = auto()
    """A date entry. The raw value stays ISO text (``YYYY-MM-DD``) and
    blank keeps its meaning (today for ``as_of`` fields, none for
    optional facts), so shells must keep typing and blankness first-
    class — a calendar is an assist, never the only input."""


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
    annuity_purchase: SectionSpec
    submit_label: str
    clear_label: str
    """The button that empties the whole form (planning §4.9)."""
    example_note: str
    """Status shown while the launch example is on screen (§4.9)."""
    cleared_note: str
    """Status shown after the form is cleared (§4.9)."""

    @property
    def sections(self) -> tuple[SectionSpec, ...]:
        """Every section, singular and repeatable alike."""
        return (
            self.person,
            self.spending,
            self.state_pension,
            self.wrapper,
            self.db_pension,
            self.annuity_purchase,
        )


@dataclass(frozen=True)
class FactsFormData:
    """Raw text captured by a shell, keyed exactly like the specs.

    Each repeatable-section row may additionally carry its entity id
    under :data:`ENTITY_ID_KEY`, opaque to the rendered fields.
    """

    person: Mapping[str, str] = field(default_factory=dict)
    spending: Mapping[str, str] = field(default_factory=dict)
    state_pension: Mapping[str, str] = field(default_factory=dict)
    wrappers: tuple[Mapping[str, str], ...] = ()
    db_pensions: tuple[Mapping[str, str], ...] = ()
    annuity_purchases: tuple[Mapping[str, str], ...] = ()


ENTITY_ID_KEY: Final = "entity_id"
"""Reserved key of a repeatable-section row's entity id (§4.3).

Never a rendered field: shells carry it opaquely per section instance
so wrappers, DB pensions, and annuity purchases keep their stable ids
— and with them any scenario overrides targeting them — through
edits, reordering, and row deletion alike. Empty or absent means a
new entity; any other value is the id, verbatim — ids only ever
originate from a parsed household."""


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
_MEMBERSHIP_NEEDS_RATE = (
    "enter the accrual rate to record active membership; blank means deferred"
)
_MEMBERSHIP_NEEDS_SALARY = "active membership needs the pensionable salary"
_FORECAST_REQUIRED = (
    "an official DWP forecast is required — free and instant from "
    "gov.uk/check-state-pension; leave the whole section blank to skip "
    "the state pension"
)
_MULTIPLIERS_NEED_SPENDING = (
    "enter the annual spending need for the stage multipliers to scale"
)
_DUPLICATE_LABEL_MESSAGE = "another wrapper already carries this name"

_AS_OF_HINT = "YYYY-MM-DD; blank means today"

_STAGE_MULTIPLIER_FIELDS: Final[tuple[tuple[str, LifeStage], ...]] = (
    ("go_go_multiplier", LifeStage.GO_GO),
    ("slow_go_multiplier", LifeStage.SLOW_GO),
    ("no_go_multiplier", LifeStage.NO_GO),
)
"""The retirement sub-stage multiplier fields the spending section offers.

The whole-retirement ``DECUMULATION`` key stays form-less — scaling all
of retirement is what the annual spending amount itself expresses — so
a plan carrying one still refuses the facts form (§4.5).
"""

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
_HUNDRED = Decimal(100)
_CASH_EQUITY_MESSAGE = "Cash accounts always hold cash — leave blank."
_EQUITY_PERCENT_MESSAGE = "Enter a percentage from 0 to 100."
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
_ANNUITY_TYPES: Mapping[str, AnnuityType] = {
    "level": AnnuityType.LEVEL,
    "escalating": AnnuityType.ESCALATING,
    "inflation_linked": AnnuityType.INFLATION_LINKED,
}

_SEX_KEYS: Mapping[Sex, str] = {value: key for key, value in _SEXES.items()}
_RELIEF_KEYS: Mapping[ReliefMechanic, str] = {
    value: key for key, value in _RELIEF_MECHANICS.items()
}
_ESCALATION_KEYS: Mapping[AssumptionKey, str] = {
    value: key for key, value in _ESCALATIONS.items()
}
_REVALUATION_KEYS: Mapping[RevaluationReference, str] = {
    value: key for key, value in _REVALUATION_REFERENCES.items()
}
_ANNUITY_TYPE_KEYS: Mapping[AnnuityType, str] = {
    value: key for key, value in _ANNUITY_TYPES.items()
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

    def fact_of[T](
        self, value: T | None, as_of_field: str | None = None
    ) -> Fact[T] | None:
        """Wrap a parsed value as a fact dated by ``as_of_field``.

        Only statement-dated facts (balances, the DWP forecast) offer
        an ``as_of`` field; every other fact passes no field and is
        dated the day it was entered.
        """
        if value is None:
            return None
        as_of = (
            self._context.default_as_of
            if as_of_field is None
            else self.as_of(as_of_field)
        )
        return Fact(value=value, as_of=as_of, recorded_on=self.recorded_on)

    def decision_of[T](self, value: T | None) -> Decision[T] | None:
        """Wrap a parsed value as a decision recorded at submission time."""
        if value is None:
            return None
        return Decision(value=value, recorded_on=self.recorded_on)


def _spending_from(reader: _SectionReader) -> SpendingPlan | None:
    """The spending plan, or ``None`` when the section is blank."""
    spending_fact = reader.fact_of(reader.money("annual_spending_real"))
    multipliers = {
        stage: value
        for field_key, stage in _STAGE_MULTIPLIER_FIELDS
        if (value := reader.decimal_value(field_key)) is not None
    }
    if spending_fact is None:
        # An unparsable amount already carries its own field error; only
        # a genuinely blank one needs the "enter the need" prompt.
        if multipliers and not reader.raw("annual_spending_real"):
            reader.error("annual_spending_real", _MULTIPLIERS_NEED_SPENDING)
        return None
    try:
        return SpendingPlan(
            annual_spending_real=spending_fact,
            stage_multipliers=multipliers or None,
        )
    except ValueError as exc:
        reader.error("", str(exc))
        return None


def _state_pension_from(reader: _SectionReader) -> StatePensionRecord | None:
    """The state pension record, or ``None`` when wholly blank (§5.1)."""
    if not reader.any_entered():
        return None
    forecast = reader.fact_of(reader.money("forecast_weekly_amount"), "forecast_as_of")
    protected = reader.fact_of(reader.money("protected_payment"), "forecast_as_of")
    deferral = reader.decimal_value("deferral_years")
    if not reader.ok:
        return None
    if forecast is None:
        reader.error("forecast_weekly_amount", _FORECAST_REQUIRED)
        return None
    try:
        return StatePensionRecord(
            forecast_weekly_amount=forecast,
            protected_payment=protected,
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
    employer = reader.fact_of(reader.money("employer_contribution"))
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
    label = reader.raw("label") or None
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
    allocation = _allocation_from(reader, kind)
    if kind is None or balance is None or not reader.ok:
        return None
    try:
        return Wrapper(
            id=entity_id,
            kind=kind,
            balance=balance,
            label=label,
            crystallised_balance=crystallised,
            contributions=contributions,
            allocation=allocation,
        )
    except ValueError as exc:
        reader.error("", str(exc))
        return None


def _allocation_from(
    reader: _SectionReader, kind: WrapperKindId | None
) -> AssetAllocation | None:
    """A wrapper's stated allocation, or ``None`` to follow the glide path.

    The form takes one number — the equity share as a percentage, the
    remainder in bonds — because that is the decision the glide path
    itself models; a cash account is always 100% cash and rejects an
    entry rather than silently ignoring it (roadmap 9.2).
    """
    equity_percent = reader.decimal_value("equity_percent")
    if kind == CASH_KIND:
        if equity_percent is not None:
            reader.error("equity_percent", _CASH_EQUITY_MESSAGE)
        return _CASH_ALLOCATION
    if equity_percent is None:
        return None
    if not Decimal(0) <= equity_percent <= _HUNDRED:
        reader.error("equity_percent", _EQUITY_PERCENT_MESSAGE)
        return None
    equity = equity_percent / _HUNDRED
    return AssetAllocation(equity=equity, bonds=Decimal(1) - equity)


def _active_membership_from(
    reader: _SectionReader, statement: date | None
) -> DBActiveMembership | None:
    """A DB pension's active membership, or ``None`` when untouched.

    The accrual rate anchors the block (like the employee contribution
    anchors a contribution schedule): blank means deferred, and filling
    a sibling without it is an error rather than a silent guess.
    """
    rate = reader.decimal_value("accrual_rate")
    salary = reader.money("pensionable_salary")
    until = reader.int_value("active_until_age")
    if rate is None:
        if salary is not None or until is not None:
            reader.error("accrual_rate", _MEMBERSHIP_NEEDS_RATE)
        return None
    if salary is None:
        reader.error("pensionable_salary", _MEMBERSHIP_NEEDS_SALARY)
        return None
    if statement is None:
        return None
    recorded = reader.recorded_on
    try:
        return DBActiveMembership(
            accrual_rate=Fact(value=rate, as_of=statement, recorded_on=recorded),
            pensionable_salary=Fact(
                value=salary, as_of=statement, recorded_on=recorded
            ),
            active_until_age=reader.decision_of(until),
        )
    except ValueError as exc:
        reader.error("", str(exc))
        return None


def _db_pension_from(reader: _SectionReader, entity_id: EntityId) -> DBPension | None:
    """One DB entitlement from its section values.

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
    membership = _active_membership_from(reader, statement)
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
            active_membership=membership,
        )
    except ValueError as exc:
        reader.error("", str(exc))
        return None


def _annuity_purchase_from(
    reader: _SectionReader, entity_id: EntityId
) -> AnnuityPurchase | None:
    """One planned annuity purchase from its section values.

    The record is wholly a decision (planning §5.1): the age, pot
    fraction, and product type are all choices, priced from the
    annuity-rate assumptions at run time. The v1 form is single-person
    (§4.4), so every purchase it writes is single-life — joint-life
    annuities wait for the couples spike (roadmap 9.4).
    """
    at_age = reader.int_value("at_age", required=True)
    fraction = reader.decimal_value("fraction_of_pot", required=True)
    annuity_type = reader.choice("annuity_type", _ANNUITY_TYPES)
    if annuity_type is None and not reader.raw("annuity_type"):
        reader.error("annuity_type", _REQUIRED_MESSAGE)
    if at_age is None or fraction is None or annuity_type is None or not reader.ok:
        return None
    recorded = reader.recorded_on
    try:
        return AnnuityPurchase(
            id=entity_id,
            at_age=Decision(value=at_age, recorded_on=recorded),
            fraction_of_pot=Decision(value=fraction, recorded_on=recorded),
            annuity_type=annuity_type,
            basis=AnnuityBasis.SINGLE,
        )
    except ValueError as exc:
        reader.error("", str(exc))
        return None


@dataclass(frozen=True)
class _PersonParts:
    """The parsed sub-entities the person section assembles around."""

    wrappers: tuple[Wrapper, ...]
    db_pensions: tuple[DBPension, ...]
    annuity_purchases: tuple[AnnuityPurchase, ...]
    state_pension: StatePensionRecord | None


def _person_from(
    reader: _SectionReader, parts: _PersonParts, entity_id: EntityId
) -> Person | None:
    """The (v1 single) person from the person section plus sub-entities."""
    dob = reader.fact_of(reader.date_value("date_of_birth", required=True))
    sex_value = reader.choice("sex_for_longevity", _SEXES)
    residency = reader.choice("tax_residency", _RESIDENCIES)
    if residency is None:
        reader.error("tax_residency", _REQUIRED_MESSAGE)
    target = reader.int_value("target_retirement_age", required=True)
    employment = reader.fact_of(reader.money("employment_income"))
    mpaa = reader.fact_of(reader.date_value("mpaa_triggered_on"))
    lsa = reader.fact_of(reader.money("lsa_used"))
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
            sex_for_longevity=reader.fact_of(sex_value),
            employment_income=employment,
            mpaa_triggered_on=mpaa,
            lsa_used=lsa,
            wrappers=parts.wrappers,
            db_pensions=parts.db_pensions,
            annuity_purchases=parts.annuity_purchases,
            state_pension=parts.state_pension,
        )
    except ValueError as exc:
        reader.error("", str(exc))
        return None


def _reject_duplicate_wrapper_labels(
    context: _FormContext, rows: Sequence[Mapping[str, str]]
) -> None:
    """Refuse two wrappers sharing one name (roadmap 9.28).

    A name exists to tell wrappers apart, so a repeat would defeat it —
    and the display layers would have to number the copies right back.
    Checked on the raw rows so the error lands even when another field
    on the row failed to parse; each repeated row errors, first seen
    keeps the name.
    """
    seen: set[str] = set()
    for index, values in enumerate(rows):
        label = values.get("label", "").strip()
        if not label:
            continue
        if label in seen:
            context.errors.append(
                FormError("wrapper", index, "label", _DUPLICATE_LABEL_MESSAGE)
            )
        seen.add(label)


def _row_entity_id(values: Mapping[str, str]) -> EntityId:
    """The row's carried entity id, or a fresh one when empty (§4.3).

    Preserved verbatim: persistence accepts any non-empty id, and
    scenario overrides target the exact stored string — normalising
    here (even stripping whitespace) would change the entity's
    identity and orphan its overrides on resave.
    """
    text = values.get(ENTITY_ID_KEY, "")
    return EntityId(text) if text else new_entity_id()


def parse_facts_form(
    data: FactsFormData,
    *,
    recorded_on: datetime,
    today: date,
    previous: Household | None = None,
) -> FactsFormResult:
    """Parse a submission into a v1 household, or the errors preventing one.

    Every fact is stamped with the submission's ``recorded_on`` and an
    ``as_of`` date — the one the user entered for statement-dated
    facts, ``today`` otherwise (and when left blank) — so
    provenance is complete at entry time (planning §1). ``today`` is
    the caller's civil date — the same one the projection will run
    with, never derived from the UTC ``recorded_on`` timestamp: around
    midnight the two calendars disagree, and a blank ``as_of``
    defaulted to the UTC date could sit a day after the run's
    ``today``, which §4.8 rejects as future-dated.

    Each wrapper, DB pension, and annuity purchase row carries its own
    entity id under :data:`ENTITY_ID_KEY` (empty means a new entity),
    so scenario overrides targeting them by stable id (§4.3) survive a
    facts edit — row deletion and reordering included — instead of
    orphaning or silently retargeting. ``previous`` is the household a
    re-submission replaces: the (single) person reuses its id, and
    unchanged undated facts keep their stored ``as_of`` dates.
    """
    context = _FormContext(
        recorded_on=recorded_on,
        default_as_of=today,
        errors=[],
    )
    prior = previous.persons[0] if previous is not None and previous.persons else None
    spending = _spending_from(_SectionReader(context, "spending", data.spending))
    state_pension = _state_pension_from(
        _SectionReader(context, "state_pension", data.state_pension)
    )
    _reject_duplicate_wrapper_labels(context, data.wrappers)
    wrappers = tuple(
        wrapper
        for index, values in enumerate(data.wrappers)
        if (
            wrapper := _wrapper_from(
                _SectionReader(context, "wrapper", values, index=index),
                _row_entity_id(values),
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
                _row_entity_id(values),
            )
        )
        is not None
    )
    annuity_purchases = tuple(
        purchase
        for index, values in enumerate(data.annuity_purchases)
        if (
            purchase := _annuity_purchase_from(
                _SectionReader(context, "annuity_purchase", values, index=index),
                _row_entity_id(values),
            )
        )
        is not None
    )
    person = _person_from(
        _SectionReader(context, "person", data.person),
        _PersonParts(
            wrappers=wrappers,
            db_pensions=db_pensions,
            annuity_purchases=annuity_purchases,
            state_pension=state_pension,
        ),
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
    return FactsFormResult(
        household=_with_carried_dates(household, previous), errors=()
    )


def _fact_dated_from[T](fact: Fact[T] | None, prior: Fact[T] | None) -> Fact[T] | None:
    """The fact re-dated from its prior statement when its value is unchanged."""
    if fact is None or prior is None or fact.value != prior.value:
        return fact
    if fact.as_of == prior.as_of:
        return fact
    return Fact(
        value=fact.value,
        as_of=prior.as_of,
        recorded_on=fact.recorded_on,
        note=fact.note,
    )


def _person_with_carried_dates(person: Person, prior: Person) -> Person:
    """The person with unchanged undated facts re-dated from ``prior``."""
    changes: dict[str, Any] = {}
    fact_pairs: dict[str, tuple[Fact[Any] | None, Fact[Any] | None]] = {
        "date_of_birth": (person.date_of_birth, prior.date_of_birth),
        "sex_for_longevity": (person.sex_for_longevity, prior.sex_for_longevity),
        "employment_income": (person.employment_income, prior.employment_income),
        "mpaa_triggered_on": (person.mpaa_triggered_on, prior.mpaa_triggered_on),
        "lsa_used": (person.lsa_used, prior.lsa_used),
    }
    for name, (fact, prior_fact) in fact_pairs.items():
        carried = _fact_dated_from(fact, prior_fact)
        if carried is not fact:
            changes[name] = carried
    return replace(person, **changes) if changes else person


def _wrapper_with_carried_dates(wrapper: Wrapper, prior: Wrapper | None) -> Wrapper:
    """The wrapper with an unchanged employer fact re-dated from ``prior``."""
    contributions = wrapper.contributions
    prior_contributions = prior.contributions if prior is not None else None
    changes: dict[str, Any] = {}
    if contributions is not None and prior_contributions is not None:
        employer = _fact_dated_from(
            contributions.employer_amount, prior_contributions.employer_amount
        )
        if employer is not contributions.employer_amount:
            changes["contributions"] = replace(contributions, employer_amount=employer)
    return replace(wrapper, **changes) if changes else wrapper


def _with_carried_dates(household: Household, previous: Household | None) -> Household:
    """Unchanged undated facts keep the replaced plan's ``as_of`` (§4.5).

    The form offers no ``as_of`` input for facts that are not
    statement-dated, so a bare reparse dates them the submission day.
    When the submission replaces a loaded plan (``previous``) and the
    value is unchanged, that would silently rewrite persisted
    provenance — the prior date carries forward instead. An edited
    value is a fresh statement and keeps the submission day. Wrappers
    pair up by form position, exactly like the §4.3 id reuse.
    """
    if previous is None or not previous.persons or not household.persons:
        return household
    prior = previous.persons[0]
    prior_by_id = {wrapper.id: wrapper for wrapper in prior.wrappers}
    person = _person_with_carried_dates(household.persons[0], prior)
    wrappers = tuple(
        _wrapper_with_carried_dates(wrapper, prior_by_id.get(wrapper.id))
        for wrapper in person.wrappers
    )
    if wrappers != person.wrappers:
        person = replace(person, wrappers=wrappers)
    changes: dict[str, Any] = {}
    if person is not household.persons[0]:
        changes["persons"] = (person,)
    spending = household.spending
    if spending is not None and previous.spending is not None:
        carried = _fact_dated_from(
            spending.annual_spending_real, previous.spending.annual_spending_real
        )
        if carried is not None and carried is not spending.annual_spending_real:
            changes["spending"] = replace(spending, annual_spending_real=carried)
    return replace(household, **changes) if changes else household


def _person_values(person: Person) -> dict[str, str]:
    """The person section's raw text for ``person``."""
    sex = person.sex_for_longevity
    employment = person.employment_income
    mpaa = person.mpaa_triggered_on
    lsa = person.lsa_used
    return {
        "date_of_birth": person.date_of_birth.value.isoformat(),
        "sex_for_longevity": "" if sex is None else _SEX_KEYS[sex.value],
        "tax_residency": str(person.tax_residency),
        "employment_income": (
            "" if employment is None else str(employment.value.amount)
        ),
        "target_retirement_age": str(person.target_retirement_age.value),
        "mpaa_triggered_on": "" if mpaa is None else mpaa.value.isoformat(),
        "lsa_used": "" if lsa is None else str(lsa.value.amount),
    }


def _spending_values(spending: SpendingPlan | None) -> dict[str, str]:
    """The spending section's raw text, empty when spending is unmodelled."""
    if spending is None:
        return {}
    fact = spending.annual_spending_real
    multipliers = spending.stage_multipliers or {}
    values = {"annual_spending_real": str(fact.value.amount)}
    for field_key, stage in _STAGE_MULTIPLIER_FIELDS:
        multiplier = multipliers.get(stage)
        values[field_key] = "" if multiplier is None else str(multiplier)
    return values


def _state_pension_values(record: StatePensionRecord | None) -> dict[str, str]:
    """The state pension section's raw text, empty when skipped."""
    if record is None:
        return {}
    forecast = record.forecast_weekly_amount
    protected = record.protected_payment
    # The form dates the forecast pair from one shared as_of field, so
    # either member of the pair can carry it back.
    forecast_dated = forecast if forecast is not None else protected
    return {
        "forecast_weekly_amount": (
            "" if forecast is None else str(forecast.value.amount)
        ),
        "protected_payment": "" if protected is None else str(protected.value.amount),
        "forecast_as_of": (
            "" if forecast_dated is None else forecast_dated.as_of.isoformat()
        ),
        "deferral_years": str(record.deferral_years.value),
    }


def _wrapper_values(wrapper: Wrapper) -> dict[str, str]:
    """One wrapper section instance's raw text."""
    crystallised = wrapper.crystallised_balance
    values = {
        ENTITY_ID_KEY: str(wrapper.id),
        "label": wrapper.label or "",
        "kind": str(wrapper.kind),
        "balance": str(wrapper.balance.value.amount),
        "crystallised_balance": (
            "" if crystallised is None else str(crystallised.value.amount)
        ),
        "balances_as_of": wrapper.balance.as_of.isoformat(),
        "equity_percent": _equity_percent_text(wrapper),
        "employee_contribution": "",
        "employer_contribution": "",
        "relief_mechanic": "",
        "escalation": "",
    }
    contributions = wrapper.contributions
    if contributions is not None:
        employer = contributions.employer_amount
        relief = contributions.relief_mechanic
        escalation = contributions.escalation
        values["employee_contribution"] = str(
            contributions.employee_amount.value.amount
        )
        values["employer_contribution"] = (
            "" if employer is None else str(employer.value.amount)
        )
        values["relief_mechanic"] = "" if relief is None else _RELIEF_KEYS[relief]
        values["escalation"] = (
            "" if escalation is None else _ESCALATION_KEYS[escalation]
        )
    return values


def _factor_table_text(table: FactorTable) -> str:
    """The factor table as the form's ``age:factor`` pair syntax."""
    return ", ".join(f"{age}:{factor}" for age, factor in sorted(table.factors.items()))


def _db_pension_values(pension: DBPension) -> dict[str, str]:
    """One DB pension section instance's raw text."""
    basis = pension.revaluation_basis
    commutation = pension.commutation_factor
    taken = pension.taken_at_age
    values = {
        ENTITY_ID_KEY: str(pension.id),
        "accrued_annual_pension": str(pension.accrued_annual_pension.value.amount),
        "statement_date": pension.statement_date.isoformat(),
        "normal_pension_age": str(pension.normal_pension_age.value),
        "revaluation_reference": _REVALUATION_KEYS[basis.reference],
        "revaluation_cap": "" if basis.cap is None else str(basis.cap.value),
        "revaluation_fixed_rate": (
            "" if basis.fixed_rate is None else str(basis.fixed_rate.value)
        ),
        "early_late_factors": _factor_table_text(pension.early_late_factors),
        "commutation_factor": "" if commutation is None else str(commutation.value),
        "taken_at_age": "" if taken is None else str(taken.value),
        "commuted_fraction": str(pension.commuted_fraction.value),
        "accrual_rate": "",
        "pensionable_salary": "",
        "active_until_age": "",
    }
    membership = pension.active_membership
    if membership is not None:
        until = membership.active_until_age
        values["accrual_rate"] = str(membership.accrual_rate.value)
        values["pensionable_salary"] = str(membership.pensionable_salary.value.amount)
        values["active_until_age"] = "" if until is None else str(until.value)
    return values


def _annuity_purchase_values(purchase: AnnuityPurchase) -> dict[str, str]:
    """One annuity purchase section instance's raw text."""
    return {
        ENTITY_ID_KEY: str(purchase.id),
        "at_age": str(purchase.at_age.value),
        "fraction_of_pot": str(purchase.fraction_of_pot.value),
        "annuity_type": _ANNUITY_TYPE_KEYS[purchase.annuity_type],
    }


def _equity_percent_text(wrapper: Wrapper) -> str:
    """The stated equity share as the percent text the form echoes.

    Blank for a glide-path follower and for cash accounts (whose
    pinned allocation is a rule, not an entry); trailing zeros are
    trimmed so a submitted "62.5" round-trips as "62.5".
    """
    allocation = wrapper.allocation
    if allocation is None or wrapper.kind == CASH_KIND:
        return ""
    text = format(allocation.equity * _HUNDRED, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _allocation_cannot_represent(wrapper: Wrapper) -> str | None:
    """Why the equity-percent field cannot express ``wrapper.allocation``.

    The field states an equity share with the remainder in bonds, so a
    cash-bearing allocation (possible only in a hand-edited plan file)
    has no faithful text; a cash account must carry exactly its pinned
    all-cash allocation.
    """
    if wrapper.kind == CASH_KIND:
        if wrapper.allocation != _CASH_ALLOCATION:
            return "a non-cash allocation on a cash account"
        return None
    if wrapper.allocation is not None and wrapper.allocation.cash != Decimal(0):
        return "an asset allocation holding cash"
    return None


def _wrapper_cannot_represent(wrapper: Wrapper) -> str | None:
    """Why the form cannot faithfully edit ``wrapper``; ``None`` if it can."""
    if str(wrapper.kind) not in _WRAPPER_KINDS:
        return f"the wrapper kind {str(wrapper.kind)!r}"
    if wrapper.fees is not None:
        return "a wrapper fee schedule"
    allocation_reason = _allocation_cannot_represent(wrapper)
    if allocation_reason is not None:
        return allocation_reason
    crystallised = wrapper.crystallised_balance
    if crystallised is not None and crystallised.as_of != wrapper.balance.as_of:
        return "wrapper balances dated on different days"
    contributions = wrapper.contributions
    if (
        contributions is not None
        and contributions.escalation is not None
        and contributions.escalation not in _ESCALATION_KEYS
    ):
        return f"the contribution escalation {contributions.escalation.value!r}"
    return None


def _state_pension_cannot_represent(record: StatePensionRecord | None) -> str | None:
    """Why the form cannot faithfully edit ``record``; ``None`` if it can."""
    if record is None:
        return None
    forecast = record.forecast_weekly_amount
    protected = record.protected_payment
    if (
        forecast is not None
        and protected is not None
        and forecast.as_of != protected.as_of
    ):
        return "state pension forecast facts dated on different days"
    return None


def _db_pension_cannot_represent(pension: DBPension) -> str | None:
    """Why the form cannot faithfully edit ``pension``; ``None`` if it can."""
    dated = [pension.accrued_annual_pension.as_of, pension.normal_pension_age.as_of]
    if pension.commutation_factor is not None:
        dated.append(pension.commutation_factor.as_of)
    membership = pension.active_membership
    if membership is not None:
        dated.append(membership.accrual_rate.as_of)
        dated.append(membership.pensionable_salary.as_of)
    if any(as_of != pension.statement_date for as_of in dated):
        return "DB scheme facts dated off the statement date"
    return None


def _annuity_purchase_cannot_represent(purchase: AnnuityPurchase) -> str | None:
    """Why the form cannot faithfully edit ``purchase``; ``None`` if it can.

    The v1 form is single-person, so it offers no basis choice: a
    joint-life purchase would silently become single-life on resave.
    """
    if purchase.basis is not AnnuityBasis.SINGLE:
        return "a joint-life annuity purchase"
    return None


def _carries_note(value: object) -> bool:
    """Whether any fact or decision inside ``value`` carries a note.

    A structural walk (dataclass fields and tuples) rather than a
    field-by-field enumeration, so a note on a fact the model grows
    tomorrow is still caught without this function changing.
    """
    if isinstance(value, Fact | Decision):
        return value.note is not None
    if isinstance(value, tuple):
        return any(_carries_note(item) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        return any(_carries_note(getattr(value, spec.name)) for spec in fields(value))
    return False


def _person_cannot_represent(person: Person) -> str | None:
    """Why the form cannot faithfully edit ``person``; ``None`` if it can."""
    if person.glide_path is not None:
        return "a personal glide path"
    if str(person.tax_residency) not in _RESIDENCIES:
        return f"the tax residency {str(person.tax_residency)!r}"
    reasons = chain(
        (_state_pension_cannot_represent(person.state_pension),),
        (_wrapper_cannot_represent(wrapper) for wrapper in person.wrappers),
        (_db_pension_cannot_represent(pension) for pension in person.db_pensions),
        (
            _annuity_purchase_cannot_represent(purchase)
            for purchase in person.annuity_purchases
        ),
    )
    return next((reason for reason in reasons if reason is not None), None)


def form_cannot_represent(household: Household) -> str | None:
    """Why the v1 facts form cannot faithfully edit ``household``.

    ``None`` when every stored detail lands in a form field. The domain
    model legitimately holds more than the form yet offers (extra
    persons, planned outflows, joint-life annuity purchases, personal
    glide paths, whole-retirement spending multipliers, wrapper
    allocations and fees, independently dated fact pairs, fact and
    decision notes) —
    resubmitting the populated form would silently rebuild a reduced
    household, so a shell must refuse to open such a plan rather than
    lose the data (§4.5).
    """
    if len(household.persons) != 1:
        return "more than one person"
    if household.planned_outflows:
        return "planned outflows"
    spending = household.spending
    if spending is not None and spending.stage_multipliers is not None:
        offered = {stage for _, stage in _STAGE_MULTIPLIER_FIELDS}
        if set(spending.stage_multipliers) - offered:
            return "spending stage multipliers beyond the go-go/slow-go/no-go fields"
    if _carries_note(household):
        return "notes on facts or decisions"
    return _person_cannot_represent(household.persons[0])


def facts_form_data_from_household(household: Household) -> FactsFormData:
    """The household re-rendered as raw form text (plan load, §4.7).

    The inverse of :func:`parse_facts_form`, used to repopulate the
    facts form from a loaded plan. Statement-dated facts (wrapper
    balances, the DWP forecast, DB scheme facts) emit
    their ``as_of`` date explicitly; the other facts offer no ``as_of``
    field, and on resubmission :func:`parse_facts_form` carries their
    stored dates forward from ``previous`` wherever the value is
    unchanged — so a plan-load round trip re-dates nothing silently
    either way. Provenance timestamps are not carried — a resubmission
    re-records its facts at submission time, exactly like any edit.
    Every repeatable row carries its entity id (:data:`ENTITY_ID_KEY`),
    so identity survives edits, reordering, and row deletion (§4.3).
    """
    person = household.persons[0]
    return FactsFormData(
        person=_person_values(person),
        spending=_spending_values(household.spending),
        state_pension=_state_pension_values(person.state_pension),
        wrappers=tuple(_wrapper_values(entry) for entry in person.wrappers),
        db_pensions=tuple(_db_pension_values(entry) for entry in person.db_pensions),
        annuity_purchases=tuple(
            _annuity_purchase_values(entry) for entry in person.annuity_purchases
        ),
    )


@dataclass(frozen=True)
class PlanEntityIds:
    """The plan's repeatable-entity ids, one per form row, in order."""

    wrappers: tuple[str, ...] = ()
    db_pensions: tuple[str, ...] = ()
    annuity_purchases: tuple[str, ...] = ()


def plan_entity_ids(household: Household) -> PlanEntityIds:
    """The ids a shell seeds back into the form after a successful save.

    A hand-typed row carries no id, so its entity is minted fresh at
    parse time; without this write-back the *next* submission would
    mint again, orphaning any scenario override created in between.
    On success the parsed household holds exactly one entity per form
    row, in row order, so the ids line up positionally.
    """
    person = household.persons[0]
    return PlanEntityIds(
        wrappers=tuple(str(entry.id) for entry in person.wrappers),
        db_pensions=tuple(str(entry.id) for entry in person.db_pensions),
        annuity_purchases=tuple(str(entry.id) for entry in person.annuity_purchases),
    )


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
    return FieldSpec(key=key, label=label, kind=FieldKind.DATE, hint=_AS_OF_HINT)


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
                kind=FieldKind.DATE,
                hint="YYYY-MM-DD, e.g. 1980-04-12",
                required=True,
            ),
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
            FieldSpec(
                key="target_retirement_age",
                label="Target retirement age (your choice)",
                hint="e.g. 62",
                required=True,
            ),
            FieldSpec(
                key="mpaa_triggered_on",
                label="MPAA triggered on",
                kind=FieldKind.DATE,
                hint="YYYY-MM-DD; blank if you have never flexibly accessed a pension",
            ),
            FieldSpec(
                key="lsa_used",
                label="Lump sum allowance already used",
                hint="blank if none",
            ),
        ),
    )


def _spending_section() -> SectionSpec:
    """The household spending section."""
    return SectionSpec(
        key="spending",
        title="Household spending",
        description=(
            "Your annual spending need in today's money, after tax. Blank "
            "means spending is not modelled yet. The optional stage "
            "multipliers scale that need across retirement's phases — "
            "the first decade (go-go), the second (slow-go), and beyond "
            "(no-go); blank means 1."
        ),
        fields=(
            FieldSpec(
                key="annual_spending_real",
                label="Annual spending (today's money, net)",
                hint="e.g. 28000",
            ),
            FieldSpec(
                key="go_go_multiplier",
                label="Go-go multiplier (first decade retired)",
                hint="e.g. 1.2; blank means 1",
            ),
            FieldSpec(
                key="slow_go_multiplier",
                label="Slow-go multiplier (second decade retired)",
                hint="e.g. 0.9; blank means 1",
            ),
            FieldSpec(
                key="no_go_multiplier",
                label="No-go multiplier (beyond two decades retired)",
                hint="e.g. 0.8; blank means 1",
            ),
        ),
    )


def _state_pension_section() -> SectionSpec:
    """The state pension section (official DWP forecast only, §5.1)."""
    return SectionSpec(
        key="state_pension",
        title="State pension",
        description=(
            "Your official DWP forecast is the fact — free and instant "
            "from gov.uk/check-state-pension — and the only route to a "
            "state pension amount. A forecast dated a whole month or "
            "more before today is uprated to today at the assumed "
            "uprating policy. Leave the whole section blank to skip "
            "the state pension."
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
                key="label",
                label="Name (your own label for this account)",
                hint="e.g. Aviva SIPP; blank shows the kind",
            ),
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
                key="equity_percent",
                label="Equity allocation, % (your choice)",
                hint=(
                    "e.g. 100 for all-equity; the rest is bonds. Blank "
                    "follows the de-risking glide path; cash accounts "
                    "are always cash"
                ),
            ),
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
    """The repeatable DB-pension section."""
    return SectionSpec(
        key="db_pension",
        title="Defined benefit pension",
        description=(
            "Scheme parameters are facts from your benefit statement — "
            "schemes vary too much to guess. The statement date dates "
            "every scheme fact. Still accruing? Enter the accrual rate "
            "and pensionable salary; leave them blank for a deferred "
            "pension."
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
                kind=FieldKind.DATE,
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
            FieldSpec(
                key="accrual_rate",
                label="Accrual rate (fraction of salary per year of service)",
                hint="e.g. 0.0166667 for a 1/60th scheme; blank if deferred",
            ),
            FieldSpec(
                key="pensionable_salary",
                label="Pensionable salary (annual)",
                hint="active members only; often below total pay",
            ),
            FieldSpec(
                key="active_until_age",
                label="Active until age (your choice)",
                hint="blank means until benefits start",
            ),
        ),
    )


def _annuity_purchase_section() -> SectionSpec:
    """The repeatable annuity-purchase section.

    Every field is a choice (planning §5.1): the record is wholly a
    decision, priced at run time from the annuity-rate assumptions in
    the inspector. Single-life only until couples activate (9.4).
    """
    return SectionSpec(
        key="annuity_purchase",
        title="Annuity purchase",
        description=(
            "A plan to convert part of your pension pot into guaranteed "
            "lifetime income at a chosen age. Anything not annuitised "
            "stays invested in drawdown; add several purchases at "
            "different ages to annuitise in stages. Everything here is "
            "your choice; the annuity rates applied are assumptions, "
            "shown in the stated-vs-assumed view. Single-life products "
            "only for now."
        ),
        repeatable=True,
        add_label="Add annuity purchase",
        remove_label="Remove this annuity purchase",
        fields=(
            FieldSpec(
                key="at_age",
                label="Buy at age (your choice)",
                hint="e.g. 68",
                required=True,
            ),
            FieldSpec(
                key="fraction_of_pot",
                label="Fraction of pension pot (your choice)",
                hint="over 0 up to 1, e.g. 0.5; 1 annuitises the whole pot",
                required=True,
            ),
            FieldSpec(
                key="annuity_type",
                label="Annuity type (your choice)",
                kind=FieldKind.CHOICE,
                required=True,
                choices=(
                    _SELECT_OPTION,
                    ChoiceOption(value="level", label="Level (constant income)"),
                    ChoiceOption(
                        value="escalating",
                        label="Escalating (fixed annual increases)",
                    ),
                    ChoiceOption(
                        value="inflation_linked",
                        label="Inflation-linked (tracks CPI)",
                    ),
                ),
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
            "make — never a guess. Balances and your state pension "
            'forecast carry an "as of" date that defaults to today '
            "when blank; estimates and defaults live in the "
            "assumptions inspector."
        ),
        person=_person_section(),
        spending=_spending_section(),
        state_pension=_state_pension_section(),
        wrapper=_wrapper_section(),
        db_pension=_db_pension_section(),
        annuity_purchase=_annuity_purchase_section(),
        submit_label="Save facts and project",
        clear_label="Clear the form",
        example_note=(
            "This is an example plan so you can see glidepath's output "
            "straight away — nothing here is your data. Replace the "
            "values with your own facts and save, or clear the form to "
            "start blank."
        ),
        cleared_note="Form cleared — enter your facts and save to project.",
    )
