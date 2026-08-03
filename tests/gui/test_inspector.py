"""Inspector widget smoke tests, offscreen (issue 8.3, §4.7).

The pane renders pre-formatted rows and forwards override input; all
copy and formatting is asserted to come from the view model.
"""

from typing import TYPE_CHECKING, ClassVar

import pytest

from glidepath.app import (
    AssumptionRow,
    DecisionRow,
    FactRow,
    InspectorViewModel,
    StructureRow,
)
from glidepath.gui import inspector as inspector_module
from glidepath.gui.inspector import InspectorPane

if TYPE_CHECKING:
    from collections.abc import Callable

SCALAR_ROW = AssumptionRow(
    key="inflation.cpi",
    description="Shipped UK default for 'inflation.cpi'",
    value="0.02",
    default_value="0.02",
    status="Shipped default",
    usage="Used in this projection",
    source="OBR EFO",
    recorded="2026-08-01",
    structured=False,
    edit_text="0.02",
)

STRUCTURED_ROW = AssumptionRow(
    key="glidepath.default_shape",
    description="Shipped UK default for 'glidepath.default_shape'",
    value="equity_start=0.80; derisk_years_before_retirement=15",
    default_value="equity_start=0.80; derisk_years_before_retirement=15",
    status="Shipped default",
    usage="Not used by this projection",
    source="Typical UK shape",
    recorded="2026-08-01",
    structured=True,
    edit_text="equity_start = 0.80\nderisk_years_before_retirement = 15",
)


def view_model() -> InspectorViewModel:
    """A hand-built inspector view model with one row per column."""
    return InspectorViewModel(
        title="Stated vs assumed",
        facts_heading="Facts you stated",
        facts_columns=("Fact", "Value", "As of", "Recorded"),
        facts=(
            FactRow(
                label="You — date of birth",
                value="1984-05-20",
                as_of="2026-08-01",
                recorded="2026-08-01",
                note="",
            ),
        ),
        assumptions_heading="Assumptions used",
        assumptions_columns=(
            "Assumption",
            "Value",
            "Default",
            "Status",
            "Used",
            "Source",
            "Recorded",
        ),
        assumptions=(SCALAR_ROW, STRUCTURED_ROW),
        decisions_heading="Your choices in effect",
        decisions_columns=("Choice", "Value", "Recorded"),
        decisions=(
            DecisionRow(
                label="You — target retirement age",
                value="62",
                recorded="2026-08-01",
                note="",
            ),
        ),
        structure_heading="Plan structure",
        structure_columns=("Entity", "Setting", "Value"),
        structure=(
            StructureRow(
                entity="You",
                setting="Tax residency",
                value="UK (England, Wales, or Northern Ireland)",
            ),
        ),
        summary="summary line",
        override_title="Override assumption",
        override_prompt="New value:",
        table_override_prompt="One 'key = value' line per figure:",
    )


class FakeInputDialog:
    """Test double for the override prompts, single- and multi-line."""

    result: ClassVar[tuple[str, bool]] = ("", False)
    calls: ClassVar[list[tuple[str, str, str]]] = []
    multiline_calls: ClassVar[list[tuple[str, str, str]]] = []

    @classmethod
    def getText(  # noqa: N802 - Qt API name
        cls,
        _parent: InspectorPane,
        title: str,
        label: str,
        *,
        text: str = "",
    ) -> tuple[str, bool]:
        """Record the prompt and return the scripted outcome."""
        cls.calls.append((title, label, text))
        return cls.result

    @classmethod
    def getMultiLineText(  # noqa: N802 - Qt API name
        cls,
        _parent: InspectorPane,
        title: str,
        label: str,
        text: str = "",
    ) -> tuple[str, bool]:
        """Record the multi-line prompt and return the scripted outcome."""
        cls.multiline_calls.append((title, label, text))
        return cls.result


@pytest.fixture(name="fake_dialog")
def fake_dialog_fixture(monkeypatch: pytest.MonkeyPatch) -> type[FakeInputDialog]:
    """Install the dialog double, reset between tests."""
    monkeypatch.setattr(inspector_module, "QInputDialog", FakeInputDialog)
    FakeInputDialog.result = ("", False)
    FakeInputDialog.calls = []
    FakeInputDialog.multiline_calls = []
    return FakeInputDialog


def refreshed_pane(
    on_override: Callable[[str, str], str | None] | None = None,
) -> InspectorPane:
    """A pane refreshed with the hand-built view model."""
    pane = InspectorPane(on_override or (lambda _key, _raw: None))
    pane.refresh(view_model())
    return pane


class TestRendering:
    """Tables render the pre-formatted rows verbatim."""

    def test_tables_render_rows_and_headings(self) -> None:
        """Row counts, headings, and cells come from the view model."""
        pane = refreshed_pane()
        assert pane.facts_table.rowCount() == 1
        assert pane.facts_table.columnCount() == 4
        item = pane.facts_table.item(0, 0)
        assert item is not None
        assert item.text() == "You — date of birth"
        assert pane.assumptions_table.rowCount() == 2
        status_item = pane.assumptions_table.item(0, 3)
        assert status_item is not None
        assert status_item.text() == "Shipped default"
        assert pane.decisions_table.rowCount() == 1
        assert pane.summary_label.text() == "summary line"

    def test_structure_table_renders_entity_rows(self) -> None:
        """The plan-structure section renders its entity-level rows (#70)."""
        pane = refreshed_pane()
        assert pane.structure_table.rowCount() == 1
        assert pane.structure_table.columnCount() == 3
        entity_item = pane.structure_table.item(0, 0)
        assert entity_item is not None
        assert entity_item.text() == "You"
        value_item = pane.structure_table.item(0, 2)
        assert value_item is not None
        assert value_item.text() == "UK (England, Wales, or Northern Ireland)"

    def test_assumption_rows_are_addressable(self) -> None:
        """The pane maps table rows back to their view-model rows."""
        pane = refreshed_pane()
        assert pane.assumption_row(0) is not None
        assert pane.assumption_row(0).key == "inflation.cpi"

    def test_rows_require_a_refresh_first(self) -> None:
        """Reading rows before any refresh is a programming error."""
        pane = InspectorPane(lambda _key, _raw: None)
        with pytest.raises(RuntimeError):
            pane.assumption_row(0)


class TestOverridePrompt:
    """Double-clicking an assumption opens the in-place override."""

    def test_accepted_input_reaches_the_callback(
        self, fake_dialog: type[FakeInputDialog]
    ) -> None:
        """The raw text goes to the app layer; success clears the status."""
        received: list[tuple[str, str]] = []

        def on_override(key: str, raw: str) -> str | None:
            received.append((key, raw))
            return None

        pane = refreshed_pane(on_override)
        fake_dialog.result = ("0.03", True)
        pane.assumptions_table.cellDoubleClicked.emit(0, 1)
        assert received == [("inflation.cpi", "0.03")]
        assert pane.status_label.text() == ""
        [(title, label, prefill)] = fake_dialog.calls
        assert title == "Override assumption"
        assert "inflation.cpi" in label
        assert prefill == "0.02"

    def test_rejected_input_shows_the_error(
        self, fake_dialog: type[FakeInputDialog]
    ) -> None:
        """An app-layer rejection lands in the status line."""
        pane = refreshed_pane(lambda _key, _raw: "no good")
        fake_dialog.result = ("bad", True)
        pane.assumptions_table.cellDoubleClicked.emit(0, 0)
        assert pane.status_label.text() == "no good"

    def test_cancelled_prompt_changes_nothing(
        self, fake_dialog: type[FakeInputDialog]
    ) -> None:
        """Cancelling the dialog never reaches the callback."""
        received: list[tuple[str, str]] = []

        def on_override(key: str, raw: str) -> str | None:
            received.append((key, raw))
            return None

        pane = refreshed_pane(on_override)
        fake_dialog.result = ("0.03", False)
        pane.assumptions_table.cellDoubleClicked.emit(0, 0)
        assert received == []

    def test_structured_rows_open_the_multiline_prompt(
        self, fake_dialog: type[FakeInputDialog]
    ) -> None:
        """A table row edits as multi-line text prefilled with its form."""
        received: list[tuple[str, str]] = []

        def on_override(key: str, raw: str) -> str | None:
            received.append((key, raw))
            return None

        pane = refreshed_pane(on_override)
        fake_dialog.result = ("equity_start = 0.70", True)
        pane.assumptions_table.cellDoubleClicked.emit(1, 0)
        assert received == [("glidepath.default_shape", "equity_start = 0.70")]
        [(title, label, prefill)] = fake_dialog.multiline_calls
        assert title == "Override assumption"
        assert "glidepath.default_shape" in label
        assert prefill == STRUCTURED_ROW.edit_text
        assert fake_dialog.calls == []
