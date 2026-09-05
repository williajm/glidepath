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
`PYTHON-LICENSE.txt` and `PYTHON-LICENSE-DOC.rst` (readable plain text).
The Python interpreter has not been modified by this project. Nuitka's runtime
licence, additional permission and bundled zstd/HACL notices are in `NUITKA-*`.
Nuitka is copyright Kay Hayen and contributors; its runtime exception permits
compiled application distribution without imposing AGPL on independent code.

The `*-ATTRIBUTIONS.txt` files retain Qt's original third-party component
records, copyrights and referenced licence texts. They include notices for
other platforms and optional components in these source modules; inclusion
of a notice does not mean that component is used by the Windows executable.
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

Refresh this folder when changing `.python-version`, the PySide6 pin, or the
locked Nuitka version. Use the matching upstream release sources, not the
latest documentation. The files here were collected as follows:

- Python: copy `LICENSE` and `Doc/license.rst` from CPython tag `v3.14.6`.
- Qt/PySide: retain the licence texts under each source module's `LICENSES/`.
  `QT-LICENCES.txt` combines these, retaining distinct texts when modules use
  different wording for the same licence identifier.
- Qt/PySide attributions: collect `qt_attribution.json` under `src/` and
  `sources/`, followed by each record's `LicenseFile`, `LicenseFiles` and
  `CopyrightFile` contents. Retain the paths and original records. Qt Charts
  has no such attribution records in this release; its GPLv3 text is included.
- Nuitka: copy `LICENSE.txt`, `LICENSE-RUNTIME.txt`, and
  `nuitka/build/inline_copy/{zstd,python_hacl}/LICENSE.txt` from the locked sdist.

Both Nuitka build modes include this directory. The release workflow also
attaches the same directory as `glidepath-X.Y.Z-third-party-licences.zip`,
covered by build provenance, so it can be read without running the executable.
