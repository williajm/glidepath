# Glidepath bundled runtime notices

These notices accompany the Glidepath Windows executable. The executable
contains Python 3.14.6, Qt/PySide6/Shiboken6 6.11.2, and the Nuitka 4.2 runtime.
Copyright and licence terms for these components are retained in this folder.
Glidepath's own MIT licence and the separate data licence are in `LICENSE`
and `LICENSE-DATA` at the root of the bundle and source repository.

Qt and PySide/Shiboken are copyright The Qt Company Ltd. and their respective
contributors. Qt Charts is distributed under GPLv3, while Qt's other bundled
libraries and PySide/Shiboken offer LGPLv3/GPL alternatives. See
`QT-LICENCES.txt` for the full terms, including GPLv3 and LGPLv3. The open-source
runtime components carry no warranty. You may modify and redistribute them
under their respective licences; nothing in Glidepath's notices restricts
those rights or reverse engineering for debugging changes to LGPL libraries.

Python's original licence and incorporated-software acknowledgements are in
`PYTHON-LICENSE.txt` and `PYTHON-THIRD-PARTY.rst` (readable plain text).
The Python interpreter has not been modified by this project. Nuitka's runtime
licence, additional permission and bundled zstd notice are in `NUITKA-NOTICES.txt`.
Nuitka is copyright Kay Hayen and contributors; its runtime exception permits
compiled application distribution without imposing AGPL on independent code.

`QT-THIRD-PARTY.txt` retains the original component records, copyrights and
referenced licence texts for the included Windows Qt libraries and plugins.
Shared library code retains its notices even if Glidepath does not call that
feature directly. Identical referenced texts are included once; each
component's own copyright is retained.
This software is based in part on the work of the Independent JPEG Group.

## Corresponding sources and build instructions

Source downloads are available without charge:

- [Glidepath source and release tags](https://github.com/williajm/glidepath/tags).
  Select the tag matching the executable's version. `docs/packaging.md`,
  `pyproject.toml`, `uv.lock` and `.github/workflows/release.yml` contain its
  build instructions, dependencies and build options.
- [Python 3.14.6](https://www.python.org/ftp/python/3.14.6/Python-3.14.6.tar.xz).
- [Qt Base 6.11.2](https://github.com/qt/qtbase/archive/refs/tags/v6.11.2.tar.gz).
- [Qt SVG 6.11.2](https://github.com/qt/qtsvg/archive/refs/tags/v6.11.2.tar.gz).
- [Qt Image Formats 6.11.2](https://github.com/qt/qtimageformats/archive/refs/tags/v6.11.2.tar.gz).
- [Qt Charts 6.11.2](https://github.com/qt/qtcharts/archive/refs/tags/v6.11.2.tar.gz).
- [PySide/Shiboken 6.11.2](https://github.com/pyside/pyside-setup/archive/refs/tags/v6.11.2.tar.gz).
- [Nuitka 4.2](https://github.com/Nuitka/Nuitka/archive/refs/tags/4.2.tar.gz).

The upstream source archives include their build instructions and third-party
sources. Glidepath bundles the Qt libraries from the pinned PySide6 wheels
without modifying their source. A local folder build keeps those libraries
beside the application, so replacements can be used when rebuilding/debugging.

## Updating these notices

Refresh this folder when changing `.python-version`, the PySide6 pin, the
locked Nuitka version, or the bundled Qt modules/plugins. Use the matching
upstream release sources and inspect the bundle's contents. This Windows-only
collection is not a notice set for Linux or macOS distributions.

- Python: retain `LICENSE` from CPython tag `v3.14.6`. From `Doc/license.rst`,
  retain the incorporated-software section, excluding `test_epoll` and
  `Select kqueue`. The history and duplicate Python licence text are omitted.
  Retain the MIT copyright header from `Modules/_hacl/Hacl_Hash_SHA2.c` for
  the interpreter's HACL code, rather than Nuitka's unused older HACL copy.
- Qt/PySide: `QT-LICENCES.txt` contains GPLv3/LGPLv3 and the common terms
  referenced by retained attribution records without their own licence file.
  Other component-specific licence texts live with their attribution record;
  unused commercial, documentation, example and platform licence templates
  are omitted. Shared Apache/CC0 terms are referenced from the component
  notices, including Python's OpenSSL notice. FreeType uses its FTL alternative
  and BLAKE2 uses its CC0 alternative, so their unused alternative texts are
  omitted; the upstream records still show the available licence choices.
- Qt/PySide attributions: collect `qt_attribution.json` under `src/` and
  `sources/`, followed by each record's `LicenseFile`, `LicenseFiles` and
  `CopyrightFile` contents. Retain the paths and original records. Omit
  Android/Gradle, Wayland, WebAssembly, Cocoa/Core Foundation, XCB, Unix
  forkfd, ARM-only pixman, Qt DBus, the SQLite plugin and Qt tests/internal
  test tools. Those components are absent from this Windows x64 bundle.
  Qt Charts has no such attribution records; its GPLv3 text is included.
- Nuitka: copy `LICENSE.txt`, `LICENSE-RUNTIME.txt`, and
  `nuitka/build/inline_copy/zstd/LICENSE.txt` from the locked sdist into
  `NUITKA-NOTICES.txt`. Nuitka's older inline HACL copy is not linked with
  Python 3.14 (`SconsPythonBuild.py:addPythonHaclLib`).

The selection was checked statically against the existing 1.1.0 Windows x64
onefile payload, SHA-256
`51bcd03ca416c4b14ed0b5addd1731c86841f6700838fab4f608cea3665fc541`.
Its compressed file list was read without executing the application. It
includes Qt Core, Gui, Widgets, Charts, Network, OpenGL/OpenGLWidgets, SVG
and PDF, plus Windows platform, image-format, icon, style and TLS plugins.
Recheck the selection against the final build when packaging changes.

Both Nuitka build modes include this directory. The release workflow also
attaches the same directory as `glidepath-X.Y.Z-third-party-licences.zip`,
covered by build provenance, so it can be read without running the executable.
