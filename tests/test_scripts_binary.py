"""Build tools must be verified before any downloaded executable is used."""

import hashlib
import io
import json
import platform
import sys
import textwrap
import urllib.request
import zipfile
from typing import TYPE_CHECKING

import pytest

import build_binary
from build_binary import prepare_dependency_walker

if TYPE_CHECKING:
    from pathlib import Path


def tool_archive() -> bytes:
    """An inert tool archive; no executable code is downloaded in these tests."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in ("depends.exe", "depends.dll", "depends.chm"):
            archive.writestr(name, "verified content")
        archive.writestr("../unexpected.exe", "unwanted file")
    return buffer.getvalue()


def test_dependency_walker_rejects_changed_archive(tmp_path: Path) -> None:
    """A corrupt cache fails closed, leaving no extracted executable."""
    (tmp_path / "depends22_x64.zip").write_bytes(b"changed download")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        prepare_dependency_walker(
            "https://example.invalid/tool.zip", "0" * 64, tmp_path
        )
    assert not (tmp_path / "depends.exe").exists()


def test_dependency_walker_extracts_only_verified_expected_files(
    tmp_path: Path,
) -> None:
    """Restore cached tools from the verified archive, ignoring extra paths."""
    data = tool_archive()
    (tmp_path / "depends22_x64.zip").write_bytes(data)
    (tmp_path / "depends.exe").write_text("changed extracted tool")
    prepare_dependency_walker(
        "https://example.invalid/tool.zip", hashlib.sha256(data).hexdigest(), tmp_path
    )
    assert (tmp_path / "depends.exe").read_text() == "verified content"
    assert not (tmp_path.parent / "unexpected.exe").exists()


def test_dependency_walker_verifies_fresh_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh response is checked and cached before the tool is extracted."""
    data = tool_archive()

    def download(_url: str, *, timeout: int) -> io.BytesIO:
        assert timeout > 0
        return io.BytesIO(data)

    monkeypatch.setattr(urllib.request, "urlopen", download)
    prepare_dependency_walker(
        "https://example.invalid/tool.zip", hashlib.sha256(data).hexdigest(), tmp_path
    )
    assert (tmp_path / "depends22_x64.zip").read_bytes() == data
    assert (tmp_path / "depends.exe").read_text() == "verified content"


@pytest.mark.parametrize("target", ["linux", "win32"])
@pytest.mark.parametrize("mode", ["standalone", "onefile"])
def test_build_propagates_failure_and_preserves_metadata_arguments(
    target: str, mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the real subprocess boundary using an inert compiler stand-in.

    A failed compiler must fail the release. Version and icon metadata must
    reach it intact, including paths with spaces, and shell metacharacters
    must remain literal arguments. This never compiles a binary in PR CI.
    """
    data = tool_archive()
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setattr(sys, "platform", target)
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(f"""\
        [project]
        version = "9.8.7"
        [tool.glidepath.binary]
        windows-icon = "brand/icon with spaces.ico"
        dependency-walker-url = "https://example.invalid/tool.zip"
        dependency-walker-sha256 = "{digest}"
        nuitka-options = ["--company-name=Test & literal $(text)"]
        """),
        encoding="utf-8",
    )
    cache = tmp_path / "build/nuitka/downloads/depends/x86_64"
    cache.mkdir(parents=True)
    (cache / "depends22_x64.zip").write_bytes(data)
    (tmp_path / "nuitka.py").write_text(
        textwrap.dedent("""\
        import json
        import sys
        from pathlib import Path
        Path("invocation.json").write_text(json.dumps({
            "args": sys.argv[1:], "stdin": sys.stdin.read()
        }))
        raise SystemExit(17)
        """),
        encoding="utf-8",
    )

    assert build_binary.main(["--mode", mode, "--jobs", "2"]) == 17
    invocation = json.loads((tmp_path / "invocation.json").read_text())
    assert invocation["stdin"] == ""
    assert f"--mode={mode}" in invocation["args"]
    assert "--jobs=2" in invocation["args"]
    assert "--file-version=9.8.7" in invocation["args"]
    assert "--product-version=9.8.7" in invocation["args"]
    assert "--company-name=Test & literal $(text)" in invocation["args"]
    if target == "win32":
        assert (
            "--windows-icon-from-ico=brand/icon with spaces.ico" in invocation["args"]
        )


@pytest.mark.parametrize(
    "arguments",
    [
        ["--jobs", "0"],
        ["--jobs", "-1"],
        ["--jobs", "2; touch unexpected"],
        ["--mode", "onefile; touch unexpected"],
        ["--mode=--run"],
    ],
)
def test_build_rejects_invalid_arguments(
    arguments: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid input fails before fetching tools or creating build output."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as error:
        build_binary.main(arguments)
    assert error.value.code != 0
    assert not (tmp_path / "build").exists()


@pytest.mark.parametrize("mode", ["onefile; touch unexpected", "--run", ""])
def test_build_command_rejects_invalid_mode_without_cli_validation(
    mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direct callers must also pass a supported mode before reading config."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="Build mode must be standalone or onefile"):
        build_binary.build_command(mode, 2)


def test_windows_build_rejects_unsupported_architecture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do not silently feed x64 tooling to an ARM Windows interpreter."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(platform, "machine", lambda: "ARM64")
    with pytest.raises(RuntimeError, match="x64 only"):
        build_binary.build_environment()
    assert not (tmp_path / "build").exists()
