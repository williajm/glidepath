"""Smoke-test an installed glidepath distribution (issue #198).

Run inside a clean venv where a built sdist or wheel has been
installed — CI runs it for both artifacts on every PR (``ci.yml``) and
``release.yml`` runs it against the release wheel, so a packaging bug
fails the PR that introduces it rather than release day. The checks
back every claim the old inline release smoke only implied:

- the package imports and its distribution metadata is present;
- the UK region data files shipped and actually load — the whole
  region bundle is built, so a missing or broken TOML fails loudly;
- the ``glidepath`` GUI entry point resolves from installed metadata
  to a module file present in the installed package that defines the
  named attribute — without importing Qt, which headless runners
  cannot load;
- optionally (``--expect-version``), the installed version matches an
  expected version, e.g. the release tag.
"""

import ast
import importlib.metadata
import sys
from pathlib import Path

import glidepath
from glidepath.regions.uk import load_returns_history, uk_region

USAGE = "usage: smoke_test_artifact.py [--expect-version X.Y.Z]"

_PACKAGE = "glidepath"


def installed_entry_point() -> importlib.metadata.EntryPoint | None:
    """The installed ``glidepath`` GUI entry point, if metadata holds one."""
    matches = importlib.metadata.entry_points(group="gui_scripts", name=_PACKAGE)
    return next(iter(matches), None)


def entry_point_error() -> str | None:
    """Why the GUI entry point does not resolve, or ``None`` when it does.

    Resolution is static on purpose: the target module's file must
    exist inside the installed package and define the named attribute
    at its top level. Importing it would pull in Qt, which the
    headless CI runner cannot load (its GUI behaviour is exercised by
    the offscreen test suite instead).
    """
    entry_point = installed_entry_point()
    if entry_point is None:
        return "no 'glidepath' gui_scripts entry point in installed metadata"
    module_name, _, attribute = entry_point.value.partition(":")
    if not attribute:
        return f"entry point {entry_point.value!r} names no attribute"
    parts = module_name.split(".")
    if parts[0] != _PACKAGE:
        return f"entry point module {module_name!r} is outside the glidepath package"
    root = Path(glidepath.__file__).parent.parent
    module_file = _module_file(root, parts)
    if module_file is None:
        return f"entry point module {module_name!r} has no file in the package"
    if not _defines(module_file, attribute.split(".")[0]):
        return f"entry point attribute {attribute!r} is not defined in {module_name}"
    return None


def _module_file(root: Path, parts: list[str]) -> Path | None:
    """The module's file under ``root``, or ``None`` when absent."""
    module_path = root.joinpath(*parts)
    plain = module_path.with_suffix(".py")
    if plain.is_file():
        return plain
    package = module_path / "__init__.py"
    if package.is_file():
        return package
    return None


def _defines(module_file: Path, name: str) -> bool:
    """Whether the module binds ``name`` at its top level (statically).

    Functions, classes, plain and annotated assignments, and imported
    (re-exported) names all count — each is a binding an entry point
    may legitimately target.
    """
    tree = ast.parse(module_file.read_text(encoding="utf-8"))
    return any(_binds(node, name) for node in tree.body)


def _binds(node: ast.stmt, name: str) -> bool:
    """Whether one top-level statement binds ``name``."""
    match node:
        case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
            return node.name == name
        case ast.Assign(targets=targets):
            return any(
                isinstance(target, ast.Name) and target.id == name for target in targets
            )
        case ast.AnnAssign(target=ast.Name(id=target_name)):
            return target_name == name
        case ast.Import(names=aliases) | ast.ImportFrom(names=aliases):
            return any(
                (alias.asname or alias.name.split(".")[0]) == name for alias in aliases
            )
        case _:
            return False


def region_data_version() -> str:
    """Build the UK region from shipped data, proving every file loads.

    ``uk_region()`` loads the age rules, every shipped tax year, the
    wrapper/contribution rules, the state pension timetable, and the
    default assumptions; ``load_returns_history`` covers the one file
    the region version deliberately excludes. Any missing or broken
    data file raises here, failing the smoke loudly.
    """
    region = uk_region()
    load_returns_history()
    return region.data_version


def main(argv: list[str]) -> int:
    """Run every check against the installed distribution."""
    match argv:
        case []:
            expected = None
        case ["--expect-version", value]:
            expected = value
        case _:
            print(USAGE, file=sys.stderr)
            return 1
    version = importlib.metadata.version(_PACKAGE)
    if expected is not None and version != expected:
        print(
            f"ERROR: installed version {version} does not match expected {expected}",
            file=sys.stderr,
        )
        return 1
    error = entry_point_error()
    if error is not None:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    data_version = region_data_version()
    print(
        f"glidepath {version} imports cleanly; GUI entry point resolves;"
        f" UK region data loads ({data_version})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
