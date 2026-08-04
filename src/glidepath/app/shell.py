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
    HELP_GUIDE_INTRO,
    HELP_GUIDE_SECTIONS,
    HELP_GUIDE_TITLE,
    HELP_MENU_LABEL,
)
from glidepath.app.files import (
    FILE_MENU_LABEL,
    OPEN_DIALOG_TITLE,
    OPEN_PLAN_LABEL,
    PLAN_FILE_FILTER,
    PLAN_FILE_SUFFIX,
    SAVE_DIALOG_TITLE,
    SAVE_PLAN_AS_LABEL,
    SAVE_PLAN_LABEL,
)
from glidepath.app.forms import FactsFormViewModel, build_facts_form_view_model

if TYPE_CHECKING:
    from glidepath.app.firstrun import FirstRunState


@dataclass(frozen=True)
class FileMenuViewModel:
    """The File menu: open and save the plan document (§4.5)."""

    menu_label: str
    open_label: str
    save_label: str
    save_as_label: str
    open_dialog_title: str
    save_dialog_title: str
    file_filter: str
    file_suffix: str


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
class HelpSectionViewModel:
    """One titled passage of the how-to-use guide."""

    heading: str
    body: str


@dataclass(frozen=True)
class HelpGuideViewModel:
    """The how-to-use guide: what each part of the shell is for."""

    title: str
    intro: str
    sections: tuple[HelpSectionViewModel, ...]


@dataclass(frozen=True)
class ShellViewModel:
    """Top-level application shell."""

    window_title: str
    file_menu: FileMenuViewModel
    help_menu_label: str
    facts_tab_label: str
    charts_tab_label: str
    scenarios_tab_label: str
    inspector_tab_label: str
    disclaimer: DisclaimerViewModel
    about: AboutViewModel
    help_guide: HelpGuideViewModel
    facts_form: FactsFormViewModel


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
        file_menu=FileMenuViewModel(
            menu_label=FILE_MENU_LABEL,
            open_label=OPEN_PLAN_LABEL,
            save_label=SAVE_PLAN_LABEL,
            save_as_label=SAVE_PLAN_AS_LABEL,
            open_dialog_title=OPEN_DIALOG_TITLE,
            save_dialog_title=SAVE_DIALOG_TITLE,
            file_filter=PLAN_FILE_FILTER,
            file_suffix=PLAN_FILE_SUFFIX,
        ),
        help_menu_label=HELP_MENU_LABEL,
        facts_tab_label="Facts",
        charts_tab_label="Charts",
        scenarios_tab_label="Scenarios",
        inspector_tab_label="Stated vs assumed",
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
        help_guide=HelpGuideViewModel(
            title=HELP_GUIDE_TITLE,
            intro=HELP_GUIDE_INTRO,
            sections=tuple(
                HelpSectionViewModel(heading=heading, body=body)
                for heading, body in HELP_GUIDE_SECTIONS
            ),
        ),
        facts_form=build_facts_form_view_model(),
    )


def should_show_disclaimer(state: FirstRunState) -> bool:
    """The disclaimer shows on every run until an acknowledgement is recorded."""
    return state.disclaimer_acknowledged_on is None
