"""Tests for scripts/build_returns_history.py (issue #130).

The script derives ``returns_history.toml`` from the JST Macrohistory
workbook; a silent mis-derivation would poison every backtest. These
tests feed it minimal hand-built xlsx workbooks (the same zip + XML
shape the real dataset uses) and pin the load, the GDP-weighted world
equity derivation — including drop-and-renormalise for incomplete
countries and hard failure on unweightable ones — and the rendered TOML.
"""

import tomllib
import zipfile
from datetime import date
from string import ascii_uppercase
from typing import TYPE_CHECKING

import pytest

import build_returns_history
from build_returns_history import (
    DerivationError,
    Table,
    _column_index,
    load_table,
    render,
    world_equity_gbp,
)

if TYPE_CHECKING:
    from pathlib import Path

_XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

HEADER: list[str | float | None] = [
    "year",
    "iso",
    "eq_tr",
    "bond_tr",
    "bill_rate",
    "cpi",
    "xrusd",
    "gdp",
]


def _workbook(
    path: Path,
    rows: list[list[str | float | None]],
    *,
    shared_strings: bool = True,
) -> Path:
    """Write a minimal xlsx workbook holding ``rows`` on its first sheet.

    Strings become shared-string cells (type ``s``), numbers inline
    numeric cells, and ``None`` a present-but-valueless cell — the
    shapes ``load_table`` must handle in the real JST workbook.
    """
    strings: list[str] = []
    row_parts: list[str] = []
    for row_number, row in enumerate(rows, start=1):
        cells: list[str] = []
        for column, value in enumerate(row):
            reference = f"{ascii_uppercase[column]}{row_number}"
            if value is None:
                # A present-but-valueless cell, as the JST sheet has for
                # missing observations.
                cells.append(f'<c r="{reference}"/>')
            elif isinstance(value, str):
                if value not in strings:
                    strings.append(value)
                index = strings.index(value)
                cells.append(f'<c r="{reference}" t="s"><v>{index}</v></c>')
            else:
                cells.append(f'<c r="{reference}"><v>{value!r}</v></c>')
        row_parts.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    sheet = (
        f'<worksheet xmlns="{_XLSX_NS}">'
        f"<sheetData>{''.join(row_parts)}</sheetData></worksheet>"
    )
    pool = "".join(f"<si><t>{text}</t></si>" for text in strings)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        if shared_strings:
            sst = f'<sst xmlns="{_XLSX_NS}">{pool}</sst>'
            archive.writestr("xl/sharedStrings.xml", sst)
    return path


def _row(
    year: int,
    iso: str,
    *,
    eq_tr: float | None = None,
    bond_tr: float | None = None,
    bill_rate: float | None = None,
    cpi: float | None = None,
    xrusd: float | None = None,
    gdp: float | None = None,
) -> list[str | float | None]:
    """One JST data row in HEADER column order."""
    return [float(year), iso, eq_tr, bond_tr, bill_rate, cpi, xrusd, gdp]


def _two_country_table() -> Table:
    """GBR + USA across 1999-2000 with hand-checkable world-equity inputs."""
    return {
        (1999, "GBR"): {"xrusd": 0.62, "gdp": 900.0, "cpi": 100.0},
        (2000, "GBR"): {
            "xrusd": 0.66,
            "eq_tr": 0.10,
            "bond_tr": 0.05,
            "bill_rate": 0.04,
            "cpi": 103.0,
        },
        (1999, "USA"): {"xrusd": 1.0, "gdp": 9000.0},
        (2000, "USA"): {"xrusd": 1.0, "eq_tr": 0.08},
    }


def _expected_two_country_equity() -> float:
    """The 2000 world equity return the two-country table implies.

    GBR: sterling investor in sterling assets — the 10% local return is
    the GBP return. USA: 8% local return times the dollar's move against
    sterling (0.66/0.62). Weights are prior-year GDP in USD after the
    thousands-to-millions unit normalisation both countries share.
    """
    weight_gbr = 900.0 * 1e3 / 0.62
    weight_usa = 9000.0 * 1e3 / 1.0
    return_gbr = 0.10
    return_usa = 1.08 * (0.66 / 0.62) - 1.0
    total = weight_gbr + weight_usa
    return (weight_gbr * return_gbr + weight_usa * return_usa) / total


# --- workbook loading -------------------------------------------------------


def test_column_index_decodes_a1_references() -> None:
    """A1-style references map to 0-based columns, ignoring row digits."""
    assert _column_index("A1") == 0
    assert _column_index("B2") == 1
    assert _column_index("Z9") == 25
    assert _column_index("AA10") == 26


def test_load_table_reads_rows_and_drops_nan(tmp_path: Path) -> None:
    """Cells parse by header name; NaN cells are treated as absent."""
    workbook = _workbook(
        tmp_path / "jst.xlsx",
        [
            HEADER,
            _row(2000, "GBR", eq_tr=0.1, cpi=float("nan"), xrusd=0.66),
            ["notes", "a stray text row without year"],
        ],
    )
    table = load_table(workbook)
    assert table == {(2000, "GBR"): {"eq_tr": 0.1, "xrusd": 0.66}}


def test_load_table_empty_worksheet_fails(tmp_path: Path) -> None:
    """A workbook with no rows at all cannot support the derivation."""
    workbook = _workbook(tmp_path / "empty.xlsx", [])
    with pytest.raises(DerivationError, match="worksheet is empty"):
        load_table(workbook)


def test_load_table_without_shared_strings(tmp_path: Path) -> None:
    """A workbook with no string pool yields no keyed rows, not a crash."""
    workbook = _workbook(tmp_path / "numbers.xlsx", [[1.0, 2.0]], shared_strings=False)
    assert load_table(workbook) == {}


# --- world equity derivation ------------------------------------------------


def test_world_equity_weights_by_prior_year_usd_gdp() -> None:
    """The two-country average matches the hand-computed weighting."""
    computed = world_equity_gbp(_two_country_table(), 2000)
    assert computed == pytest.approx(_expected_two_country_equity())


def test_world_equity_drops_incomplete_country_and_renormalises() -> None:
    """A country missing a required figure is dropped, weights rescaled."""
    table = _two_country_table()
    # FRA has a 2000 return but no prior-year GDP — it cannot be weighted.
    table[(1999, "FRA")] = {"xrusd": 6.5}
    table[(2000, "FRA")] = {"xrusd": 6.6, "eq_tr": 0.50}
    computed = world_equity_gbp(table, 2000)
    assert computed == pytest.approx(_expected_two_country_equity())


def test_world_equity_unknown_country_unit_fails() -> None:
    """A weightable country with no GDP unit multiplier is a hard error."""
    table = _two_country_table()
    table[(1999, "ZZZ")] = {"xrusd": 2.0, "gdp": 500.0}
    table[(2000, "ZZZ")] = {"xrusd": 2.0, "eq_tr": 0.05}
    with pytest.raises(DerivationError, match="no GDP unit multiplier"):
        world_equity_gbp(table, 2000)


def test_world_equity_no_eligible_country_fails() -> None:
    """A year in which no country qualifies cannot produce an average."""
    table: Table = {
        (1999, "GBR"): {"xrusd": 0.62},
        (2000, "GBR"): {"xrusd": 0.66},
    }
    with pytest.raises(DerivationError, match="no country has the columns"):
        world_equity_gbp(table, 2000)


def test_missing_uk_exchange_rate_fails_loudly() -> None:
    """Without the UK cross rate the GBP conversion cannot even start."""
    table: Table = {(2000, "USA"): {"xrusd": 1.0, "eq_tr": 0.08}}
    with pytest.raises(KeyError):
        world_equity_gbp(table, 2000)


# --- rendering --------------------------------------------------------------


def test_render_produces_parseable_toml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rendered file parses as TOML with the derived figures."""
    monkeypatch.setattr(build_returns_history, "FIRST_YEAR", 2000)
    monkeypatch.setattr(build_returns_history, "LAST_YEAR", 2000)
    text = render(_two_country_table(), date(2026, 8, 5))
    document = tomllib.loads(text)
    assert document["meta"]["verified_on"] == date(2026, 8, 5)
    (entry,) = document["returns"]["series"]
    assert entry["year"] == 2000
    assert entry["bonds"] == "0.050000"
    assert entry["cash"] == "0.040000"
    assert entry["cpi"] == "0.030000"
    assert float(entry["equity"]) == pytest.approx(
        _expected_two_country_equity(), abs=1e-6
    )


def test_render_header_carries_the_data_licence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CC BY-NC-SA attribution must ship with the derived figures."""
    monkeypatch.setattr(build_returns_history, "FIRST_YEAR", 2000)
    monkeypatch.setattr(build_returns_history, "LAST_YEAR", 2000)
    text = render(_two_country_table(), date(2026, 8, 5))
    assert "CC BY-NC-SA 4.0" in text
    assert "Macrohistory Database" in text


# --- command line -----------------------------------------------------------


def test_main_rejects_wrong_argument_count(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both arguments — workbook and verification date — are required."""
    exit_code = build_returns_history.main(["build_returns_history.py"])
    assert exit_code == 2
    assert "usage:" in capsys.readouterr().err


def test_main_rejects_malformed_verification_date(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The verified_on argument must be an ISO date, never defaulted."""
    workbook = _workbook(tmp_path / "jst.xlsx", [HEADER])
    argv = ["build_returns_history.py", str(workbook), "yesterday"]
    exit_code = build_returns_history.main(argv)
    assert exit_code == 2
    assert "re-verification date" in capsys.readouterr().err


def test_main_renders_workbook_to_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End to end: workbook in, parseable TOML data file on stdout."""
    monkeypatch.setattr(build_returns_history, "FIRST_YEAR", 2000)
    monkeypatch.setattr(build_returns_history, "LAST_YEAR", 2000)
    workbook = _workbook(
        tmp_path / "jst.xlsx",
        [
            HEADER,
            _row(1999, "GBR", cpi=100.0, xrusd=0.62, gdp=900.0),
            _row(
                2000,
                "GBR",
                eq_tr=0.10,
                bond_tr=0.05,
                bill_rate=0.04,
                cpi=103.0,
                xrusd=0.66,
            ),
            _row(1999, "USA", xrusd=1.0, gdp=9000.0),
            _row(2000, "USA", eq_tr=0.08, xrusd=1.0),
        ],
    )
    argv = ["build_returns_history.py", str(workbook), "2026-08-05"]
    exit_code = build_returns_history.main(argv)
    assert exit_code == 0
    document = tomllib.loads(capsys.readouterr().out)
    assert document["meta"]["verified_on"] == date(2026, 8, 5)
    (entry,) = document["returns"]["series"]
    assert entry["year"] == 2000
    assert float(entry["equity"]) == pytest.approx(
        _expected_two_country_equity(), abs=1e-6
    )
