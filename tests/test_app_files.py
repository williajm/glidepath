"""Plan save/load app-layer tests (planning §4.5, §4.7).

The acceptance criterion: a session saved to ``.glidepath.json`` loads
back into an equivalent, freshly projected session — household,
scenarios, and assumption overrides intact — with every failure folded
into a status message instead of an exception at the shell.
"""

import json
from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from glidepath.app import (
    NOTHING_TO_SAVE_MESSAGE,
    PlanState,
    document_from_state,
    example_facts_form_data,
    initial_plan_state,
    load_plan_state,
    parse_facts_form,
    save_plan_state,
    state_with_household,
    state_with_override,
    state_with_scenario_added,
)
from glidepath.core import (
    AssumptionKey,
    AssumptionTarget,
    Decision,
    EntityId,
    Money,
    Override,
    PlannedOutflow,
    Provenance,
    Scenario,
)
from glidepath.persistence import AssumptionOverride, save_plan

if TYPE_CHECKING:
    from pathlib import Path

    from glidepath.persistence import PlanDocument

RECORDED = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
TODAY = RECORDED.date()


def projected_state() -> PlanState:
    """A projected session over the launch example's household."""
    result = parse_facts_form(
        example_facts_form_data(), recorded_on=RECORDED, today=TODAY
    )
    assert result.household is not None
    return state_with_household(initial_plan_state(), result.household, today=TODAY)


class TestSavePlanState:
    """Saving writes the canonical document, or says why not."""

    def test_empty_session_has_nothing_to_save(self, tmp_path: Path) -> None:
        """Without a household there is no document to write."""
        path = tmp_path / "plan.glidepath.json"
        outcome = save_plan_state(initial_plan_state(), path)
        assert not outcome.saved
        assert outcome.message == NOTHING_TO_SAVE_MESSAGE
        assert not path.exists()

    def test_save_reports_the_path(self, tmp_path: Path) -> None:
        """A successful save names the file it wrote."""
        path = tmp_path / "plan.glidepath.json"
        outcome = save_plan_state(projected_state(), path)
        assert outcome.saved
        assert str(path) in outcome.message
        assert path.exists()

    def test_unwritable_path_reports_rather_than_raises(self, tmp_path: Path) -> None:
        """An OS failure comes back as a status message."""
        outcome = save_plan_state(
            projected_state(), tmp_path / "missing-dir" / "plan.glidepath.json"
        )
        assert not outcome.saved
        assert outcome.message.startswith("Could not save the plan")

    def test_document_from_empty_state_is_none(self) -> None:
        """No household, no document."""
        assert document_from_state(initial_plan_state()) is None


class TestLoadPlanState:
    """Loading rebuilds a projected session from the file."""

    def test_round_trips_household_scenarios_and_overrides(
        self, tmp_path: Path
    ) -> None:
        """Everything the user owns survives a save → load cycle."""
        state = projected_state()
        override = state_with_override(
            state,
            AssumptionKey.INFLATION_CPI.value,
            "0.031",
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert override.error is None
        state = override.state
        added = state_with_scenario_added(state, "Retire later", today=TODAY)
        assert added.error is None
        state = added.state
        path = tmp_path / "plan.glidepath.json"
        assert save_plan_state(state, path).saved
        outcome = load_plan_state(path, today=TODAY)
        loaded = outcome.state
        assert loaded is not None
        assert str(path) in outcome.message
        assert loaded.household == state.household
        assert [entry.name for entry in loaded.scenarios] == ["Retire later"]
        cpi = loaded.assumptions.get(AssumptionKey.INFLATION_CPI)
        assert cpi.value == Decimal("0.031")
        assert cpi.provenance is Provenance.USER_OVERRIDE
        assert loaded.result is not None
        assert loaded.run_error is None

    def test_missing_file_reports_rather_than_raises(self, tmp_path: Path) -> None:
        """A vanished file comes back as a status message."""
        outcome = load_plan_state(tmp_path / "gone.glidepath.json", today=TODAY)
        assert outcome.state is None
        assert outcome.message.startswith("Could not open the plan")

    def test_garbage_file_reports_rather_than_raises(self, tmp_path: Path) -> None:
        """A corrupt document comes back as a status message."""
        path = tmp_path / "plan.glidepath.json"
        path.write_text("not json at all", encoding="utf-8")
        outcome = load_plan_state(path, today=TODAY)
        assert outcome.state is None
        assert outcome.message.startswith("Could not open the plan")

    def test_unsupported_region_is_rejected(self, tmp_path: Path) -> None:
        """A plan for a region this build lacks fails loudly."""
        path = tmp_path / "plan.glidepath.json"
        assert save_plan_state(projected_state(), path).saved
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["region"] = "atlantis"
        path.write_text(json.dumps(raw), encoding="utf-8")
        outcome = load_plan_state(path, today=TODAY)
        assert outcome.state is None
        assert "atlantis" in outcome.message

    def test_moved_defaults_are_noted_on_load(self, tmp_path: Path) -> None:
        """A stale recorded data version surfaces in the status message."""
        path = tmp_path / "plan.glidepath.json"
        assert save_plan_state(projected_state(), path).saved
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["assumptions_resolved_against"] = "uk data from another era"
        path.write_text(json.dumps(raw), encoding="utf-8")
        outcome = load_plan_state(path, today=TODAY)
        assert outcome.state is not None
        assert "default assumptions have changed" in outcome.message

    def test_matching_defaults_load_without_the_note(self, tmp_path: Path) -> None:
        """An up-to-date file gets a plain loaded message."""
        path = tmp_path / "plan.glidepath.json"
        assert save_plan_state(projected_state(), path).saved
        outcome = load_plan_state(path, today=TODAY)
        assert outcome.state is not None
        assert "default assumptions have changed" not in outcome.message


def _altered[T](entity: T, **changes: object) -> T:
    """A copy of a frozen dataclass with ``changes`` applied."""
    source = cast("Any", entity)
    values = {spec.name: getattr(source, spec.name) for spec in fields(source)}
    values.update(changes)
    return cast("T", type(source)(**values))


def _saved_document(path: Path, **changes: object) -> PlanDocument:
    """The example plan's document, altered and written to ``path``."""
    document = document_from_state(projected_state())
    assert document is not None
    varied = _altered(document, **changes)
    save_plan(varied, path)
    return varied


class TestLoadGuards:
    """A load that could lose or crash on data is refused up front."""

    def test_unrepresentable_plan_is_refused(self, tmp_path: Path) -> None:
        """A plan the facts form cannot faithfully edit never opens."""
        path = tmp_path / "plan.glidepath.json"
        document = document_from_state(projected_state())
        assert document is not None
        outflow = PlannedOutflow(
            id=EntityId("files-outflow"),
            label="New roof",
            amount_real=Decision(value=Money(Decimal(20000)), recorded_on=RECORDED),
            at_age_of=(document.household.persons[0].id, 60),
        )
        _saved_document(
            path,
            household=_altered(document.household, planned_outflows=(outflow,)),
        )
        outcome = load_plan_state(path, today=TODAY)
        assert outcome.state is None
        assert "planned outflows" in outcome.message
        assert "cannot edit yet" in outcome.message

    def test_noted_plan_is_refused(self, tmp_path: Path) -> None:
        """A note on a fact is refused at open, never silently stripped.

        No in-app surface authors fact or decision notes yet, so a
        noted plan is hand-edited — but the refuse-rather-than-reduce
        contract holds for it all the same (§4.5).
        """
        path = tmp_path / "plan.glidepath.json"
        document = document_from_state(projected_state())
        assert document is not None
        person = document.household.persons[0]
        noted = _altered(person.date_of_birth, note="from my passport")
        _saved_document(
            path,
            household=_altered(
                document.household, persons=(_altered(person, date_of_birth=noted),)
            ),
        )
        outcome = load_plan_state(path, today=TODAY)
        assert outcome.state is None
        assert "notes on facts or decisions" in outcome.message
        assert "cannot edit yet" in outcome.message

    def test_defective_table_override_is_refused(self, tmp_path: Path) -> None:
        """A stored table its policy parser rejects fails the load."""
        path = tmp_path / "plan.glidepath.json"
        override = AssumptionOverride(
            key=AssumptionKey.GLIDEPATH_DEFAULT_SHAPE,
            value={"nonsense": 1},
            source="hand-edited",
            recorded_on=RECORDED,
        )
        _saved_document(path, assumption_overrides=(override,))
        outcome = load_plan_state(path, today=TODAY)
        assert outcome.state is None
        assert "glidepath.default_shape" in outcome.message

    def test_defective_scenario_table_override_is_refused(self, tmp_path: Path) -> None:
        """A scenario's stored table is vetted exactly like the base's."""
        path = tmp_path / "plan.glidepath.json"
        scenario = Scenario(
            name="Riskier shape",
            overrides=(
                Override(
                    target=AssumptionTarget(key=AssumptionKey.GLIDEPATH_DEFAULT_SHAPE),
                    value={"nonsense": 1},
                ),
            ),
        )
        _saved_document(path, scenarios=(scenario,))
        outcome = load_plan_state(path, today=TODAY)
        assert outcome.state is None
        assert "Riskier shape" in outcome.message
        assert "glidepath.default_shape" in outcome.message

    def test_non_utf8_file_reports_rather_than_raises(self, tmp_path: Path) -> None:
        """A binary or mis-encoded file comes back as a status message."""
        path = tmp_path / "plan.glidepath.json"
        path.write_bytes(b"\xff\xfe\x00\x00 not text")
        outcome = load_plan_state(path, today=TODAY)
        assert outcome.state is None
        assert outcome.message.startswith("Could not open the plan")
        assert "UTF-8" in outcome.message
