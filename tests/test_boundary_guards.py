"""Boundary guard tests (planning §4.2, §4.7; issues 1.5, 8.1).

Cheap failing tests turn the repo's isolation rules into merge gates:

- ``glidepath.core`` must never import region code (dependency direction
  is region → core only).
- No policy figure may appear as a literal anywhere in ``src/glidepath``
  outside ``regions/*/data/`` — UK policy figures live in TOML data files
  with ``verified_on`` + ``sources``, never in logic.
- Qt may be imported only by the ``glidepath.gui`` shell — the app layer
  and everything below it stay UI-toolkit-agnostic so a web shell can be
  added later.
"""

import ast
import re
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "glidepath"
CORE_ROOT = SRC_ROOT / "core"

# Distinctive UK policy figures from planning §6. Matching any of these in
# code (rather than region data) fails the build. Extend the list as new
# figures are verified.
POLICY_FIGURES = (
    "12570",  # personal allowance
    "12,570",
    "37700",  # basic-rate band width
    "50270",  # higher-rate threshold
    "125140",  # additional-rate threshold / PA fully tapered
    "100000",  # PA taper threshold
    "3967",  # Scottish starter band
    "16956",  # Scottish basic band
    "31092",  # Scottish intermediate band
    "62430",  # Scottish higher band
    "60000",  # annual allowance
    "200000",  # AA taper threshold income
    "260000",  # AA taper adjusted income
    "10000",  # MPAA / AA taper floor
    "268275",  # lump sum allowance
    "1073100",  # LSDBA
    "20000",  # ISA allowance
    "4000",  # LISA allowance
    "3600",  # member relief basic amount
    "241.30",  # full new state pension, weekly
    "184.90",  # full basic (old) state pension, weekly
    "5000",  # starting rate for savings limit
    "17570",  # PA + starting-rate limit: savings taper exhaustion point
    "0.1075",  # dividend ordinary rate
    "0.3575",  # dividend upper rate
    "0.3935",  # dividend additional rate
    # The PSA amounts (1000/500/0) and dividend allowance (500) are too
    # generic to grep for without false positives; the loader tests pin
    # them to §6 instead.
)


def _python_files(root: Path) -> list[Path]:
    """All Python source files under ``root``, sorted for stable output."""
    return sorted(root.rglob("*.py"))


def _imported_modules(tree: ast.AST) -> set[str]:
    """Every module path named by an import statement in ``tree``."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if base:
                modules.add(base)
            modules.update(
                f"{base}.{alias.name}" if base else alias.name for alias in node.names
            )
    return modules


def test_core_never_imports_regions() -> None:
    """Dependency direction is region → core only (planning §4.2)."""
    core_files = _python_files(CORE_ROOT)
    assert core_files, "core package not found — guard would pass vacuously"
    for path in core_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module in _imported_modules(tree):
            offending = "regions" in module.split(".")
            assert not offending, f"{path.name} imports region code: {module}"


def test_qt_imports_confined_to_gui_shell() -> None:
    """Only the PySide6 shell may import Qt, directly or via the shell (§4.7).

    Importing ``glidepath.gui`` from outside the shell would load Qt
    transitively, so the dependency direction (shell → app, never the
    reverse) is enforced alongside the direct-import ban.
    """
    qt_distributions = {"PySide6", "shiboken6", "PyQt5", "PyQt6"}
    source_files = _python_files(SRC_ROOT)
    assert source_files, "src/glidepath not found — guard would pass vacuously"
    for path in source_files:
        if path.relative_to(SRC_ROOT).parts[0] == "gui":
            continue  # the shell is the one place Qt may appear
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module in _imported_modules(tree):
            offending = module.split(".")[0] in qt_distributions or (
                module == "glidepath.gui" or module.startswith("glidepath.gui.")
            )
            assert not offending, (
                f"{path.relative_to(SRC_ROOT)} imports {module}"
                " — Qt (and the gui shell) is confined to glidepath/gui"
                " (planning §4.7)"
            )


def test_no_policy_figures_outside_region_data() -> None:
    """Policy figures live in region data files, never in code."""
    source_files = _python_files(SRC_ROOT)
    assert source_files, "src/glidepath not found — guard would pass vacuously"
    for path in source_files:
        relative_parts = path.relative_to(SRC_ROOT).parts
        if "regions" in relative_parts and "data" in relative_parts:
            continue  # shipped data may (must) carry the figures
        text = path.read_text(encoding="utf-8")
        for figure in POLICY_FIGURES:
            pattern = rf"(?<![\d.]){re.escape(figure)}(?![\d.])"
            match = re.search(pattern, text)
            assert match is None, (
                f"policy figure {figure!r} found in {path.relative_to(SRC_ROOT)}"
                " — policy figures belong in regions/*/data/ TOML files"
            )
