"""The stated-vs-assumed inspector (roadmap 8.3; planning §1, §5.1).

Renders ``ProjectionResult.provenance`` — the engine's own record of
what the run rested on — as three columns: facts the user stated,
assumptions used (default vs overridden, with source and date), and
decisions in effect. The full shipped assumption catalogue follows the
read list so every default is overridable in place even before it is
read by a run. A fourth, entity-level section surfaces the plan's
*structural* inputs — fields that drive results but are neither
``Fact`` nor ``Decision`` wrapped, so they never appear as provenance
rows (planning §5.1, issue #70).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from glidepath.app.display import (
    format_date,
    format_money,
    format_recorded,
    format_value,
)
from glidepath.app.tables import table_edit_text
from glidepath.core import (
    Assumption,
    AssumptionKey,
    Household,
    Provenance,
    RevaluationReference,
)

if TYPE_CHECKING:
    from decimal import Decimal

    from glidepath.app.plan import PlanState
    from glidepath.core import (
        AnnuityPurchase,
        AssetAllocation,
        DBPension,
        FactorTable,
        FeeSchedule,
        GlidePathConfig,
        LifeStage,
        Person,
        RevaluationBasis,
        Wrapper,
    )

_LABEL_PATTERN = re.compile(r"^(?P<kind>[a-z_]+)\[(?P<id>[^\]]+)\]\.(?P<path>.+)$")

_STATUS_LABELS: Mapping[Provenance, str] = {
    Provenance.DEFAULT_ASSUMPTION: "Shipped default",
    Provenance.USER_OVERRIDE: "Your override",
    Provenance.SCENARIO_OVERRIDE: "Scenario override",
}

_WRAPPER_KIND_NAMES: Mapping[str, str] = {
    "uk.workplace_dc": "Workplace DC",
    "uk.sipp": "SIPP",
    "uk.isa": "ISA",
}

_RESIDENCY_NAMES: Mapping[str, str] = {
    "uk.ruk": "UK (England, Wales, or Northern Ireland)",
    "uk.scotland": "UK (Scotland)",
}

_USED_LABEL = "Used in this projection"
_UNUSED_LABEL = "Not used by this projection"
_NO_RUN_LABEL = "No projection yet"
_REGION_BUILD_LABEL = "Applied at region build (see the run manifest)"

_REGION_BUILD_KEYS = frozenset(
    {
        AssumptionKey.POLICY_STATE_PENSION_UPRATING,
        AssumptionKey.POLICY_TAX_FUTURE_YEARS,
    }
)
"""Keys read while building the region, before the run's read recorder
exists — their effect is identified through the region data version,
not the assumption read list (planning §5.1)."""


@dataclass(frozen=True)
class FactRow:
    """One stated fact, formatted for display."""

    label: str
    value: str
    as_of: str
    recorded: str
    note: str


@dataclass(frozen=True)
class RollForwardRow:
    """One wrapper balance rolled forward to the run start (§4.8).

    The stated fact is unchanged; this row shows the estimate the
    engine layered on it — the statement value, its date, the whole
    months rolled, and the opening value the projection actually used.
    """

    label: str
    stated: str
    as_of: str
    months: str
    opening: str


@dataclass(frozen=True)
class DecisionRow:
    """One decision in effect, formatted for display."""

    label: str
    value: str
    recorded: str
    note: str


@dataclass(frozen=True)
class AssumptionRow:
    """One assumption with its full §1 provenance payload.

    ``structured`` marks a table-valued row; its ``edit_text`` is the
    round-trippable multi-line text form, while a scalar's is simply
    its value (issue #71).
    """

    key: str
    description: str
    value: str
    default_value: str
    status: str
    usage: str
    source: str
    recorded: str
    structured: bool
    edit_text: str


@dataclass(frozen=True)
class StructureRow:
    """One structural plan input, surfaced entity-level (§5.1, issue #70)."""

    entity: str
    setting: str
    value: str


@dataclass(frozen=True)
class InspectorViewModel:
    """The whole stated-vs-assumed screen (roadmap 8.3)."""

    title: str
    facts_heading: str
    facts_columns: tuple[str, ...]
    facts: tuple[FactRow, ...]
    roll_forwards_heading: str
    roll_forwards_columns: tuple[str, ...]
    roll_forwards: tuple[RollForwardRow, ...]
    assumptions_heading: str
    assumptions_columns: tuple[str, ...]
    assumptions: tuple[AssumptionRow, ...]
    decisions_heading: str
    decisions_columns: tuple[str, ...]
    decisions: tuple[DecisionRow, ...]
    structure_heading: str
    structure_columns: tuple[str, ...]
    structure: tuple[StructureRow, ...]
    summary: str
    override_title: str
    override_prompt: str
    table_override_prompt: str


def _capitalised(text: str) -> str:
    """The text with just its first letter upper-cased."""
    return text[:1].upper() + text[1:]


def _pretty_path(path: str) -> str:
    """A dotted field path as display text."""
    return " / ".join(segment.replace("_", " ") for segment in path.split("."))


def _entity_names(household: Household | None) -> dict[str, str]:
    """Friendly names for the entity ids provenance labels address."""
    if household is None:
        return {}
    names: dict[str, str] = {}
    for person in household.persons:
        names[str(person.id)] = "You"
        for number, wrapper in enumerate(person.wrappers, start=1):
            kind = _WRAPPER_KIND_NAMES.get(str(wrapper.kind), str(wrapper.kind))
            names[str(wrapper.id)] = f"Wrapper {number} ({kind})"
        for number, pension in enumerate(person.db_pensions, start=1):
            names[str(pension.id)] = f"DB pension {number}"
        for number, purchase in enumerate(person.annuity_purchases, start=1):
            names[str(purchase.id)] = f"Annuity purchase {number}"
    for outflow in household.planned_outflows:
        names[str(outflow.id)] = outflow.label
    return names


def _display_label(label: str, names: Mapping[str, str]) -> str:
    """A provenance label as display text, entity ids named for humans."""
    match = _LABEL_PATTERN.match(label)
    if match is None:
        return _capitalised(_pretty_path(label))
    name = names.get(match["id"], _capitalised(match["kind"].replace("_", " ")))
    return f"{name} — {_pretty_path(match['path'])}"


def _assumption_row(assumption: Assumption[Any], usage: str) -> AssumptionRow:
    """One assumption as its display row."""
    structured = isinstance(assumption.value, Mapping)
    return AssumptionRow(
        key=str(assumption.key.value),
        description=assumption.description,
        value=format_value(assumption.value),
        default_value=format_value(assumption.default_value),
        status=_STATUS_LABELS[assumption.provenance],
        usage=usage,
        source=assumption.source,
        recorded=format_recorded(assumption.recorded_on),
        structured=structured,
        edit_text=(
            table_edit_text(assumption.value)
            if structured
            else format_value(assumption.value)
        ),
    )


def _glide_path_text(config: GlidePathConfig | None) -> str:
    """A person's glide-path source as display text, knots included."""
    if config is None:
        return "Default shape assumption (glidepath.default_shape)"
    knots = "; ".join(
        f"{point.years_to_retirement}y out: {_allocation_text(point.allocation)}"
        for point in config.points
    )
    return f"Custom — {knots}"


def _allocation_text(allocation: AssetAllocation | None) -> str:
    """A wrapper's asset-allocation source as display text."""
    if allocation is None:
        return "From the glide path"
    return (
        f"Equity {allocation.equity} / bonds {allocation.bonds}"
        f" / cash {allocation.cash}"
    )


def _fees_text(fees: FeeSchedule | None) -> str:
    """A wrapper's fee source as display text."""
    if fees is None:
        return "Shipped fee assumptions (fees.platform, fees.fund)"
    return f"Platform {fees.platform.value} / fund {fees.fund.value}"


def _revaluation_text(basis: RevaluationBasis) -> str:
    """A DB scheme's revaluation basis as display text."""
    if basis.fixed_rate is not None:  # FIXED, by the RevaluationBasis invariant
        return f"Fixed {basis.fixed_rate.value} per year"
    if basis.reference is RevaluationReference.CPI:
        if basis.cap is None:
            return "CPI"
        return f"CPI, capped at {basis.cap.value}"
    return "None (frozen in nominal terms)"


def _factors_text(table: FactorTable) -> str:
    """A DB scheme's early/late factor table as display text."""
    if not table.factors:
        return "None (taken at the normal pension age)"
    return "; ".join(
        f"{age}: {factor}" for age, factor in sorted(table.factors.items())
    )


def _stage_multipliers_text(multipliers: Mapping[LifeStage, Decimal] | None) -> str:
    """A spending plan's life-stage multipliers as display text."""
    if not multipliers:
        return "None (flat spending)"
    return "; ".join(
        f"{format_value(stage)}: {multiplier}"
        for stage, multiplier in sorted(
            multipliers.items(), key=lambda item: item[0].value
        )
    )


def _person_structure(person: Person, name: str) -> list[StructureRow]:
    """One person's structural inputs as display rows (issue #70)."""
    residency = str(person.tax_residency)
    return [
        StructureRow(name, "Tax residency", _RESIDENCY_NAMES.get(residency, residency)),
        StructureRow(name, "Glide path", _glide_path_text(person.glide_path)),
    ]


def _wrapper_structure(wrapper: Wrapper, name: str) -> list[StructureRow]:
    """One wrapper's structural inputs as display rows (issue #70)."""
    kind = str(wrapper.kind)
    rows = [StructureRow(name, "Wrapper kind", _WRAPPER_KIND_NAMES.get(kind, kind))]
    schedule = wrapper.contributions
    if schedule is not None:
        relief = (
            format_value(schedule.relief_mechanic)
            if schedule.relief_mechanic is not None
            else "No tax relief"
        )
        rows.append(StructureRow(name, "Contribution relief", relief))
        escalation = (
            str(schedule.escalation.value)
            if schedule.escalation is not None
            else "None (fixed amounts)"
        )
        rows.append(StructureRow(name, "Contribution escalation", escalation))
    rows.append(
        StructureRow(name, "Asset allocation", _allocation_text(wrapper.allocation))
    )
    rows.append(StructureRow(name, "Fees", _fees_text(wrapper.fees)))
    return rows


def _db_structure(pension: DBPension, name: str) -> list[StructureRow]:
    """One DB pension's scheme structure as display rows (issue #70)."""
    return [
        StructureRow(name, "Statement date", format_date(pension.statement_date)),
        StructureRow(name, "Revaluation", _revaluation_text(pension.revaluation_basis)),
        StructureRow(
            name, "Early/late factors", _factors_text(pension.early_late_factors)
        ),
    ]


def _annuity_structure(purchase: AnnuityPurchase, name: str) -> list[StructureRow]:
    """One annuity purchase's structural choices as display rows (issue #70)."""
    return [
        StructureRow(name, "Annuity type", format_value(purchase.annuity_type)),
        StructureRow(name, "Annuity basis", format_value(purchase.basis)),
    ]


def _structure_rows(household: Household | None) -> tuple[StructureRow, ...]:
    """Structural plan inputs, entity-level (planning §5.1, issue #70).

    These fields drive results but are neither ``Fact`` nor
    ``Decision`` wrapped, so they never appear as provenance rows —
    the persisted plan itself is their record, and this section is
    where the UI shows it.
    """
    if household is None:
        return ()
    names = _entity_names(household)
    rows: list[StructureRow] = []
    for person in household.persons:
        rows.extend(_person_structure(person, names[str(person.id)]))
        for wrapper in person.wrappers:
            rows.extend(_wrapper_structure(wrapper, names[str(wrapper.id)]))
        for pension in person.db_pensions:
            rows.extend(_db_structure(pension, names[str(pension.id)]))
        for purchase in person.annuity_purchases:
            rows.extend(_annuity_structure(purchase, names[str(purchase.id)]))
    if household.spending is not None:
        rows.append(
            StructureRow(
                "Household",
                "Spending stages",
                _stage_multipliers_text(household.spending.stage_multipliers),
            )
        )
    for outflow in household.planned_outflows:
        person_id, age = outflow.at_age_of
        rows.append(
            StructureRow(outflow.label, "Due", f"{names[str(person_id)]} at age {age}")
        )
    return tuple(rows)


def _assumption_rows(state: PlanState) -> tuple[AssumptionRow, ...]:
    """Read assumptions in first-read order, then the rest of the catalogue."""
    read = state.result.provenance.assumptions if state.result is not None else ()
    read_keys = {assumption.key for assumption in read}
    has_result = state.result is not None

    def unread_usage(key: AssumptionKey) -> str:
        if not has_result:
            return _NO_RUN_LABEL
        if key in _REGION_BUILD_KEYS:
            return _REGION_BUILD_LABEL
        return _UNUSED_LABEL

    rows = [_assumption_row(assumption, _USED_LABEL) for assumption in read]
    rows.extend(
        _assumption_row(state.assumptions.get(key), unread_usage(key))
        for key in sorted(state.assumptions.keys - read_keys, key=str)
    )
    return tuple(rows)


def _summary(state: PlanState) -> str:
    """The run-manifest line, the failure, or the getting-started hint."""
    if state.run_error is not None:
        return f"The projection failed: {state.run_error}"
    if state.result is not None:
        seed = state.result.provenance.seed
        seed_text = "none (deterministic)" if seed is None else str(seed)
        version = state.result.provenance.region_data_version
        return f"Run manifest — {version}; seed: {seed_text}"
    return (
        "No projection yet: enter your facts and save them. The shipped "
        "default assumptions below apply until you override them."
    )


def build_inspector_view_model(state: PlanState) -> InspectorViewModel:
    """Assemble the stated-vs-assumed screen from the session state.

    Facts and decisions come straight from
    ``ProjectionResult.provenance`` (roadmap 8.3 acceptance); the
    assumptions column carries value, source, date recorded, and
    default-vs-overridden status for every row (planning §1).
    """
    names = _entity_names(state.household)
    provenance = state.result.provenance if state.result is not None else None
    facts = tuple(
        FactRow(
            label=_display_label(labelled.label, names),
            value=format_value(labelled.fact.value),
            as_of=format_date(labelled.fact.as_of),
            recorded=format_recorded(labelled.fact.recorded_on),
            note=labelled.fact.note or "",
        )
        for labelled in (provenance.facts if provenance is not None else ())
    )
    decisions = tuple(
        DecisionRow(
            label=_display_label(labelled.label, names),
            value=format_value(labelled.decision.value),
            recorded=format_recorded(labelled.decision.recorded_on),
            note=labelled.decision.note or "",
        )
        for labelled in (provenance.decisions if provenance is not None else ())
    )
    roll_forwards = tuple(
        RollForwardRow(
            label=_display_label(entry.label, names),
            stated=format_money(entry.stated),
            as_of=format_date(entry.as_of),
            months=str(entry.months),
            opening=format_money(entry.opening),
        )
        for entry in (
            provenance.balance_roll_forwards if provenance is not None else ()
        )
    )
    return InspectorViewModel(
        title="Stated vs assumed",
        facts_heading="Facts you stated",
        facts_columns=("Fact", "Value", "As of", "Recorded"),
        facts=facts,
        roll_forwards_heading="Balances rolled forward to today",
        roll_forwards_columns=("Balance", "Stated", "As of", "Months", "Value today"),
        roll_forwards=roll_forwards,
        assumptions_heading="Assumptions used",
        assumptions_columns=(
            "Assumption",
            "Value",
            "Default",
            "Status",
            "Used",
            "Source",
            "Recorded",
        ),
        assumptions=_assumption_rows(state),
        decisions_heading="Your choices in effect",
        decisions_columns=("Choice", "Value", "Recorded"),
        decisions=decisions,
        structure_heading="Plan structure",
        structure_columns=("Entity", "Setting", "Value"),
        structure=_structure_rows(state.household),
        summary=_summary(state),
        override_title="Override assumption",
        override_prompt="New value (blank restores the shipped default):",
        table_override_prompt=(
            "New value — one 'key = value' line per figure, dotted keys for"
            " nested tables (blank restores the shipped default):"
        ),
    )
