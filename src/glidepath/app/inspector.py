"""The stated-vs-assumed inspector (roadmap 8.3; planning §1, §5.1).

Renders ``ProjectionResult.provenance`` — the engine's own record of
what the run rested on — as three columns: facts the user stated,
assumptions used (default vs overridden, with source and date), and
decisions in effect. The full shipped assumption catalogue follows the
read list so every default is overridable in place even before it is
read by a run.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from glidepath.app.display import format_date, format_recorded, format_value
from glidepath.core import Assumption, AssumptionKey, Household, Provenance

if TYPE_CHECKING:
    from collections.abc import Mapping

    from glidepath.app.plan import PlanState

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
class DecisionRow:
    """One decision in effect, formatted for display."""

    label: str
    value: str
    recorded: str
    note: str


@dataclass(frozen=True)
class AssumptionRow:
    """One assumption with its full §1 provenance payload."""

    key: str
    description: str
    value: str
    default_value: str
    status: str
    usage: str
    source: str
    recorded: str
    editable: bool


@dataclass(frozen=True)
class InspectorViewModel:
    """The whole stated-vs-assumed screen (roadmap 8.3)."""

    title: str
    facts_heading: str
    facts_columns: tuple[str, ...]
    facts: tuple[FactRow, ...]
    assumptions_heading: str
    assumptions_columns: tuple[str, ...]
    assumptions: tuple[AssumptionRow, ...]
    decisions_heading: str
    decisions_columns: tuple[str, ...]
    decisions: tuple[DecisionRow, ...]
    summary: str
    override_title: str
    override_prompt: str
    not_editable_message: str


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
    return AssumptionRow(
        key=str(assumption.key.value),
        description=assumption.description,
        value=format_value(assumption.value),
        default_value=format_value(assumption.default_value),
        status=_STATUS_LABELS[assumption.provenance],
        usage=usage,
        source=assumption.source,
        recorded=format_recorded(assumption.recorded_on),
        editable=isinstance(assumption.value, Decimal | int),
    )


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
    return InspectorViewModel(
        title="Stated vs assumed",
        facts_heading="Facts you stated",
        facts_columns=("Fact", "Value", "As of", "Recorded"),
        facts=facts,
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
        summary=_summary(state),
        override_title="Override assumption",
        override_prompt="New value (blank restores the shipped default):",
        not_editable_message=(
            "This assumption is a structured table; it cannot be edited in place yet."
        ),
    )
