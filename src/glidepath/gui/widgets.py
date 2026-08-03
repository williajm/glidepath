"""The shell's widgets: disclaimer, main window, and its tabs (§1, §4.7).

The main window owns the immutable app-layer session state and swaps
it through the pure transitions in :mod:`glidepath.app`; widgets only
render view models and forward raw user input back.
"""

from datetime import UTC, datetime

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from glidepath.app import (
    AboutViewModel,
    DisclaimerViewModel,
    FactsFormData,
    ShellViewModel,
    build_inspector_view_model,
    facts_saved_message,
    format_form_errors,
    initial_plan_state,
    parse_facts_form,
    state_with_household,
    state_with_override,
)
from glidepath.gui.forms import FactsEntryPane
from glidepath.gui.inspector import InspectorPane


class DisclaimerDialog(QDialog):
    """Modal first-run disclaimer; accepting is required to proceed (§1)."""

    def __init__(
        self, view_model: DisclaimerViewModel, parent: QWidget | None = None
    ) -> None:
        """Bind the disclaimer view model to the dialog."""
        super().__init__(parent)
        self.setWindowTitle(view_model.title)
        self.setModal(True)

        body = QLabel(view_model.body, self)
        body.setWordWrap(True)

        buttons = QDialogButtonBox(self)
        self.accept_button = QPushButton(view_model.accept_label, self)
        self.decline_button = QPushButton(view_model.decline_label, self)
        buttons.addButton(self.accept_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(self.decline_button, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(body)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    """The application shell: facts entry, the inspector, and Help → About."""

    def __init__(self, view_model: ShellViewModel) -> None:
        """Bind the shell view model to the window and start a session."""
        super().__init__()
        self._view_model = view_model
        self._about_view_model = view_model.about
        self._state = initial_plan_state()
        self.setWindowTitle(view_model.window_title)

        self.facts_pane = FactsEntryPane(
            view_model.facts_form, self._handle_facts_submitted
        )
        self.inspector_pane = InspectorPane(self._handle_override)
        tabs = QTabWidget(self)
        tabs.addTab(self.facts_pane, view_model.facts_tab_label)
        tabs.addTab(self.inspector_pane, view_model.inspector_tab_label)
        self.setCentralWidget(tabs)
        self.inspector_pane.refresh(build_inspector_view_model(self._state))

        # The "&" mnemonic is toolkit mechanics, not copy — the label
        # itself comes from the app layer (§4.7).
        help_menu = self.menuBar().addMenu(f"&{view_model.help_menu_label}")
        about_action = help_menu.addAction(view_model.about.title)
        about_action.triggered.connect(self.show_about)

    def _handle_facts_submitted(self, data: FactsFormData) -> str:
        """Parse a facts submission; on success, re-project and refresh."""
        now = datetime.now(tz=UTC)
        result = parse_facts_form(data, recorded_on=now)
        if result.household is None:
            return format_form_errors(self._view_model.facts_form, result.errors)
        self._state = state_with_household(
            self._state, result.household, today=now.date()
        )
        self.inspector_pane.refresh(build_inspector_view_model(self._state))
        return facts_saved_message(self._state)

    def _handle_override(self, key: str, raw_value: str) -> str | None:
        """Apply an in-place assumption override; report a rejection."""
        now = datetime.now(tz=UTC)
        outcome = state_with_override(
            self._state, key, raw_value, recorded_on=now, today=now.date()
        )
        if outcome.error is not None:
            return outcome.error
        self._state = outcome.state
        self.inspector_pane.refresh(build_inspector_view_model(self._state))
        return None

    def show_about(self) -> None:
        """Show the About box; it repeats the disclaimer (§1)."""
        about = self._about_view_model
        QMessageBox.about(self, about.title, about.body)


def prompt_disclaimer(view_model: DisclaimerViewModel) -> bool:
    """Run the disclaimer dialog modally; True means the user accepted."""
    dialog = DisclaimerDialog(view_model)
    return dialog.exec() == QDialog.DialogCode.Accepted


__all__ = [
    "AboutViewModel",
    "DisclaimerDialog",
    "MainWindow",
    "prompt_disclaimer",
]
