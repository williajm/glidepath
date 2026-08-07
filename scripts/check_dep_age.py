"""Fail if the locked dependency set violates the supply-chain cooldown.

Independently verifies what ``uv lock`` promised. Reads the resolution
cutoff (``exclude-newer``) embedded in ``uv.lock`` and requires it to be
at least ``COOLDOWN`` old right now, requires every locked package to come
from PyPI, and checks the PyPI upload time of every individual artifact
(each wheel and sdist) recorded in the lockfile against the cutoff.
Anything that cannot be verified fails the check. See CLAUDE.md for the
policy.
"""

import http.client
import json
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from http import HTTPStatus
from pathlib import Path
from typing import Any

COOLDOWN = timedelta(days=7)
LOCKFILE = Path("uv.lock")
PYPI_REGISTRY = "https://pypi.org/simple"
_ROOT_SOURCES = ({"virtual": "."}, {"editable": "."})
_HTTP_TIMEOUT_SECONDS = 30.0
_MAX_WORKERS = 8
_RETRY_PAUSES = (5.0, 10.0)
# Enough for a handful of flaky queries; a systemic PyPI outage exhausts
# it quickly so the run fails in one timeout wave instead of stacking
# every package's retries and sleeps.
_MAX_TOTAL_RETRIES = 10
# 408/429 are timeout/rate-limit signals — the one transient corner of
# the otherwise deterministic 4xx range (the check's own 8-way burst can
# plausibly draw a 429).
_RETRYABLE_CLIENT_STATUSES = frozenset(
    {HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.TOO_MANY_REQUESTS}
)
# HTTPError (an OSError) is classified per-status by _should_retry;
# HTTPException covers a connection severed mid-body (IncompleteRead);
# JSONDecodeError covers a truncated or garbled 200 payload.
_TRANSIENT_ERRORS = (OSError, http.client.HTTPException, json.JSONDecodeError)


class PolicyError(Exception):
    """A supply-chain policy violation that must fail the check."""


class _RetryTracker:
    """Thread-safe retry bookkeeping shared by the worker threads.

    Caps the retries spent across the whole run — a couple of flaky
    queries retry freely, while a systemic outage exhausts the cap and
    later failures go straight to their violations instead of stacking
    minutes of sleeps — and records what was retried so ``main`` can
    report it once, from one thread, instead of interleaving prints.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._lock = threading.Lock()
        self._events: list[str] = []

    def take(self, event: str) -> bool:
        """Consume one retry for ``event``; False once the cap is spent."""
        with self._lock:
            if len(self._events) >= self._limit:
                return False
            self._events.append(event)
            return True

    @property
    def events(self) -> tuple[str, ...]:
        """The granted retries, in grant order."""
        with self._lock:
            return tuple(self._events)


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


def _release_payload(name: str, version: str) -> dict[str, Any]:
    """Fetch one release's JSON document from PyPI."""
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        payload: dict[str, Any] = json.load(response)
    return payload


def _should_retry(exc: Exception) -> bool:
    """Whether a failed fetch is worth another attempt.

    HTTP client errors are deterministic — bar the timeout/rate-limit
    statuses in ``_RETRYABLE_CLIENT_STATUSES`` — while server errors,
    network faults, and truncated or garbled bodies are transient.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return (
            exc.code >= HTTPStatus.INTERNAL_SERVER_ERROR
            or exc.code in _RETRYABLE_CLIENT_STATUSES
        )
    return True


def _release_payload_with_retry(
    name: str, version: str, tracker: _RetryTracker
) -> dict[str, Any]:
    """Fetch a release's JSON, retrying transient failures.

    A transient failure — a network fault, an HTTP 5xx/408/429, a
    truncated or garbled body — gets one more attempt per pause in
    ``_RETRY_PAUSES``, so a runner blip does not read as a cooldown
    violation; ``tracker`` caps the retries spent across the whole run.
    A deterministic client error, and the last failure of anything
    transient, always propagates — the caller reports it as a
    violation, keeping the check fail-closed.
    """
    pauses = iter(_RETRY_PAUSES)
    while True:
        try:
            payload = _release_payload(name, version)
        except _TRANSIENT_ERRORS as exc:
            pause = next(pauses, None)
            if (
                pause is None
                or not _should_retry(exc)
                or not tracker.take(f"{name}=={version}: {exc}")
            ):
                raise
            time.sleep(pause)
        else:
            return payload


def _verify_package(
    cutoff: datetime,
    item: tuple[str, str, list[str]],
    tracker: _RetryTracker | None = None,
) -> list[str]:
    """Return violation messages for one locked package (empty if clean)."""
    name, version, filenames = item
    if tracker is None:
        tracker = _RetryTracker(_MAX_TOTAL_RETRIES)
    try:
        payload = _release_payload_with_retry(name, version, tracker)
    except _TRANSIENT_ERRORS as exc:
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
    tracker = _RetryTracker(_MAX_TOTAL_RETRIES)
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        results = pool.map(partial(_verify_package, cutoff, tracker=tracker), packages)
    violations = [violation for package in results for violation in package]
    for event in tracker.events:
        print(f"  retried {event}")
    if violations:
        print("COOLDOWN VIOLATIONS:")
        for line in violations:
            print(f"  - {line}")
        return 1
    print(f"OK: every locked artifact satisfies the {COOLDOWN.days}-day cooldown.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
