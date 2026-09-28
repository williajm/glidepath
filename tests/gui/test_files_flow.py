"""File menu flow tests, offscreen (planning §4.5, §4.7).

The shell is thin by policy: these tests check that the File menu's
open/save actions route through the app layer's transitions, that the
facts form repopulates from a loaded plan, that the settings file
remembers the plan for the next launch, and that closing with unsaved
changes prompts save/discard/cancel (issue #136).
"""

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QMessageBox

from glidepath.app import (
    build_shell_view_model,
    example_facts_form_data,
    load_state,
)
from glidepath.app.files import SaveOutcome
from glidepath.gui import widgets
from glidepath.gui.widgets import MainWindow
from glidepath.persistence import load_plan

if TYPE_CHECKING:
    from pathlib import Path


def _window_with_example(settings_path: Path | None = None) -> MainWindow:
    """A window whose launch example is already submitted and projected."""
    return MainWindow(build_shell_view_model(), settings_path=settings_path)


def _message_box_answering(choice: QMessageBox.StandardButton) -> SimpleNamespace:
    """A QMessageBox stand-in whose question always answers ``choice``."""
    return SimpleNamespace(
        StandardButton=QMessageBox.StandardButton,
        question=lambda *_args: choice,
    )


class TestSaveFlow:
    """Save writes the session's plan through the app layer."""

    def test_save_as_writes_the_file_and_remembers_the_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Save As asks for a path, writes it, and records the settings."""
        settings = tmp_path / "settings.json"
        plan = tmp_path / "my-plan.glidepath.json"
        window = _window_with_example(settings)
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(plan), "")),
        )
        window.save_plan_as_dialog()
        assert plan.exists()
        assert str(plan) in window.statusBar().currentMessage()
        assert load_state(settings).last_plan_path == plan

    def test_save_with_unwritable_settings_saves_and_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A failed settings write must not break the save — but must log."""
        plan = tmp_path / "my-plan.glidepath.json"
        window = _window_with_example(tmp_path / "settings.json")

        def unwritable(_settings: Path, _plan: Path) -> None:
            msg = "config directory is read-only"
            raise OSError(msg)

        monkeypatch.setattr(widgets, "record_last_plan_path", unwritable)
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(plan), "")),
        )
        with caplog.at_level(logging.WARNING):
            window.save_plan_as_dialog()
        assert plan.exists()
        assert any(
            "Could not remember the plan path" in record.getMessage()
            for record in caplog.records
        )

    def test_save_as_appends_the_plan_suffix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bare filename gains the canonical .glidepath.json suffix."""
        window = _window_with_example()
        bare = tmp_path / "my-plan"
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(bare), "")),
        )
        window.save_plan_as_dialog()
        assert (tmp_path / "my-plan.glidepath.json").exists()

    def test_cancelled_dialog_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cancelling the save dialog leaves the disk untouched."""
        window = _window_with_example()
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: ("", "")),
        )
        window.save_plan_as_dialog()
        assert list(tmp_path.iterdir()) == []

    def test_dialogs_start_in_a_user_directory_never_the_install_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """First dialog opens on Documents; later ones beside the plan."""
        window = _window_with_example()
        directories: list[str] = []

        def fake_save(
            _parent: object, _title: str, directory: str, _filter: str
        ) -> tuple[str, str]:
            directories.append(directory)
            return "", ""

        monkeypatch.setattr(
            widgets, "QFileDialog", SimpleNamespace(getSaveFileName=fake_save)
        )
        window.save_plan_as_dialog()
        documents = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        )
        assert directories == [documents]

        plan = tmp_path / "my-plan.glidepath.json"
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(plan), "")),
        )
        window.save_plan_as_dialog()
        directories.clear()

        def fake_open(
            _parent: object, _title: str, directory: str, _filter: str
        ) -> tuple[str, str]:
            directories.append(directory)
            return "", ""

        monkeypatch.setattr(
            widgets, "QFileDialog", SimpleNamespace(getOpenFileName=fake_open)
        )
        window.open_plan_dialog()
        assert directories == [str(tmp_path)]

    def test_save_reuses_the_sessions_path_without_asking(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Once saved, Save writes the same file with no dialog."""
        plan = tmp_path / "my-plan.glidepath.json"
        window = _window_with_example()
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(plan), "")),
        )
        window.save_plan_as_dialog()
        assert plan.exists()

        def unexpected(*_args: object) -> tuple[str, str]:
            msg = "Save must not re-ask for a path"
            raise AssertionError(msg)

        monkeypatch.setattr(
            widgets, "QFileDialog", SimpleNamespace(getSaveFileName=unexpected)
        )
        plan.unlink()
        window.save_plan()
        assert plan.exists()

    def test_save_before_save_as_asks_for_a_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no session path yet, Save falls through to Save As."""
        plan = tmp_path / "first-save.glidepath.json"
        window = _window_with_example()
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(plan), "")),
        )
        window.save_plan()
        assert plan.exists()


class TestOpenFlow:
    """Open loads a plan and repopulates every surface."""

    def test_open_restores_the_saved_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A saved plan reopens with charts and the facts form filled."""
        settings = tmp_path / "settings.json"
        plan = tmp_path / "my-plan.glidepath.json"
        saved_window = _window_with_example()
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(plan), "")),
        )
        saved_window.save_plan_as_dialog()

        window = _window_with_example(settings)
        window.facts_pane.clear_button.click()
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getOpenFileName=lambda *_args: (str(plan), "")),
        )
        window.open_plan_dialog()
        assert str(plan) in window.statusBar().currentMessage()
        assert window.charts_pane.chart_tabs.count() == 3
        example_dob = example_facts_form_data().persons[0].person["date_of_birth"]
        reloaded = window.facts_pane.form_data()
        assert reloaded.persons[0].person["date_of_birth"] == example_dob
        assert load_state(settings).last_plan_path == plan

    def test_failed_open_keeps_the_session(self, tmp_path: Path) -> None:
        """A missing file reports and leaves the current plan alone."""
        window = _window_with_example()
        before = window.facts_pane.form_data()
        assert not window.open_plan(tmp_path / "gone.glidepath.json")
        assert window.statusBar().currentMessage().startswith("Could not open the plan")
        assert window.facts_pane.form_data() == before
        assert window.charts_pane.chart_tabs.count() == 3

    def test_cancelled_open_dialog_changes_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cancelling the open dialog leaves the session untouched."""
        window = _window_with_example()
        before = window.facts_pane.form_data()
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getOpenFileName=lambda *_args: ("", "")),
        )
        window.open_plan_dialog()
        assert window.facts_pane.form_data() == before

    def test_clear_detaches_the_plan_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a clear, Save asks afresh instead of overwriting.

        Clearing resets the session; a later plan entered into the
        blank form is a different plan, so Save must never silently
        overwrite the file the cleared plan came from.
        """
        plan = tmp_path / "my-plan.glidepath.json"
        window = _window_with_example()
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(plan), "")),
        )
        window.save_plan_as_dialog()
        original = plan.read_bytes()

        window.facts_pane.clear_button.click()
        window.facts_pane.set_form_data(example_facts_form_data())
        window.facts_pane.submit_button.click()
        other = tmp_path / "other-plan.glidepath.json"
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(other), "")),
        )
        window.save_plan()
        assert other.exists()
        assert plan.read_bytes() == original

    def test_opened_plan_saves_back_without_asking(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Open adopts the file as the session's path for later saves."""
        plan = tmp_path / "my-plan.glidepath.json"
        saved_window = _window_with_example()
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(plan), "")),
        )
        saved_window.save_plan_as_dialog()

        window = _window_with_example()
        assert window.open_plan(plan)
        plan.unlink()
        window.save_plan()
        assert plan.exists()


def _edited_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, saved: bool = True
) -> tuple[MainWindow, Path, Path]:
    """A salary edit, with another file ready to replace the session."""
    current = tmp_path / "current.glidepath.json"
    other = tmp_path / "other.glidepath.json"
    original = _window_with_example()
    monkeypatch.setattr(
        widgets,
        "QFileDialog",
        SimpleNamespace(getSaveFileName=lambda *_args: (str(other), "")),
    )
    original.save_plan_as_dialog()
    window = _window_with_example()
    monkeypatch.setattr(
        widgets,
        "QFileDialog",
        SimpleNamespace(getSaveFileName=lambda *_args: (str(current), "")),
    )
    if saved:
        window.save_plan_as_dialog()
    window.facts_pane.person_form.set_value("employment_income", "61000")
    window.facts_pane.submit_button.click()
    return window, current, other


def _replace_plan(window: MainWindow, path: Path, action: str) -> None:
    """Request either of the actions that replaces the current plan."""
    if action == "open":
        window.open_plan(path)
    else:
        window.facts_pane.clear_button.click()


@pytest.mark.parametrize("action", ["open", "clear"])
class TestPlanReplacement:
    """Opening and clearing honour the close flow's unsaved-edit protection."""

    @pytest.mark.parametrize("rejection", ["cancel", "cancel_save", "failed_save"])
    def test_abandoned_replacement_preserves_form_and_session(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        action: str,
        rejection: str,
    ) -> None:
        """Cancel or an unsuccessful save preserves edits and the save path."""
        window, current, other = _edited_window(
            tmp_path, monkeypatch, saved=rejection != "cancel_save"
        )
        before = window.facts_pane.form_data()
        status = window.facts_pane.status_label.text()
        with monkeypatch.context() as rejecting:
            choice = (
                QMessageBox.StandardButton.Cancel
                if rejection == "cancel"
                else QMessageBox.StandardButton.Save
            )
            rejecting.setattr(widgets, "QMessageBox", _message_box_answering(choice))
            if rejection == "cancel_save":
                rejecting.setattr(
                    widgets,
                    "QFileDialog",
                    SimpleNamespace(getSaveFileName=lambda *_args: ("", "")),
                )
            elif rejection == "failed_save":
                rejecting.setattr(
                    widgets,
                    "save_plan_state",
                    lambda *_args: SaveOutcome(saved=False, message="Disk full"),
                )
            _replace_plan(window, other, action)
        assert window.facts_pane.form_data() == before
        assert window.facts_pane.status_label.text() == status
        assert window.inspector_pane.facts_table.rowCount() > 0
        window.save_plan()
        income = load_plan(current).household.persons[0].employment_income
        assert income is not None
        assert income.value.amount == 61000

    @pytest.mark.parametrize(
        "choice", [QMessageBox.StandardButton.Save, QMessageBox.StandardButton.Discard]
    )
    def test_confirmed_replacement_saves_only_when_requested(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        action: str,
        choice: QMessageBox.StandardButton,
    ) -> None:
        """Save writes the old plan before replacing it; Discard leaves it alone."""
        window, current, other = _edited_window(tmp_path, monkeypatch)
        original = current.read_bytes()
        monkeypatch.setattr(widgets, "QMessageBox", _message_box_answering(choice))
        _replace_plan(window, other, action)
        if choice == QMessageBox.StandardButton.Save:
            income = load_plan(current).household.persons[0].employment_income
            assert income is not None
            assert income.value.amount == 61000
        else:
            assert current.read_bytes() == original
        if action == "open":
            values = window.facts_pane.person_form.values()
            assert values["employment_income"] == "52000"
        else:
            assert window.facts_pane.wrappers.values_list() == ()
            assert window.inspector_pane.facts_table.rowCount() == 0


def test_saving_before_reopening_the_same_file_loads_the_saved_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Open must read the file after Save, otherwise it restores stale facts."""
    window, current, _other = _edited_window(tmp_path, monkeypatch)
    monkeypatch.setattr(
        widgets, "QMessageBox", _message_box_answering(QMessageBox.StandardButton.Save)
    )
    assert window.open_plan(current)
    assert window.facts_pane.person_form.values()["employment_income"] == "61000"


class TestCloseFlow:
    """Closing with unsaved changes prompts save/discard/cancel (#136)."""

    def test_clean_close_never_prompts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The launch example is shipped demo data — close is silent."""

        def unexpected(*_args: object) -> QMessageBox.StandardButton:
            msg = "a clean session must not prompt on close"
            raise AssertionError(msg)

        window = _window_with_example()
        window.show()
        monkeypatch.setattr(
            widgets,
            "QMessageBox",
            SimpleNamespace(
                StandardButton=QMessageBox.StandardButton, question=unexpected
            ),
        )
        assert window.close()
        assert not window.isVisible()

    def test_cancel_keeps_the_window_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Answering Cancel abandons the close, edits intact."""
        window = _window_with_example()
        window.show()
        window.facts_pane.submit_button.click()
        monkeypatch.setattr(
            widgets,
            "QMessageBox",
            _message_box_answering(QMessageBox.StandardButton.Cancel),
        )
        assert not window.close()
        assert window.isVisible()

    def test_discard_closes_without_writing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Answering Discard closes; no save dialog, no file."""

        def unexpected(*_args: object) -> tuple[str, str]:
            msg = "Discard must not open a save dialog"
            raise AssertionError(msg)

        window = _window_with_example()
        window.show()
        window.facts_pane.submit_button.click()
        monkeypatch.setattr(
            widgets, "QFileDialog", SimpleNamespace(getSaveFileName=unexpected)
        )
        monkeypatch.setattr(
            widgets,
            "QMessageBox",
            _message_box_answering(QMessageBox.StandardButton.Discard),
        )
        assert window.close()
        assert not window.isVisible()

    def test_save_writes_the_sessions_file_and_closes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Answering Save writes the session's plan file, then closes."""
        plan = tmp_path / "my-plan.glidepath.json"
        window = _window_with_example()
        window.show()
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(plan), "")),
        )
        window.save_plan_as_dialog()
        window.facts_pane.submit_button.click()
        plan.unlink()
        monkeypatch.setattr(
            widgets,
            "QMessageBox",
            _message_box_answering(QMessageBox.StandardButton.Save),
        )
        assert window.close()
        assert plan.exists()
        assert not window.isVisible()

    def test_a_cancelled_save_keeps_the_window_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Answering Save then cancelling the path dialog must not close.

        The prompt promised to keep the changes; closing anyway would
        discard them behind the user's back.
        """
        window = _window_with_example()
        window.show()
        window.facts_pane.submit_button.click()
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: ("", "")),
        )
        monkeypatch.setattr(
            widgets,
            "QMessageBox",
            _message_box_answering(QMessageBox.StandardButton.Save),
        )
        assert not window.close()
        assert window.isVisible()

    def test_saving_clears_the_prompt_for_later_closes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Once saved, a close is silent until the next edit."""

        def unexpected(*_args: object) -> QMessageBox.StandardButton:
            msg = "a saved session must not prompt on close"
            raise AssertionError(msg)

        plan = tmp_path / "my-plan.glidepath.json"
        window = _window_with_example()
        window.show()
        window.facts_pane.submit_button.click()
        monkeypatch.setattr(
            widgets,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *_args: (str(plan), "")),
        )
        window.save_plan()
        monkeypatch.setattr(
            widgets,
            "QMessageBox",
            SimpleNamespace(
                StandardButton=QMessageBox.StandardButton, question=unexpected
            ),
        )
        assert window.close()

    def test_quit_action_routes_through_the_close_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """File → Quit is a close, so it honours the same prompt."""
        window = _window_with_example()
        window.show()
        window.facts_pane.submit_button.click()
        monkeypatch.setattr(
            widgets,
            "QMessageBox",
            _message_box_answering(QMessageBox.StandardButton.Cancel),
        )
        window.quit_action.trigger()
        assert window.isVisible()
        monkeypatch.setattr(
            widgets,
            "QMessageBox",
            _message_box_answering(QMessageBox.StandardButton.Discard),
        )
        window.quit_action.trigger()
        assert not window.isVisible()
