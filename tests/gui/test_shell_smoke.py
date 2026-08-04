"""PySide6 shell smoke tests, offscreen (issue 8.1, §1, §4.7).

The shell is thin by policy, so these tests only check the bindings:
view-model copy reaches the widgets, the disclaimer gates the shell,
and declining exits without recording an acknowledgement.

The offscreen QPA platform is selected (conftest fixture) before the
singleton QApplication is created, so the suite runs headless on both
CI and workstations.
"""

from datetime import date
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QTabWidget

from glidepath.app import (
    build_shell_view_model,
    load_state,
    record_disclaimer_acknowledged,
)
from glidepath.gui import main as main_module
from glidepath.gui import widgets
from glidepath.gui.main import main, run
from glidepath.gui.widgets import DisclaimerDialog, MainWindow, prompt_disclaimer

if TYPE_CHECKING:
    from pathlib import Path


class TestDisclaimerDialog:
    """The dialog binds the view model and maps buttons to accept/reject."""

    def test_binds_view_model_copy(self) -> None:
        """Title and button labels come from the view model, not the widget."""
        view_model = build_shell_view_model().disclaimer
        dialog = DisclaimerDialog(view_model)
        assert dialog.windowTitle() == view_model.title
        assert dialog.accept_button.text() == view_model.accept_label
        assert dialog.decline_button.text() == view_model.decline_label

    def test_accept_button_accepts(self) -> None:
        """Clicking accept resolves the dialog as accepted."""
        dialog = DisclaimerDialog(build_shell_view_model().disclaimer)
        dialog.accept_button.click()
        assert dialog.result() == QDialog.DialogCode.Accepted

    def test_decline_button_rejects(self) -> None:
        """Clicking decline resolves the dialog as rejected."""
        dialog = DisclaimerDialog(build_shell_view_model().disclaimer)
        dialog.decline_button.click()
        assert dialog.result() == QDialog.DialogCode.Rejected

    @pytest.mark.parametrize(
        ("dialog_code", "expected"),
        [(QDialog.DialogCode.Accepted, True), (QDialog.DialogCode.Rejected, False)],
    )
    def test_prompt_reports_the_dialog_outcome(
        self,
        monkeypatch: pytest.MonkeyPatch,
        dialog_code: QDialog.DialogCode,
        expected: bool,  # noqa: FBT001 — parametrized expected outcome.
    ) -> None:
        """The modal prompt maps the dialog code to a plain bool."""
        monkeypatch.setattr(
            DisclaimerDialog, "exec", lambda _self: dialog_code, raising=True
        )
        assert prompt_disclaimer(build_shell_view_model().disclaimer) is expected


class TestMainWindow:
    """The window binds the shell view model and exposes About."""

    def test_binds_view_model_copy(self) -> None:
        """Window title and tab labels come from the view model."""
        view_model = build_shell_view_model()
        window = MainWindow(view_model)
        assert window.windowTitle() == view_model.window_title
        central = window.centralWidget()
        assert isinstance(central, QTabWidget)
        tab_labels = [central.tabText(index) for index in range(central.count())]
        assert tab_labels == [
            view_model.facts_tab_label,
            view_model.charts_tab_label,
            view_model.scenarios_tab_label,
            view_model.inspector_tab_label,
        ]
        menu_titles = [action.text() for action in window.menuBar().actions()]
        assert menu_titles == [f"&{view_model.help_menu_label}"]

    def test_about_shows_view_model_copy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """About shows the about view model — which repeats the disclaimer."""
        shown: list[tuple[str, str]] = []

        class RecordingMessageBox:
            """Test double capturing the About invocation."""

            @staticmethod
            def about(_parent: MainWindow, title: str, body: str) -> None:
                """Record what would have been shown."""
                shown.append((title, body))

        monkeypatch.setattr(widgets, "QMessageBox", RecordingMessageBox)
        view_model = build_shell_view_model()
        MainWindow(view_model).show_about()
        assert shown == [(view_model.about.title, view_model.about.body)]


class TestRun:
    """The entry point gates the shell behind the disclaimer."""

    @pytest.fixture
    def state_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Point the entry point at a per-test state file."""
        path = tmp_path / "settings.json"
        monkeypatch.setattr(main_module, "default_state_path", lambda: path)
        return path

    def test_decline_exits_without_recording(
        self, state_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Declining exits cleanly; the next launch must ask again."""
        monkeypatch.setattr(widgets, "prompt_disclaimer", lambda _vm: False)
        assert run([]) == 0
        assert load_state(state_path).disclaimer_acknowledged_on is None

    def test_accept_records_and_opens_the_shell(
        self, state_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Accepting records the acknowledgement and enters the event loop."""
        monkeypatch.setattr(widgets, "prompt_disclaimer", lambda _vm: True)
        app = QApplication.instance()
        assert app is not None
        QTimer.singleShot(0, app.quit)
        assert run([]) == 0
        assert load_state(state_path).disclaimer_acknowledged_on is not None

    @pytest.mark.usefixtures("state_path")
    def test_accept_survives_an_unwritable_state_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Failing to record the acknowledgement must not kill the session."""

        def unwritable(_path: Path, _on: date) -> None:
            msg = "config directory is read-only"
            raise OSError(msg)

        monkeypatch.setattr(widgets, "prompt_disclaimer", lambda _vm: True)
        monkeypatch.setattr(main_module, "record_disclaimer_acknowledged", unwritable)
        app = QApplication.instance()
        assert app is not None
        QTimer.singleShot(0, app.quit)
        assert run([]) == 0

    def test_acknowledged_run_skips_the_disclaimer(
        self, state_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Once acknowledged, later launches never re-prompt."""

        def unexpected(_vm: object) -> bool:
            msg = "disclaimer must not be shown after acknowledgement"
            raise AssertionError(msg)

        record_disclaimer_acknowledged(state_path, date(2026, 8, 3))
        monkeypatch.setattr(widgets, "prompt_disclaimer", unexpected)
        app = QApplication.instance()
        assert app is not None
        QTimer.singleShot(0, app.quit)
        assert run([]) == 0

    def test_main_raises_system_exit_with_the_exit_code(self, state_path: Path) -> None:
        """The console entry point converts the exit code to SystemExit."""
        record_disclaimer_acknowledged(state_path, date(2026, 8, 3))
        app = QApplication.instance()
        assert app is not None
        QTimer.singleShot(0, app.quit)
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 0
