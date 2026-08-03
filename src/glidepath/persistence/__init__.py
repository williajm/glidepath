"""``.glidepath.json`` persistence (roadmap 6.2, 6.4; planning §4.5).

One deterministic JSON document per plan: the household, its
scenarios, and the user's assumption overrides — never resolved
assumption values, which re-resolve on load against the region's
current shipped data. The reader runs every file through the versioned
migration harness first, so old files keep opening as the schema
evolves. Region-agnostic like the core: the region's defaults and data
version are inputs, never imports.
"""

from glidepath.persistence.assumptions import (
    assumption_overrides_from,
    resolve_assumptions,
)
from glidepath.persistence.decode import load_plan, loads_plan
from glidepath.persistence.document import (
    SCHEMA_VERSION,
    AssumptionOverride,
    PersistenceError,
    PlanDocument,
)
from glidepath.persistence.encode import dumps_plan, save_plan
from glidepath.persistence.migrations import (
    UPGRADERS,
    RawDocument,
    Upgrader,
    apply_migrations,
    document_schema_version,
)

__all__ = [
    "SCHEMA_VERSION",
    "UPGRADERS",
    "AssumptionOverride",
    "PersistenceError",
    "PlanDocument",
    "RawDocument",
    "Upgrader",
    "apply_migrations",
    "assumption_overrides_from",
    "document_schema_version",
    "dumps_plan",
    "load_plan",
    "loads_plan",
    "resolve_assumptions",
    "save_plan",
]
