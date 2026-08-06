"""Tests for the release scripts (issue #130).

``scripts/bump_version.py`` is `make bump`'s first step and the only
sanctioned way to set the release version; a bad rewrite would corrupt
pyproject.toml for every later gate. ``scripts/release_notes.py`` runs
in ``release.yml`` on a tag push and must refuse to publish a release
whose tag, pyproject version, and changelog section disagree
(planning §4.10).
"""

import textwrap
from typing import TYPE_CHECKING

import pytest

import bump_version
import release_notes
from release_notes import changelog_section

if TYPE_CHECKING:
    from pathlib import Path

# --- bump_version -----------------------------------------------------------

PYPROJECT_TEMPLATE = textwrap.dedent(
    """\
    [project]
    name = "example"
    version = "0.1.0"
    description = "kept byte-for-byte"

    [dependency-groups]
    dev = [
        "pytest",
    ]

    [tool.uv]
    exclude-newer = "2026-01-01T00:00:00Z"
    """
)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["1.2.3", "4.5.6"],
        ["v1.2.3"],
        ["1.2"],
        ["1.2.3.4"],
        ["1.2.3-rc1"],
        ["one.two.three"],
    ],
)
def test_bump_rejects_malformed_arguments(
    argv: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Anything but a single plain X.Y.Z argument is usage-rejected."""
    monkeypatch.chdir(tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT_TEMPLATE, encoding="utf-8")
    exit_code = bump_version.main(argv)
    assert exit_code == 1
    assert "usage:" in capsys.readouterr().out
    assert pyproject.read_text(encoding="utf-8") == PYPROJECT_TEMPLATE


def test_bump_rewrites_only_the_version_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The version field changes; every other byte is preserved."""
    monkeypatch.chdir(tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT_TEMPLATE, encoding="utf-8")
    exit_code = bump_version.main(["1.2.3"])
    assert exit_code == 0
    text = pyproject.read_text(encoding="utf-8")
    expected = PYPROJECT_TEMPLATE.replace('version = "0.1.0"', 'version = "1.2.3"')
    assert text == expected
    assert "version set to 1.2.3" in capsys.readouterr().out


def test_bump_fails_without_version_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No version field means nothing safe to rewrite."""
    monkeypatch.chdir(tmp_path)
    original = '[project]\nname = "example"\n'
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(original, encoding="utf-8")
    exit_code = bump_version.main(["1.2.3"])
    assert exit_code == 1
    assert pyproject.read_text(encoding="utf-8") == original
    assert "exactly one top-level version field" in capsys.readouterr().out


def test_bump_fails_on_ambiguous_version_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second column-zero version line (any table) refuses the rewrite."""
    monkeypatch.chdir(tmp_path)
    original = PYPROJECT_TEMPLATE + '\n[tool.other]\nversion = "9.9.9"\n'
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(original, encoding="utf-8")
    exit_code = bump_version.main(["1.2.3"])
    assert exit_code == 1
    assert pyproject.read_text(encoding="utf-8") == original


# --- release_notes: section extraction --------------------------------------

CHANGELOG_TEMPLATE = textwrap.dedent(
    """\
    # Changelog

    Introductory prose that must never leak into release notes.

    ## [Unreleased]

    ### Added

    - a pending item

    ## [0.2.0] - 2026-08-01

    ### Added

    - feature B

    ### Fixed

    - bug fix B

    ## [0.1.0] - 2026-07-01

    - feature A
    """
)


def test_changelog_section_extracts_between_headings() -> None:
    """A middle section runs from its heading to the next ## heading."""
    body = changelog_section("0.2.0", CHANGELOG_TEMPLATE)
    assert body == "### Added\n\n- feature B\n\n### Fixed\n\n- bug fix B"


def test_changelog_section_extracts_final_section() -> None:
    """The last section runs to the end of the file."""
    body = changelog_section("0.1.0", CHANGELOG_TEMPLATE)
    assert body == "- feature A"


def test_changelog_section_missing_version_returns_none() -> None:
    """No heading for the version means no section."""
    body = changelog_section("9.9.9", CHANGELOG_TEMPLATE)
    assert body is None


def test_changelog_section_dots_are_literal() -> None:
    """Version dots must not act as regex wildcards when matching."""
    text = "## [0x1x0]\n\n- wrong section\n"
    body = changelog_section("0.1.0", text)
    assert body is None


def test_changelog_section_duplicate_headings_uses_first() -> None:
    """With duplicate headings the first section wins (and is returned)."""
    text = "## [0.1.0]\n\n- first\n\n## [0.1.0]\n\n- second\n"
    body = changelog_section("0.1.0", text)
    assert body == "- first"


# --- release_notes: end to end ----------------------------------------------


def _write_release_inputs(
    directory: Path, *, project_version: str, changelog: str = CHANGELOG_TEMPLATE
) -> None:
    """Write the pyproject.toml and CHANGELOG.md the script reads."""
    directory.joinpath("pyproject.toml").write_text(
        f'[project]\nname = "example"\nversion = "{project_version}"\n',
        encoding="utf-8",
    )
    directory.joinpath("CHANGELOG.md").write_text(changelog, encoding="utf-8")


@pytest.mark.parametrize("argv", [[], ["0.2.0"], ["v0.2.0", "extra"]])
def test_release_notes_rejects_malformed_arguments(
    argv: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The tag argument must be a single vX.Y.Z string."""
    monkeypatch.chdir(tmp_path)
    _write_release_inputs(tmp_path, project_version="0.2.0")
    exit_code = release_notes.main(argv)
    assert exit_code == 1
    assert "usage:" in capsys.readouterr().err


def test_release_notes_prints_the_tagged_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A consistent tag prints exactly its changelog section body."""
    monkeypatch.chdir(tmp_path)
    _write_release_inputs(tmp_path, project_version="0.2.0")
    exit_code = release_notes.main(["v0.2.0"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "### Added\n\n- feature B\n\n### Fixed\n\n- bug fix B\n"
    assert "feature A" not in captured.out
    assert "pending item" not in captured.out


def test_release_notes_fails_on_version_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tag that disagrees with pyproject.toml must not publish."""
    monkeypatch.chdir(tmp_path)
    _write_release_inputs(tmp_path, project_version="0.3.0")
    exit_code = release_notes.main(["v0.2.0"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "does not match pyproject.toml" in captured.err
    assert captured.out == ""


def test_release_notes_fails_without_changelog_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A version with no changelog section must not publish."""
    monkeypatch.chdir(tmp_path)
    _write_release_inputs(tmp_path, project_version="0.9.0")
    exit_code = release_notes.main(["v0.9.0"])
    assert exit_code == 1
    assert "no section for [0.9.0]" in capsys.readouterr().err


def test_release_notes_fails_on_empty_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A heading with an empty body is as unpublishable as no heading."""
    monkeypatch.chdir(tmp_path)
    changelog = "# Changelog\n\n## [0.2.0] - 2026-08-01\n\n## [0.1.0]\n\n- old\n"
    _write_release_inputs(tmp_path, project_version="0.2.0", changelog=changelog)
    exit_code = release_notes.main(["v0.2.0"])
    assert exit_code == 1
    assert "no section for [0.2.0]" in capsys.readouterr().err
