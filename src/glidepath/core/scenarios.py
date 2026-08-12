"""Scenario/Override model and resolution (roadmap 6.1; planning §4.3).

A scenario is a named list of typed :class:`Override` records over a base
plan — stored as deltas, so a scenario *is* its own diff and base-fact
corrections propagate to every what-if automatically. Targets are either
assumption keys or **decision variables** addressed by stable entity id +
field path, so overrides survive reordering and insertion.

The facts/decisions boundary is type-enforced (planning §4.3): the
field-path grammar names only decision variables — ``Decision[T]``
fields, plus the annuity purchase's plain enum choices (the record is
wholly a decision, §5.1) — and has no way to name a ``Fact``, so
nothing user-stated can be silently replaced. An override whose target
cannot be addressed in the plan (the entity is gone, the field path is
unknown, or the optional record it lives on is absent) is an *orphan*:
it flags the scenario invalid (:func:`scenario_orphans`) without
breaking anything else, until the user removes or retargets it.

Resolution (:func:`resolve_scenario`) computes effective inputs =
base ⊕ overrides: overridden assumptions carry ``SCENARIO_OVERRIDE``
provenance in-type; overridden decisions keep the base's ``recorded_on``
(resolution is pure — no clock reads, planning §4.6) and the resolution's
``applied`` record lists every override at its stable label, which is
where a decision override's ``SCENARIO_OVERRIDE`` provenance lives.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from glidepath.core.provenance import (
    Assumption,
    AssumptionKey,
    AssumptionSet,
    Decision,
    Provenance,
)

if TYPE_CHECKING:
    from glidepath.core.annuities import AnnuityPurchase
    from glidepath.core.entities import EntityId, Household, Person, PlannedOutflow
    from glidepath.core.pensions import DBPension
    from glidepath.core.wrappers import Wrapper


class ScenarioError(ValueError):
    """A scenario that cannot be resolved against its base plan."""


@dataclass(frozen=True, slots=True)
class AssumptionTarget:
    """An override target naming an assumption by its stable key."""

    key: AssumptionKey


@dataclass(frozen=True, slots=True)
class DecisionTarget:
    """An override target naming a decision variable (planning §4.3).

    ``entity_id`` is the persisted id of the person, wrapper, DB
    pension, annuity purchase, or planned outflow the decision lives
    on; ``field_path`` is the dotted path of the ``Decision`` field on
    that entity. The addressable paths are exactly the decision
    whitelist:

    - person: ``target_retirement_age``,
      ``state_pension.deferral_years``
    - wrapper: ``contributions.employee_amount``
    - DB pension: ``taken_at_age``, ``commuted_fraction``,
      ``active_membership.active_until_age``
    - annuity purchase: ``at_age``, ``fraction_of_pot``,
      ``annuity_type``, ``basis``
    - planned outflow: ``amount_real``

    Most paths name ``Decision[T]`` fields; the annuity purchase's
    ``annuity_type`` and ``basis`` are plain enum fields, addressable
    because the record is wholly a decision (planning §5.1). The
    household's ``claim_marriage_allowance`` decision (planning §4.11)
    is not addressable: the household carries no ``EntityId``, and a
    what-if over a bounded relief reads directly off the base run —
    a recorded 9.32 limitation.
    """

    entity_id: EntityId
    field_path: str

    def __post_init__(self) -> None:
        """Reject an empty field path."""
        if not self.field_path:
            msg = "DecisionTarget.field_path must be non-empty"
            raise ValueError(msg)


type OverrideTarget = AssumptionTarget | DecisionTarget
"""What an override points at: an assumption key or a decision variable."""


@dataclass(frozen=True, slots=True)
class Override:
    """One scenario delta: replace the target's value (planning §4.3).

    ``value`` must have exactly the runtime type of the base value it
    replaces — checked at resolution, so a drifted override fails
    loudly rather than corrupting a run. ``note`` says why ("what if I
    retire at 60") and becomes the resolved decision's note.
    """

    target: OverrideTarget
    value: Any
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Scenario:
    """A named what-if: a list of overrides over the base plan (§4.3)."""

    name: str
    overrides: tuple[Override, ...] = ()
    note: str | None = None

    def __post_init__(self) -> None:
        """Reject an empty name and ambiguous duplicate targets."""
        if not self.name:
            msg = "Scenario.name must be non-empty"
            raise ValueError(msg)
        targets = [override.target for override in self.overrides]
        if len(set(targets)) != len(targets):
            msg = f"scenario {self.name!r} has two overrides on one target"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class AppliedOverride:
    """One override applied during resolution, at its stable label.

    The label uses the provenance grammar of
    :func:`~glidepath.core.results.collect_plan_decisions` (e.g.
    ``person[<id>].target_retirement_age``) or the bare dotted
    assumption key. Every applied override carries
    ``SCENARIO_OVERRIDE`` provenance (planning §4.3) — for assumptions
    it is also stamped in-type on the resolved
    :class:`~glidepath.core.provenance.Assumption`.
    """

    label: str
    override: Override

    @property
    def provenance(self) -> Provenance:
        """Always ``SCENARIO_OVERRIDE`` (planning §4.3)."""
        return Provenance.SCENARIO_OVERRIDE


@dataclass(frozen=True, slots=True)
class ScenarioResolution:
    """Effective inputs for one scenario: base ⊕ overrides (§4.3).

    ``household`` and ``assumptions`` are the resolved inputs to hand
    to the engine; ``applied`` lists every override at its stable
    label, in the scenario's own order.
    """

    scenario: Scenario
    household: Household
    assumptions: AssumptionSet
    applied: tuple[AppliedOverride, ...]


@dataclass(frozen=True, slots=True)
class DecisionTargetInfo:
    """One addressable decision target: its stable label and current value.

    ``value`` is the decision's bare value (the ``Decision`` wrapper
    unwrapped; the annuity purchase's enum choices are already bare) —
    what an override on this target would replace. UIs list these to
    offer exactly the decision whitelist (planning §4.3) without
    re-deriving it.
    """

    target: DecisionTarget
    label: str
    value: Any


def decision_target_catalogue(household: Household) -> tuple[DecisionTargetInfo, ...]:
    """Every addressable decision target, with its label and current value.

    The same whitelist :func:`scenario_orphans` checks against, in the
    same order — planned outflows first, then each person's targets.
    """
    entities = _entities_by_id(household)
    infos = []
    for (entity_id, field_path), label in _decision_target_labels(household).items():
        value: Any = entities[entity_id]
        for segment in field_path.split("."):
            value = getattr(value, segment)
        if isinstance(value, Decision):
            value = value.value
        infos.append(
            DecisionTargetInfo(
                target=DecisionTarget(entity_id=entity_id, field_path=field_path),
                label=label,
                value=value,
            )
        )
    return tuple(infos)


def _entities_by_id(household: Household) -> dict[EntityId, Any]:
    """Every override-addressable entity in the household, by id."""
    entities: dict[EntityId, Any] = {
        outflow.id: outflow for outflow in household.planned_outflows
    }
    for person in household.persons:
        entities[person.id] = person
        entities.update({wrapper.id: wrapper for wrapper in person.wrappers})
        entities.update({pension.id: pension for pension in person.db_pensions})
        entities.update(
            {purchase.id: purchase for purchase in person.annuity_purchases}
        )
    return entities


def scenario_orphans(
    scenario: Scenario, household: Household, assumptions: AssumptionSet
) -> tuple[Override, ...]:
    """The scenario's overrides whose targets cannot be addressed.

    An orphan's target entity no longer exists, its field path is not
    a whitelisted decision on that entity, the optional record it
    lives on is absent (no state pension record, no contribution
    schedule, no ``taken_at_age`` decision), or its assumption key has
    no registered base assumption. Orphans flag the scenario invalid
    without breaking file load (planning §4.3): callers surface them
    for the user to remove or retarget.
    """
    decision_labels = _decision_target_labels(household)
    orphans = []
    for override in scenario.overrides:
        target = override.target
        if isinstance(target, AssumptionTarget):
            if target.key not in assumptions:
                orphans.append(override)
        elif (target.entity_id, target.field_path) not in decision_labels:
            orphans.append(override)
    return tuple(orphans)


def is_scenario_valid(
    scenario: Scenario, household: Household, assumptions: AssumptionSet
) -> bool:
    """Whether every override target is addressable (no orphans)."""
    return not scenario_orphans(scenario, household, assumptions)


def resolve_scenario(
    household: Household, assumptions: AssumptionSet, scenario: Scenario
) -> ScenarioResolution:
    """Compute the scenario's effective inputs: base ⊕ overrides (§4.3).

    Pure (planning §4.6): resolved decisions keep the base value's
    ``recorded_on``; resolved assumptions carry ``SCENARIO_OVERRIDE``
    provenance with the base's default, source, and description intact.
    The base household and assumption set are never mutated.

    Raises:
        ScenarioError: If any override is an orphan or its value's
            runtime type differs from the base value it replaces.
        ValueError: If an override value violates the target entity's
            own invariants (e.g. a negative outflow amount).
    """
    orphans = scenario_orphans(scenario, household, assumptions)
    if orphans:
        labels = ", ".join(_target_description(o.target) for o in orphans)
        msg = f"scenario {scenario.name!r} has orphaned overrides: {labels}"
        raise ScenarioError(msg)
    decision_labels = _decision_target_labels(household)
    picks: dict[tuple[EntityId, str], Override] = {}
    applied = []
    for override in scenario.overrides:
        target = override.target
        if isinstance(target, AssumptionTarget):
            applied.append(AppliedOverride(label=target.key.value, override=override))
        else:
            key = (target.entity_id, target.field_path)
            picks[key] = override
            applied.append(
                AppliedOverride(label=decision_labels[key], override=override)
            )
    resolved_household = _apply_to_household(household, picks)
    resolved_assumptions = _apply_to_assumptions(assumptions, scenario)
    return ScenarioResolution(
        scenario=scenario,
        household=resolved_household,
        assumptions=resolved_assumptions,
        applied=tuple(applied),
    )


def _target_description(target: OverrideTarget) -> str:
    """A human-readable name for an override target in error messages."""
    if isinstance(target, AssumptionTarget):
        return target.key.value
    return f"{target.entity_id}.{target.field_path}"


def _decision_target_labels(household: Household) -> dict[tuple[EntityId, str], str]:
    """Every addressable decision target, mapped to its stable label.

    The keys are ``(entity_id, field_path)`` pairs — entity ids are
    unique across the household (enforced by ``Household``), so a pair
    is unambiguous. Paths through absent optional records are simply
    not addressable and never appear.
    """
    labels: dict[tuple[EntityId, str], str] = {}
    for outflow in household.planned_outflows:
        labels[(outflow.id, "amount_real")] = (
            f"planned_outflow[{outflow.id}].amount_real"
        )
    for person in household.persons:
        labels.update(_person_decision_target_labels(person))
    return labels


def _person_decision_target_labels(person: Person) -> dict[tuple[EntityId, str], str]:
    """One person's addressable decision targets, mapped to labels."""
    labels: dict[tuple[EntityId, str], str] = {}
    labels[(person.id, "target_retirement_age")] = (
        f"person[{person.id}].target_retirement_age"
    )
    if person.state_pension is not None:
        labels[(person.id, "state_pension.deferral_years")] = (
            f"person[{person.id}].state_pension.deferral_years"
        )
    for wrapper in person.wrappers:
        if wrapper.contributions is not None:
            labels[(wrapper.id, "contributions.employee_amount")] = (
                f"wrapper[{wrapper.id}].contributions.employee_amount"
            )
    for pension in person.db_pensions:
        if pension.taken_at_age is not None:
            labels[(pension.id, "taken_at_age")] = (
                f"db_pension[{pension.id}].taken_at_age"
            )
        labels[(pension.id, "commuted_fraction")] = (
            f"db_pension[{pension.id}].commuted_fraction"
        )
        if (
            pension.active_membership is not None
            and pension.active_membership.active_until_age is not None
        ):
            labels[(pension.id, "active_membership.active_until_age")] = (
                f"db_pension[{pension.id}].active_membership.active_until_age"
            )
    for purchase in person.annuity_purchases:
        for field in ("at_age", "fraction_of_pot", "annuity_type", "basis"):
            labels[(purchase.id, field)] = f"annuity_purchase[{purchase.id}].{field}"
    return labels


def _check_value_type(base_value: object, override_value: object, label: str) -> None:
    """Reject an override value shaped unlike the base value it replaces.

    Scalars must match the base's exact runtime type (``bool`` is an
    ``int`` subtype but never a number here, §4.6 spirit). A mapping
    base accepts *any* mapping: shipped structured defaults arrive as
    read-only ``MappingProxyType`` views, while user overrides are
    ordinarily plain dicts, and the structured-assumption parsers
    accept either.

    Raises:
        ScenarioError: If the value's shape does not match.
    """
    if isinstance(base_value, Mapping):
        if isinstance(override_value, Mapping):
            return
        msg = (
            f"override on {label} must hold a mapping,"
            f" got {type(override_value).__name__}"
        )
        raise ScenarioError(msg)
    if type(override_value) is not type(base_value):
        msg = (
            f"override on {label} must hold a {type(base_value).__name__},"
            f" got {type(override_value).__name__}"
        )
        raise ScenarioError(msg)


def _resolved_decision(
    base: Decision[Any], override: Override, label: str
) -> Decision[Any]:
    """The decision with the override's value and note applied.

    ``recorded_on`` stays the base's — resolution reads no clock
    (planning §4.6); the override's own provenance lives in the
    resolution's ``applied`` record.
    """
    _check_value_type(base.value, override.value, label)
    return Decision(
        value=override.value, recorded_on=base.recorded_on, note=override.note
    )


def _apply_to_assumptions(
    assumptions: AssumptionSet, scenario: Scenario
) -> AssumptionSet:
    """The assumption set with the scenario's assumption overrides applied."""
    overrides = {
        override.target.key: override
        for override in scenario.overrides
        if isinstance(override.target, AssumptionTarget)
    }
    if not overrides:
        return assumptions
    resolved = []
    for key in assumptions.keys:
        base = assumptions.get(key)
        override = overrides.get(key)
        if override is not None:
            base = _overridden_assumption(base, override)
        resolved.append(base)
    return AssumptionSet(resolved)


def _overridden_assumption(
    base: Assumption[Any], override: Override
) -> Assumption[Any]:
    """The assumption re-stamped with the override's value (§4.3).

    The shipped default, source, and description survive so the UI can
    still answer "what would this have been?"; provenance becomes
    ``SCENARIO_OVERRIDE``.
    """
    _check_value_type(base.value, override.value, base.key.value)
    return replace(base, value=override.value, provenance=Provenance.SCENARIO_OVERRIDE)


def _apply_to_household(
    household: Household, picks: dict[tuple[EntityId, str], Override]
) -> Household:
    """The household with the picked decision overrides applied."""
    changes: dict[str, Any] = {}
    if picks:
        changes["planned_outflows"] = tuple(
            _apply_to_outflow(outflow, picks) for outflow in household.planned_outflows
        )
        changes["persons"] = tuple(
            _apply_to_person(person, picks) for person in household.persons
        )
    return replace(household, **changes) if changes else household


def _apply_to_outflow(
    outflow: PlannedOutflow, picks: dict[tuple[EntityId, str], Override]
) -> PlannedOutflow:
    """One planned outflow with its ``amount_real`` override applied."""
    changes: dict[str, Any] = {}
    override = picks.get((outflow.id, "amount_real"))
    if override is not None:
        label = f"planned_outflow[{outflow.id}].amount_real"
        changes["amount_real"] = _resolved_decision(
            outflow.amount_real, override, label
        )
    return replace(outflow, **changes) if changes else outflow


def _apply_to_person(
    person: Person, picks: dict[tuple[EntityId, str], Override]
) -> Person:
    """One person with every decision override on them applied."""
    changes: dict[str, Any] = {}
    override = picks.get((person.id, "target_retirement_age"))
    if override is not None:
        changes["target_retirement_age"] = _resolved_decision(
            person.target_retirement_age,
            override,
            f"person[{person.id}].target_retirement_age",
        )
    if person.state_pension is not None:
        override = picks.get((person.id, "state_pension.deferral_years"))
        if override is not None:
            deferral = _resolved_decision(
                person.state_pension.deferral_years,
                override,
                f"person[{person.id}].state_pension.deferral_years",
            )
            changes["state_pension"] = replace(
                person.state_pension, deferral_years=deferral
            )
    wrappers = tuple(_apply_to_wrapper(wrapper, picks) for wrapper in person.wrappers)
    if wrappers != person.wrappers:
        changes["wrappers"] = wrappers
    pensions = tuple(_apply_to_db_pension(p, picks) for p in person.db_pensions)
    if pensions != person.db_pensions:
        changes["db_pensions"] = pensions
    purchases = tuple(
        _apply_to_annuity_purchase(p, picks) for p in person.annuity_purchases
    )
    if purchases != person.annuity_purchases:
        changes["annuity_purchases"] = purchases
    return replace(person, **changes) if changes else person


def _apply_to_wrapper(
    wrapper: Wrapper, picks: dict[tuple[EntityId, str], Override]
) -> Wrapper:
    """One wrapper with its employee-contribution override applied."""
    changes: dict[str, Any] = {}
    override = picks.get((wrapper.id, "contributions.employee_amount"))
    if override is not None and wrapper.contributions is not None:
        label = f"wrapper[{wrapper.id}].contributions.employee_amount"
        changes["contributions"] = replace(
            wrapper.contributions,
            employee_amount=_resolved_decision(
                wrapper.contributions.employee_amount, override, label
            ),
        )
    return replace(wrapper, **changes) if changes else wrapper


def _apply_to_db_pension(
    pension: DBPension, picks: dict[tuple[EntityId, str], Override]
) -> DBPension:
    """One DB pension with its decision overrides applied."""
    changes: dict[str, Any] = {}
    override = picks.get((pension.id, "taken_at_age"))
    if override is not None and pension.taken_at_age is not None:
        changes["taken_at_age"] = _resolved_decision(
            pension.taken_at_age, override, f"db_pension[{pension.id}].taken_at_age"
        )
    override = picks.get((pension.id, "commuted_fraction"))
    if override is not None:
        changes["commuted_fraction"] = _resolved_decision(
            pension.commuted_fraction,
            override,
            f"db_pension[{pension.id}].commuted_fraction",
        )
    override = picks.get((pension.id, "active_membership.active_until_age"))
    membership = pension.active_membership
    if (
        override is not None
        and membership is not None
        and membership.active_until_age is not None
    ):
        changes["active_membership"] = replace(
            membership,
            active_until_age=_resolved_decision(
                membership.active_until_age,
                override,
                f"db_pension[{pension.id}].active_membership.active_until_age",
            ),
        )
    return replace(pension, **changes) if changes else pension


def _apply_to_annuity_purchase(
    purchase: AnnuityPurchase, picks: dict[tuple[EntityId, str], Override]
) -> AnnuityPurchase:
    """One annuity purchase with its decision overrides applied.

    ``annuity_type`` and ``basis`` are plain enum fields — the whole
    record is a decision (planning §5.1) — so their overrides replace
    the value directly; the override's note lives only in the
    resolution's ``applied`` record.
    """
    changes: dict[str, Any] = {}
    for field in ("at_age", "fraction_of_pot"):
        override = picks.get((purchase.id, field))
        if override is not None:
            changes[field] = _resolved_decision(
                getattr(purchase, field),
                override,
                f"annuity_purchase[{purchase.id}].{field}",
            )
    for field in ("annuity_type", "basis"):
        override = picks.get((purchase.id, field))
        if override is not None:
            label = f"annuity_purchase[{purchase.id}].{field}"
            _check_value_type(getattr(purchase, field), override.value, label)
            changes[field] = override.value
    return replace(purchase, **changes) if changes else purchase
