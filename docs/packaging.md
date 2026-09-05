# Windows Nuitka builds

The binary bundles Glidepath, Python, PySide6, icons and the UK data.
Users do not need Python or uv to run the result. From v1.1.0,
tagged GitHub Releases include a Windows x64 executable;
the wheel and sdist continue to be published to PyPI.

## Build on Windows

Use a native Windows checkout on a local drive, uv, and Visual Studio 2022
or newer with the **Desktop development with C++** workload. Build tools
are needed only on the developer's machine. Use the repository's pinned
Python and locked dependencies:

```powershell
$env:UV_PROJECT_ENVIRONMENT = '.venv-win'
Remove-Item Env:UV_EXCLUDE_NEWER -ErrorAction SilentlyContinue
uv sync --locked --no-dev --group binary
uv run --locked --no-dev --group binary python scripts/build_binary.py
```

This produces `build/nuitka/glidepath_binary.dist/glidepath.exe` plus its
supporting files. Keep that entire folder together. With GNU Make installed,
`make binary` runs the same build (and includes the normal development tools).
The `binary` dependency group is optional and never installed by end users
installing Glidepath from PyPI. Add or update it only through `make deps`.

Test the folder bundle before producing a single file:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_binary.ps1 `
    -Executable build/nuitka/glidepath_binary.dist/glidepath.exe `
    -Report build/nuitka/smoke-standalone.json
```

The test copies the build to a temporary directory, removes Python and venv
paths from its environment, and runs the actual executable. It loads the
packaged data and artwork, projects an example, saves and reloads the plan,
compares serial Monte Carlo results with results from two spawned workers,
and renders the main window using Qt's offscreen platform. It does not touch
your saved plans or normal app settings. A timeout catches worker-spawn loops.

## Produce a single executable

Once the folder passes:

```powershell
uv run --locked --no-dev --group binary python scripts/build_binary.py --mode onefile
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_binary.ps1 `
    -Executable build/nuitka/glidepath.exe `
    -Report build/nuitka/smoke-onefile.json
```

Or build with `make binary BINARY_MODE=onefile`. The result is
`build/nuitka/glidepath.exe`: double-click it to launch. Nuitka extracts its
bundled dependencies to a temporary location at startup. It is a runnable
app, not an installer; it does not add shortcuts or update itself.

The executable embeds `src/glidepath/gui/assets/glidepath.ico`, containing
the existing PNG artwork at 16, 24, 32, 48, 64, 128 and 256 pixels. Its path
is configured by `windows-icon` in `pyproject.toml`.

Executables are not yet Authenticode-signed. Windows may show a SmartScreen "unrecognised
app" warning; dismissing it can be remembered locally for that file.
Signing releases with a trusted publisher identity is separate from
embedding an icon or adding an installer. Signing helps establish publisher
reputation, but does not guarantee that new downloads avoid the warning.
See [Microsoft's SmartScreen guidance](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation).

Normal launches still show the first-run disclaimer. Before sharing a build,
also launch it normally and check the disclaimer, native window, file dialogs,
charts, and a full Monte Carlo run on a Windows machine without Python. The
automated smoke test complements that check; clearing PATH does not remove
software installed on the build machine.

## Tagged releases in GitHub Actions

The **Release** workflow runs when a `vX.Y.Z` tag is pushed. It validates
the tag against `main`, the project version and the changelog, and builds
the Python packages before starting the Windows job. That job builds and
tests the folder bundle first, then builds and tests the single executable
using its final filename, `glidepath-X.Y.Z-windows-x64.exe`. The smoke report's
version must match the tag. A failure blocks publication.

After both builds pass and the existing PyPI environment approval is given,
the workflow publishes only the wheel and sdist to PyPI. It then attaches
those files and the executable to the GitHub Release, with build provenance
covering all three. This provenance is separate from Windows code signing.
Build diagnostics remain available as an Actions artifact.

Ordinary pushes and PRs do not compile binaries. There is no separate manual
binary workflow; use the local commands above for packaging experiments.

Build options live in `[tool.glidepath.binary]` in `pyproject.toml`; compiler
output and the compilation report live under `build/nuitka/`. The build uses
the installed MSVC toolchain. It downloads Dependency Walker 2.2.6000 from
its official site, verifies the SHA-256 pinned in `pyproject.toml`, and
keeps it in a build-only cache. Other automatic tool downloads are declined.

Nuitka 4.2 also offers an NSIS installer option. An installer, code signing
and automatic updates remain follow-up work.

References: [Nuitka distribution modes](https://nuitka.net/user-documentation/use-cases.html),
[compiler requirements](https://nuitka.net/user-documentation/user-manual.html).
