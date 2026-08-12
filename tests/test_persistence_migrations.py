"""Tests for the schema migration harness (issue 6.4, planning §4.5).

These tests exercise it three ways: the real registry (the 9.6 v1→v2
upgrader adding ``active_membership`` to DB pensions, the #97 v2→v3
upgrader dropping the qualifying-years derivation fields, and the
#129 v3→v4 upgrader dropping the accumulation-stage spending
multipliers), synthetic upgrader registries that prove sequencing,
missing-step detection, and the one-version-per-upgrader rule, and
the load boundary refusing a future-version file (the checked-in v1
file's end-to-end load lives with the round-trip tests in
``test_persistence.py``).
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
    """A raw document at schema version 1."""
    return {"schema_version": 1, "marker": "untouched"}


def current_document() -> RawDocument:
    """A raw document already at the current schema version."""
    return {"schema_version": SCHEMA_VERSION, "marker": "untouched"}


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
    """A file at the current version passes through untouched."""

    def test_current_version_passes_through_untouched(self) -> None:
        """A current document is returned as-is — same object, unchanged."""
        raw = current_document()
        migrated = apply_migrations(raw)
        assert migrated is raw
        assert migrated == {"schema_version": SCHEMA_VERSION, "marker": "untouched"}


class TestV1ToV2:
    """The 9.6 migration: DB pensions gain ``active_membership`` (§4.5)."""

    def test_db_pensions_gain_a_null_active_membership(self) -> None:
        """Every DB pension in a v1 file decodes as deferred."""
        raw: RawDocument = {
            "schema_version": 1,
            "household": {
                "persons": [
                    {"db_pensions": [{"id": "a"}, {"id": "b"}]},
                    {"db_pensions": []},
                ]
            },
        }
        migrated = apply_migrations(raw)
        assert migrated["schema_version"] == SCHEMA_VERSION
        pensions = migrated["household"]["persons"][0]["db_pensions"]
        assert pensions == [
            {"id": "a", "active_membership": None},
            {"id": "b", "active_membership": None},
        ]

    def test_a_document_without_pensions_just_bumps_the_version(self) -> None:
        """Nothing to upgrade still steps the version."""
        raw = v1_document()
        migrated = apply_migrations(raw)
        assert migrated["schema_version"] == SCHEMA_VERSION
        assert migrated["marker"] == "untouched"

    def test_malformed_shapes_pass_through_for_the_strict_decoder(self) -> None:
        """The upgrader never crashes on shapes the decoder will reject."""
        raw: RawDocument = {
            "schema_version": 1,
            "household": {"persons": [{"db_pensions": "not-a-list"}, "not-a-dict"]},
        }
        migrated = apply_migrations(raw)
        assert migrated["schema_version"] == SCHEMA_VERSION


class TestV2ToV3:
    """The #97 migration: state pensions lose the derivation fields (§4.5)."""

    def test_state_pension_records_drop_the_derivation_fields(self) -> None:
        """The retired keys go; everything else on the record stays."""
        raw: RawDocument = {
            "schema_version": 2,
            "household": {
                "persons": [
                    {
                        "state_pension": {
                            "ni_record_start": {"value": "2016-04-06"},
                            "qualifying_years": {"value": 20},
                            "planned_extra_years": {"value": 2},
                            "deferral_years": {"value": "0.5"},
                        }
                    },
                    {"state_pension": None},
                ]
            },
        }
        migrated = apply_migrations(raw)
        assert migrated["schema_version"] == SCHEMA_VERSION
        persons = migrated["household"]["persons"]
        assert persons[0]["state_pension"] == {"deferral_years": {"value": "0.5"}}
        assert persons[1]["state_pension"] is None

    def test_a_record_without_the_retired_fields_is_untouched(self) -> None:
        """A record already free of the retired keys passes unchanged."""
        record = {"forecast_weekly_amount": None, "deferral_years": {"value": "0"}}
        raw: RawDocument = {
            "schema_version": 2,
            "household": {"persons": [{"state_pension": dict(record)}]},
        }
        migrated = apply_migrations(raw)
        assert migrated["schema_version"] == SCHEMA_VERSION
        assert migrated["household"]["persons"][0]["state_pension"] == record

    def test_a_document_without_persons_just_bumps_the_version(self) -> None:
        """Nothing to upgrade still steps the version."""
        raw: RawDocument = {"schema_version": 2, "marker": "untouched"}
        migrated = apply_migrations(raw)
        assert migrated["schema_version"] == SCHEMA_VERSION
        assert migrated["marker"] == "untouched"

    @pytest.mark.parametrize(
        "raw",
        [
            {"schema_version": 2, "household": "not-an-object"},
            {"schema_version": 2, "household": {"persons": "not-a-list"}},
            {
                "schema_version": 2,
                "household": {
                    "persons": [{"state_pension": "not-a-dict"}, "not-a-dict"]
                },
            },
        ],
    )
    def test_malformed_shapes_pass_through_for_the_strict_decoder(
        self, raw: RawDocument
    ) -> None:
        """The upgrader never crashes on shapes the decoder will reject."""
        migrated = apply_migrations(raw)
        assert migrated["schema_version"] == SCHEMA_VERSION


class TestV3ToV4:
    """The #129 migration: the accumulation-stage multipliers go (§4.5)."""

    def test_spending_drops_the_accumulation_stage_keys(self) -> None:
        """The retired keys go; the retirement-stage keys stay."""
        raw: RawDocument = {
            "schema_version": 3,
            "household": {
                "spending": {
                    "annual_spending_real": {"value": "24000"},
                    "stage_multipliers": {
                        "decumulation": "1.10",
                        "early_accumulation": "1.00",
                        "go_go": "1.20",
                        "mid_accumulation": "1.05",
                        "pre_retirement": "0.95",
                    },
                }
            },
        }
        migrated = apply_migrations(raw)
        assert migrated["schema_version"] == SCHEMA_VERSION
        assert migrated["household"]["spending"]["stage_multipliers"] == {
            "decumulation": "1.10",
            "go_go": "1.20",
        }

    def test_multipliers_without_the_retired_keys_are_untouched(self) -> None:
        """A mapping already free of the retired keys passes unchanged."""
        multipliers = {"decumulation": "1.10"}
        raw: RawDocument = {
            "schema_version": 3,
            "household": {"spending": {"stage_multipliers": dict(multipliers)}},
        }
        migrated = apply_migrations(raw)
        assert migrated["schema_version"] == SCHEMA_VERSION
        stored = migrated["household"]["spending"]["stage_multipliers"]
        assert stored == multipliers

    def test_a_document_without_spending_just_bumps_the_version(self) -> None:
        """Nothing to upgrade still steps the version.

        The later v5→v6 step of the full chain adds the household's
        marriage-allowance claim key (roadmap 9.32).
        """
        raw: RawDocument = {"schema_version": 3, "household": {"spending": None}}
        migrated = apply_migrations(raw)
        assert migrated["schema_version"] == SCHEMA_VERSION
        assert migrated["household"] == {
            "spending": None,
            "claim_marriage_allowance": None,
        }

    @pytest.mark.parametrize(
        "raw",
        [
            {"schema_version": 3, "household": "not-an-object"},
            {"schema_version": 3, "household": {"spending": "not-an-object"}},
            {
                "schema_version": 3,
                "household": {"spending": {"stage_multipliers": "not-a-dict"}},
            },
        ],
    )
    def test_malformed_shapes_pass_through_for_the_strict_decoder(
        self, raw: RawDocument
    ) -> None:
        """The upgrader never crashes on shapes the decoder will reject."""
        migrated = apply_migrations(raw)
        assert migrated["schema_version"] == SCHEMA_VERSION


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
