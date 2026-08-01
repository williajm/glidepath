"""Fail if any locked dependency was published to PyPI within the cooldown window.

Supply-chain mitigation: freshly published releases are the highest-risk
window for compromised packages. This script parses ``uv.lock``, asks the
PyPI JSON API when each pinned version was first uploaded, and exits
non-zero if anything was published fewer than ``COOLDOWN`` days before the
lockfile's last git commit date (or before "now" if the lockfile is not
yet committed). See CLAUDE.md for the full policy.
"""

import json
import subprocess
import sys
import tomllib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

COOLDOWN = timedelta(days=7)
LOCKFILE = Path("uv.lock")
_HTTP_TIMEOUT_SECONDS = 30.0
_MAX_WORKERS = 8


def _lockfile_reference_date() -> datetime:
    """Return the lockfile's last git commit date, or now (UTC) if uncommitted."""
    result = subprocess.run(  # noqa: S603 -- fixed argv, no untrusted input.
        ["git", "log", "-1", "--format=%cI", "--", str(LOCKFILE)],  # noqa: S607 -- git resolved from PATH by design.
        capture_output=True,
        text=True,
        check=False,
    )
    stamp = result.stdout.strip()
    if result.returncode != 0 or not stamp:
        return datetime.now(tz=UTC)
    return datetime.fromisoformat(stamp)


def _pinned_packages() -> list[tuple[str, str]]:
    """Return (name, version) for every registry-sourced package in uv.lock."""
    with LOCKFILE.open("rb") as handle:
        lock = tomllib.load(handle)
    packages: list[tuple[str, str]] = []
    for package in lock.get("package", []):
        if "registry" not in package.get("source", {}):
            continue  # The project itself is virtual/editable, not from PyPI.
        packages.append((package["name"], package["version"]))
    return packages


def _first_upload_time(package: tuple[str, str]) -> datetime | None:
    """Return the earliest upload time of the pinned release's files on PyPI."""
    name, version = package
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    if not url.startswith("https://pypi.org/"):
        msg = f"refusing non-PyPI URL: {url}"
        raise ValueError(msg)
    with urllib.request.urlopen(
        url,
        timeout=_HTTP_TIMEOUT_SECONDS,
    ) as response:
        payload = json.load(response)
    times = [
        datetime.fromisoformat(file["upload_time_iso_8601"]) for file in payload["urls"]
    ]
    return min(times) if times else None


def main() -> int:
    """Check every locked package against the cooldown and report violations."""
    reference = _lockfile_reference_date()
    cutoff = reference - COOLDOWN
    packages = _pinned_packages()
    print(
        f"Checking {len(packages)} locked packages against "
        f"cutoff {cutoff:%Y-%m-%d %H:%M} UTC"
    )
    violations: list[str] = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        upload_times = list(pool.map(_first_upload_time, packages))
    for (name, version), uploaded in zip(packages, upload_times, strict=True):
        if uploaded is None:
            print(f"WARNING: no files on PyPI for {name}=={version}; skipping")
            continue
        if uploaded > cutoff:
            violations.append(
                f"{name}=={version} published {uploaded:%Y-%m-%d %H:%M} UTC"
            )
    if violations:
        print(f"COOLDOWN VIOLATIONS (published < {COOLDOWN.days} days before lock):")
        for line in violations:
            print(f"  - {line}")
        return 1
    print(f"OK: all packages satisfy the {COOLDOWN.days}-day cooldown.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
