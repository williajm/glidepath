"""The shell's widgets: disclaimer dialog and main window (§1, §4.7)."""

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from glidepath.app import AboutViewModel, DisclaimerViewModel, ShellViewModel


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
    """The application shell: placeholder content and a Help → About menu."""

    def __init__(self, view_model: ShellViewModel) -> None:
        """Bind the shell view model to the window."""
        super().__init__()
        self._about_view_model = view_model.about
        self.setWindowTitle(view_model.window_title)

        placeholder = QLabel(view_model.placeholder, self)
        placeholder.setWordWrap(True)
        self.setCentralWidget(placeholder)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = help_menu.addAction(view_model.about.title)
        about_action.triggered.connect(self.show_about)

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
