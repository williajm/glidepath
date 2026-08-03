"""Assumption re-resolution on load (roadmap 6.2; planning §4.5).

User files store only assumption *overrides*: on every load the
effective set is rebuilt from the region's current shipped defaults
with the stored overrides applied on top, each carrying
``USER_OVERRIDE`` provenance and the current shipped default alongside
its value. A default that moved since the file was last saved therefore
changes the plan visibly — the document's recorded data version tells
the caller to surface it (planning §4.5).
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from glidepath.core import Assumption, AssumptionSet, Provenance
from glidepath.persistence.document import AssumptionOverride, PersistenceError

if TYPE_CHECKING:
    from glidepath.persistence.document import PlanDocument


def resolve_assumptions(
    document: PlanDocument, defaults: AssumptionSet
) -> AssumptionSet:
    """The effective assumptions: shipped defaults ⊕ stored overrides.

    ``defaults`` is the region's current shipped set (e.g.
    ``glidepath.regions.uk.default_assumption_set()``); the core stays
    region-agnostic by taking it as an input. Each override keeps the
    shipped entry's default value and description, so the UI can always
    answer "what would this have been?".

    Raises:
        PersistenceError: If an override targets a key the defaults do
            not register, or its value's shape no longer matches the
            shipped default's.
    """
    pending = {override.key: override for override in document.assumption_overrides}
    resolved = []
    for key in defaults.keys:
        base = defaults.get(key)
        override = pending.pop(key, None)
        resolved.append(base if override is None else _overridden(base, override))
    if pending:
        unknown = ", ".join(sorted(key.value for key in pending))
        msg = f"assumption overrides target unregistered keys: {unknown}"
        raise PersistenceError(msg)
    return AssumptionSet(resolved)


def assumption_overrides_from(
    assumptions: AssumptionSet,
) -> tuple[AssumptionOverride, ...]:
    """The set's user overrides as storable records, sorted by key.

    The inverse of :func:`resolve_assumptions` for saving: only
    ``USER_OVERRIDE`` entries persist (planning §4.5) — defaults are
    never written, and scenario overrides live on their scenarios.
    """
    return tuple(
        AssumptionOverride(
            key=entry.key,
            value=entry.value,
            source=entry.source,
            recorded_on=entry.recorded_on,
        )
        for entry in (assumptions.get(key) for key in sorted(assumptions.keys))
        if entry.provenance is Provenance.USER_OVERRIDE
    )


def _overridden(base: Assumption[Any], override: AssumptionOverride) -> Assumption[Any]:
    """The shipped default re-stamped with the stored override."""
    _check_value_shape(base, override)
    return Assumption(
        key=base.key,
        value=override.value,
        default_value=base.default_value,
        provenance=Provenance.USER_OVERRIDE,
        source=override.source,
        recorded_on=override.recorded_on,
        description=base.description,
    )


def _check_value_shape(base: Assumption[Any], override: AssumptionOverride) -> None:
    """Reject a stored value shaped unlike the current shipped default.

    The same rule as scenario resolution (planning §4.3): a mapping
    default accepts any mapping; a scalar default requires the exact
    runtime type. A shipped default that changed shape since the file
    was saved fails loudly rather than corrupting a run.

    Raises:
        PersistenceError: If the shapes no longer match.
    """
    base_value = base.value
    override_value = override.value
    if isinstance(base_value, Mapping):
        if isinstance(override_value, Mapping):
            return
        msg = (
            f"stored override on {base.key.value!r} must hold a mapping,"
            f" got {type(override_value).__name__}"
        )
        raise PersistenceError(msg)
    if type(override_value) is not type(base_value):
        msg = (
            f"stored override on {base.key.value!r} must hold a"
            f" {type(base_value).__name__}, got {type(override_value).__name__}"
        )
        raise PersistenceError(msg)
