"""The launch example parses, projects, and carries its copy (§4.9).

The facts form opens pre-filled with this example, so these tests are
the §4.9 guarantee that the opening screen can never show an error —
and that it actually shows output.
"""

from datetime import UTC, date, datetime

from glidepath.app import (
    build_charts_view_model,
    build_facts_form_view_model,
    example_facts_form_data,
    initial_plan_state,
    parse_facts_form,
    state_with_household,
)

RECORDED = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
TODAY = date(2026, 8, 4)


class TestExamplePlan:
    """The shipped example is a valid, projectable plan."""

    def test_parses_without_errors(self) -> None:
        """The example is raw form text the normal parser accepts."""
        result = parse_facts_form(
            example_facts_form_data(), recorded_on=RECORDED, today=TODAY
        )
        assert result.errors == ()
        assert result.household is not None
        [person] = result.household.persons
        assert len(person.wrappers) == 2
        assert person.state_pension is not None
        assert result.household.spending is not None

    def test_projects_chartable_output(self) -> None:
        """Submitting the example yields populated charts, no message."""
        result = parse_facts_form(
            example_facts_form_data(), recorded_on=RECORDED, today=TODAY
        )
        assert result.household is not None
        state = state_with_household(
            initial_plan_state(), result.household, today=TODAY
        )
        charts = build_charts_view_model(state)
        assert charts.message == ""
        assert charts.charts
        assert charts.categories

    def test_form_copy_labels_the_example_and_the_clear_action(self) -> None:
        """The §4.9 status copy exists and says what the example is."""
        form = build_facts_form_view_model()
        assert form.clear_label
        assert "example" in form.example_note
        assert "nothing here is your data" in form.example_note
        assert form.cleared_note
