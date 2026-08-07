"""Tests for the supply-chain cooldown scripts (issue #130).

``scripts/check_dep_age.py`` is the independent verifier behind the
7-day cooldown policy (CLAUDE.md): it gates every merge, so a silent
failure — a check that passes when it should not, or one that trusts a
weakened cutoff — has outsized impact. These tests pin the fail-closed
behaviour: malformed lockfiles, non-PyPI sources, artifacts newer than
the cutoff, and network failures must all fail the check.

``scripts/update_exclude_newer.py`` is the `make deps` half of the same
policy: it must rewrite exactly the ``exclude-newer`` timestamp to now
minus the cooldown and touch nothing else.
"""

import http.client
import io
import json
import re
import time
import urllib.error
from datetime import UTC, datetime, timedelta
from email.message import Message
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest

import check_dep_age
import update_exclude_newer
from check_dep_age import (
    _RETRY_PAUSES,
    PolicyError,
    _cutoff,
    _locked_artifacts,
    _RetryTracker,
    _verify_package,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

# A cutoff comfortably before every upload time used in these tests.
CUTOFF = datetime(2026, 1, 8, tzinfo=UTC)


def _payload_bytes(files: list[tuple[str, str]]) -> bytes:
    """A PyPI release JSON payload for (filename, upload time) pairs."""
    return json.dumps(
        {
            "urls": [
                {"filename": filename, "upload_time_iso_8601": uploaded}
                for filename, uploaded in files
            ]
        }
    ).encode()


def _serve_sequence(
    monkeypatch: pytest.MonkeyPatch,
    responses: Sequence[bytes | Exception],
    requested: list[str],
) -> None:
    """Answer PyPI queries from ``responses`` in order, recording URLs.

    A bytes entry is served as the response body; an exception entry is
    raised. The last entry repeats once the sequence is exhausted.
    """

    def fake_urlopen(url: str, timeout: float = 0.0) -> io.BytesIO:
        del timeout
        response = responses[min(len(requested), len(responses) - 1)]
        requested.append(url)
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(response)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def _serve_payload(
    monkeypatch: pytest.MonkeyPatch, payload: bytes, requested: list[str]
) -> None:
    """Answer every PyPI query with ``payload``, recording requested URLs."""
    _serve_sequence(monkeypatch, [payload], requested)


def _http_error(status: HTTPStatus) -> urllib.error.HTTPError:
    """An HTTPError for ``status`` with the boilerplate filled in."""
    return urllib.error.HTTPError(
        "https://pypi.org/pypi/alpha/1.0.0/json", status, status.phrase, Message(), None
    )


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Recorded retry pauses — time.sleep patched to record, not wait."""
    recorded: list[float] = []
    monkeypatch.setattr(time, "sleep", recorded.append)
    return recorded


# --- check_dep_age: cutoff validation ---------------------------------------


def test_cutoff_missing_fails() -> None:
    """A lockfile with no exclude-newer cutoff is a policy violation."""
    lock: dict[str, Any] = {"options": {}}
    with pytest.raises(PolicyError, match="no exclude-newer cutoff"):
        _cutoff(lock)


def test_cutoff_too_recent_fails() -> None:
    """A cutoff younger than the cooldown cannot pass, even by an hour."""
    lock = {"options": {"exclude-newer": datetime.now(tz=UTC) - timedelta(days=6)}}
    with pytest.raises(PolicyError, match="less than 7 days ago"):
        _cutoff(lock)


def test_cutoff_future_dated_fails() -> None:
    """A future-dated (hand-edited) cutoff cannot weaken the policy."""
    lock = {"options": {"exclude-newer": datetime.now(tz=UTC) + timedelta(days=365)}}
    with pytest.raises(PolicyError, match="less than 7 days ago"):
        _cutoff(lock)


def test_cutoff_accepts_old_datetime() -> None:
    """A cutoff older than the cooldown is returned as-is."""
    stamp = datetime.now(tz=UTC) - timedelta(days=8)
    cutoff = _cutoff({"options": {"exclude-newer": stamp}})
    assert cutoff == stamp


def test_cutoff_parses_string_form() -> None:
    """uv.lock records the cutoff as a string; it parses timezone-aware."""
    stamp = datetime.now(tz=UTC) - timedelta(days=10)
    raw = stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff = _cutoff({"options": {"exclude-newer": raw}})
    assert cutoff == stamp.replace(microsecond=0)


# --- check_dep_age: locked package extraction -------------------------------


def _registry_package(
    name: str = "alpha",
    *,
    wheels: list[dict[str, str]] | None = None,
    sdist: dict[str, str] | None = None,
) -> dict[str, Any]:
    """A locked PyPI package entry with the given artifacts."""
    package: dict[str, Any] = {
        "name": name,
        "version": "1.0.0",
        "source": {"registry": "https://pypi.org/simple"},
    }
    if wheels is not None:
        package["wheels"] = wheels
    if sdist is not None:
        package["sdist"] = sdist
    return package


def test_locked_artifacts_skips_root_project() -> None:
    """The virtual/editable "." root project is skipped, not verified."""
    lock = {
        "package": [
            {"name": "glidepath", "version": "0.1.0", "source": {"virtual": "."}},
            {"name": "glidepath", "version": "0.1.0", "source": {"editable": "."}},
        ]
    }
    assert _locked_artifacts(lock) == []


def test_locked_artifacts_rejects_non_pypi_source() -> None:
    """Any registry other than PyPI is a policy violation."""
    lock = {
        "package": [
            {
                "name": "alpha",
                "version": "1.0.0",
                "source": {"registry": "https://evil.example/simple"},
            }
        ]
    }
    with pytest.raises(PolicyError, match="not PyPI"):
        _locked_artifacts(lock)


def test_locked_artifacts_rejects_git_source() -> None:
    """A git dependency has no registry key at all — still a violation."""
    lock = {
        "package": [
            {
                "name": "alpha",
                "version": "1.0.0",
                "source": {"git": "https://github.com/example/alpha"},
            }
        ]
    }
    with pytest.raises(PolicyError, match="not PyPI"):
        _locked_artifacts(lock)


def test_locked_artifacts_rejects_package_without_artifacts() -> None:
    """A registry package with nothing to verify cannot pass silently."""
    lock = {"package": [_registry_package()]}
    with pytest.raises(PolicyError, match="no artifacts recorded"):
        _locked_artifacts(lock)


def test_locked_artifacts_collects_wheel_and_sdist_filenames() -> None:
    """Every wheel and the sdist are extracted by trailing URL segment."""
    lock = {
        "package": [
            _registry_package(
                wheels=[
                    {"url": "https://files.example/x/alpha-1.0.0-py3-none-any.whl"}
                ],
                sdist={"url": "https://files.example/x/alpha-1.0.0.tar.gz"},
            )
        ]
    }
    artifacts = _locked_artifacts(lock)
    assert artifacts == [
        ("alpha", "1.0.0", ["alpha-1.0.0-py3-none-any.whl", "alpha-1.0.0.tar.gz"])
    ]


# --- check_dep_age: per-package verification --------------------------------


def test_verify_package_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Artifacts uploaded before the cutoff produce no violations."""
    requested: list[str] = []
    payload = _payload_bytes([("alpha-1.0.0.tar.gz", "2026-01-01T00:00:00Z")])
    _serve_payload(monkeypatch, payload, requested)
    violations = _verify_package(CUTOFF, ("alpha", "1.0.0", ["alpha-1.0.0.tar.gz"]))
    assert violations == []
    assert requested == ["https://pypi.org/pypi/alpha/1.0.0/json"]


def test_verify_package_flags_upload_after_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An artifact uploaded after the cutoff is reported."""
    payload = _payload_bytes([("alpha-1.0.0.tar.gz", "2026-01-09T00:00:00Z")])
    _serve_payload(monkeypatch, payload, [])
    violations = _verify_package(CUTOFF, ("alpha", "1.0.0", ["alpha-1.0.0.tar.gz"]))
    expected = (
        "alpha==1.0.0: alpha-1.0.0.tar.gz uploaded 2026-01-09 00:00 UTC,"
        " after the cutoff"
    )
    assert violations == [expected]


def test_verify_package_flags_unknown_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A locked artifact PyPI does not list cannot be verified — it fails."""
    _serve_payload(monkeypatch, _payload_bytes([]), [])
    violations = _verify_package(CUTOFF, ("alpha", "1.0.0", ["alpha-1.0.0.tar.gz"]))
    assert violations == ["alpha==1.0.0: alpha-1.0.0.tar.gz not found on PyPI"]


def test_verify_package_fails_closed_on_network_error(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    """A persistent network failure is a violation once retries are spent."""
    requested: list[str] = []
    _serve_sequence(
        monkeypatch, [urllib.error.URLError("connection refused")], requested
    )
    violations = _verify_package(CUTOFF, ("alpha", "1.0.0", ["alpha-1.0.0.tar.gz"]))
    assert len(violations) == 1
    assert "PyPI query failed" in violations[0]
    assert len(requested) == 1 + len(_RETRY_PAUSES)
    assert sleeps == list(_RETRY_PAUSES)


def test_verify_package_fails_closed_on_persistent_server_error(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    """A 5xx on every attempt still converts to a violation, fail-closed."""
    requested: list[str] = []
    _serve_sequence(
        monkeypatch, [_http_error(HTTPStatus.SERVICE_UNAVAILABLE)], requested
    )
    violations = _verify_package(CUTOFF, ("alpha", "1.0.0", ["alpha-1.0.0.tar.gz"]))
    assert len(violations) == 1
    assert "PyPI query failed" in violations[0]
    assert len(requested) == 1 + len(_RETRY_PAUSES)
    assert sleeps == list(_RETRY_PAUSES)


def test_verify_package_retries_transient_server_error(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    """A PyPI blip (here a 503) is retried and the clean answer verifies."""
    payload = _payload_bytes([("alpha-1.0.0.tar.gz", "2026-01-01T00:00:00Z")])
    requested: list[str] = []
    _serve_sequence(
        monkeypatch, [_http_error(HTTPStatus.SERVICE_UNAVAILABLE), payload], requested
    )
    violations = _verify_package(CUTOFF, ("alpha", "1.0.0", ["alpha-1.0.0.tar.gz"]))
    assert violations == []
    assert len(requested) == 2
    assert sleeps == [_RETRY_PAUSES[0]]


def test_verify_package_retries_rate_limit(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    """A 429 is a transient rate-limit signal, not a deterministic 4xx."""
    payload = _payload_bytes([("alpha-1.0.0.tar.gz", "2026-01-01T00:00:00Z")])
    requested: list[str] = []
    _serve_sequence(
        monkeypatch, [_http_error(HTTPStatus.TOO_MANY_REQUESTS), payload], requested
    )
    violations = _verify_package(CUTOFF, ("alpha", "1.0.0", ["alpha-1.0.0.tar.gz"]))
    assert violations == []
    assert len(requested) == 2
    assert sleeps == [_RETRY_PAUSES[0]]


def test_verify_package_retries_garbled_body(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    """A garbled 200 body (JSONDecodeError) is transient, never a crash."""
    payload = _payload_bytes([("alpha-1.0.0.tar.gz", "2026-01-01T00:00:00Z")])
    requested: list[str] = []
    _serve_sequence(monkeypatch, [b"not json", payload], requested)
    violations = _verify_package(CUTOFF, ("alpha", "1.0.0", ["alpha-1.0.0.tar.gz"]))
    assert violations == []
    assert len(requested) == 2
    assert sleeps == [_RETRY_PAUSES[0]]


def test_verify_package_converts_mid_body_disconnect_to_violation(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    """A connection severed mid-body (IncompleteRead) never crashes the check."""
    requested: list[str] = []
    _serve_sequence(monkeypatch, [http.client.IncompleteRead(b"")], requested)
    violations = _verify_package(CUTOFF, ("alpha", "1.0.0", ["alpha-1.0.0.tar.gz"]))
    assert len(violations) == 1
    assert "PyPI query failed" in violations[0]
    assert len(requested) == 1 + len(_RETRY_PAUSES)
    assert sleeps == list(_RETRY_PAUSES)


def test_verify_package_does_not_retry_client_error(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    """A deterministic client error (here a 404) fails without retrying."""
    requested: list[str] = []
    _serve_sequence(monkeypatch, [_http_error(HTTPStatus.NOT_FOUND)], requested)
    violations = _verify_package(CUTOFF, ("alpha", "1.0.0", ["alpha-1.0.0.tar.gz"]))
    assert len(violations) == 1
    assert "PyPI query failed" in violations[0]
    assert len(requested) == 1
    assert sleeps == []


def test_verify_package_stops_retrying_when_tracker_exhausted(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    """An exhausted run-wide retry cap fails straight to the violation."""
    requested: list[str] = []
    _serve_sequence(monkeypatch, [urllib.error.URLError("PyPI down")], requested)
    violations = _verify_package(
        CUTOFF, ("alpha", "1.0.0", ["alpha-1.0.0.tar.gz"]), tracker=_RetryTracker(0)
    )
    assert len(violations) == 1
    assert "PyPI query failed" in violations[0]
    assert len(requested) == 1
    assert sleeps == []


def test_retry_tracker_caps_and_records() -> None:
    """The tracker grants up to its limit and records what it granted."""
    tracker = _RetryTracker(2)
    grants = [tracker.take("a"), tracker.take("b"), tracker.take("c")]
    assert grants == [True, True, False]
    assert tracker.events == ("a", "b")


# --- check_dep_age: end to end ----------------------------------------------


def _write_lockfile(directory: Path, *, cutoff: str | None) -> None:
    """Write a minimal uv.lock with one editable root and one PyPI package."""
    options = f'[options]\nexclude-newer = "{cutoff}"\n\n' if cutoff else ""
    directory.joinpath("uv.lock").write_text(
        f"""\
version = 1
requires-python = ">=3.14"

{options}[[package]]
name = "glidepath"
version = "0.1.0"
source = {{ editable = "." }}

[[package]]
name = "alpha"
version = "1.0.0"
source = {{ registry = "https://pypi.org/simple" }}
sdist = {{ url = "https://files.example/x/alpha-1.0.0.tar.gz" }}
wheels = [
  {{ url = "https://files.example/x/alpha-1.0.0-py3-none-any.whl" }},
]
""",
        encoding="utf-8",
    )


def _old_cutoff_string(days: int = 30) -> str:
    """An exclude-newer string safely older than the cooldown."""
    return (datetime.now(tz=UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_main_passes_clean_lockfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A compliant lockfile verifies every artifact and exits 0."""
    monkeypatch.chdir(tmp_path)
    _write_lockfile(tmp_path, cutoff=_old_cutoff_string())
    payload = _payload_bytes(
        [
            ("alpha-1.0.0.tar.gz", "2020-01-01T00:00:00Z"),
            ("alpha-1.0.0-py3-none-any.whl", "2020-01-01T00:00:00Z"),
        ]
    )
    requested: list[str] = []
    _serve_payload(monkeypatch, payload, requested)
    exit_code = check_dep_age.main()
    assert exit_code == 0
    assert "OK: every locked artifact" in capsys.readouterr().out
    assert requested == ["https://pypi.org/pypi/alpha/1.0.0/json"]


@pytest.mark.usefixtures("sleeps")
def test_main_reports_retries_from_main_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A retried query is reported once, after the workers finish."""
    monkeypatch.chdir(tmp_path)
    _write_lockfile(tmp_path, cutoff=_old_cutoff_string())
    payload = _payload_bytes(
        [
            ("alpha-1.0.0.tar.gz", "2020-01-01T00:00:00Z"),
            ("alpha-1.0.0-py3-none-any.whl", "2020-01-01T00:00:00Z"),
        ]
    )
    requested: list[str] = []
    _serve_sequence(
        monkeypatch, [urllib.error.URLError("handshake timed out"), payload], requested
    )
    exit_code = check_dep_age.main()
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "retried alpha==1.0.0" in out
    assert "OK: every locked artifact" in out
    assert len(requested) == 2


def test_main_fails_on_late_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One artifact newer than the cutoff fails the whole check."""
    monkeypatch.chdir(tmp_path)
    _write_lockfile(tmp_path, cutoff=_old_cutoff_string())
    late = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = _payload_bytes(
        [
            ("alpha-1.0.0.tar.gz", "2020-01-01T00:00:00Z"),
            ("alpha-1.0.0-py3-none-any.whl", late),
        ]
    )
    _serve_payload(monkeypatch, payload, [])
    exit_code = check_dep_age.main()
    assert exit_code == 1
    assert "COOLDOWN VIOLATIONS" in capsys.readouterr().out


def test_main_fails_on_missing_cutoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A lockfile without the embedded cutoff never reaches the network."""
    monkeypatch.chdir(tmp_path)
    _write_lockfile(tmp_path, cutoff=None)
    requested: list[str] = []
    _serve_payload(monkeypatch, _payload_bytes([]), requested)
    exit_code = check_dep_age.main()
    assert exit_code == 1
    assert "POLICY VIOLATION" in capsys.readouterr().out
    assert requested == []


# --- update_exclude_newer ---------------------------------------------------

PYPROJECT_TEMPLATE = """\
[project]
name = "example"
version = "0.1.0"

[tool.uv]
# Supply-chain cooldown comment preserved verbatim.
exclude-newer = "2026-01-01T00:00:00Z"

[tool.other]
setting = "untouched"
"""


def test_update_rewrites_cutoff_to_now_minus_cooldown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The cutoff becomes now minus 7 days; nothing else changes."""
    monkeypatch.chdir(tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT_TEMPLATE, encoding="utf-8")
    before = datetime.now(tz=UTC)
    exit_code = update_exclude_newer.main()
    after = datetime.now(tz=UTC)
    assert exit_code == 0
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^exclude-newer = "(.*)"$', text, flags=re.MULTILINE)
    assert match is not None
    written = datetime.fromisoformat(match.group(1))
    assert written.tzinfo is not None
    cooldown = timedelta(days=7)
    assert written >= before - cooldown - timedelta(seconds=5)
    assert written <= after - cooldown
    expected = PYPROJECT_TEMPLATE.replace(
        'exclude-newer = "2026-01-01T00:00:00Z"', match.group(0)
    )
    assert text == expected
    assert "exclude-newer set to" in capsys.readouterr().out


def test_update_fails_without_cutoff_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pyproject with no exclude-newer field is left untouched."""
    monkeypatch.chdir(tmp_path)
    original = '[project]\nname = "example"\nversion = "0.1.0"\n'
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(original, encoding="utf-8")
    exit_code = update_exclude_newer.main()
    assert exit_code == 1
    assert pyproject.read_text(encoding="utf-8") == original
    assert "expected exactly one exclude-newer field" in capsys.readouterr().out


def test_update_fails_on_duplicate_cutoff_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two exclude-newer lines are ambiguous — refuse to rewrite either."""
    monkeypatch.chdir(tmp_path)
    original = (
        'exclude-newer = "2026-01-01T00:00:00Z"\n'
        'exclude-newer = "2026-02-01T00:00:00Z"\n'
    )
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(original, encoding="utf-8")
    exit_code = update_exclude_newer.main()
    assert exit_code == 1
    assert pyproject.read_text(encoding="utf-8") == original
