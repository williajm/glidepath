"""Plan file save and load for shells (planning §4.5, §4.7).

The app-layer face of :mod:`glidepath.persistence`: the File menu's
copy, the session-state ↔ plan-document conversions, and save/load
transitions that fold every failure into a status message instead of
raising at a shell. The document stores the household, the scenarios,
and the user's assumption *overrides*; shipped defaults re-resolve on
every load, and a load records nothing back to disk — plans live
wherever the user chooses (§4.5).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from glidepath.app.forms import form_cannot_represent
from glidepath.app.plan import (
    PlanState,
    check_table_override,
    region_for,
    replanned_state,
)
from glidepath.core import AssumptionTarget
from glidepath.persistence import (
    PersistenceError,
    PlanDocument,
    assumption_overrides_from,
    load_plan,
    resolve_assumptions,
    save_plan,
)
from glidepath.regions.uk import default_assumption_set

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path

PLAN_REGION: Final = "uk"

PLAN_FILE_SUFFIX: Final = ".glidepath.json"

PLAN_FILE_FILTER: Final = "glidepath plan (*.glidepath.json)"

FILE_MENU_LABEL: Final = "File"

OPEN_PLAN_LABEL: Final = "Open plan…"

SAVE_PLAN_LABEL: Final = "Save plan"

SAVE_PLAN_AS_LABEL: Final = "Save plan as…"

OPEN_DIALOG_TITLE: Final = "Open plan"

SAVE_DIALOG_TITLE: Final = "Save plan"

NOTHING_TO_SAVE_MESSAGE: Final = (
    "Nothing to save yet — save facts on the Facts tab first."
)

_SAVE_FAILED_PREFIX: Final = "Could not save the plan: "

_OPEN_FAILED_PREFIX: Final = "Could not open the plan: "

_WRONG_REGION_MESSAGE: Final = "this plan's region {region!r} is not supported"

_UNREPRESENTABLE_MESSAGE: Final = (
    "this plan holds {reason}, which the facts form cannot edit yet — "
    "opening it would lose that data on the next save"
)

_DEFAULTS_MOVED_NOTE: Final = (
    "Note: the shipped default assumptions have changed since this plan "
    "was last saved — review the stated-vs-assumed view."
)


@dataclass(frozen=True)
class SaveOutcome:
    """Whether a save wrote the file, and the status line saying so."""

    saved: bool
    message: str


@dataclass(frozen=True)
class LoadOutcome:
    """The loaded session state, or the message explaining the failure.

    ``message`` always carries the status line to show — success and
    failure alike.
    """

    state: PlanState | None
    message: str


def _shipped_data_version() -> str:
    """The shipped region data's content version, defaults applied.

    Computed from the shipped defaults rather than the session's
    effective assumptions, so the recorded version moves exactly when
    the shipped data (or a shipped default policy) moves — never when
    the user overrides an assumption.
    """
    return region_for(default_assumption_set()).data_version


def document_from_state(state: PlanState) -> PlanDocument | None:
    """The session as a storable plan document; ``None`` without a plan."""
    if state.household is None:
        return None
    return PlanDocument(
        region=PLAN_REGION,
        assumptions_resolved_against=_shipped_data_version(),
        household=state.household,
        assumption_overrides=assumption_overrides_from(state.assumptions),
        scenarios=state.scenarios,
    )


def save_plan_state(state: PlanState, path: Path) -> SaveOutcome:
    """Write the session's plan to ``path``, reporting the outcome."""
    document = document_from_state(state)
    if document is None:
        return SaveOutcome(saved=False, message=NOTHING_TO_SAVE_MESSAGE)
    try:
        save_plan(document, path)
    except (PersistenceError, OSError) as exc:
        return SaveOutcome(saved=False, message=f"{_SAVE_FAILED_PREFIX}{exc}")
    return SaveOutcome(saved=True, message=f"Plan saved to {path}.")


def _table_override_error(document: PlanDocument) -> str | None:
    """The first stored table override its policy parser rejects.

    ``resolve_assumptions`` checks only broad value shape (mapping vs
    scalar); a structurally defective table would otherwise decode
    cleanly and raise mid-run, past the load's failure boundary. The
    same policy parsers the editors use are the contract here (issue
    #71), for the base overrides and every scenario's alike.
    """
    for override in document.assumption_overrides:
        if isinstance(override.value, Mapping):
            try:
                check_table_override(override.key, override.value)
            except ValueError as exc:
                return f"override on {override.key.value!r}: {exc}"
    for scenario in document.scenarios:
        for entry in scenario.overrides:
            target = entry.target
            if isinstance(target, AssumptionTarget) and isinstance(
                entry.value, Mapping
            ):
                try:
                    check_table_override(target.key, entry.value)
                except ValueError as exc:
                    return (
                        f"scenario {scenario.name!r} override on"
                        f" {target.key.value!r}: {exc}"
                    )
    return None


def load_plan_state(path: Path, *, today: date) -> LoadOutcome:
    """Read the plan at ``path`` into a freshly projected session state.

    Assumptions re-resolve against the current shipped defaults with
    the stored overrides on top (§4.5); when the shipped data moved
    since the file was saved, the status message says so rather than
    silently re-pricing the plan. A run failure inside an otherwise
    loadable plan still loads — the state carries the run error, and
    the result panes report it exactly as they do after a facts edit.
    A plan the facts form cannot faithfully edit is refused outright:
    opening it would silently discard the extra data on the next save.
    """
    try:
        document = load_plan(path)
    except (PersistenceError, OSError) as exc:
        return LoadOutcome(state=None, message=f"{_OPEN_FAILED_PREFIX}{exc}")
    if document.region != PLAN_REGION:
        detail = _WRONG_REGION_MESSAGE.format(region=document.region)
        return LoadOutcome(state=None, message=f"{_OPEN_FAILED_PREFIX}{detail}")
    reason = form_cannot_represent(document.household)
    if reason is not None:
        detail = _UNREPRESENTABLE_MESSAGE.format(reason=reason)
        return LoadOutcome(state=None, message=f"{_OPEN_FAILED_PREFIX}{detail}")
    table_error = _table_override_error(document)
    if table_error is not None:
        return LoadOutcome(state=None, message=f"{_OPEN_FAILED_PREFIX}{table_error}")
    try:
        assumptions = resolve_assumptions(document, default_assumption_set())
    except PersistenceError as exc:
        return LoadOutcome(state=None, message=f"{_OPEN_FAILED_PREFIX}{exc}")
    state = replanned_state(
        assumptions, document.household, document.scenarios, today=today
    )
    message = f"Plan loaded from {path}."
    if document.assumptions_resolved_against != _shipped_data_version():
        message = f"{message} {_DEFAULTS_MOVED_NOTE}"
    return LoadOutcome(state=state, message=message)


__all__ = [
    "FILE_MENU_LABEL",
    "NOTHING_TO_SAVE_MESSAGE",
    "OPEN_DIALOG_TITLE",
    "OPEN_PLAN_LABEL",
    "PLAN_FILE_FILTER",
    "PLAN_FILE_SUFFIX",
    "PLAN_REGION",
    "SAVE_DIALOG_TITLE",
    "SAVE_PLAN_AS_LABEL",
    "SAVE_PLAN_LABEL",
    "LoadOutcome",
    "SaveOutcome",
    "document_from_state",
    "load_plan_state",
    "save_plan_state",
]
