"""Build the desktop app with the locked Nuitka toolchain.

Run via ``make binary`` or the equivalent uv command in docs/packaging.md.
The default is a folder bundle; validate it before trying ``--mode onefile``.
"""

import argparse
import hashlib
import io
import os
import platform
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path


def prepare_dependency_walker(url: str, digest: str, cache: Path) -> None:
    """Verify the pinned build tool before extracting only its expected files."""
    archive = cache / "depends22_x64.zip"
    cache.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        data = archive.read_bytes()
    else:
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 — repository-controlled HTTPS URL, digest checked below.
            data = response.read()
    if hashlib.sha256(data).hexdigest() != digest:
        message = "Dependency Walker checksum mismatch; refusing to use the download"
        raise RuntimeError(message)
    archive.write_bytes(data)
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        for name in ("depends.exe", "depends.dll", "depends.chm"):
            (cache / name).write_bytes(zipped.read(name))


def build_environment() -> dict[str, str]:
    """Use a local, verified Dependency Walker cache on Windows x64."""
    environment = dict(os.environ)
    if sys.platform == "win32":
        if platform.machine().lower() not in {"amd64", "x86_64"}:
            message = "The Windows binary trial currently supports x64 only"
            raise RuntimeError(message)
        with Path("pyproject.toml").open("rb") as stream:
            settings = tomllib.load(stream)["tool"]["glidepath"]["binary"]
        downloads = Path("build/nuitka/downloads").resolve()
        prepare_dependency_walker(
            settings["dependency-walker-url"],
            settings["dependency-walker-sha256"],
            downloads / "depends" / "x86_64",
        )
        environment["NUITKA_CACHE_DIR_DOWNLOADS"] = str(downloads)
    return environment


def build_command(mode: str, jobs: int) -> list[str]:
    """Read shared packaging options and the canonical version from pyproject."""
    with Path("pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)
    version = project["project"]["version"]
    command = [
        sys.executable,
        "-m",
        "nuitka",
        f"--mode={mode}",
        "--output-dir=build/nuitka",
        "--report=build/nuitka/compilation-report.xml",
        f"--jobs={jobs}",
        f"--product-version={version}",
        f"--file-version={version}",
        *project["tool"]["glidepath"]["binary"]["nuitka-options"],
    ]
    if sys.platform == "win32":
        command.extend(
            [
                "--msvc=latest",
                "--disable-cache=ccache",
                "--windows-console-mode=disable",
                f"--windows-icon-from-ico={project['tool']['glidepath']['binary']['windows-icon']}",
                "--output-filename=glidepath.exe",
            ]
        )
    else:
        command.append("--output-filename=glidepath")
    command.append("scripts/glidepath_binary.py")
    return command


def main(argv: list[str] | None = None) -> int:
    """Compile without a shell; propagate a failed compiler exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("standalone", "onefile"), default="standalone"
    )
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    Path("build/nuitka").mkdir(parents=True, exist_ok=True)
    # Closed stdin declines optional tool downloads; dependencies come from
    # the lockfile and the C compiler must already be installed.
    return subprocess.run(  # noqa: S603 — fixed executable and argument list, no shell.
        build_command(args.mode, args.jobs),
        check=False,
        stdin=subprocess.DEVNULL,
        env=build_environment(),
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
