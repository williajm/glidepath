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
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QDialog, QTabWidget

from glidepath.app import (
    build_shell_view_model,
    load_state,
    record_disclaimer_acknowledged,
    record_last_plan_path,
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
    """The window binds the shell view model and exposes the Help menu."""

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
        assert menu_titles == [
            f"&{view_model.file_menu.menu_label}",
            f"&{view_model.help_menu_label}",
        ]

    def test_help_menu_offers_guide_and_about(self) -> None:
        """The Help menu's actions carry the view-model titles."""
        view_model = build_shell_view_model()
        window = MainWindow(view_model)
        assert window.help_guide_action.text() == view_model.help_guide.title
        assert window.about_action.text() == view_model.about.title

    def test_file_menu_offers_quit(self) -> None:
        """The File menu ends with a Quit action carrying the app copy."""
        view_model = build_shell_view_model()
        window = MainWindow(view_model)
        assert window.quit_action.text() == view_model.file_menu.quit_label

    def test_about_shows_view_model_copy(self) -> None:
        """About shows the about view model — which repeats the disclaimer."""
        view_model = build_shell_view_model()
        dialog = MainWindow(view_model).about_dialog()
        assert dialog.windowTitle() == view_model.about.title
        assert dialog.body_label.text() == view_model.about.body

    def test_help_guide_shows_every_section(self) -> None:
        """The guide dialog renders one card per view-model section."""
        view_model = build_shell_view_model()
        dialog = MainWindow(view_model).help_guide_dialog()
        assert dialog.windowTitle() == view_model.help_guide.title
        assert dialog.intro_label.text() == view_model.help_guide.intro
        headings = [card.title() for card in dialog.section_cards]
        expected = [section.heading for section in view_model.help_guide.sections]
        assert headings == expected

    def test_show_about_runs_the_dialog_modally(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The About menu handler executes the dialog."""
        executed: list[str] = []

        def fake_exec(_self: widgets.AboutDialog) -> int:
            executed.append("about")
            return 0

        monkeypatch.setattr(widgets.AboutDialog, "exec", fake_exec, raising=True)
        MainWindow(build_shell_view_model()).show_about()
        assert executed == ["about"]

    def test_show_help_guide_runs_the_dialog_modally(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guide menu handler executes the dialog."""
        executed: list[str] = []

        def fake_exec(_self: widgets.HelpGuideDialog) -> int:
            executed.append("guide")
            return 0

        monkeypatch.setattr(widgets.HelpGuideDialog, "exec", fake_exec, raising=True)
        MainWindow(build_shell_view_model()).show_help_guide()
        assert executed == ["guide"]


class TestKeyboardShortcuts:
    """The main window binds the standard accelerators (issue #135)."""

    def test_file_actions_use_the_standard_keys(self) -> None:
        """Open, Save, and Save As follow the platform's conventions."""
        window = MainWindow(build_shell_view_model())
        open_key = QKeySequence(QKeySequence.StandardKey.Open)
        save_key = QKeySequence(QKeySequence.StandardKey.Save)
        save_as_key = QKeySequence(QKeySequence.StandardKey.SaveAs)
        assert window.open_action.shortcut() == open_key
        assert window.save_action.shortcut() == save_key
        assert window.save_as_action.shortcut() == save_as_key

    def test_exports_carry_explicit_accelerators(self) -> None:
        """The exports have no standard key, so they take Ctrl+E variants."""
        window = MainWindow(build_shell_view_model())
        assert window.export_cash_flow_action.shortcut() == QKeySequence("Ctrl+E")
        assert window.export_report_action.shortcut() == QKeySequence("Ctrl+Shift+E")

    def test_quit_is_bound_to_ctrl_q_on_every_platform(self) -> None:
        """Quit takes the Ctrl+Q literal, not StandardKey.Quit.

        Windows resolves the standard key to the rare Key_Exit
        multimedia key rather than an accelerator; the literal gives
        Ctrl+Q everywhere, mapped to Command+Q on macOS.
        """
        window = MainWindow(build_shell_view_model())
        assert window.quit_action.shortcut() == QKeySequence("Ctrl+Q")

    def test_help_guide_uses_the_standard_help_key(self) -> None:
        """The how-to-use guide answers the platform's help key."""
        window = MainWindow(build_shell_view_model())
        help_key = QKeySequence(QKeySequence.StandardKey.HelpContents)
        assert window.help_guide_action.shortcut() == help_key


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

    def test_launch_reopens_the_last_plan(
        self, state_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A remembered plan path is opened as the shell comes up."""
        record_disclaimer_acknowledged(state_path, date(2026, 8, 3))
        plan = tmp_path / "my-plan.glidepath.json"
        record_last_plan_path(state_path, plan)
        opened: list[Path] = []

        def spy_open(_self: MainWindow, path: Path) -> bool:
            opened.append(path)
            return True

        monkeypatch.setattr(widgets.MainWindow, "open_plan", spy_open)
        app = QApplication.instance()
        assert app is not None
        QTimer.singleShot(0, app.quit)
        assert run([]) == 0
        assert opened == [plan]

    def test_launch_without_a_remembered_plan_opens_nothing(
        self, state_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No remembered path — the launch example stays untouched."""
        record_disclaimer_acknowledged(state_path, date(2026, 8, 3))

        def unexpected(_self: MainWindow, _path: Path) -> bool:
            msg = "no plan should be opened at launch"
            raise AssertionError(msg)

        monkeypatch.setattr(widgets.MainWindow, "open_plan", unexpected)
        app = QApplication.instance()
        assert app is not None
        QTimer.singleShot(0, app.quit)
        assert run([]) == 0
