"""Fail if the locked dependency set violates the supply-chain cooldown.

Independently verifies what ``uv lock`` promised. Reads the resolution
cutoff (``exclude-newer``) embedded in ``uv.lock`` and requires it to be
at least ``COOLDOWN`` old right now, requires every locked package to come
from PyPI, and checks the PyPI upload time of every individual artifact
(each wheel and sdist) recorded in the lockfile against the cutoff.
Anything that cannot be verified fails the check. See CLAUDE.md for the
policy.
"""

import json
import sys
import tomllib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

COOLDOWN = timedelta(days=7)
LOCKFILE = Path("uv.lock")
PYPI_REGISTRY = "https://pypi.org/simple"
_ROOT_SOURCES = ({"virtual": "."}, {"editable": "."})
_HTTP_TIMEOUT_SECONDS = 30.0
_MAX_WORKERS = 8


class PolicyError(Exception):
    """A supply-chain policy violation that must fail the check."""


def _load_lock() -> dict[str, Any]:
    """Parse uv.lock."""
    with LOCKFILE.open("rb") as handle:
        return tomllib.load(handle)


def _cutoff(lock: dict[str, Any]) -> datetime:
    """Return the exclude-newer cutoff recorded in uv.lock, validated.

    The cutoff must exist and must be at least ``COOLDOWN`` in the past
    right now — a future-dated or hand-edited timestamp cannot weaken the
    policy.
    """
    raw = lock.get("options", {}).get("exclude-newer")
    if raw is None:
        msg = "uv.lock records no exclude-newer cutoff; re-lock with `make deps`"
        raise PolicyError(msg)
    cutoff = raw if isinstance(raw, datetime) else datetime.fromisoformat(raw)
    if cutoff > datetime.now(tz=UTC) - COOLDOWN:
        msg = f"exclude-newer cutoff {raw} is less than {COOLDOWN.days} days ago"
        raise PolicyError(msg)
    return cutoff


def _locked_artifacts(lock: dict[str, Any]) -> list[tuple[str, str, list[str]]]:
    """Return (name, version, artifact filenames) for every locked package.

    Fails if any package comes from anywhere but PyPI — the root project
    itself (virtual/editable ".") is the single permitted exception — or
    if a registry package has no artifacts recorded.
    """
    packages: list[tuple[str, str, list[str]]] = []
    for package in lock.get("package", []):
        name = package["name"]
        version = package.get("version", "?")
        source = package.get("source", {})
        if source in _ROOT_SOURCES:
            continue
        if source.get("registry") != PYPI_REGISTRY:
            msg = f"{name}=={version} comes from {source!r}, not PyPI"
            raise PolicyError(msg)
        files = [*package.get("wheels", [])]
        if "sdist" in package:
            files.append(package["sdist"])
        filenames = [file["url"].rsplit("/", 1)[-1] for file in files]
        if not filenames:
            msg = f"{name}=={version} has no artifacts recorded in uv.lock"
            raise PolicyError(msg)
        packages.append((name, version, filenames))
    return packages


def _verify_package(cutoff: datetime, item: tuple[str, str, list[str]]) -> list[str]:
    """Return violation messages for one locked package (empty if clean)."""
    name, version, filenames = item
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except OSError as exc:  # URLError/HTTPError/timeouts are all OSError.
        return [f"{name}=={version}: PyPI query failed: {exc}"]
    uploads = {
        file["filename"]: datetime.fromisoformat(file["upload_time_iso_8601"])
        for file in payload["urls"]
    }
    violations = []
    for filename in filenames:
        uploaded = uploads.get(filename)
        if uploaded is None:
            violations.append(f"{name}=={version}: {filename} not found on PyPI")
        elif uploaded > cutoff:
            violations.append(
                f"{name}=={version}: {filename} uploaded "
                f"{uploaded:%Y-%m-%d %H:%M} UTC, after the cutoff"
            )
    return violations


def main() -> int:
    """Verify every locked artifact against the cooldown cutoff."""
    try:
        lock = _load_lock()
        cutoff = _cutoff(lock)
        packages = _locked_artifacts(lock)
    except PolicyError as exc:
        print(f"POLICY VIOLATION: {exc}")
        return 1
    print(
        f"Verifying {len(packages)} locked packages against "
        f"cutoff {cutoff:%Y-%m-%d %H:%M} UTC"
    )
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        results = pool.map(partial(_verify_package, cutoff), packages)
    violations = [violation for package in results for violation in package]
    if violations:
        print("COOLDOWN VIOLATIONS:")
        for line in violations:
            print(f"  - {line}")
        return 1
    print(f"OK: every locked artifact satisfies the {COOLDOWN.days}-day cooldown.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
