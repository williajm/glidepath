"""The ``.glidepath.json`` plan document model (roadmap 6.2; planning §4.5).

One JSON document per plan holds everything the user owns: the household
(facts and decisions), the scenarios over it, and the user's assumption
*overrides* — never resolved assumption values. Shipped defaults
re-resolve on every load against the region data the app carries, and
the document records the region data version it was last resolved
against so a default change surfaces visibly instead of silently
re-pricing the plan (planning §4.5).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime

    from glidepath.core import AssumptionKey, Household, Scenario

SCHEMA_VERSION = 5
"""The schema version this build reads and writes (planning §4.5).

v2 (roadmap 9.6): every DB pension carries an ``active_membership``
key — ``null`` for a deferred entitlement.

v3 (#97): the state pension record loses the qualifying-years
derivation fields (``ni_record_start``, ``qualifying_years``,
``planned_extra_years``) — the official DWP forecast is the only
route to an amount.

v4 (#129): the spending plan loses the accumulation-stage multiplier
keys (``early_accumulation``, ``mid_accumulation``,
``pre_retirement``) — spending is modelled only in retirement, so
they never bound (#114 retired the tokens without a migration).

v5 (roadmap 9.28): every wrapper carries a ``label`` key — the user's
own name for the account, ``null`` for an unnamed one.
"""


class PersistenceError(ValueError):
    """A plan document that cannot be encoded, decoded, or resolved."""


@dataclass(frozen=True, slots=True)
class AssumptionOverride:
    """One stored user override of a shipped default (planning §4.5).

    Only the override is persisted — the effective assumption is rebuilt
    on load from the current shipped default plus this record, carrying
    ``USER_OVERRIDE`` provenance. ``source`` is the user's stated basis
    for departing from the default; ``recorded_on`` is when they did.
    """

    key: AssumptionKey
    value: Any
    source: str
    recorded_on: datetime

    def __post_init__(self) -> None:
        """Reject an empty source and a naive timestamp."""
        if not self.source:
            msg = "AssumptionOverride.source must be non-empty"
            raise ValueError(msg)
        moment = self.recorded_on
        if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
            msg = "AssumptionOverride.recorded_on must be timezone-aware"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PlanDocument:
    """Everything one ``.glidepath.json`` file stores (planning §4.5).

    ``region`` names the region module whose shipped data the plan
    resolves against (e.g. ``"uk"``); ``assumptions_resolved_against``
    is that region's data content version at the last resolution, so a
    later load against newer data can tell the user their defaults
    moved. The schema version is a wire-format detail stamped by the
    writer, not part of the in-memory model.
    """

    region: str
    assumptions_resolved_against: str
    household: Household
    assumption_overrides: tuple[AssumptionOverride, ...] = ()
    scenarios: tuple[Scenario, ...] = ()

    def __post_init__(self) -> None:
        """Reject empty identifiers and ambiguous duplicates."""
        if not self.region:
            msg = "PlanDocument.region must be non-empty"
            raise ValueError(msg)
        if not self.assumptions_resolved_against:
            msg = "PlanDocument.assumptions_resolved_against must be non-empty"
            raise ValueError(msg)
        keys = [override.key for override in self.assumption_overrides]
        if len(set(keys)) != len(keys):
            msg = "PlanDocument.assumption_overrides must target distinct keys"
            raise ValueError(msg)
        names = [scenario.name for scenario in self.scenarios]
        if len(set(names)) != len(names):
            msg = "PlanDocument.scenarios must have distinct names"
            raise ValueError(msg)
