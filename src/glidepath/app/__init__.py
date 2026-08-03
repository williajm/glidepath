"""UI-agnostic application layer (roadmap 8.1; planning §4.7).

View models, user-facing copy (including the §1 disclaimer), and
first-run state for any UI shell. Everything here is plain typed
Python over the scenario layer and engine — no Qt imports (guard
test), no assumption of a desktop — so a future web shell can reuse
it unchanged.
"""

from glidepath.app.copy import (
    ABOUT_TITLE,
    APP_NAME,
    DISCLAIMER_ACCEPT_LABEL,
    DISCLAIMER_BODY,
    DISCLAIMER_DECLINE_LABEL,
    DISCLAIMER_TITLE,
    HELP_MENU_LABEL,
)
from glidepath.app.firstrun import (
    FirstRunState,
    default_state_path,
    load_state,
    record_disclaimer_acknowledged,
)
from glidepath.app.shell import (
    AboutViewModel,
    DisclaimerViewModel,
    ShellViewModel,
    build_shell_view_model,
    should_show_disclaimer,
)

__all__ = [
    "ABOUT_TITLE",
    "APP_NAME",
    "DISCLAIMER_ACCEPT_LABEL",
    "DISCLAIMER_BODY",
    "DISCLAIMER_DECLINE_LABEL",
    "DISCLAIMER_TITLE",
    "HELP_MENU_LABEL",
    "AboutViewModel",
    "DisclaimerViewModel",
    "FirstRunState",
    "ShellViewModel",
    "build_shell_view_model",
    "default_state_path",
    "load_state",
    "record_disclaimer_acknowledged",
    "should_show_disclaimer",
]
