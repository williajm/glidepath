"""Regenerate the historical return series data file from the JST dataset.

Derives ``src/glidepath/regions/uk/data/returns_history.toml`` (roadmap
9.18) from the Jordà-Schularick-Taylor Macrohistory Database release 6
(``JSTdatasetR6.xlsx``, downloaded from https://www.macrohistory.net/database/
— not committed to this repository). Stdlib only; run it as::

    uv run --locked python scripts/build_returns_history.py \
        JSTdatasetR6.xlsx 2026-08-05

and redirect stdout over the data file. The second argument is the
``verified_on`` date and must be the day you completed re-verifying the
derived figures against the upstream dataset — it is required, never
defaulted from the clock, so an unreviewed regeneration cannot stamp
itself verified.

Per year, the series carries four nominal annual rates (TOML strings, the
§5.3 convention):

- ``equity`` — world equity total return in GBP terms: the GDP-weighted
  average of each JST country's local-currency equity total return
  (``eq_tr``) converted into GBP through the year's ``xrusd`` cross
  rates, weighted by prior-year USD GDP (the JST papers' own world-index
  convention; GDP weights overweight Europe and Japan relative to a
  market-cap index such as MSCI World). A country missing any required
  figure in a year is dropped from that year's average and the weights
  renormalised over the countries that remain.
- ``bonds`` — UK long government bond (gilt) total return (``bond_tr``).
- ``cash`` — UK short-term bill rate (``bill_rate``).
- ``cpi`` — UK CPI inflation, derived from the ``cpi`` index.

Real rates are never emitted; consumers derive them as
``(1 + nominal) / (1 + cpi) - 1``.

The JST database is licensed CC BY-NC-SA 4.0 — see the generated file's
header for the attribution that must ship with the derived figures.
"""

from __future__ import annotations

import io
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from math import isnan
from pathlib import Path

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_SHEET = "xl/worksheets/sheet1.xml"
_SHARED_STRINGS = "xl/sharedStrings.xml"

#: First series year: the first with full-coverage JST equity data (16 of
#: the 18 countries; Canada and Ireland enter the dataset later).
FIRST_YEAR = 1900
#: Last series year: the final year of JST release 6.
LAST_YEAR = 2020

_COLUMNS = ("eq_tr", "bond_tr", "bill_rate", "cpi", "xrusd", "gdp")
_EXPECTED_ARGC = 3

Table = dict[tuple[int, str], dict[str, float]]


class DerivationError(Exception):
    """The dataset cannot support the derivation this script promises."""


def _column_index(reference: str) -> int:
    """Turn an A1-style cell reference's letters into a 0-based column."""
    index = 0
    for char in reference:
        if char.isdigit():
            break
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    """Read the workbook's shared-string pool (cell values of type ``s``)."""
    try:
        text = archive.read(_SHARED_STRINGS)
    except KeyError:
        return []
    root = ET.fromstring(text)
    return ["".join(t.text or "" for t in si.iter(f"{_NS}t")) for si in root]


def _sheet_rows(archive: zipfile.ZipFile) -> list[dict[int, str | float]]:
    """Return the first worksheet as rows of {column index: value}."""
    strings = _shared_strings(archive)
    root = ET.fromstring(archive.read(_SHEET))
    rows: list[dict[int, str | float]] = []
    for row in root.iter(f"{_NS}row"):
        cells: dict[int, str | float] = {}
        for cell in row.iter(f"{_NS}c"):
            value = cell.find(f"{_NS}v")
            if value is None or value.text is None:
                continue
            column = _column_index(cell.get("r", ""))
            if cell.get("t") == "s":
                cells[column] = strings[int(value.text)]
            else:
                cells[column] = float(value.text)
        rows.append(cells)
    return rows


def load_table(path: Path) -> Table:
    """Load the JST sheet into ``{(year, iso): {column: value}}``."""
    with zipfile.ZipFile(path) as archive:
        rows = _sheet_rows(archive)
    if not rows:
        msg = f"{path}: worksheet is empty"
        raise DerivationError(msg)
    header = {column: str(name) for column, name in rows[0].items()}
    table: Table = {}
    for cells in rows[1:]:
        record = {header[c]: v for c, v in cells.items() if c in header}
        year = record.get("year")
        iso = record.get("iso")
        if not isinstance(year, float) or not isinstance(iso, str):
            continue
        table[(int(year), iso)] = {
            name: value
            for name, value in record.items()
            if name in _COLUMNS and isinstance(value, float) and not isnan(value)
        }
    return table


def world_equity_gbp(table: Table, year: int) -> float:
    """GDP-weighted world equity total return in GBP terms for one year.

    Each included country's local-currency total return is converted into
    GBP through the year's exchange-rate move (``xrusd`` is local currency
    per USD, so GBP per local unit is the UK rate over the country's) and
    weighted by its prior-year GDP expressed in USD — weights an investor
    could have formed at the start of the year.
    """
    uk_now = table[(year, "GBR")]["xrusd"]
    uk_prev = table[(year - 1, "GBR")]["xrusd"]
    weighted: list[tuple[float, float]] = []
    for iso in sorted({key[1] for key in table}):
        now = table.get((year, iso), {})
        prev = table.get((year - 1, iso), {})
        if "eq_tr" not in now or "xrusd" not in now:
            continue
        if "xrusd" not in prev or "gdp" not in prev:
            continue
        gbp_factor = (uk_now / now["xrusd"]) / (uk_prev / prev["xrusd"])
        return_gbp = (1.0 + now["eq_tr"]) * gbp_factor - 1.0
        usd_gdp = prev["gdp"] / prev["xrusd"]
        weighted.append((usd_gdp, return_gbp))
    if not weighted:
        msg = f"{year}: no country has the columns the world average needs"
        raise DerivationError(msg)
    total = sum(weight for weight, _ in weighted)
    return sum(weight * rate for weight, rate in weighted) / total


def series_lines(table: Table) -> list[str]:
    """Render one ``series`` entry per year, FIRST_YEAR..LAST_YEAR."""
    lines: list[str] = []
    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        uk = table[(year, "GBR")]
        uk_previous = table[(year - 1, "GBR")]
        equity = world_equity_gbp(table, year)
        cpi = uk["cpi"] / uk_previous["cpi"] - 1.0
        lines.append(
            f'  {{ year = {year}, equity = "{equity:.6f}",'
            f' bonds = "{uk["bond_tr"]:.6f}", cash = "{uk["bill_rate"]:.6f}",'
            f' cpi = "{cpi:.6f}" }},'
        )
    return lines


def render(table: Table, verified_on: date) -> str:
    """Render the complete returns_history.toml text.

    ``verified_on`` is the caller-stated date the derived figures were
    re-verified against the upstream dataset — never the clock (module
    docstring).
    """
    header = f"""\
# Historical annual return series for the backtest engine (roadmap 9.18;
# conventions per docs/planning.md §5.3): world equity total returns in
# GBP terms (GDP-weighted over the JST countries, currency moves
# included), UK long gilt total returns, UK bills, and UK CPI inflation.
# All rates are NOMINAL; real rates are derived in code as
# (1 + nominal) / (1 + cpi) - 1. Derived from the Jordà-Schularick-Taylor
# Macrohistory Database R6 by scripts/build_returns_history.py — never
# edit a figure by hand; regenerate, re-verify, and update [meta].
#
# Data licence: unlike the code in this repository, the figures below are
# a derived work of the JST Macrohistory Database (© Òscar Jordà, Moritz
# Schularick, Alan M. Taylor and co-authors), distributed under
# CC BY-NC-SA 4.0 (https://creativecommons.org/licenses/by-nc-sa/4.0/).
# Cite Jordà, Schularick & Taylor (2017) and, for the return series,
# Jordà, Knoll, Kuvshinov, Schularick & Taylor (2019).

schema_version = 2

[meta]
verified_on = {verified_on.isoformat()}
sources = [
  "https://www.macrohistory.net/database/",
  "https://doi.org/10.1093/qje/qjz012",
]

# One entry per calendar year, contiguous.
[returns]
"""
    return header + "series = [\n" + "\n".join(series_lines(table)) + "\n]\n"


def main(argv: list[str]) -> int:
    """Render the data file for the workbook path and verification date."""
    usage = "usage: build_returns_history.py JSTdatasetR6.xlsx YYYY-MM-DD"
    if len(argv) != _EXPECTED_ARGC:
        print(usage, file=sys.stderr)
        return 2
    try:
        verified_on = date.fromisoformat(argv[2])
    except ValueError:
        print(usage, file=sys.stderr)
        print("the second argument is the re-verification date", file=sys.stderr)
        return 2
    # The file text is UTF-8 regardless of the console code page (Windows
    # consoles otherwise mangle the header's accented names).
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    print(render(load_table(Path(argv[1])), verified_on), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
