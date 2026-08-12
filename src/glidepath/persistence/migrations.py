"""Versioned schema migrations for ``.glidepath.json`` (roadmap 6.4; §4.5).

The harness exists from day one — the accepted cost of the §4.5
persistence decision — so a future schema change is a registered
upgrader, not a file-format fork. Upgraders are keyed by the schema
version they *read* and each returns the document exactly one version
higher; loading applies them in sequence from the file's version up to
:data:`~glidepath.persistence.document.SCHEMA_VERSION`. A file already
at the current version passes through untouched.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from glidepath.persistence.document import SCHEMA_VERSION, PersistenceError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

type RawDocument = dict[str, Any]
"""A parsed ``.glidepath.json`` document before strict decoding."""

type Upgrader = Callable[[RawDocument], RawDocument]
"""One registered migration: reads version *n*, returns version *n + 1*."""


def _upgrade_v1_to_v2(raw: RawDocument) -> RawDocument:
    """v2 adds ``active_membership`` to every DB pension (roadmap 9.6).

    A v1 file predates active DB accrual, so every pension it holds is
    deferred — the new key decodes as ``null``. The document is
    upgraded in place at the parsed-JSON layer; a malformed document
    passes through shape-checked, for the strict decoder to reject
    with a proper path-carrying error.
    """
    household = raw.get("household")
    persons = household.get("persons") if isinstance(household, dict) else []
    for person in persons if isinstance(persons, list) else []:
        pensions = person.get("db_pensions") if isinstance(person, dict) else []
        for pension in pensions if isinstance(pensions, list) else []:
            if isinstance(pension, dict):
                pension["active_membership"] = None
    raw[_VERSION_KEY] = 2
    return raw


def _upgrade_v2_to_v3(raw: RawDocument) -> RawDocument:
    """v3 drops the state pension qualifying-years derivation (#97).

    The official DWP forecast is the only route to a state pension
    amount, so the fields that existed solely to feed the derivation —
    the qualifying-years count, the NI record start date, and the
    planned extra accrual years — are removed from the record. A
    migrated record without a forecast still loads (the deferral
    choice is kept); projecting it fails with a clear demand for a
    forecast, and the facts form requires one on the next save.
    """
    household = raw.get("household")
    persons = household.get("persons") if isinstance(household, dict) else []
    for person in persons if isinstance(persons, list) else []:
        record = person.get("state_pension") if isinstance(person, dict) else None
        if isinstance(record, dict):
            for key in ("ni_record_start", "qualifying_years", "planned_extra_years"):
                record.pop(key, None)
    raw[_VERSION_KEY] = 3
    return raw


def _upgrade_v3_to_v4(raw: RawDocument) -> RawDocument:
    """v4 drops the accumulation-stage spending multipliers (#129).

    Spending is modelled only in retirement, so the accumulation-stage
    keys older builds accepted and wrote never scaled anything —
    dropping them loses no behaviour. #114 retired the tokens from the
    ``SpendingPlan`` invariant without a migration, so a genuine v1-era
    file carrying them failed to load until this step.
    """
    household = raw.get("household")
    spending = household.get("spending") if isinstance(household, dict) else None
    multipliers = (
        spending.get("stage_multipliers") if isinstance(spending, dict) else None
    )
    if isinstance(multipliers, dict):
        for key in ("early_accumulation", "mid_accumulation", "pre_retirement"):
            multipliers.pop(key, None)
    raw[_VERSION_KEY] = 4
    return raw


def _upgrade_v4_to_v5(raw: RawDocument) -> RawDocument:
    """v5 adds ``label`` to every wrapper (roadmap 9.28).

    A v4 file predates wrapper naming, so every wrapper it holds is
    unnamed — the new key decodes as ``null`` and the display layers
    keep deriving names from the kind.
    """
    household = raw.get("household")
    persons = household.get("persons") if isinstance(household, dict) else []
    for person in persons if isinstance(persons, list) else []:
        wrappers = person.get("wrappers") if isinstance(person, dict) else []
        for wrapper in wrappers if isinstance(wrappers, list) else []:
            if isinstance(wrapper, dict):
                wrapper["label"] = None
    raw[_VERSION_KEY] = 5
    return raw


def _upgrade_v5_to_v6(raw: RawDocument) -> RawDocument:
    """v6 adds ``claim_marriage_allowance`` to the household (roadmap 9.32).

    A v5 file predates the couples marriage-allowance claim, so every
    household it holds keeps the default — the new key decodes as
    ``null``, meaning "claim whenever a tax year's eligibility check
    passes" (planning §4.11).
    """
    household = raw.get("household")
    if isinstance(household, dict):
        household["claim_marriage_allowance"] = None
    raw[_VERSION_KEY] = 6
    return raw


UPGRADERS: Mapping[int, Upgrader] = MappingProxyType(
    {
        1: _upgrade_v1_to_v2,
        2: _upgrade_v2_to_v3,
        3: _upgrade_v3_to_v4,
        4: _upgrade_v4_to_v5,
        5: _upgrade_v5_to_v6,
    }
)
"""The registered upgraders, keyed by the version each reads."""

_VERSION_KEY = "schema_version"
_FLOOR_VERSION = 1


def document_schema_version(raw: RawDocument) -> int:
    """The document's declared schema version, validated.

    Raises:
        PersistenceError: If the key is missing, not a whole number, or
            below the version-1 floor.
    """
    if _VERSION_KEY not in raw:
        msg = f"document is missing required key {_VERSION_KEY!r}"
        raise PersistenceError(msg)
    version = raw[_VERSION_KEY]
    if isinstance(version, bool) or not isinstance(version, int):
        msg = f"{_VERSION_KEY} must be a whole number, got {type(version).__name__}"
        raise PersistenceError(msg)
    if version < _FLOOR_VERSION:
        msg = f"{_VERSION_KEY} must be at least {_FLOOR_VERSION}, got {version}"
        raise PersistenceError(msg)
    return version


def apply_migrations(
    raw: RawDocument,
    *,
    upgraders: Mapping[int, Upgrader] = UPGRADERS,
    target: int = SCHEMA_VERSION,
) -> RawDocument:
    """Upgrade a parsed document to ``target``, one version at a time.

    A document already at ``target`` is returned as-is (the v1→v1
    no-op). ``upgraders`` and ``target`` are injectable so the harness
    itself is testable ahead of any real schema change.

    Raises:
        PersistenceError: If the document declares a version newer than
            ``target``, no upgrader is registered for a needed step, or
            an upgrader fails to step exactly one version.
    """
    version = document_schema_version(raw)
    if version > target:
        msg = (
            f"document schema version {version} is newer than this build"
            f" reads (up to {target}); upgrade glidepath to open it"
        )
        raise PersistenceError(msg)
    while version < target:
        upgrader = upgraders.get(version)
        if upgrader is None:
            msg = f"no migration is registered from schema version {version}"
            raise PersistenceError(msg)
        raw = upgrader(raw)
        upgraded_version = document_schema_version(raw)
        if upgraded_version != version + 1:
            msg = (
                f"migration from schema version {version} produced"
                f" version {upgraded_version}; upgraders must step"
                " exactly one version"
            )
            raise PersistenceError(msg)
        version = upgraded_version
    return raw
