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

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from glidepath.app.display import (
    format_assumption_key,
    format_date,
    format_money,
    format_recorded,
    format_value,
    format_wrapper_kind,
)
from glidepath.app.labels import display_label, entity_names
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

_STATUS_LABELS: Mapping[Provenance, str] = {
    Provenance.DEFAULT_ASSUMPTION: "Shipped default",
    Provenance.USER_OVERRIDE: "Your override",
    Provenance.SCENARIO_OVERRIDE: "Scenario override",
}

_RESIDENCY_NAMES: Mapping[str, str] = {
    "uk.ruk": "UK (England, Wales, or Northern Ireland)",
    "uk.scotland": "UK (Scotland)",
}

_USED_LABEL = "Used"
_UNUSED_LABEL = "Not used"
_NO_RUN_LABEL = "No projection yet"
_REGION_BUILD_LABEL = "Applied at region build"

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

    ``key`` is the stable dotted id (what overrides target); ``label``
    is its human display name and is what screens show. ``structured``
    marks a table-valued row; its ``edit_text`` is the round-trippable
    multi-line text form, while a scalar's is simply its value
    (issue #71).
    """

    key: str
    label: str
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
    summary_detail: str
    override_title: str
    override_prompt: str
    table_override_prompt: str


def _assumption_row(assumption: Assumption[Any], usage: str) -> AssumptionRow:
    """One assumption as its display row.

    The default column repeats the value verbatim whenever nothing is
    overridden (the status column already says "Shipped default"), so
    it renders as a dash unless the two differ — compared on the
    underlying values, since long table values format to a truncated
    text that can collide.
    """
    structured = isinstance(assumption.value, Mapping)
    value_text = format_value(assumption.value)
    overridden = assumption.value != assumption.default_value
    return AssumptionRow(
        key=str(assumption.key.value),
        label=format_assumption_key(assumption.key.value),
        description=assumption.description,
        value=value_text,
        default_value=format_value(assumption.default_value) if overridden else "—",
        status=_STATUS_LABELS[assumption.provenance],
        usage=usage,
        source=assumption.source,
        recorded=format_recorded(assumption.recorded_on),
        structured=structured,
        edit_text=table_edit_text(assumption.value) if structured else value_text,
    )


def _glide_path_text(config: GlidePathConfig | None) -> str:
    """A person's glide-path source as display text, knots included."""
    if config is None:
        return "Default glide path shape assumption"
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


def _escalation_text(escalation: AssumptionKey | None) -> str:
    """A contribution schedule's escalation as display text."""
    if escalation is None:
        return "None (fixed amounts)"
    if escalation is AssumptionKey.EARNINGS_GROWTH_REAL:
        return "Grows with earnings"
    return f"Grows with the {format_assumption_key(escalation.value)} assumption"


def _fees_text(fees: FeeSchedule | None) -> str:
    """A wrapper's fee source as display text."""
    if fees is None:
        return "Shipped platform and fund fee assumptions"
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
    rows = [StructureRow(name, "Wrapper kind", format_wrapper_kind(wrapper.kind))]
    schedule = wrapper.contributions
    if schedule is not None:
        relief = (
            format_value(schedule.relief_mechanic)
            if schedule.relief_mechanic is not None
            else "No tax relief"
        )
        rows.append(StructureRow(name, "Contribution relief", relief))
        rows.append(
            StructureRow(
                name, "Contribution escalation", _escalation_text(schedule.escalation)
            )
        )
    rows.append(
        StructureRow(name, "Asset allocation", _allocation_text(wrapper.allocation))
    )
    rows.append(StructureRow(name, "Fees", _fees_text(wrapper.fees)))
    return rows


def _db_structure(pension: DBPension, name: str) -> list[StructureRow]:
    """One DB pension's scheme structure as display rows (issue #70).

    Membership is structural — active accrual's rate and salary are
    ``Fact``-wrapped and appear as provenance rows instead (9.6).
    """
    membership = (
        "Deferred" if pension.active_membership is None else "Active (accruing)"
    )
    return [
        StructureRow(name, "Membership", membership),
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
    names = entity_names(household)
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


def _summary_lines(state: PlanState) -> tuple[str, str]:
    """The human summary line plus the full run-manifest detail.

    The manifest string (data-file digests, policy parameters, seed) is
    load-bearing §4.6 provenance but not reading matter, so the summary
    stays human and the exact manifest rides along as ``summary_detail``
    for the shell to reveal on demand (a tooltip in the Qt shell).
    """
    if state.run_error is not None:
        return f"The projection failed: {state.run_error}", ""
    if state.result is None:
        return (
            "No projection yet: enter your facts and save them. The shipped "
            "default assumptions below apply until you override them."
        ), ""
    seed = state.result.provenance.seed
    seed_text = "none (deterministic)" if seed is None else str(seed)
    run_kind = "deterministic run" if seed is None else f"Monte Carlo run, seed {seed}"
    years = len(state.result.snapshots)
    start = format_date(state.result.config.today)
    summary = (
        f"Projected across {years} tax years from {start} ({run_kind}) — the "
        "first and last may be partial. Hover for the run manifest: the "
        "exact rule data and policies behind these numbers."
    )
    version = state.result.provenance.region_data_version
    return summary, f"Run manifest — {version}; seed: {seed_text}"


def build_inspector_view_model(state: PlanState) -> InspectorViewModel:
    """Assemble the stated-vs-assumed screen from the session state.

    Facts and decisions come straight from
    ``ProjectionResult.provenance`` (roadmap 8.3 acceptance); the
    assumptions column carries value, source, date recorded, and
    default-vs-overridden status for every row (planning §1).
    """
    names = entity_names(state.household)
    provenance = state.result.provenance if state.result is not None else None
    facts = tuple(
        FactRow(
            label=display_label(labelled.label, names),
            value=format_value(labelled.fact.value),
            as_of=format_date(labelled.fact.as_of),
            recorded=format_recorded(labelled.fact.recorded_on),
            note=labelled.fact.note or "",
        )
        for labelled in (provenance.facts if provenance is not None else ())
    )
    decisions = tuple(
        DecisionRow(
            label=display_label(labelled.label, names),
            value=format_value(labelled.decision.value),
            recorded=format_recorded(labelled.decision.recorded_on),
            note=labelled.decision.note or "",
        )
        for labelled in (provenance.decisions if provenance is not None else ())
    )
    roll_forwards = tuple(
        RollForwardRow(
            label=display_label(entry.label, names),
            stated=format_money(entry.stated),
            as_of=format_date(entry.as_of),
            months=str(entry.months),
            opening=format_money(entry.opening),
        )
        for entry in (
            provenance.balance_roll_forwards if provenance is not None else ()
        )
    )
    summary, summary_detail = _summary_lines(state)
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
        summary=summary,
        summary_detail=summary_detail,
        override_title="Override assumption",
        override_prompt="New value (blank restores the shipped default):",
        table_override_prompt=(
            "New value — one 'key = value' line per figure, dotted keys for"
            " nested tables (blank restores the shipped default):"
        ),
    )
