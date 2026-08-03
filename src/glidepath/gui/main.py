"""Desktop entry point: first-run disclaimer, then the shell (§1, §4.7).

The disclaimer must be accepted before the shell opens; declining
exits without recording anything, so the next launch asks again.
"""

import sys
from datetime import UTC, datetime

from PySide6.QtWidgets import QApplication

from glidepath.app import (
    build_shell_view_model,
    default_state_path,
    load_state,
    record_disclaimer_acknowledged,
    should_show_disclaimer,
)
from glidepath.gui import widgets


def run(argv: list[str] | None = None) -> int:
    """Run the desktop shell; returns the process exit code."""
    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    view_model = build_shell_view_model()
    state_path = default_state_path()
    if should_show_disclaimer(load_state(state_path)):
        if not widgets.prompt_disclaimer(view_model.disclaimer):
            return 0
        record_disclaimer_acknowledged(state_path, datetime.now(tz=UTC).date())
    window = widgets.MainWindow(view_model)
    window.show()
    return app.exec()


def main() -> None:
    """Console-script entry point."""
    raise SystemExit(run())
