"""Whole-shell flow, offscreen (issues 8.2 + 8.3, §4.7).

Facts entered in the first tab run a real projection through the UK
region; the stated-vs-assumed tab re-renders from the result's
provenance; an in-place override flows back through the app layer.
"""

from typing import TYPE_CHECKING

from glidepath.app import ENTITY_ID_KEY, build_shell_view_model
from glidepath.gui import inspector as inspector_module
from glidepath.gui.widgets import MainWindow
from glidepath.regions.uk import ISA_KIND, RUK_RESIDENCY

if TYPE_CHECKING:
    import pytest


def blank_window() -> MainWindow:
    """A window cleared back from the launch example to an empty form."""
    window = MainWindow(build_shell_view_model())
    window.facts_pane.clear_button.click()
    return window


def filled_window() -> MainWindow:
    """A window with a minimal projectable plan typed into the facts tab."""
    window = blank_window()
    facts = window.facts_pane
    facts.person_form.set_value("date_of_birth", "1991-02-01")
    facts.person_form.set_value("tax_residency", str(RUK_RESIDENCY))
    facts.person_form.set_value("target_retirement_age", "60")
    facts.spending_form.set_value("annual_spending_real", "18000")
    wrapper_form = facts.wrappers.add_entry()
    wrapper_form.set_value("kind", str(ISA_KIND))
    wrapper_form.set_value("balance", "25000")
    return window


class TestLaunchExample:
    """The window opens with the §4.9 example plan projected."""

    def test_opens_with_the_example_projected(self) -> None:
        """First launch shows a filled form, live output, and the note."""
        view_model = build_shell_view_model()
        window = MainWindow(view_model)
        facts = window.facts_pane
        assert facts.status_label.text() == view_model.facts_form.example_note
        assert facts.person_form.values()["date_of_birth"] != ""
        assert len(facts.wrappers.forms) == 2
        assert window.inspector_pane.facts_table.rowCount() > 0

    def test_about_dialog_carries_the_wordmark_and_disclaimer(self) -> None:
        """The About dialog heads the §1 disclaimer with the brand wordmark."""
        window = MainWindow(build_shell_view_model())
        dialog = window.about_dialog()
        wordmark = dialog.wordmark_label.pixmap()
        assert not wordmark.isNull()
        assert "glidepath" in dialog.body_label.text()

    def test_clear_button_resets_the_form_and_the_session(self) -> None:
        """One click back to a blank form and an empty projection."""
        view_model = build_shell_view_model()
        window = MainWindow(view_model)
        window.facts_pane.clear_button.click()
        facts = window.facts_pane
        assert facts.status_label.text() == view_model.facts_form.cleared_note
        assert facts.person_form.values()["date_of_birth"] == ""
        assert facts.wrappers.values_list() == ()
        assert facts.db_pensions.values_list() == ()
        assert window.inspector_pane.facts_table.rowCount() == 0


class TestFactsToInspectorFlow:
    """The two tabs share one session state through the app layer."""

    def test_valid_submission_projects_and_fills_the_inspector(self) -> None:
        """Saving facts runs the projection and re-renders the inspector."""
        window = filled_window()
        window.facts_pane.submit_button.click()
        assert "projection run" in window.facts_pane.status_label.text()
        assert window.inspector_pane.facts_table.rowCount() > 0
        assert window.inspector_pane.decisions_table.rowCount() > 0
        assert "Projected" in window.inspector_pane.summary_label.text()
        assert "Run manifest" in window.inspector_pane.summary_label.toolTip()

    def test_successful_save_seeds_row_entity_ids(self) -> None:
        """A typed row adopts its minted id on save (§4.3).

        Without the write-back a resave would mint a fresh entity and
        orphan any scenario override created in between.
        """
        window = filled_window()
        window.facts_pane.submit_button.click()
        [values] = window.facts_pane.wrappers.values_list()
        minted_id = values[ENTITY_ID_KEY]
        assert minted_id != ""
        window.facts_pane.submit_button.click()
        [resubmitted] = window.facts_pane.wrappers.values_list()
        assert resubmitted[ENTITY_ID_KEY] == minted_id

    def test_invalid_submission_reports_field_errors(self) -> None:
        """An empty submission lists the missing required fields."""
        window = blank_window()
        window.facts_pane.submit_button.click()
        status = window.facts_pane.status_label.text()
        assert "required" in status
        assert "Date of birth" in status
        assert window.inspector_pane.facts_table.rowCount() == 0

    def test_override_through_the_inspector_re_stamps_the_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A double-click override lands in the session state (§1)."""
        window = MainWindow(build_shell_view_model())
        pane = window.inspector_pane
        target = next(
            index
            for index in range(pane.assumptions_table.rowCount())
            if pane.assumption_row(index).key == "inflation.cpi"
        )

        class AcceptingDialog:
            """Dialog double that accepts a scripted value."""

            @staticmethod
            def getText(  # noqa: N802 - Qt API name
                _parent: object, _title: str, _label: str, *, text: str = ""
            ) -> tuple[str, bool]:
                """Accept with the scripted override value."""
                del text
                return ("0.03", True)

        monkeypatch.setattr(inspector_module, "QInputDialog", AcceptingDialog)
        pane.assumptions_table.cellDoubleClicked.emit(target, 0)
        updated = next(
            pane.assumption_row(index)
            for index in range(pane.assumptions_table.rowCount())
            if pane.assumption_row(index).key == "inflation.cpi"
        )
        assert updated.status == "Your override"
        assert updated.value == "0.03"
        assert pane.status_label.text() == ""
