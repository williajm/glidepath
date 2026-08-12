"""Strict reader for ``.glidepath.json`` (roadmap 6.2; planning §4.5).

Everything fails loudly with a document path in the message: unknown
keys, missing keys, mistyped values, JSON floats (money is ``Decimal``,
never float — planning §4.6), and any entity invariant violated by the
stored data. Migration (roadmap 6.4) runs before decoding, so this
module only ever reads the current schema version.
"""

import json
from typing import TYPE_CHECKING

from glidepath.core import (
    AnnuityPurchase,
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
    Override,
    Person,
    PlannedOutflow,
    Rate,
    RevaluationBasis,
    Scenario,
    SpendingPlan,
    StatePensionRecord,
    TaxResidencyId,
    Wrapper,
    WrapperKindId,
)
from glidepath.persistence.document import (
    SCHEMA_VERSION,
    AssumptionOverride,
    PersistenceError,
    PlanDocument,
)
from glidepath.persistence.migrations import apply_migrations
from glidepath.persistence.values import (
    ANNUITY_BASIS_TOKENS,
    ANNUITY_TYPE_TOKENS,
    LIFE_STAGE_TOKENS,
    RELIEF_MECHANIC_TOKENS,
    REVALUATION_REFERENCE_TOKENS,
    SEX_TOKENS,
    decode_value,
    parse_bool,
    parse_date,
    parse_datetime,
    parse_decimal,
    parse_int,
    parse_money,
    parse_str,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from decimal import Decimal
    from pathlib import Path

    from glidepath.core import LifeStage, Sex


def loads_plan(text: str) -> PlanDocument:
    """Parse canonical ``.glidepath.json`` text into a document.

    Older schema versions are upgraded through the migration harness
    (roadmap 6.4) before strict decoding.

    Raises:
        PersistenceError: If the text is not valid JSON, needs a
            migration this build lacks, or violates the schema.
    """
    raw = _parse_json(text)
    return _document(apply_migrations(raw))


def load_plan(path: Path) -> PlanDocument:
    """Read and parse the plan document stored at ``path``.

    Raises:
        PersistenceError: As :func:`loads_plan`, or if the file is not
            UTF-8 text (a defective document, same as invalid JSON).
        OSError: If the file cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        msg = "plan file is not UTF-8 text"
        raise PersistenceError(msg) from exc
    return loads_plan(text)


def _reject_float(text: str) -> object:
    """Refuse JSON floats: money and rates travel as strings (§4.6)."""
    msg = f"JSON floats are not permitted in a plan document, got {text!r}"
    raise PersistenceError(msg)


def _parse_json(text: str) -> dict[str, object]:
    """Parse the raw JSON text into a top-level object."""
    try:
        raw: object = json.loads(text, parse_float=_reject_float)
    except json.JSONDecodeError as error:
        msg = f"not valid JSON: {error}"
        raise PersistenceError(msg) from error
    if not isinstance(raw, dict):
        msg = f"document root must be an object, got {type(raw).__name__}"
        raise PersistenceError(msg)
    return raw


class _Reader:
    """One JSON object being decoded strictly, with a document path."""

    __slots__ = ("_entries", "path")

    def __init__(self, raw: object, path: str) -> None:
        """Require an object and copy its entries for consumption."""
        if not isinstance(raw, dict):
            msg = f"{path}: expected an object, got {type(raw).__name__}"
            raise PersistenceError(msg)
        self._entries: dict[str, object] = dict(raw)
        self.path = path

    def take(self, key: str) -> object:
        """Consume a required key (its value may still be null).

        Raises:
            PersistenceError: If the key is absent.
        """
        if key not in self._entries:
            msg = f"{self.path}: missing required key {key!r}"
            raise PersistenceError(msg)
        return self._entries.pop(key)

    def at(self, key: str) -> str:
        """The document path of ``key`` under this object."""
        return f"{self.path}.{key}"

    def finish(self) -> None:
        """Reject any keys the schema does not define.

        Raises:
            PersistenceError: If unconsumed keys remain.
        """
        if self._entries:
            unknown = ", ".join(sorted(self._entries))
            msg = f"{self.path}: unknown keys: {unknown}"
            raise PersistenceError(msg)


def _built[T](factory: Callable[[], T], path: str) -> T:
    """Construct an entity, converting invariant errors to schema errors."""
    try:
        return factory()
    except PersistenceError:
        raise
    except ValueError as error:
        msg = f"{path}: {error}"
        raise PersistenceError(msg) from error


def _sequence[T](
    raw: object, path: str, decode: Callable[[object, str], T]
) -> tuple[T, ...]:
    """Decode a JSON array element by element."""
    if not isinstance(raw, list):
        msg = f"{path}: expected an array, got {type(raw).__name__}"
        raise PersistenceError(msg)
    return tuple(decode(entry, f"{path}[{index}]") for index, entry in enumerate(raw))


def _optional[T](
    raw: object, path: str, decode: Callable[[object, str], T]
) -> T | None:
    """Decode a nullable value."""
    return None if raw is None else decode(raw, path)


def _optional_str(raw: object, path: str) -> str | None:
    """Decode a nullable string (a fact or decision note)."""
    return None if raw is None else parse_str(raw, path)


def _fact[T](raw: object, path: str, value: Callable[[object, str], T]) -> Fact[T]:
    """Decode a user-stated fact with its provenance fields."""
    reader = _Reader(raw, path)
    decoded = value(reader.take("value"), reader.at("value"))
    as_of = parse_date(reader.take("as_of"), reader.at("as_of"))
    recorded_on = parse_datetime(reader.take("recorded_on"), reader.at("recorded_on"))
    note = _optional_str(reader.take("note"), reader.at("note"))
    reader.finish()
    return _built(
        lambda: Fact(value=decoded, as_of=as_of, recorded_on=recorded_on, note=note),
        path,
    )


def _decision[T](
    raw: object, path: str, value: Callable[[object, str], T]
) -> Decision[T]:
    """Decode a user choice with its provenance fields."""
    reader = _Reader(raw, path)
    decoded = value(reader.take("value"), reader.at("value"))
    recorded_on = parse_datetime(reader.take("recorded_on"), reader.at("recorded_on"))
    note = _optional_str(reader.take("note"), reader.at("note"))
    reader.finish()
    return _built(
        lambda: Decision(value=decoded, recorded_on=recorded_on, note=note), path
    )


def _sex(raw: object, path: str) -> Sex:
    """Decode a longevity-default sex token."""
    return SEX_TOKENS.member(raw, path)


def _rate(raw: object, path: str) -> Rate:
    """Decode an annual rate stored as a decimal string."""
    return Rate(parse_decimal(raw, path))


def _entity_id(raw: object, path: str) -> EntityId:
    """Decode a stable entity id."""
    text = parse_str(raw, path)
    if not text:
        msg = f"{path}: entity ids must be non-empty"
        raise PersistenceError(msg)
    return EntityId(text)


def _assumption_key(raw: object, path: str) -> AssumptionKey:
    """Decode a dotted assumption key.

    Raises:
        PersistenceError: If the key is not in the stable catalogue.
    """
    text = parse_str(raw, path)
    try:
        return AssumptionKey(text)
    except ValueError:
        msg = f"{path}: unknown assumption key {text!r}"
        raise PersistenceError(msg) from None


def _document(raw: dict[str, object]) -> PlanDocument:
    """Decode the whole migrated document."""
    reader = _Reader(raw, "document")
    version = parse_int(reader.take("schema_version"), reader.at("schema_version"))
    if version != SCHEMA_VERSION:
        msg = (
            f"document.schema_version: expected {SCHEMA_VERSION} after"
            f" migration, got {version}"
        )
        raise PersistenceError(msg)
    region = parse_str(reader.take("region"), reader.at("region"))
    resolved_against = parse_str(
        reader.take("assumptions_resolved_against"),
        reader.at("assumptions_resolved_against"),
    )
    household = _household(reader.take("household"), reader.at("household"))
    overrides = _sequence(
        reader.take("assumption_overrides"),
        reader.at("assumption_overrides"),
        _assumption_override,
    )
    scenarios = _sequence(reader.take("scenarios"), reader.at("scenarios"), _scenario)
    reader.finish()
    return _built(
        lambda: PlanDocument(
            region=region,
            assumptions_resolved_against=resolved_against,
            household=household,
            assumption_overrides=overrides,
            scenarios=scenarios,
        ),
        "document",
    )


def _assumption_override(raw: object, path: str) -> AssumptionOverride:
    """Decode one stored assumption override."""
    reader = _Reader(raw, path)
    key = _assumption_key(reader.take("key"), reader.at("key"))
    recorded_on = parse_datetime(reader.take("recorded_on"), reader.at("recorded_on"))
    source = parse_str(reader.take("source"), reader.at("source"))
    value = decode_value(reader.take("value"), reader.at("value"))
    reader.finish()
    return _built(
        lambda: AssumptionOverride(
            key=key, value=value, source=source, recorded_on=recorded_on
        ),
        path,
    )


def _scenario(raw: object, path: str) -> Scenario:
    """Decode one named what-if."""
    reader = _Reader(raw, path)
    name = parse_str(reader.take("name"), reader.at("name"))
    note = _optional_str(reader.take("note"), reader.at("note"))
    overrides = _sequence(reader.take("overrides"), reader.at("overrides"), _override)
    reader.finish()
    return _built(lambda: Scenario(name=name, overrides=overrides, note=note), path)


def _override(raw: object, path: str) -> Override:
    """Decode one scenario override."""
    reader = _Reader(raw, path)
    note = _optional_str(reader.take("note"), reader.at("note"))
    target = _target(reader.take("target"), reader.at("target"))
    value = decode_value(reader.take("value"), reader.at("value"))
    reader.finish()
    return Override(target=target, value=value, note=note)


def _target(raw: object, path: str) -> AssumptionTarget | DecisionTarget:
    """Decode an override target of either kind."""
    reader = _Reader(raw, path)
    kind = parse_str(reader.take("kind"), reader.at("kind"))
    if kind == "assumption":
        key = _assumption_key(reader.take("key"), reader.at("key"))
        reader.finish()
        return AssumptionTarget(key=key)
    if kind == "decision":
        entity_id = _entity_id(reader.take("entity_id"), reader.at("entity_id"))
        field_path = parse_str(reader.take("field_path"), reader.at("field_path"))
        reader.finish()
        return _built(
            lambda: DecisionTarget(entity_id=entity_id, field_path=field_path), path
        )
    msg = f"{path}.kind: expected 'assumption' or 'decision', got {kind!r}"
    raise PersistenceError(msg)


def _household(raw: object, path: str) -> Household:
    """Decode the household: persons plus shared economics."""
    reader = _Reader(raw, path)
    persons = _sequence(reader.take("persons"), reader.at("persons"), _person)
    outflows = _sequence(
        reader.take("planned_outflows"),
        reader.at("planned_outflows"),
        _planned_outflow,
    )
    spending = _optional(reader.take("spending"), reader.at("spending"), _spending_plan)
    claim = _optional(
        reader.take("claim_marriage_allowance"),
        reader.at("claim_marriage_allowance"),
        _claim_decision,
    )
    reader.finish()
    return _built(
        lambda: Household(
            persons=persons,
            spending=spending,
            planned_outflows=outflows,
            claim_marriage_allowance=claim,
        ),
        path,
    )


def _claim_decision(raw: object, path: str) -> Decision[bool]:
    """Decode the household's marriage-allowance claim decision."""
    return _decision(raw, path, parse_bool)


def _person(raw: object, path: str) -> Person:
    """Decode one person and everything that hangs off them."""
    reader = _Reader(raw, path)
    person_id = _entity_id(reader.take("id"), reader.at("id"))
    date_of_birth = _fact(
        reader.take("date_of_birth"), reader.at("date_of_birth"), parse_date
    )
    retirement_age = _decision(
        reader.take("target_retirement_age"),
        reader.at("target_retirement_age"),
        parse_int,
    )
    residency = TaxResidencyId(
        parse_str(reader.take("tax_residency"), reader.at("tax_residency"))
    )
    sex = _optional_fact(reader, "sex_for_longevity", _sex)
    income = _optional_fact(reader, "employment_income", parse_money)
    mpaa = _optional_fact(reader, "mpaa_triggered_on", parse_date)
    lsa_used = _optional_fact(reader, "lsa_used", parse_money)
    wrappers = _sequence(reader.take("wrappers"), reader.at("wrappers"), _wrapper)
    pensions = _sequence(
        reader.take("db_pensions"), reader.at("db_pensions"), _db_pension
    )
    purchases = _sequence(
        reader.take("annuity_purchases"),
        reader.at("annuity_purchases"),
        _annuity_purchase,
    )
    state_pension = _optional(
        reader.take("state_pension"), reader.at("state_pension"), _state_pension
    )
    glide_path = _optional(
        reader.take("glide_path"), reader.at("glide_path"), _glide_path
    )
    reader.finish()
    return _built(
        lambda: Person(
            id=person_id,
            date_of_birth=date_of_birth,
            target_retirement_age=retirement_age,
            tax_residency=residency,
            sex_for_longevity=sex,
            employment_income=income,
            mpaa_triggered_on=mpaa,
            lsa_used=lsa_used,
            wrappers=wrappers,
            db_pensions=pensions,
            annuity_purchases=purchases,
            state_pension=state_pension,
            glide_path=glide_path,
        ),
        path,
    )


def _optional_fact[T](
    reader: _Reader, key: str, value: Callable[[object, str], T]
) -> Fact[T] | None:
    """Consume a nullable fact field from ``reader``."""
    raw = reader.take(key)
    path = reader.at(key)
    return None if raw is None else _fact(raw, path, value)


def _wrapper(raw: object, path: str) -> Wrapper:
    """Decode one account of an opaque region-defined kind."""
    reader = _Reader(raw, path)
    wrapper_id = _entity_id(reader.take("id"), reader.at("id"))
    kind = WrapperKindId(parse_str(reader.take("kind"), reader.at("kind")))
    label = _optional_str(reader.take("label"), reader.at("label"))
    balance = _fact(reader.take("balance"), reader.at("balance"), parse_money)
    crystallised = _optional_fact(reader, "crystallised_balance", parse_money)
    contributions = _optional(
        reader.take("contributions"),
        reader.at("contributions"),
        _contribution_schedule,
    )
    allocation = _optional(
        reader.take("allocation"), reader.at("allocation"), _allocation
    )
    fees = _optional(reader.take("fees"), reader.at("fees"), _fee_schedule)
    reader.finish()
    return _built(
        lambda: Wrapper(
            id=wrapper_id,
            kind=kind,
            balance=balance,
            label=label,
            crystallised_balance=crystallised,
            contributions=contributions,
            allocation=allocation,
            fees=fees,
        ),
        path,
    )


def _contribution_schedule(raw: object, path: str) -> ContributionSchedule:
    """Decode one wrapper's planned annual contributions."""
    reader = _Reader(raw, path)
    employee = _decision(
        reader.take("employee_amount"), reader.at("employee_amount"), parse_money
    )
    employer = _optional_fact(reader, "employer_amount", parse_money)
    mechanic_raw = reader.take("relief_mechanic")
    mechanic = (
        None
        if mechanic_raw is None
        else RELIEF_MECHANIC_TOKENS.member(mechanic_raw, reader.at("relief_mechanic"))
    )
    escalation = _optional(
        reader.take("escalation"), reader.at("escalation"), _assumption_key
    )
    reader.finish()
    return _built(
        lambda: ContributionSchedule(
            employee_amount=employee,
            employer_amount=employer,
            relief_mechanic=mechanic,
            escalation=escalation,
        ),
        path,
    )


def _allocation(raw: object, path: str) -> AssetAllocation:
    """Decode portfolio weights over the three priced asset classes."""
    reader = _Reader(raw, path)
    equity = parse_decimal(reader.take("equity"), reader.at("equity"))
    bonds = parse_decimal(reader.take("bonds"), reader.at("bonds"))
    cash = parse_decimal(reader.take("cash"), reader.at("cash"))
    reader.finish()
    return _built(lambda: AssetAllocation(equity=equity, bonds=bonds, cash=cash), path)


def _fee_schedule(raw: object, path: str) -> FeeSchedule:
    """Decode one wrapper's annual percentage fees."""
    reader = _Reader(raw, path)
    platform = _rate(reader.take("platform"), reader.at("platform"))
    fund = _rate(reader.take("fund"), reader.at("fund"))
    reader.finish()
    return _built(lambda: FeeSchedule(platform=platform, fund=fund), path)


def _db_pension(raw: object, path: str) -> DBPension:
    """Decode one DB entitlement."""
    reader = _Reader(raw, path)
    pension_id = _entity_id(reader.take("id"), reader.at("id"))
    accrued = _fact(
        reader.take("accrued_annual_pension"),
        reader.at("accrued_annual_pension"),
        parse_money,
    )
    statement_date = parse_date(
        reader.take("statement_date"), reader.at("statement_date")
    )
    npa = _fact(
        reader.take("normal_pension_age"),
        reader.at("normal_pension_age"),
        parse_int,
    )
    basis = _revaluation_basis(
        reader.take("revaluation_basis"), reader.at("revaluation_basis")
    )
    factors = _factor_table(
        reader.take("early_late_factors"), reader.at("early_late_factors")
    )
    commuted = _decision(
        reader.take("commuted_fraction"),
        reader.at("commuted_fraction"),
        parse_decimal,
    )
    commutation_raw = reader.take("commutation_factor")
    commutation = (
        None
        if commutation_raw is None
        else _fact(commutation_raw, reader.at("commutation_factor"), parse_decimal)
    )
    taken_raw = reader.take("taken_at_age")
    taken = (
        None
        if taken_raw is None
        else _decision(taken_raw, reader.at("taken_at_age"), parse_int)
    )
    membership = _optional(
        reader.take("active_membership"),
        reader.at("active_membership"),
        _active_membership,
    )
    reader.finish()
    return _built(
        lambda: DBPension(
            id=pension_id,
            accrued_annual_pension=accrued,
            statement_date=statement_date,
            normal_pension_age=npa,
            revaluation_basis=basis,
            early_late_factors=factors,
            commuted_fraction=commuted,
            commutation_factor=commutation,
            taken_at_age=taken,
            active_membership=membership,
        ),
        path,
    )


def _active_membership(raw: object, path: str) -> DBActiveMembership:
    """Decode active CARE-style accrual on a DB entitlement (9.6)."""
    reader = _Reader(raw, path)
    accrual_rate = _fact(
        reader.take("accrual_rate"), reader.at("accrual_rate"), parse_decimal
    )
    salary = _fact(
        reader.take("pensionable_salary"),
        reader.at("pensionable_salary"),
        parse_money,
    )
    until_raw = reader.take("active_until_age")
    until = (
        None
        if until_raw is None
        else _decision(until_raw, reader.at("active_until_age"), parse_int)
    )
    reader.finish()
    return _built(
        lambda: DBActiveMembership(
            accrual_rate=accrual_rate,
            pensionable_salary=salary,
            active_until_age=until,
        ),
        path,
    )


def _revaluation_basis(raw: object, path: str) -> RevaluationBasis:
    """Decode how a DB entitlement revalues."""
    reader = _Reader(raw, path)
    reference = REVALUATION_REFERENCE_TOKENS.member(
        reader.take("reference"), reader.at("reference")
    )
    cap = _optional(reader.take("cap"), reader.at("cap"), _rate)
    fixed_rate = _optional(reader.take("fixed_rate"), reader.at("fixed_rate"), _rate)
    reader.finish()
    return _built(
        lambda: RevaluationBasis(reference=reference, cap=cap, fixed_rate=fixed_rate),
        path,
    )


def _factor_table(raw: object, path: str) -> FactorTable:
    """Decode a whole-year-age → factor table."""
    if not isinstance(raw, dict):
        msg = f"{path}: expected an object, got {type(raw).__name__}"
        raise PersistenceError(msg)
    factors: dict[int, Decimal] = {}
    for age_text, factor_raw in raw.items():
        try:
            age = int(age_text)
        except ValueError:
            msg = f"{path}: ages must be whole years, got {age_text!r}"
            raise PersistenceError(msg) from None
        factors[age] = parse_decimal(factor_raw, f"{path}.{age_text}")
    return _built(lambda: FactorTable(factors=factors), path)


def _annuity_purchase(raw: object, path: str) -> AnnuityPurchase:
    """Decode one planned annuity purchase."""
    reader = _Reader(raw, path)
    purchase_id = _entity_id(reader.take("id"), reader.at("id"))
    at_age = _decision(reader.take("at_age"), reader.at("at_age"), parse_int)
    fraction = _decision(
        reader.take("fraction_of_pot"), reader.at("fraction_of_pot"), parse_decimal
    )
    annuity_type = ANNUITY_TYPE_TOKENS.member(
        reader.take("annuity_type"), reader.at("annuity_type")
    )
    basis = ANNUITY_BASIS_TOKENS.member(reader.take("basis"), reader.at("basis"))
    reader.finish()
    return _built(
        lambda: AnnuityPurchase(
            id=purchase_id,
            at_age=at_age,
            fraction_of_pot=fraction,
            annuity_type=annuity_type,
            basis=basis,
        ),
        path,
    )


def _state_pension(raw: object, path: str) -> StatePensionRecord:
    """Decode one person's state pension position."""
    reader = _Reader(raw, path)
    forecast = _optional_fact(reader, "forecast_weekly_amount", parse_money)
    protected = _optional_fact(reader, "protected_payment", parse_money)
    deferral = _decision(
        reader.take("deferral_years"), reader.at("deferral_years"), parse_decimal
    )
    reader.finish()
    return _built(
        lambda: StatePensionRecord(
            forecast_weekly_amount=forecast,
            protected_payment=protected,
            deferral_years=deferral,
        ),
        path,
    )


def _glide_path(raw: object, path: str) -> GlidePathConfig:
    """Decode a person's own glide path."""
    reader = _Reader(raw, path)
    points = _sequence(reader.take("points"), reader.at("points"), _glide_point)
    reader.finish()
    return _built(lambda: GlidePathConfig(points=points), path)


def _glide_point(raw: object, path: str) -> GlidePathPoint:
    """Decode one glide-path knot."""
    reader = _Reader(raw, path)
    years = parse_int(
        reader.take("years_to_retirement"), reader.at("years_to_retirement")
    )
    allocation = _allocation(reader.take("allocation"), reader.at("allocation"))
    reader.finish()
    return _built(
        lambda: GlidePathPoint(years_to_retirement=years, allocation=allocation), path
    )


def _spending_plan(raw: object, path: str) -> SpendingPlan:
    """Decode the household's retirement spending need."""
    reader = _Reader(raw, path)
    annual = _fact(
        reader.take("annual_spending_real"),
        reader.at("annual_spending_real"),
        parse_money,
    )
    multipliers = _optional(
        reader.take("stage_multipliers"),
        reader.at("stage_multipliers"),
        _stage_multipliers,
    )
    reader.finish()
    return _built(
        lambda: SpendingPlan(
            annual_spending_real=annual, stage_multipliers=multipliers
        ),
        path,
    )


def _stage_multipliers(raw: object, path: str) -> dict[LifeStage, Decimal]:
    """Decode per-life-stage spending multipliers."""
    if not isinstance(raw, dict):
        msg = f"{path}: expected an object, got {type(raw).__name__}"
        raise PersistenceError(msg)
    return {
        LIFE_STAGE_TOKENS.member(token, f"{path}.{token}"): parse_decimal(
            value, f"{path}.{token}"
        )
        for token, value in raw.items()
    }


def _planned_outflow(raw: object, path: str) -> PlannedOutflow:
    """Decode one dated one-off outflow."""
    reader = _Reader(raw, path)
    outflow_id = _entity_id(reader.take("id"), reader.at("id"))
    label = parse_str(reader.take("label"), reader.at("label"))
    amount = _decision(
        reader.take("amount_real"), reader.at("amount_real"), parse_money
    )
    at_reader = _Reader(reader.take("at_age_of"), reader.at("at_age_of"))
    person_id = _entity_id(at_reader.take("person_id"), at_reader.at("person_id"))
    age = parse_int(at_reader.take("age"), at_reader.at("age"))
    at_reader.finish()
    reader.finish()
    return _built(
        lambda: PlannedOutflow(
            id=outflow_id, label=label, amount_real=amount, at_age_of=(person_id, age)
        ),
        path,
    )
