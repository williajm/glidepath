"""Versioned schema migrations for ``.glidepath.json`` (roadmap 6.4; §4.5).

The harness exists from day one — the accepted cost of the §4.5
persistence decision — so a future schema change is a registered
upgrader, not a file-format fork. Upgraders are keyed by the schema
version they *read* and each returns the document exactly one version
higher; loading applies them in sequence from the file's version up to
:data:`~glidepath.persistence.document.SCHEMA_VERSION`. A file already
at the current version passes through untouched — the wired v1→v1
no-op while version 1 is both floor and current.
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

UPGRADERS: Mapping[int, Upgrader] = MappingProxyType({})
"""The registered upgraders, keyed by the version each reads.

Empty while schema version 1 is both the floor and the current version:
a v1 file needs no upgrading, and no earlier version ever existed.
"""

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
