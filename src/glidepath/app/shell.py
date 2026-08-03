"""Shell view models: everything a UI shell renders, as plain data (§4.7).

Shells (PySide6 today, possibly web later) bind these to widgets and
forward user actions back; they add no copy or formatting of their own.
"""

from dataclasses import dataclass
from importlib import metadata
from typing import TYPE_CHECKING

from glidepath.app.copy import (
    ABOUT_TITLE,
    APP_NAME,
    DISCLAIMER_ACCEPT_LABEL,
    DISCLAIMER_BODY,
    DISCLAIMER_DECLINE_LABEL,
    DISCLAIMER_TITLE,
    HELP_MENU_LABEL,
)

if TYPE_CHECKING:
    from glidepath.app.firstrun import FirstRunState


@dataclass(frozen=True)
class DisclaimerViewModel:
    """The first-run disclaimer screen (§1 product requirement)."""

    title: str
    body: str
    accept_label: str
    decline_label: str


@dataclass(frozen=True)
class AboutViewModel:
    """The About screen; repeats the disclaimer (§1 product requirement)."""

    title: str
    body: str


@dataclass(frozen=True)
class ShellViewModel:
    """Top-level application shell."""

    window_title: str
    placeholder: str
    help_menu_label: str
    disclaimer: DisclaimerViewModel
    about: AboutViewModel


def _installed_version() -> str:
    """The installed distribution version, or a placeholder outside an install."""
    try:
        return metadata.version(APP_NAME)
    except metadata.PackageNotFoundError:
        return "unknown"


def build_shell_view_model() -> ShellViewModel:
    """Assemble the shell view model from canonical copy and metadata."""
    return ShellViewModel(
        window_title=APP_NAME,
        placeholder=(
            "No plan open yet. Facts entry, the assumptions inspector, and "
            "projection views arrive in later milestones."
        ),
        help_menu_label=HELP_MENU_LABEL,
        disclaimer=DisclaimerViewModel(
            title=DISCLAIMER_TITLE,
            body=DISCLAIMER_BODY,
            accept_label=DISCLAIMER_ACCEPT_LABEL,
            decline_label=DISCLAIMER_DECLINE_LABEL,
        ),
        about=AboutViewModel(
            title=ABOUT_TITLE,
            body=f"{APP_NAME} {_installed_version()}\n\n{DISCLAIMER_BODY}",
        ),
    )


def should_show_disclaimer(state: FirstRunState) -> bool:
    """The disclaimer shows on every run until an acknowledgement is recorded."""
    return state.disclaimer_acknowledged_on is None
