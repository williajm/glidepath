"""First-run state: has the user acknowledged the disclaimer? (§1, §4.7).

A tiny local JSON file records the acknowledgement date. Anything
unreadable — missing file, bad JSON, wrong shape, a schema version
this reader does not know — degrades to "not acknowledged", so the
disclaimer is shown again rather than silently skipped. The state
lives in the platform's per-user configuration directory; shells pass
``default_state_path()`` (or a substitute) in, so this module never
touches Qt or a display.
"""

import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

_STATE_SCHEMA_VERSION = 1
_SCHEMA_KEY = "schema"
_ACKNOWLEDGED_KEY = "disclaimer_acknowledged_on"


@dataclass(frozen=True)
class FirstRunState:
    """What the local state file says about previous runs."""

    disclaimer_acknowledged_on: date | None


def default_state_path() -> Path:
    """Per-user settings file location, following platform convention."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "glidepath" / "settings.json"


def load_state(path: Path) -> FirstRunState:
    """Read first-run state, degrading to "not acknowledged" on any defect."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw[_SCHEMA_KEY] != _STATE_SCHEMA_VERSION:
            return FirstRunState(disclaimer_acknowledged_on=None)
        acknowledged_on = date.fromisoformat(raw[_ACKNOWLEDGED_KEY])
    except OSError, ValueError, KeyError, TypeError:
        return FirstRunState(disclaimer_acknowledged_on=None)
    return FirstRunState(disclaimer_acknowledged_on=acknowledged_on)


def record_disclaimer_acknowledged(path: Path, acknowledged_on: date) -> None:
    """Persist the acknowledgement so later runs skip the first-run screen."""
    payload = {
        _SCHEMA_KEY: _STATE_SCHEMA_VERSION,
        _ACKNOWLEDGED_KEY: acknowledged_on.isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
