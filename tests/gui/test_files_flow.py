"""File menu flow tests, offscreen (planning §4.5, §4.7).

The shell is thin by policy: these tests check that the File menu's
open/save actions route through the app layer's transitions, that the
facts form repopulates from a loaded plan, and that the settings file
remembers the plan for the next launch.
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING

from PySide6.QtCore import QStandardPaths

from glidepath.app import (
    build_shell_view_model,
    example_facts_form_data,
    load_state,
)
from glidepath.gui import widgets
from glidepath.gui.widgets import MainWindow

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _window_with_example(settings_path: Path | None = None) -> MainWindow:
    """A window whose launch example is already submitted and projected."""
    return MainWindow(build_shell_view_model(), settings_path=settings_path)


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
        example_dob = example_facts_form_data().person["date_of_birth"]
        assert window.facts_pane.form_data().person["date_of_birth"] == example_dob
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
