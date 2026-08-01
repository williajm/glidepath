"""Bump ``[tool.uv] exclude-newer`` in pyproject.toml to now minus the cooldown.

Run only via ``make deps`` — see the supply-chain policy in CLAUDE.md.
"""

import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

COOLDOWN = timedelta(days=7)
PYPROJECT = Path("pyproject.toml")
_PATTERN = re.compile(r'^exclude-newer = ".*"$', flags=re.MULTILINE)


def main() -> int:
    """Rewrite the exclude-newer timestamp; fail if the field is missing."""
    cutoff = (datetime.now(tz=UTC) - COOLDOWN).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = PYPROJECT.read_text(encoding="utf-8")
    new_text, count = _PATTERN.subn(f'exclude-newer = "{cutoff}"', text)
    if count != 1:
        print("ERROR: expected exactly one exclude-newer field in pyproject.toml")
        return 1
    PYPROJECT.write_text(new_text, encoding="utf-8", newline="\n")
    print(f"exclude-newer set to {cutoff}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
