"""Canonical writer for ``.glidepath.json`` (roadmap 6.2; planning §4.5).

Deterministic output for clean diffs: ``schema_version`` stamped in,
keys sorted, 2-space indent, LF line endings, ``Decimal`` as strings,
ISO-8601 timezone-aware datetimes. Equal documents produce
byte-identical text. Only the user's assumption *overrides* are
written; defaults re-resolve on load against shipped region data.
"""

import json
from typing import TYPE_CHECKING

from glidepath.core import (
    AssumptionTarget,
    ContributionSchedule,
    DBPension,
    Household,
    Person,
    PlannedOutflow,
    Scenario,
    SpendingPlan,
    StatePensionRecord,
    Wrapper,
)
from glidepath.persistence.document import SCHEMA_VERSION, PlanDocument
from glidepath.persistence.values import (
    ANNUITY_BASIS_TOKENS,
    ANNUITY_TYPE_TOKENS,
    LIFE_STAGE_TOKENS,
    RELIEF_MECHANIC_TOKENS,
    REVALUATION_REFERENCE_TOKENS,
    SEX_TOKENS,
    encode_value,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import date
    from decimal import Decimal
    from pathlib import Path

    from glidepath.core import (
        AnnuityPurchase,
        AssetAllocation,
        Decision,
        Fact,
        FeeSchedule,
        GlidePathConfig,
        LifeStage,
        Money,
        Override,
        RevaluationBasis,
        Sex,
    )
    from glidepath.persistence.document import AssumptionOverride


def dumps_plan(document: PlanDocument) -> str:
    """The document as canonical ``.glidepath.json`` text (planning §4.5).

    Raises:
        PersistenceError: If an override value's type is outside the
            persistable vocabulary.
    """
    payload = _document_payload(document)
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def save_plan(document: PlanDocument, path: Path) -> None:
    """Write the document to ``path`` in canonical form.

    ``newline=""`` disables platform newline translation so the file
    carries LF endings on every platform (planning §4.5).
    """
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(dumps_plan(document))


def _document_payload(document: PlanDocument) -> dict[str, object]:
    """The whole document as a JSON-ready payload."""
    return {
        "schema_version": SCHEMA_VERSION,
        "region": document.region,
        "assumptions_resolved_against": document.assumptions_resolved_against,
        "household": _household(document.household),
        "assumption_overrides": [
            _assumption_override(override, f"assumption_overrides[{index}]")
            for index, override in enumerate(document.assumption_overrides)
        ],
        "scenarios": [
            _scenario(scenario, f"scenarios[{index}]")
            for index, scenario in enumerate(document.scenarios)
        ],
    }


def _assumption_override(override: AssumptionOverride, path: str) -> dict[str, object]:
    """One stored assumption override."""
    return {
        "key": override.key.value,
        "recorded_on": override.recorded_on.isoformat(),
        "source": override.source,
        "value": encode_value(override.value, f"{path}.value"),
    }


def _scenario(scenario: Scenario, path: str) -> dict[str, object]:
    """One named what-if with its override deltas."""
    return {
        "name": scenario.name,
        "note": scenario.note,
        "overrides": [
            _override(override, f"{path}.overrides[{index}]")
            for index, override in enumerate(scenario.overrides)
        ],
    }


def _override(override: Override, path: str) -> dict[str, object]:
    """One scenario override: its target and replacement value."""
    target = override.target
    if isinstance(target, AssumptionTarget):
        target_payload: dict[str, object] = {
            "kind": "assumption",
            "key": target.key.value,
        }
    else:
        target_payload = {
            "kind": "decision",
            "entity_id": str(target.entity_id),
            "field_path": target.field_path,
        }
    return {
        "note": override.note,
        "target": target_payload,
        "value": encode_value(override.value, f"{path}.value"),
    }


def _household(household: Household) -> dict[str, object]:
    """The household: persons plus shared economics."""
    return {
        "persons": [_person(person) for person in household.persons],
        "planned_outflows": [
            _planned_outflow(outflow) for outflow in household.planned_outflows
        ],
        "spending": _optional(household.spending, _spending_plan),
    }


def _person(person: Person) -> dict[str, object]:
    """One person and everything that hangs off them."""
    return {
        "id": str(person.id),
        "date_of_birth": _fact(person.date_of_birth, _date_value),
        "target_retirement_age": _decision(person.target_retirement_age, _int_value),
        "tax_residency": str(person.tax_residency),
        "sex_for_longevity": _optional_fact(person.sex_for_longevity, _sex_value),
        "employment_income": _optional_fact(person.employment_income, _money_value),
        "mpaa_triggered_on": _optional_fact(person.mpaa_triggered_on, _date_value),
        "lsa_used": _optional_fact(person.lsa_used, _money_value),
        "wrappers": [_wrapper(wrapper) for wrapper in person.wrappers],
        "db_pensions": [_db_pension(pension) for pension in person.db_pensions],
        "annuity_purchases": [
            _annuity_purchase(purchase) for purchase in person.annuity_purchases
        ],
        "state_pension": _optional(person.state_pension, _state_pension),
        "glide_path": _optional(person.glide_path, _glide_path),
    }


def _wrapper(wrapper: Wrapper) -> dict[str, object]:
    """One account, of an opaque region-defined kind."""
    return {
        "id": str(wrapper.id),
        "kind": str(wrapper.kind),
        "balance": _fact(wrapper.balance, _money_value),
        "crystallised_balance": _optional_fact(
            wrapper.crystallised_balance, _money_value
        ),
        "contributions": _optional(wrapper.contributions, _contribution_schedule),
        "allocation": _optional(wrapper.allocation, _allocation),
        "fees": _optional(wrapper.fees, _fee_schedule),
    }


def _contribution_schedule(schedule: ContributionSchedule) -> dict[str, object]:
    """One wrapper's planned annual contributions."""
    mechanic = schedule.relief_mechanic
    escalation = schedule.escalation
    return {
        "employee_amount": _decision(schedule.employee_amount, _money_value),
        "employer_amount": _optional_fact(schedule.employer_amount, _money_value),
        "relief_mechanic": (
            None if mechanic is None else RELIEF_MECHANIC_TOKENS.token(mechanic)
        ),
        "escalation": None if escalation is None else escalation.value,
    }


def _allocation(allocation: AssetAllocation) -> dict[str, object]:
    """Portfolio weights over the three priced asset classes."""
    return {
        "equity": str(allocation.equity),
        "bonds": str(allocation.bonds),
        "cash": str(allocation.cash),
    }


def _fee_schedule(fees: FeeSchedule) -> dict[str, object]:
    """One wrapper's annual percentage fees."""
    return {"platform": str(fees.platform.value), "fund": str(fees.fund.value)}


def _db_pension(pension: DBPension) -> dict[str, object]:
    """One deferred DB entitlement's scheme facts and choices."""
    return {
        "id": str(pension.id),
        "accrued_annual_pension": _fact(pension.accrued_annual_pension, _money_value),
        "statement_date": pension.statement_date.isoformat(),
        "normal_pension_age": _fact(pension.normal_pension_age, _int_value),
        "revaluation_basis": _revaluation_basis(pension.revaluation_basis),
        "early_late_factors": _factor_table(pension.early_late_factors.factors),
        "commuted_fraction": _decision(pension.commuted_fraction, _decimal_value),
        "commutation_factor": _optional_fact(
            pension.commutation_factor, _decimal_value
        ),
        "taken_at_age": _optional_decision(pension.taken_at_age, _int_value),
    }


def _revaluation_basis(basis: RevaluationBasis) -> dict[str, object]:
    """How a DB entitlement revalues."""
    return {
        "reference": REVALUATION_REFERENCE_TOKENS.token(basis.reference),
        "cap": None if basis.cap is None else str(basis.cap.value),
        "fixed_rate": None if basis.fixed_rate is None else str(basis.fixed_rate.value),
    }


def _factor_table(factors: Mapping[int, Decimal]) -> dict[str, str]:
    """A whole-year-age → factor table, ages as JSON object keys."""
    return {str(age): str(factor) for age, factor in factors.items()}


def _annuity_purchase(purchase: AnnuityPurchase) -> dict[str, object]:
    """One planned annuity purchase — wholly a decision record."""
    return {
        "id": str(purchase.id),
        "at_age": _decision(purchase.at_age, _int_value),
        "fraction_of_pot": _decision(purchase.fraction_of_pot, _decimal_value),
        "annuity_type": ANNUITY_TYPE_TOKENS.token(purchase.annuity_type),
        "basis": ANNUITY_BASIS_TOKENS.token(purchase.basis),
    }


def _state_pension(record: StatePensionRecord) -> dict[str, object]:
    """One person's state pension position."""
    return {
        "forecast_weekly_amount": _optional_fact(
            record.forecast_weekly_amount, _money_value
        ),
        "protected_payment": _optional_fact(record.protected_payment, _money_value),
        "ni_record_start": _optional_fact(record.ni_record_start, _date_value),
        "qualifying_years": _optional_fact(record.qualifying_years, _int_value),
        "planned_extra_years": _decision(record.planned_extra_years, _int_value),
        "deferral_years": _decision(record.deferral_years, _decimal_value),
    }


def _glide_path(config: GlidePathConfig) -> dict[str, object]:
    """A person's own glide path: the factor-table knots in order."""
    return {
        "points": [
            {
                "years_to_retirement": point.years_to_retirement,
                "allocation": _allocation(point.allocation),
            }
            for point in config.points
        ]
    }


def _spending_plan(spending: SpendingPlan) -> dict[str, object]:
    """The household's retirement spending need."""
    multipliers = spending.stage_multipliers
    return {
        "annual_spending_real": _fact(spending.annual_spending_real, _money_value),
        "stage_multipliers": (
            None if multipliers is None else _stage_multipliers(multipliers)
        ),
    }


def _stage_multipliers(multipliers: Mapping[LifeStage, Decimal]) -> dict[str, str]:
    """Per-life-stage spending multipliers, stages as stable tokens."""
    return {
        LIFE_STAGE_TOKENS.token(stage): str(value)
        for stage, value in multipliers.items()
    }


def _planned_outflow(outflow: PlannedOutflow) -> dict[str, object]:
    """One dated one-off outflow."""
    person_id, age = outflow.at_age_of
    return {
        "id": str(outflow.id),
        "label": outflow.label,
        "amount_real": _decision(outflow.amount_real, _money_value),
        "at_age_of": {"person_id": str(person_id), "age": age},
    }


def _fact[T](fact: Fact[T], value: Callable[[T], object]) -> dict[str, object]:
    """A user-stated fact with its provenance fields."""
    return {
        "value": value(fact.value),
        "as_of": fact.as_of.isoformat(),
        "recorded_on": fact.recorded_on.isoformat(),
        "note": fact.note,
    }


def _optional_fact[T](
    fact: Fact[T] | None, value: Callable[[T], object]
) -> dict[str, object] | None:
    """A fact the schema allows to be absent."""
    return None if fact is None else _fact(fact, value)


def _decision[T](
    decision: Decision[T], value: Callable[[T], object]
) -> dict[str, object]:
    """A user choice with its provenance fields."""
    return {
        "value": value(decision.value),
        "recorded_on": decision.recorded_on.isoformat(),
        "note": decision.note,
    }


def _optional_decision[T](
    decision: Decision[T] | None, value: Callable[[T], object]
) -> dict[str, object] | None:
    """A decision the schema allows to be absent."""
    return None if decision is None else _decision(decision, value)


def _optional[T](
    value: T | None, encode: Callable[[T], dict[str, object]]
) -> dict[str, object] | None:
    """An optional nested record."""
    return None if value is None else encode(value)


def _money_value(money: Money) -> str:
    """A monetary amount as its exact decimal string."""
    return str(money.amount)


def _decimal_value(value: Decimal) -> str:
    """A decimal figure as its exact string."""
    return str(value)


def _int_value(value: int) -> int:
    """A whole number, natively JSON."""
    return value


def _date_value(value: date) -> str:
    """A calendar date in ISO-8601 form."""
    return value.isoformat()


def _sex_value(value: Sex) -> str:
    """The longevity-default sex as its stable token."""
    return SEX_TOKENS.token(value)
