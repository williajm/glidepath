"""Local per-user settings: disclaimer, last plan, facts view (§1, §4.7, §4.13).

A tiny local JSON file records the disclaimer acknowledgement date,
the path of the last plan file saved or opened, and the facts form's
Simple/Detailed view, so the next launch can skip the first-run
screen, reopen the user's plan, and show the form the way they left
it. The view is a display preference, never plan data (§4.13). Anything
unreadable — missing file, bad JSON, wrong shape, a schema version
this reader does not know — degrades to an empty state, so the
disclaimer is shown again (and no plan auto-opens) rather than
guessing. The state lives in the platform's per-user configuration
directory; shells pass ``default_state_path()`` (or a substitute) in,
so this module never touches Qt or a display.
"""

import json
import os
import sys
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

_STATE_SCHEMA_VERSION = 1
_SCHEMA_KEY = "schema"
_ACKNOWLEDGED_KEY = "disclaimer_acknowledged_on"
_LAST_PLAN_KEY = "last_plan_path"
_FACTS_VIEW_KEY = "facts_view"


@dataclass(frozen=True)
class FirstRunState:
    """What the local state file says about previous runs."""

    disclaimer_acknowledged_on: date | None
    last_plan_path: Path | None = None
    facts_view: str | None = None
    """The facts form's view token (``FormView`` value), if ever chosen."""


_EMPTY_STATE = FirstRunState(disclaimer_acknowledged_on=None)


def default_state_path() -> Path:
    """Per-user settings file location, following platform convention."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "glidepath" / "settings.json"


def _acknowledged_from(raw: dict[str, Any]) -> date | None:
    """The recorded acknowledgement date; ``None`` on any defect."""
    try:
        return date.fromisoformat(raw[_ACKNOWLEDGED_KEY])
    except ValueError, KeyError, TypeError:
        return None


def _last_plan_from(raw: dict[str, Any]) -> Path | None:
    """The recorded last plan path; ``None`` on any defect."""
    text = raw.get(_LAST_PLAN_KEY)
    if not isinstance(text, str) or not text:
        return None
    return Path(text)


def _facts_view_from(raw: dict[str, Any]) -> str | None:
    """The recorded facts view token; ``None`` on any defect."""
    text = raw.get(_FACTS_VIEW_KEY)
    return text if isinstance(text, str) and text else None


def load_state(path: Path) -> FirstRunState:
    """Read the local state, degrading to an empty state on any defect.

    Each field degrades independently: a defective acknowledgement
    date never discards a readable last-plan path, and vice versa.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw[_SCHEMA_KEY] != _STATE_SCHEMA_VERSION:
            return _EMPTY_STATE
    except OSError, ValueError, KeyError, TypeError:
        return _EMPTY_STATE
    return FirstRunState(
        disclaimer_acknowledged_on=_acknowledged_from(raw),
        last_plan_path=_last_plan_from(raw),
        facts_view=_facts_view_from(raw),
    )


def _write_state(path: Path, state: FirstRunState) -> None:
    """Persist the whole state, omitting unset fields."""
    payload: dict[str, Any] = {_SCHEMA_KEY: _STATE_SCHEMA_VERSION}
    if state.disclaimer_acknowledged_on is not None:
        payload[_ACKNOWLEDGED_KEY] = state.disclaimer_acknowledged_on.isoformat()
    if state.last_plan_path is not None:
        payload[_LAST_PLAN_KEY] = str(state.last_plan_path)
    if state.facts_view is not None:
        payload[_FACTS_VIEW_KEY] = state.facts_view
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def record_disclaimer_acknowledged(path: Path, acknowledged_on: date) -> None:
    """Persist the acknowledgement so later runs skip the first-run screen."""
    _write_state(
        path, replace(load_state(path), disclaimer_acknowledged_on=acknowledged_on)
    )


def record_last_plan_path(path: Path, plan_path: Path) -> None:
    """Persist the plan file just saved or opened, for the next launch."""
    _write_state(path, replace(load_state(path), last_plan_path=plan_path))


def record_facts_view(path: Path, view: str) -> None:
    """Persist the facts form's chosen view token for the next launch."""
    _write_state(path, replace(load_state(path), facts_view=view))
