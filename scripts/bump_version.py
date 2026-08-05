"""Set ``[project] version`` in pyproject.toml for a release.

Run only via ``make bump V=X.Y.Z`` — see the release process in
CLAUDE.md and docs/planning.md §4.10.
"""

import re
import sys
from pathlib import Path

PYPROJECT = Path("pyproject.toml")
_VERSION_ARG = re.compile(r"\d+\.\d+\.\d+")
_FIELD = re.compile(r'^version = ".*"$', flags=re.MULTILINE)


def main(argv: list[str]) -> int:
    """Rewrite the project version; fail on a malformed or missing argument."""
    if len(argv) != 1 or not _VERSION_ARG.fullmatch(argv[0]):
        print("usage: bump_version.py X.Y.Z (plain SemVer, no leading v)")
        return 1
    text = PYPROJECT.read_text(encoding="utf-8")
    new_text, count = _FIELD.subn(f'version = "{argv[0]}"', text)
    if count != 1:
        print("ERROR: expected exactly one top-level version field in pyproject.toml")
        return 1
    PYPROJECT.write_text(new_text, encoding="utf-8", newline="\n")
    print(f"version set to {argv[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
