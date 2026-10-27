"""Bump ``[tool.uv] exclude-newer`` in pyproject.toml to now minus the cooldown.

Packages named on the command line (``make deps SECURITY_FIX="..."``) get a
security-fix override in ``exclude-newer-package`` at now minus the shorter
``SECURITY_COOLDOWN``. Existing overrides still ahead of the new cutoff are
kept, so a later ``make deps`` cannot roll a fix back; those the cutoff has
caught up with are dropped.

Run only via ``make deps`` — see the supply-chain policy in CLAUDE.md.
"""

import re
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

COOLDOWN = timedelta(days=7)
SECURITY_COOLDOWN = timedelta(days=1)
PYPROJECT = Path("pyproject.toml")
_PATTERN = re.compile(r'^exclude-newer = ".*"$', flags=re.MULTILINE)
_OVERRIDES = re.compile(r"^exclude-newer-package = .*\n", flags=re.MULTILINE)
# PEP 508 project names: letters, digits and . _ - inside, alphanumeric ends.
_NAME = re.compile(r"[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?")
_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _normalise(name: str) -> str:
    """Return the PEP 503 normalised name, as uv records it in uv.lock."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _kept_overrides(text: str, cutoff: datetime) -> dict[str, datetime]:
    """Return the current overrides that are still ahead of ``cutoff``.

    Raises ValueError (tomllib.TOMLDecodeError is one) or TypeError when
    pyproject.toml does not parse, the table is not the single inline line
    this script maintains, or a value is not a timezone-aware timestamp.
    """
    uv = tomllib.loads(text).get("tool", {}).get("uv", {})
    current = uv.get("exclude-newer-package", {})
    if current and len(_OVERRIDES.findall(text)) != 1:
        msg = "exclude-newer-package must be a single inline-table line"
        raise ValueError(msg)
    kept: dict[str, datetime] = {}
    for name, raw in current.items():
        value = raw if isinstance(raw, datetime) else datetime.fromisoformat(raw)
        if value > cutoff:
            kept[_normalise(name)] = value.astimezone(UTC)
    return kept


def main(packages: Sequence[str] = ()) -> int:
    """Rewrite the cutoffs; on anything unexpected, fail and write nothing."""
    invalid = [name for name in packages if not _NAME.fullmatch(name)]
    if invalid:
        print(f"ERROR: invalid package name(s): {', '.join(invalid)}")
        return 1
    now = datetime.now(tz=UTC)
    cutoff = now - COOLDOWN
    text = PYPROJECT.read_text(encoding="utf-8")
    try:
        overrides = _kept_overrides(text, cutoff)
    except (TypeError, ValueError) as exc:
        print(f"ERROR: cannot read the cutoffs in pyproject.toml: {exc}")
        return 1
    for name in packages:
        overrides[_normalise(name)] = now - SECURITY_COOLDOWN
    replacement = f'exclude-newer = "{cutoff:{_FORMAT}}"'
    if overrides:
        pairs = ", ".join(
            f'{name} = "{stamp:{_FORMAT}}"' for name, stamp in sorted(overrides.items())
        )
        replacement += f"\nexclude-newer-package = {{ {pairs} }}"
    new_text, count = _PATTERN.subn(lambda _: replacement, _OVERRIDES.sub("", text))
    if count != 1:
        print("ERROR: expected exactly one exclude-newer field in pyproject.toml")
        return 1
    PYPROJECT.write_text(new_text, encoding="utf-8", newline="\n")
    print(f"exclude-newer set to {cutoff:{_FORMAT}}")
    for name, value in sorted(overrides.items()):
        print(f"exclude-newer-package {name} = {value:{_FORMAT}}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
