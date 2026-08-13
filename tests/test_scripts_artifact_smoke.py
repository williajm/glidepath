"""Tests for the artifact smoke script (issue #198).

``scripts/smoke_test_artifact.py`` is what CI and ``release.yml`` run
against a freshly installed sdist or wheel; a hole in it would let a
broken artifact reach PyPI. The dev environment installs the project
the same way (entry point metadata included), so every check runs for
real here, and the failure paths are reached by faking only the entry
point lookup or the package location.
"""

import importlib.metadata
from typing import TYPE_CHECKING

import glidepath
import smoke_test_artifact
from smoke_test_artifact import entry_point_error, main, region_data_version

if TYPE_CHECKING:
    import pytest


def fake_entry_point(value: str) -> importlib.metadata.EntryPoint:
    """A gui_scripts entry point with an arbitrary target."""
    return importlib.metadata.EntryPoint(
        name="glidepath", value=value, group="gui_scripts"
    )


class TestMain:
    """The orchestration: exit codes and messages."""

    def test_the_installed_project_passes(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Every check holds against the dev install."""
        exit_code = main([])
        captured = capsys.readouterr()
        assert exit_code == 0
        assert "imports cleanly" in captured.out
        assert "GUI entry point resolves" in captured.out
        assert "UK region data loads" in captured.out

    def test_a_matching_expected_version_passes(self) -> None:
        """The release-tag check accepts the installed version."""
        version = importlib.metadata.version("glidepath")
        assert main(["--expect-version", version]) == 0

    def test_a_version_mismatch_fails(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A tag that disagrees with the metadata must not publish."""
        exit_code = main(["--expect-version", "0.0.0"])
        assert exit_code == 1
        assert "does not match" in capsys.readouterr().err

    def test_unknown_arguments_print_usage(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Anything but the one supported flag is refused."""
        exit_code = main(["--nonsense"])
        assert exit_code == 1
        assert "usage" in capsys.readouterr().err

    def test_an_unresolvable_entry_point_fails(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An entry-point failure surfaces as the exit code."""
        monkeypatch.setattr(smoke_test_artifact, "installed_entry_point", lambda: None)
        exit_code = main([])
        assert exit_code == 1
        assert "entry point" in capsys.readouterr().err


class TestEntryPointResolution:
    """The static entry-point checks, no Qt import anywhere."""

    def test_the_installed_entry_point_resolves(self) -> None:
        """The real metadata names a module file defining the target."""
        assert entry_point_error() is None

    def test_a_missing_entry_point_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Metadata without the gui_scripts entry is a failure."""
        monkeypatch.setattr(smoke_test_artifact, "installed_entry_point", lambda: None)
        error = entry_point_error()
        assert error is not None
        assert "no 'glidepath' gui_scripts entry point" in error

    def test_a_target_without_an_attribute_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bare module target cannot launch anything."""
        target = fake_entry_point("glidepath.gui.main")
        monkeypatch.setattr(
            smoke_test_artifact, "installed_entry_point", lambda: target
        )
        error = entry_point_error()
        assert error is not None
        assert "names no attribute" in error

    def test_a_target_outside_the_package_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The entry point must live in the distribution it ships with."""
        target = fake_entry_point("elsewhere.main:main")
        monkeypatch.setattr(
            smoke_test_artifact, "installed_entry_point", lambda: target
        )
        error = entry_point_error()
        assert error is not None
        assert "outside the glidepath package" in error

    def test_a_missing_module_file_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A target module absent from the artifact is the core failure."""
        target = fake_entry_point("glidepath.gui.nonexistent:main")
        monkeypatch.setattr(
            smoke_test_artifact, "installed_entry_point", lambda: target
        )
        error = entry_point_error()
        assert error is not None
        assert "has no file in the package" in error

    def test_a_package_module_target_resolves_via_init(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A package target is looked up at its __init__, re-exports included.

        ``uk_region`` reaches ``glidepath.regions.uk`` through a
        ``from … import`` — an imported binding is as launchable as a
        local ``def``, so the static check must count it.
        """
        target = fake_entry_point("glidepath.regions.uk:uk_region")
        monkeypatch.setattr(
            smoke_test_artifact, "installed_entry_point", lambda: target
        )
        assert entry_point_error() is None

    def test_a_missing_attribute_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A module that lacks the named callable cannot launch."""
        target = fake_entry_point("glidepath.gui.main:not_there")
        monkeypatch.setattr(
            smoke_test_artifact, "installed_entry_point", lambda: target
        )
        error = entry_point_error()
        assert error is not None
        assert "'not_there' is not defined" in error

    def test_an_assigned_attribute_counts_as_defined(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A plain top-level assignment satisfies the static check."""
        target = fake_entry_point("glidepath.regions.uk.region:_TAX_YEAR_ANCHOR_MONTH")
        monkeypatch.setattr(
            smoke_test_artifact, "installed_entry_point", lambda: target
        )
        assert entry_point_error() is None

    def test_an_annotated_constant_counts_as_defined(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An annotated top-level assignment satisfies the static check."""
        target = fake_entry_point("glidepath.app.outlook:OUTLOOK_HEADING")
        monkeypatch.setattr(
            smoke_test_artifact, "installed_entry_point", lambda: target
        )
        assert entry_point_error() is None


class TestRegionData:
    """The shipped-data load check."""

    def test_the_shipped_data_builds_the_region(self) -> None:
        """The whole UK region builds and names its data version."""
        data_version = region_data_version()
        assert data_version.startswith("uk schema=")


class TestVersionSource:
    """One version source: pyproject metadata, read back everywhere."""

    def test_dunder_version_reads_the_installed_metadata(self) -> None:
        """``glidepath.__version__`` must never state its own figure.

        It previously hardcoded 0.1.0 and silently went stale; pinning
        it to the distribution metadata keeps ``make bump`` the only
        place a version is ever written.
        """
        dunder = glidepath.__version__
        installed = importlib.metadata.version("glidepath")
        assert dunder == installed
