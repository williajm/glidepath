"""Doc-sync: §7 of docs/planning.md mirrors assumptions_default.toml.

Issue 2.2 acceptance criterion: a doc-sync test keeps §7 aligned with
the defaults file — same keys, same order, and matching numeric values
(percent cells in the doc are compared against fraction values in the
file).
"""

import re
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

from glidepath.regions.uk import load_default_assumptions

PLANNING = Path(__file__).resolve().parents[1] / "docs" / "planning.md"

_ROW = re.compile(r"^\| `(?P<key>[^`]+)` \| (?P<default>[^|]+) \|", re.MULTILINE)
_NUMBER = re.compile(r"(?P<number>-?\d+(?:\.\d+)?)(?P<percent>%?)")

# §7 rows whose defaults are structured (mirrored as TOML tables), so no
# single number comparison applies.
STRUCTURED_KEYS = 3


def _section7_rows() -> list[tuple[str, str]]:
    """Key and Default columns of every §7 assumption row, in order."""
    text = PLANNING.read_text(encoding="utf-8")
    section = text.split("\n## 7. ")[1].split("\n## ")[0]
    return [
        (match["key"], match["default"].strip()) for match in _ROW.finditer(section)
    ]


def test_section7_keys_match_file_keys_in_order() -> None:
    """The doc table and the data file list the same keys in the same order."""
    doc_keys = [key for key, _ in _section7_rows()]
    file_keys = [entry.key.value for entry in load_default_assumptions().defaults]
    assert doc_keys == file_keys


def test_section7_numeric_defaults_match_file_values() -> None:
    """Every numeric default in the file equals the number documented in §7."""
    rows = dict(_section7_rows())
    checked = 0
    for entry in load_default_assumptions().defaults:
        if isinstance(entry.value, Mapping):
            continue
        cell = rows[entry.key.value].replace("\N{MINUS SIGN}", "-")
        match = _NUMBER.search(cell)
        assert match is not None, f"no number in the §7 default for {entry.key}"
        documented = Decimal(match["number"])
        value = Decimal(entry.value) if isinstance(entry.value, int) else entry.value
        assert isinstance(value, Decimal), f"{entry.key} should be numeric"
        in_doc_units = value * 100 if match["percent"] else value
        assert documented == in_doc_units, (
            f"{entry.key}: §7 says {documented}{match['percent']}"
            f" but the file value implies {in_doc_units}"
        )
        checked += 1
    assert checked == len(load_default_assumptions().defaults) - STRUCTURED_KEYS
