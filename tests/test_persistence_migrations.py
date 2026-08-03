"""Tests for the schema migration harness (issue 6.4, planning §4.5).

The harness ships before any real schema change exists, so these tests
exercise it two ways: the real registry (empty — a v1 document passes
through untouched, the wired v1→v1 no-op) and synthetic upgrader
registries that prove sequencing, missing-step detection, and the
one-version-per-upgrader rule ahead of the first real migration.
"""

import pytest

from glidepath.persistence import (
    SCHEMA_VERSION,
    PersistenceError,
    RawDocument,
    apply_migrations,
    document_schema_version,
    loads_plan,
)


def v1_document() -> RawDocument:
    """A raw document already at the current schema version."""
    return {"schema_version": 1, "marker": "untouched"}


def _bump_to(version: int, note: str) -> RawDocument:
    """A raw document stamped with ``version`` and a breadcrumb note."""
    return {"schema_version": version, "notes": note}


class TestSchemaVersion:
    """Reading the declared version, strictly."""

    def test_reads_a_valid_version(self) -> None:
        """The declared whole number comes back."""
        assert document_schema_version(v1_document()) == 1

    def test_rejects_a_missing_version(self) -> None:
        """A document must declare its schema version."""
        with pytest.raises(PersistenceError, match="missing required key"):
            document_schema_version({})

    def test_rejects_a_textual_version(self) -> None:
        """The version must be a whole number."""
        with pytest.raises(PersistenceError, match="whole number"):
            document_schema_version({"schema_version": "1"})

    def test_rejects_a_boolean_version(self) -> None:
        """``True`` is not a schema version."""
        with pytest.raises(PersistenceError, match="whole number"):
            document_schema_version({"schema_version": True})

    def test_rejects_a_version_below_the_floor(self) -> None:
        """Schema versions started at 1; nothing older ever existed."""
        with pytest.raises(PersistenceError, match="at least 1"):
            document_schema_version({"schema_version": 0})


class TestNoOpPath:
    """The wired v1→v1 no-op (issue 6.4's acceptance)."""

    def test_current_version_passes_through_untouched(self) -> None:
        """A v1 document is returned as-is — the same object, unchanged."""
        raw = v1_document()
        migrated = apply_migrations(raw)
        assert migrated is raw
        assert migrated == {"schema_version": 1, "marker": "untouched"}

    def test_current_schema_version_is_one(self) -> None:
        """While this holds, the shipped upgrade registry stays empty."""
        assert SCHEMA_VERSION == 1


class TestUpgradeSequencing:
    """Synthetic registries prove the harness ahead of real migrations."""

    def test_upgraders_apply_in_sequence(self) -> None:
        """Each step reads version n and hands version n+1 to the next."""
        upgraders = {
            1: lambda raw: _bump_to(2, f"{raw['notes']} then 1->2"),
            2: lambda raw: _bump_to(3, f"{raw['notes']} then 2->3"),
        }
        migrated = apply_migrations(
            {"schema_version": 1, "notes": "start"}, upgraders=upgraders, target=3
        )
        assert migrated == {"schema_version": 3, "notes": "start then 1->2 then 2->3"}

    def test_starts_midway_when_the_file_is_newer(self) -> None:
        """A version-2 file only runs the 2→3 step."""
        upgraders = {
            1: lambda _raw: _bump_to(2, "ran 1->2"),
            2: lambda raw: _bump_to(3, f"{raw['notes']} then 2->3"),
        }
        migrated = apply_migrations(
            {"schema_version": 2, "notes": "start"}, upgraders=upgraders, target=3
        )
        assert migrated == {"schema_version": 3, "notes": "start then 2->3"}

    def test_rejects_a_document_from_the_future(self) -> None:
        """A newer file tells the user to upgrade the app, not corrupt."""
        raw = {"schema_version": SCHEMA_VERSION + 1}
        with pytest.raises(PersistenceError, match="newer than this build"):
            apply_migrations(raw)

    def test_rejects_a_missing_upgrade_step(self) -> None:
        """A gap in the registry fails loudly, never skips."""
        raw = {"schema_version": 1}
        with pytest.raises(PersistenceError, match="no migration is registered"):
            apply_migrations(raw, upgraders={}, target=2)

    def test_rejects_an_upgrader_that_skips_versions(self) -> None:
        """An upgrader must step exactly one version."""
        raw = {"schema_version": 1}
        upgraders = {1: lambda _raw: _bump_to(3, "skipped")}
        with pytest.raises(PersistenceError, match="exactly one version"):
            apply_migrations(raw, upgraders=upgraders, target=3)


class TestLoadIntegration:
    """Loading runs every file through the harness (planning §4.5)."""

    def test_future_schema_version_fails_at_load(self) -> None:
        """The reader refuses a file written by a newer glidepath."""
        text = f'{{"schema_version": {SCHEMA_VERSION + 1}}}'
        with pytest.raises(PersistenceError, match="newer than this build"):
            loads_plan(text)
