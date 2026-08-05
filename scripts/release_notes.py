"""Print the CHANGELOG.md section for a release tag, verifying consistency.

Called by ``.github/workflows/release.yml`` on a ``vX.Y.Z`` tag push to
produce the GitHub Release body. Stdlib-only on purpose: the workflow
runs it with the runner's system Python, no project sync. Fails (exit 1)
when the tag does not match ``[project] version`` in pyproject.toml or
the changelog has no section for the version — a bad tag must not
publish a release. See the release process in docs/planning.md §4.10.
"""

import re
import sys
import tomllib
from pathlib import Path

CHANGELOG = Path("CHANGELOG.md")
PYPROJECT = Path("pyproject.toml")


def changelog_section(version: str, text: str) -> str | None:
    """Return the body under ``## [version]``, or None if the heading is absent."""
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\][^\n]*\n(?P<body>.*?)(?=^## |\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        return None
    return match.group("body").strip()


def main(argv: list[str]) -> int:
    """Print the release notes for the tag; fail on any mismatch."""
    if len(argv) != 1 or not argv[0].startswith("v"):
        print("usage: release_notes.py vX.Y.Z", file=sys.stderr)
        return 1
    version = argv[0].removeprefix("v")
    with PYPROJECT.open("rb") as stream:
        project_version = tomllib.load(stream)["project"]["version"]
    if project_version != version:
        print(
            f"ERROR: tag {argv[0]} does not match pyproject.toml "
            f"version {project_version}",
            file=sys.stderr,
        )
        return 1
    body = changelog_section(version, CHANGELOG.read_text(encoding="utf-8"))
    if not body:
        print(f"ERROR: CHANGELOG.md has no section for [{version}]", file=sys.stderr)
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
