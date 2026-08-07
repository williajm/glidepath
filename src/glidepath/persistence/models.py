"""Pydantic wire models for ``.glidepath.json`` (roadmap 6.2; planning §4.5).

One pydantic model per JSON object, mirroring the document schema
exactly: unknown keys are rejected (``extra="forbid"``), every key is
required, and the leaf types carry the repo's persistence rules —
``Decimal`` and ``Money`` travel as strings with their exact spellings
preserved, datetimes must be timezone-aware, whole numbers refuse
smuggled booleans.

Each model builds its domain entity inside an after-validator, so a
domain invariant violated by stored data surfaces as a
``ValidationError`` carrying the precise document location — the same
path-in-every-message property the hand-rolled reader guaranteed. The
writer constructs the same models from domain objects, so the writer
can never produce a file the reader rejects: one schema enforces both
directions (planning §4.5).

Validation context selects the direction for the polymorphic stored
values: :data:`WIRE_CONTEXT` decodes tagged JSON objects; without it
(the writer's constructor path) a runtime value is checked against the
closed persistable vocabulary instead.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    PlainValidator,
    PrivateAttr,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from glidepath.core import (
    AnnuityBasis,
    AnnuityPurchase,
    AnnuityType,
    AssetAllocation,
    AssumptionKey,
    AssumptionTarget,
    ContributionSchedule,
    DBActiveMembership,
    DBPension,
    Decision,
    DecisionTarget,
    EntityId,
    Fact,
    FactorTable,
    FeeSchedule,
    GlidePathConfig,
    GlidePathPoint,
    Household,
    LifeStage,
    Money,
    Override,
    Person,
    PlannedOutflow,
    Rate,
    ReliefMechanic,
    RevaluationBasis,
    RevaluationReference,
    Scenario,
    Sex,
    SpendingPlan,
    StatePensionRecord,
    TaxResidencyId,
    Wrapper,
    WrapperKindId,
)
from glidepath.persistence.document import (
    SCHEMA_VERSION,
    AssumptionOverride,
    PlanDocument,
)
from glidepath.persistence.values import (
    ANNUITY_BASIS_TOKENS,
    ANNUITY_TYPE_TOKENS,
    LIFE_STAGE_TOKENS,
    RELIEF_MECHANIC_TOKENS,
    REVALUATION_REFERENCE_TOKENS,
    SEX_TOKENS,
    decode_value,
    encode_value,
    parse_date,
    parse_datetime,
    parse_decimal,
    parse_int,
    parse_str,
)

WIRE_CONTEXT = "wire"
"""Validation context marking JSON input (decode tagged stored values)."""


def document_error_message(error: ValidationError) -> str:
    """One document-path-prefixed message from a validation failure.

    The first error's location renders as the legacy dotted document
    path (``document.household.persons[0].balance.value``); pydantic's
    ``Value error, `` prefix is stripped so domain messages read
    unchanged. Further errors are counted rather than printed — the
    reader fails loudly on the first defect, as ever (planning §4.5).
    """
    details = error.errors()
    first = details[0]
    path = "document" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in first["loc"]
    )
    message = first["msg"].removeprefix("Value error, ")
    suffix = f" (and {len(details) - 1} more)" if len(details) > 1 else ""
    return f"{path}: {message}{suffix}"


def _decimal_value(raw: object) -> Decimal:
    """A stored decimal string — or a domain ``Decimal`` passing through."""
    if isinstance(raw, Decimal):
        if not raw.is_finite():
            msg = f"decimal values must be finite, got {raw!r}"
            raise ValueError(msg)
        return raw
    return parse_decimal(raw)


def _money_value(raw: object) -> Money:
    """A stored money string — or a domain ``Money`` passing through."""
    if isinstance(raw, Money):
        return raw
    return Money(parse_decimal(raw))


def _rate_value(raw: object) -> Rate:
    """A stored rate string — or a domain ``Rate`` passing through."""
    if isinstance(raw, Rate):
        return raw
    return Rate(parse_decimal(raw))


def _int_value(raw: object) -> int:
    """A stored whole number (``bool`` rejected, as ever)."""
    return parse_int(raw)


def _date_value(raw: object) -> date:
    """A stored ISO-8601 date — or a domain ``date`` passing through."""
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    return parse_date(raw)


def _datetime_value(raw: object) -> datetime:
    """A stored ISO-8601 aware datetime — or a domain one passing through."""
    if isinstance(raw, datetime):
        if raw.tzinfo is None or raw.tzinfo.utcoffset(raw) is None:
            msg = "datetimes must be timezone-aware"
            raise ValueError(msg)
        return raw
    return parse_datetime(raw)


def _entity_id_value(raw: object) -> EntityId:
    """A stable entity id, required non-empty."""
    text = parse_str(raw)
    if not text:
        msg = "entity ids must be non-empty"
        raise ValueError(msg)
    return EntityId(text)


def _tax_residency_value(raw: object) -> TaxResidencyId:
    """An opaque region-defined tax residency id."""
    return TaxResidencyId(parse_str(raw))


def _wrapper_kind_value(raw: object) -> WrapperKindId:
    """An opaque region-defined wrapper kind id."""
    return WrapperKindId(parse_str(raw))


def _assumption_key_value(raw: object) -> AssumptionKey:
    """A dotted assumption key from the stable catalogue."""
    text = parse_str(raw)
    try:
        return AssumptionKey(text)
    except ValueError:
        msg = f"unknown assumption key {text!r}"
        raise ValueError(msg) from None


def _factor_age_value(raw: object) -> int:
    """A factor-table age: a whole-year object key."""
    if isinstance(raw, int) and raw is not True and raw is not False:
        return raw
    text = parse_str(raw)
    try:
        return int(text)
    except ValueError:
        msg = f"ages must be whole years, got {text!r}"
        raise ValueError(msg) from None


def _sex_value(raw: object) -> Sex:
    """A longevity-default sex token — or the member passing through."""
    return raw if isinstance(raw, Sex) else SEX_TOKENS.member(raw)


def _relief_mechanic_value(raw: object) -> ReliefMechanic:
    """A relief-mechanic token — or the member passing through."""
    return (
        raw if isinstance(raw, ReliefMechanic) else RELIEF_MECHANIC_TOKENS.member(raw)
    )


def _revaluation_reference_value(raw: object) -> RevaluationReference:
    """A revaluation-reference token — or the member passing through."""
    if isinstance(raw, RevaluationReference):
        return raw
    return REVALUATION_REFERENCE_TOKENS.member(raw)


def _annuity_type_value(raw: object) -> AnnuityType:
    """An annuity-type token — or the member passing through."""
    return raw if isinstance(raw, AnnuityType) else ANNUITY_TYPE_TOKENS.member(raw)


def _annuity_basis_value(raw: object) -> AnnuityBasis:
    """An annuity-basis token — or the member passing through."""
    return raw if isinstance(raw, AnnuityBasis) else ANNUITY_BASIS_TOKENS.member(raw)


def _life_stage_value(raw: object) -> LifeStage:
    """A life-stage token — or the member passing through."""
    return raw if isinstance(raw, LifeStage) else LIFE_STAGE_TOKENS.member(raw)


def _stored_value(raw: object, info: ValidationInfo) -> object:
    """A polymorphic stored value, direction chosen by context.

    JSON input (:data:`WIRE_CONTEXT`) decodes the tagged object back
    to its exact runtime type; the writer's constructor path instead
    checks the runtime value against the closed persistable vocabulary
    so the writer can never produce a file the reader rejects.
    """
    if info.context == WIRE_CONTEXT:
        return decode_value(raw)
    encode_value(raw)
    return raw


def _decimal_text(value: Decimal) -> str:
    """A decimal figure as its exact string."""
    return str(value)


def _money_text(value: Money) -> str:
    """A monetary amount as its exact decimal string."""
    return str(value.amount)


def _rate_text(value: Rate) -> str:
    """An annual rate as its exact decimal string."""
    return str(value.value)


def _date_text(value: date) -> str:
    """A calendar date in ISO-8601 form."""
    return value.isoformat()


def _datetime_text(value: datetime) -> str:
    """A timezone-aware datetime in ISO-8601 form."""
    return value.isoformat()


def _assumption_key_text(value: AssumptionKey) -> str:
    """A dotted assumption key as its stable string."""
    return value.value


DecimalField = Annotated[
    Decimal, PlainValidator(_decimal_value), PlainSerializer(_decimal_text)
]
MoneyField = Annotated[
    Money, PlainValidator(_money_value), PlainSerializer(_money_text)
]
RateField = Annotated[Rate, PlainValidator(_rate_value), PlainSerializer(_rate_text)]
IntField = Annotated[int, PlainValidator(_int_value)]
DateField = Annotated[date, PlainValidator(_date_value), PlainSerializer(_date_text)]
DatetimeField = Annotated[
    datetime, PlainValidator(_datetime_value), PlainSerializer(_datetime_text)
]
EntityIdField = Annotated[EntityId, PlainValidator(_entity_id_value)]
TaxResidencyField = Annotated[TaxResidencyId, PlainValidator(_tax_residency_value)]
WrapperKindField = Annotated[WrapperKindId, PlainValidator(_wrapper_kind_value)]
AssumptionKeyField = Annotated[
    AssumptionKey,
    PlainValidator(_assumption_key_value),
    PlainSerializer(_assumption_key_text),
]
FactorAgeField = Annotated[int, PlainValidator(_factor_age_value)]
SexField = Annotated[Sex, PlainValidator(_sex_value), PlainSerializer(SEX_TOKENS.token)]
ReliefMechanicField = Annotated[
    ReliefMechanic,
    PlainValidator(_relief_mechanic_value),
    PlainSerializer(RELIEF_MECHANIC_TOKENS.token),
]
RevaluationReferenceField = Annotated[
    RevaluationReference,
    PlainValidator(_revaluation_reference_value),
    PlainSerializer(REVALUATION_REFERENCE_TOKENS.token),
]
AnnuityTypeField = Annotated[
    AnnuityType,
    PlainValidator(_annuity_type_value),
    PlainSerializer(ANNUITY_TYPE_TOKENS.token),
]
AnnuityBasisField = Annotated[
    AnnuityBasis,
    PlainValidator(_annuity_basis_value),
    PlainSerializer(ANNUITY_BASIS_TOKENS.token),
]
LifeStageField = Annotated[
    LifeStage,
    PlainValidator(_life_stage_value),
    PlainSerializer(LIFE_STAGE_TOKENS.token),
]
StoredValueField = Annotated[
    object, PlainValidator(_stored_value), PlainSerializer(encode_value)
]


class _WireModel(BaseModel):
    """Base wire model: unknown keys rejected, every key required."""

    model_config = ConfigDict(extra="forbid")


class WireFact[T](_WireModel):
    """A user-stated fact with its provenance fields."""

    value: T
    as_of: DateField
    recorded_on: DatetimeField
    note: str | None

    _domain: Fact[T] = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain fact, surfacing its invariants here."""
        self._domain = Fact(
            value=self.value,
            as_of=self.as_of,
            recorded_on=self.recorded_on,
            note=self.note,
        )
        return self

    @property
    def domain(self) -> Fact[T]:
        """The validated domain fact."""
        return self._domain

    @classmethod
    def from_domain(cls, fact: Fact[T]) -> Self:
        """The wire form of a domain fact."""
        return cls(
            value=fact.value,
            as_of=fact.as_of,
            recorded_on=fact.recorded_on,
            note=fact.note,
        )


class WireDecision[T](_WireModel):
    """A user choice with its provenance fields."""

    value: T
    recorded_on: DatetimeField
    note: str | None

    _domain: Decision[T] = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain decision, surfacing its invariants here."""
        self._domain = Decision(
            value=self.value, recorded_on=self.recorded_on, note=self.note
        )
        return self

    @property
    def domain(self) -> Decision[T]:
        """The validated domain decision."""
        return self._domain

    @classmethod
    def from_domain(cls, decision: Decision[T]) -> Self:
        """The wire form of a domain decision."""
        return cls(
            value=decision.value, recorded_on=decision.recorded_on, note=decision.note
        )


class WireAllocation(_WireModel):
    """Portfolio weights over the three priced asset classes."""

    equity: DecimalField
    bonds: DecimalField
    cash: DecimalField

    _domain: AssetAllocation = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain allocation, surfacing its invariants."""
        self._domain = AssetAllocation(
            equity=self.equity, bonds=self.bonds, cash=self.cash
        )
        return self

    @property
    def domain(self) -> AssetAllocation:
        """The validated domain allocation."""
        return self._domain

    @classmethod
    def from_domain(cls, allocation: AssetAllocation) -> Self:
        """The wire form of a domain allocation."""
        return cls(
            equity=allocation.equity, bonds=allocation.bonds, cash=allocation.cash
        )


class WireFees(_WireModel):
    """One wrapper's annual percentage fees."""

    platform: RateField
    fund: RateField

    _domain: FeeSchedule = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain fee schedule."""
        self._domain = FeeSchedule(platform=self.platform, fund=self.fund)
        return self

    @property
    def domain(self) -> FeeSchedule:
        """The validated domain fee schedule."""
        return self._domain

    @classmethod
    def from_domain(cls, fees: FeeSchedule) -> Self:
        """The wire form of a domain fee schedule."""
        return cls(platform=fees.platform, fund=fees.fund)


class WireContributionSchedule(_WireModel):
    """One wrapper's planned annual contributions."""

    employee_amount: WireDecision[MoneyField]
    employer_amount: WireFact[MoneyField] | None
    relief_mechanic: ReliefMechanicField | None
    escalation: AssumptionKeyField | None

    _domain: ContributionSchedule = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain schedule, surfacing its invariants."""
        employer = self.employer_amount
        self._domain = ContributionSchedule(
            employee_amount=self.employee_amount.domain,
            employer_amount=None if employer is None else employer.domain,
            relief_mechanic=self.relief_mechanic,
            escalation=self.escalation,
        )
        return self

    @property
    def domain(self) -> ContributionSchedule:
        """The validated domain schedule."""
        return self._domain

    @classmethod
    def from_domain(cls, schedule: ContributionSchedule) -> Self:
        """The wire form of a domain contribution schedule."""
        employer = schedule.employer_amount
        return cls(
            employee_amount=WireDecision.from_domain(schedule.employee_amount),
            employer_amount=(
                None if employer is None else WireFact.from_domain(employer)
            ),
            relief_mechanic=schedule.relief_mechanic,
            escalation=schedule.escalation,
        )


class WireWrapper(_WireModel):
    """One account of an opaque region-defined kind."""

    id: EntityIdField
    kind: WrapperKindField
    balance: WireFact[MoneyField]
    crystallised_balance: WireFact[MoneyField] | None
    contributions: WireContributionSchedule | None
    allocation: WireAllocation | None
    fees: WireFees | None

    _domain: Wrapper = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain wrapper, surfacing its invariants."""
        crystallised = self.crystallised_balance
        contributions = self.contributions
        allocation = self.allocation
        fees = self.fees
        self._domain = Wrapper(
            id=self.id,
            kind=self.kind,
            balance=self.balance.domain,
            crystallised_balance=(
                None if crystallised is None else crystallised.domain
            ),
            contributions=None if contributions is None else contributions.domain,
            allocation=None if allocation is None else allocation.domain,
            fees=None if fees is None else fees.domain,
        )
        return self

    @property
    def domain(self) -> Wrapper:
        """The validated domain wrapper."""
        return self._domain

    @classmethod
    def from_domain(cls, wrapper: Wrapper) -> Self:
        """The wire form of a domain wrapper."""
        crystallised = wrapper.crystallised_balance
        contributions = wrapper.contributions
        allocation = wrapper.allocation
        fees = wrapper.fees
        return cls(
            id=wrapper.id,
            kind=wrapper.kind,
            balance=WireFact.from_domain(wrapper.balance),
            crystallised_balance=(
                None if crystallised is None else WireFact.from_domain(crystallised)
            ),
            contributions=(
                None
                if contributions is None
                else WireContributionSchedule.from_domain(contributions)
            ),
            allocation=(
                None if allocation is None else WireAllocation.from_domain(allocation)
            ),
            fees=None if fees is None else WireFees.from_domain(fees),
        )


class WireRevaluationBasis(_WireModel):
    """How a DB entitlement revalues."""

    reference: RevaluationReferenceField
    cap: RateField | None
    fixed_rate: RateField | None

    _domain: RevaluationBasis = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain basis, surfacing its invariants."""
        self._domain = RevaluationBasis(
            reference=self.reference, cap=self.cap, fixed_rate=self.fixed_rate
        )
        return self

    @property
    def domain(self) -> RevaluationBasis:
        """The validated domain basis."""
        return self._domain

    @classmethod
    def from_domain(cls, basis: RevaluationBasis) -> Self:
        """The wire form of a domain revaluation basis."""
        return cls(
            reference=basis.reference, cap=basis.cap, fixed_rate=basis.fixed_rate
        )


class WireActiveMembership(_WireModel):
    """Active CARE-style accrual on a DB entitlement (roadmap 9.6)."""

    accrual_rate: WireFact[DecimalField]
    pensionable_salary: WireFact[MoneyField]
    active_until_age: WireDecision[IntField] | None

    _domain: DBActiveMembership = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain membership, surfacing its invariants."""
        until = self.active_until_age
        self._domain = DBActiveMembership(
            accrual_rate=self.accrual_rate.domain,
            pensionable_salary=self.pensionable_salary.domain,
            active_until_age=None if until is None else until.domain,
        )
        return self

    @property
    def domain(self) -> DBActiveMembership:
        """The validated domain membership."""
        return self._domain

    @classmethod
    def from_domain(cls, membership: DBActiveMembership) -> Self:
        """The wire form of a domain active membership."""
        until = membership.active_until_age
        return cls(
            accrual_rate=WireFact.from_domain(membership.accrual_rate),
            pensionable_salary=WireFact.from_domain(membership.pensionable_salary),
            active_until_age=None if until is None else WireDecision.from_domain(until),
        )


class WireDBPension(_WireModel):
    """One DB entitlement's scheme facts and choices."""

    id: EntityIdField
    accrued_annual_pension: WireFact[MoneyField]
    statement_date: DateField
    normal_pension_age: WireFact[IntField]
    revaluation_basis: WireRevaluationBasis
    early_late_factors: dict[FactorAgeField, DecimalField]
    commuted_fraction: WireDecision[DecimalField]
    commutation_factor: WireFact[DecimalField] | None
    taken_at_age: WireDecision[IntField] | None
    active_membership: WireActiveMembership | None

    _domain: DBPension = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain pension, surfacing its invariants."""
        commutation = self.commutation_factor
        taken = self.taken_at_age
        self._domain = DBPension(
            id=self.id,
            accrued_annual_pension=self.accrued_annual_pension.domain,
            statement_date=self.statement_date,
            normal_pension_age=self.normal_pension_age.domain,
            revaluation_basis=self.revaluation_basis.domain,
            early_late_factors=FactorTable(factors=self.early_late_factors),
            commuted_fraction=self.commuted_fraction.domain,
            commutation_factor=None if commutation is None else commutation.domain,
            taken_at_age=None if taken is None else taken.domain,
            active_membership=(
                None
                if self.active_membership is None
                else self.active_membership.domain
            ),
        )
        return self

    @property
    def domain(self) -> DBPension:
        """The validated domain pension."""
        return self._domain

    @classmethod
    def from_domain(cls, pension: DBPension) -> Self:
        """The wire form of a domain DB pension."""
        commutation = pension.commutation_factor
        taken = pension.taken_at_age
        membership = pension.active_membership
        return cls(
            id=pension.id,
            accrued_annual_pension=WireFact.from_domain(pension.accrued_annual_pension),
            statement_date=pension.statement_date,
            normal_pension_age=WireFact.from_domain(pension.normal_pension_age),
            revaluation_basis=WireRevaluationBasis.from_domain(
                pension.revaluation_basis
            ),
            early_late_factors=dict(pension.early_late_factors.factors),
            commuted_fraction=WireDecision.from_domain(pension.commuted_fraction),
            commutation_factor=(
                None if commutation is None else WireFact.from_domain(commutation)
            ),
            taken_at_age=None if taken is None else WireDecision.from_domain(taken),
            active_membership=(
                None
                if membership is None
                else WireActiveMembership.from_domain(membership)
            ),
        )


class WireAnnuityPurchase(_WireModel):
    """One planned annuity purchase — wholly a decision record."""

    id: EntityIdField
    at_age: WireDecision[IntField]
    fraction_of_pot: WireDecision[DecimalField]
    annuity_type: AnnuityTypeField
    basis: AnnuityBasisField

    _domain: AnnuityPurchase = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain purchase, surfacing its invariants."""
        self._domain = AnnuityPurchase(
            id=self.id,
            at_age=self.at_age.domain,
            fraction_of_pot=self.fraction_of_pot.domain,
            annuity_type=self.annuity_type,
            basis=self.basis,
        )
        return self

    @property
    def domain(self) -> AnnuityPurchase:
        """The validated domain purchase."""
        return self._domain

    @classmethod
    def from_domain(cls, purchase: AnnuityPurchase) -> Self:
        """The wire form of a domain annuity purchase."""
        return cls(
            id=purchase.id,
            at_age=WireDecision.from_domain(purchase.at_age),
            fraction_of_pot=WireDecision.from_domain(purchase.fraction_of_pot),
            annuity_type=purchase.annuity_type,
            basis=purchase.basis,
        )


class WireStatePension(_WireModel):
    """One person's state pension position."""

    forecast_weekly_amount: WireFact[MoneyField] | None
    protected_payment: WireFact[MoneyField] | None
    deferral_years: WireDecision[DecimalField]

    _domain: StatePensionRecord = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain record, surfacing its invariants."""
        forecast = self.forecast_weekly_amount
        protected = self.protected_payment
        self._domain = StatePensionRecord(
            forecast_weekly_amount=None if forecast is None else forecast.domain,
            protected_payment=None if protected is None else protected.domain,
            deferral_years=self.deferral_years.domain,
        )
        return self

    @property
    def domain(self) -> StatePensionRecord:
        """The validated domain record."""
        return self._domain

    @classmethod
    def from_domain(cls, record: StatePensionRecord) -> Self:
        """The wire form of a domain state pension record."""
        forecast = record.forecast_weekly_amount
        protected = record.protected_payment
        return cls(
            forecast_weekly_amount=(
                None if forecast is None else WireFact.from_domain(forecast)
            ),
            protected_payment=(
                None if protected is None else WireFact.from_domain(protected)
            ),
            deferral_years=WireDecision.from_domain(record.deferral_years),
        )


class WireGlidePoint(_WireModel):
    """One glide-path knot."""

    years_to_retirement: IntField
    allocation: WireAllocation

    _domain: GlidePathPoint = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain knot, surfacing its invariants."""
        self._domain = GlidePathPoint(
            years_to_retirement=self.years_to_retirement,
            allocation=self.allocation.domain,
        )
        return self

    @property
    def domain(self) -> GlidePathPoint:
        """The validated domain knot."""
        return self._domain

    @classmethod
    def from_domain(cls, point: GlidePathPoint) -> Self:
        """The wire form of a domain glide-path knot."""
        return cls(
            years_to_retirement=point.years_to_retirement,
            allocation=WireAllocation.from_domain(point.allocation),
        )


class WireGlidePath(_WireModel):
    """A person's own glide path: the knots in order."""

    points: tuple[WireGlidePoint, ...]

    _domain: GlidePathConfig = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain glide path, surfacing its invariants."""
        self._domain = GlidePathConfig(
            points=tuple(point.domain for point in self.points)
        )
        return self

    @property
    def domain(self) -> GlidePathConfig:
        """The validated domain glide path."""
        return self._domain

    @classmethod
    def from_domain(cls, config: GlidePathConfig) -> Self:
        """The wire form of a domain glide path."""
        return cls(
            points=tuple(WireGlidePoint.from_domain(point) for point in config.points)
        )


class WireSpendingPlan(_WireModel):
    """The household's retirement spending need."""

    annual_spending_real: WireFact[MoneyField]
    stage_multipliers: dict[LifeStageField, DecimalField] | None

    _domain: SpendingPlan = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain spending plan, surfacing its invariants."""
        self._domain = SpendingPlan(
            annual_spending_real=self.annual_spending_real.domain,
            stage_multipliers=self.stage_multipliers,
        )
        return self

    @property
    def domain(self) -> SpendingPlan:
        """The validated domain spending plan."""
        return self._domain

    @classmethod
    def from_domain(cls, spending: SpendingPlan) -> Self:
        """The wire form of a domain spending plan."""
        multipliers = spending.stage_multipliers
        return cls(
            annual_spending_real=WireFact.from_domain(spending.annual_spending_real),
            stage_multipliers=None if multipliers is None else dict(multipliers),
        )


class WireAtAge(_WireModel):
    """The person-and-age coordinate of a planned outflow."""

    person_id: EntityIdField
    age: IntField


class WirePlannedOutflow(_WireModel):
    """One dated one-off outflow."""

    id: EntityIdField
    label: str
    amount_real: WireDecision[MoneyField]
    at_age_of: WireAtAge

    _domain: PlannedOutflow = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain outflow, surfacing its invariants."""
        self._domain = PlannedOutflow(
            id=self.id,
            label=self.label,
            amount_real=self.amount_real.domain,
            at_age_of=(self.at_age_of.person_id, self.at_age_of.age),
        )
        return self

    @property
    def domain(self) -> PlannedOutflow:
        """The validated domain outflow."""
        return self._domain

    @classmethod
    def from_domain(cls, outflow: PlannedOutflow) -> Self:
        """The wire form of a domain planned outflow."""
        person_id, age = outflow.at_age_of
        return cls(
            id=outflow.id,
            label=outflow.label,
            amount_real=WireDecision.from_domain(outflow.amount_real),
            at_age_of=WireAtAge(person_id=person_id, age=age),
        )


class WirePerson(_WireModel):
    """One person and everything that hangs off them."""

    id: EntityIdField
    date_of_birth: WireFact[DateField]
    target_retirement_age: WireDecision[IntField]
    tax_residency: TaxResidencyField
    sex_for_longevity: WireFact[SexField] | None
    employment_income: WireFact[MoneyField] | None
    mpaa_triggered_on: WireFact[DateField] | None
    lsa_used: WireFact[MoneyField] | None
    wrappers: tuple[WireWrapper, ...]
    db_pensions: tuple[WireDBPension, ...]
    annuity_purchases: tuple[WireAnnuityPurchase, ...]
    state_pension: WireStatePension | None
    glide_path: WireGlidePath | None

    _domain: Person = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain person, surfacing their invariants."""
        sex = self.sex_for_longevity
        income = self.employment_income
        mpaa = self.mpaa_triggered_on
        lsa = self.lsa_used
        self._domain = Person(
            id=self.id,
            date_of_birth=self.date_of_birth.domain,
            target_retirement_age=self.target_retirement_age.domain,
            tax_residency=self.tax_residency,
            sex_for_longevity=None if sex is None else sex.domain,
            employment_income=None if income is None else income.domain,
            mpaa_triggered_on=None if mpaa is None else mpaa.domain,
            lsa_used=None if lsa is None else lsa.domain,
            wrappers=tuple(wrapper.domain for wrapper in self.wrappers),
            db_pensions=tuple(pension.domain for pension in self.db_pensions),
            annuity_purchases=tuple(
                purchase.domain for purchase in self.annuity_purchases
            ),
            state_pension=(
                None if self.state_pension is None else self.state_pension.domain
            ),
            glide_path=None if self.glide_path is None else self.glide_path.domain,
        )
        return self

    @property
    def domain(self) -> Person:
        """The validated domain person."""
        return self._domain

    @classmethod
    def from_domain(cls, person: Person) -> Self:
        """The wire form of a domain person."""
        sex = person.sex_for_longevity
        income = person.employment_income
        mpaa = person.mpaa_triggered_on
        lsa = person.lsa_used
        state = person.state_pension
        glide = person.glide_path
        return cls(
            id=person.id,
            date_of_birth=WireFact.from_domain(person.date_of_birth),
            target_retirement_age=WireDecision.from_domain(
                person.target_retirement_age
            ),
            tax_residency=person.tax_residency,
            sex_for_longevity=None if sex is None else WireFact.from_domain(sex),
            employment_income=None if income is None else WireFact.from_domain(income),
            mpaa_triggered_on=None if mpaa is None else WireFact.from_domain(mpaa),
            lsa_used=None if lsa is None else WireFact.from_domain(lsa),
            wrappers=tuple(
                WireWrapper.from_domain(wrapper) for wrapper in person.wrappers
            ),
            db_pensions=tuple(
                WireDBPension.from_domain(pension) for pension in person.db_pensions
            ),
            annuity_purchases=tuple(
                WireAnnuityPurchase.from_domain(purchase)
                for purchase in person.annuity_purchases
            ),
            state_pension=(
                None if state is None else WireStatePension.from_domain(state)
            ),
            glide_path=None if glide is None else WireGlidePath.from_domain(glide),
        )


class WireHousehold(_WireModel):
    """The household: persons plus shared economics."""

    persons: tuple[WirePerson, ...]
    planned_outflows: tuple[WirePlannedOutflow, ...]
    spending: WireSpendingPlan | None

    _domain: Household = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain household, surfacing its invariants."""
        self._domain = Household(
            persons=tuple(person.domain for person in self.persons),
            spending=None if self.spending is None else self.spending.domain,
            planned_outflows=tuple(outflow.domain for outflow in self.planned_outflows),
        )
        return self

    @property
    def domain(self) -> Household:
        """The validated domain household."""
        return self._domain

    @classmethod
    def from_domain(cls, household: Household) -> Self:
        """The wire form of a domain household."""
        spending = household.spending
        return cls(
            persons=tuple(
                WirePerson.from_domain(person) for person in household.persons
            ),
            planned_outflows=tuple(
                WirePlannedOutflow.from_domain(outflow)
                for outflow in household.planned_outflows
            ),
            spending=None
            if spending is None
            else WireSpendingPlan.from_domain(spending),
        )


class WireAssumptionTarget(_WireModel):
    """An override target naming an assumption key."""

    kind: Literal["assumption"]
    key: AssumptionKeyField

    @property
    def domain(self) -> AssumptionTarget:
        """The validated domain target."""
        return AssumptionTarget(key=self.key)


class WireDecisionTarget(_WireModel):
    """An override target naming an entity's decision field."""

    kind: Literal["decision"]
    entity_id: EntityIdField
    field_path: str

    _domain: DecisionTarget = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain target, surfacing its invariants."""
        self._domain = DecisionTarget(
            entity_id=self.entity_id, field_path=self.field_path
        )
        return self

    @property
    def domain(self) -> DecisionTarget:
        """The validated domain target."""
        return self._domain


class WireOverride(_WireModel):
    """One scenario override: its target and replacement value."""

    note: str | None
    target: WireAssumptionTarget | WireDecisionTarget = Field(discriminator="kind")
    value: StoredValueField

    _domain: Override = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain override, surfacing its invariants."""
        self._domain = Override(
            target=self.target.domain, value=self.value, note=self.note
        )
        return self

    @property
    def domain(self) -> Override:
        """The validated domain override."""
        return self._domain

    @classmethod
    def from_domain(cls, override: Override) -> Self:
        """The wire form of a domain override."""
        target = override.target
        wire_target: WireAssumptionTarget | WireDecisionTarget
        if isinstance(target, AssumptionTarget):
            wire_target = WireAssumptionTarget(kind="assumption", key=target.key)
        else:
            wire_target = WireDecisionTarget(
                kind="decision",
                entity_id=target.entity_id,
                field_path=target.field_path,
            )
        return cls(note=override.note, target=wire_target, value=override.value)


class WireScenario(_WireModel):
    """One named what-if with its override deltas."""

    name: str
    note: str | None
    overrides: tuple[WireOverride, ...]

    _domain: Scenario = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain scenario, surfacing its invariants."""
        self._domain = Scenario(
            name=self.name,
            overrides=tuple(override.domain for override in self.overrides),
            note=self.note,
        )
        return self

    @property
    def domain(self) -> Scenario:
        """The validated domain scenario."""
        return self._domain

    @classmethod
    def from_domain(cls, scenario: Scenario) -> Self:
        """The wire form of a domain scenario."""
        return cls(
            name=scenario.name,
            note=scenario.note,
            overrides=tuple(
                WireOverride.from_domain(override) for override in scenario.overrides
            ),
        )


class WireAssumptionOverride(_WireModel):
    """One stored user override of a shipped default."""

    key: AssumptionKeyField
    recorded_on: DatetimeField
    source: str
    value: StoredValueField

    _domain: AssumptionOverride = PrivateAttr()

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain override, surfacing its invariants."""
        self._domain = AssumptionOverride(
            key=self.key,
            value=self.value,
            source=self.source,
            recorded_on=self.recorded_on,
        )
        return self

    @property
    def domain(self) -> AssumptionOverride:
        """The validated domain override."""
        return self._domain

    @classmethod
    def from_domain(cls, override: AssumptionOverride) -> Self:
        """The wire form of a stored assumption override."""
        return cls(
            key=override.key,
            recorded_on=override.recorded_on,
            source=override.source,
            value=override.value,
        )


class WireDocument(_WireModel):
    """The whole ``.glidepath.json`` document."""

    schema_version: IntField
    region: str
    assumptions_resolved_against: str
    household: WireHousehold
    assumption_overrides: tuple[WireAssumptionOverride, ...]
    scenarios: tuple[WireScenario, ...]

    _domain: PlanDocument = PrivateAttr()

    @field_validator("schema_version", mode="after")
    @classmethod
    def _current_version(cls, value: int) -> int:
        """Require the current schema version — migration runs first."""
        if value != SCHEMA_VERSION:
            msg = f"expected {SCHEMA_VERSION} after migration, got {value}"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _build(self) -> Self:
        """Construct the domain document, surfacing its invariants."""
        self._domain = PlanDocument(
            region=self.region,
            assumptions_resolved_against=self.assumptions_resolved_against,
            household=self.household.domain,
            assumption_overrides=tuple(
                override.domain for override in self.assumption_overrides
            ),
            scenarios=tuple(scenario.domain for scenario in self.scenarios),
        )
        return self

    @property
    def domain(self) -> PlanDocument:
        """The validated domain document."""
        return self._domain

    @classmethod
    def from_domain(cls, document: PlanDocument) -> Self:
        """The wire form of a domain document, version stamped in."""
        return cls(
            schema_version=SCHEMA_VERSION,
            region=document.region,
            assumptions_resolved_against=document.assumptions_resolved_against,
            household=WireHousehold.from_domain(document.household),
            assumption_overrides=tuple(
                WireAssumptionOverride.from_domain(override)
                for override in document.assumption_overrides
            ),
            scenarios=tuple(
                WireScenario.from_domain(scenario) for scenario in document.scenarios
            ),
        )
