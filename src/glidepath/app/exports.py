"""Exports: the cash-flow CSV and the printable plan report (roadmap 9.19).

Gets the plan out of the app (planning §1, §4.7): a per-year cash-flow
CSV serialising the active run's report exactly as the charts read it,
and a print-ready plan report — inputs with their FACTS vs ASSUMPTIONS
vs DECISIONS provenance, projection results with the three charts, and
the scenario comparison when scenarios exist. Every export carries the
§1 disclaimer. Generation is pure app-layer work over the same view
models the screens render; the shell contributes only the file dialog
and, for the report, the chart rasteriser and the PDF paint device.
The report's charts appear in the HTML as ``chart:<index>`` image
resources — :func:`chart_resource_name` names them — which the shell
registers against the accompanying :class:`~glidepath.app.ChartSpec`
list before printing.
"""

import csv
from dataclasses import dataclass
from html import escape
from io import StringIO
from typing import TYPE_CHECKING, Final

from glidepath.app.charts import (
    basis_key,
    basis_options,
    build_charts_view_model,
    wrapper_display_labels,
)
from glidepath.app.copy import APP_NAME, DISCLAIMER_BODY
from glidepath.app.display import format_value
from glidepath.app.files import PLAN_FILE_SUFFIX
from glidepath.app.inspector import build_inspector_view_model
from glidepath.app.labels import entity_names
from glidepath.app.scenarios import BASE_RUN_LABEL, build_scenarios_view_model
from glidepath.core import ReportBasis, build_report

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from glidepath.app.charts import ChartSpec
    from glidepath.app.plan import PlanState
    from glidepath.core import (
        Money,
        PeriodReportRow,
        ProjectionResult,
        RunMode,
    )

EXPORT_CASH_FLOW_LABEL: Final = "Export cash flow (CSV)…"

EXPORT_REPORT_LABEL: Final = "Export report (PDF)…"

EXPORT_CASH_FLOW_DIALOG_TITLE: Final = "Export cash flow"

EXPORT_REPORT_DIALOG_TITLE: Final = "Export report"

CASH_FLOW_FILE_SUFFIX: Final = ".csv"

CASH_FLOW_FILE_FILTER: Final = "Cash-flow table (*.csv)"

REPORT_FILE_SUFFIX: Final = ".pdf"

REPORT_FILE_FILTER: Final = "Plan report (*.pdf)"

NOTHING_TO_EXPORT_MESSAGE: Final = (
    "Nothing to export yet — save facts on the Facts tab first."
)

REPORT_EXPORT_FAILED_PREFIX: Final = "Could not export the report: "

REPORT_NOT_WRITTEN_MESSAGE: Final = (
    f"{REPORT_EXPORT_FAILED_PREFIX}the file could not be written."
)

UNSAVED_PLAN_NAME: Final = "Unsaved plan"

_CASH_FLOW_EXPORT_FAILED_PREFIX: Final = "Could not export the cash flow: "

_RESULTS_HEADING: Final = "Projection results"

_INPUTS_HEADING: Final = "Inputs and provenance"

_MONTE_CARLO_HEADING: Final = "Monte Carlo"

_METRIC_COLUMNS: Final = ("Metric", "Value")

_DETERMINISTIC_RUN_TEXT: Final = "Deterministic"

_CHART_RESOURCE_PREFIX: Final = "chart:"

_REPORT_CHART_WIDTH: Final = 640
"""The report's chart display width in document pixels.

Comfortably inside an A4 page at the 96 dpi the shell prints at, so a
chart never overflows into the margin.
"""

_FLOW_COLUMNS: Final[tuple[tuple[str, Callable[[PeriodReportRow], Money]], ...]] = (
    ("Employment income", lambda row: row.employment_income),
    ("DB income", lambda row: row.db_income),
    ("DB lump sum", lambda row: row.db_lump_sum),
    ("Pension lump sum", lambda row: row.pension_lump_sum),
    ("State pension income", lambda row: row.state_pension_income),
    ("Annuity income", lambda row: row.annuity_income),
    ("Annuity lump sum", lambda row: row.annuity_lump_sum),
    ("Annuity purchases", lambda row: row.annuity_purchases),
    ("Tax due", lambda row: row.tax_due),
    ("Spending need", lambda row: row.spending_need),
    ("Planned outflows", lambda row: row.planned_outflows),
    ("Net withdrawn", lambda row: row.net_withdrawn),
    ("Shortfall", lambda row: row.shortfall),
    ("Contributions", lambda row: row.contributions),
    ("Fees", lambda row: row.fees),
    ("Growth", lambda row: row.growth),
    ("Growth tax", lambda row: row.growth_tax),
    ("Banked", lambda row: row.banked),
    ("Withdrawals (gross)", lambda row: row.withdrawals_gross),
    ("Closing balance", lambda row: row.closing_balance),
)
"""The CSV's money columns, in the report row's field order.

Each amount serialises as the report model's exact quantized decimal
(``1234.56``), so the file round-trips the on-screen numbers without a
formatting layer in between (the 9.19 acceptance criterion).
"""


@dataclass(frozen=True)
class ExportOutcome:
    """Whether an export wrote the file, and the status line saying so."""

    saved: bool
    message: str


@dataclass(frozen=True)
class ReportRequest:
    """One plan report's presentation choices, as the shell holds them.

    ``basis`` and ``mode`` are the charts screen's selections and drive
    the report's charts and metrics; ``comparison_basis`` and
    ``comparison_metric_key`` are the scenarios screen's and drive the
    comparison table.
    """

    plan_name: str
    basis: ReportBasis
    mode: RunMode
    comparison_basis: ReportBasis
    comparison_metric_key: str


@dataclass(frozen=True)
class PlanReport:
    """A print-ready plan report: the document and its chart resources.

    ``html`` references each chart as an image resource named by
    :func:`chart_resource_name`; the shell renders ``charts`` over
    ``categories`` and registers the images under those names before
    printing.
    """

    html: str
    charts: tuple[ChartSpec, ...]
    categories: tuple[str, ...]


def plan_display_name(path: Path | None) -> str:
    """The plan's display name: the file's name, or the unsaved copy."""
    if path is None:
        return UNSAVED_PLAN_NAME
    return path.name.removesuffix(PLAN_FILE_SUFFIX)


def chart_resource_name(index: int) -> str:
    """The resource name the report's HTML gives its ``index``-th chart."""
    return f"{_CHART_RESOURCE_PREFIX}{index}"


def _basis_label(basis: ReportBasis) -> str:
    """The display label of ``basis``, as the basis toggles show it."""
    key = basis_key(basis)
    return next(option.label for option in basis_options() if option.key == key)


def _run_basis_text(result: ProjectionResult) -> str:
    """The run's basis line: deterministic, or Monte Carlo with its seed."""
    seed = result.provenance.seed
    if seed is None:
        return _DETERMINISTIC_RUN_TEXT
    return f"Monte Carlo, seed {seed}"


def cash_flow_csv(
    state: PlanState, *, basis: ReportBasis, plan_name: str
) -> str | None:
    """The active run's per-year cash-flow table as CSV text (9.19).

    A header block names the plan, the run basis, the scenario (the
    active run is always the base plan), the money basis, and the §1
    disclaimer; the table then carries one row per person per period
    with every amount as the report model's exact decimal, so the file
    round-trips the on-screen numbers. ``None`` without a projection.
    """
    if state.result is None:
        return None
    report = build_report(state.result, basis)
    names = entity_names(state.household)
    wrappers = wrapper_display_labels(report.rows)
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(("Plan", plan_name))
    writer.writerow(("Run", _run_basis_text(state.result)))
    writer.writerow(("Scenario", BASE_RUN_LABEL))
    writer.writerow(("Money basis", _basis_label(basis)))
    writer.writerow(("Disclaimer", DISCLAIMER_BODY))
    writer.writerow(())
    writer.writerow(
        (
            "Period start",
            "Period end",
            "Person",
            "Age at period start",
            "Life stage",
            "Year fraction",
            *(label for label, _amount in _FLOW_COLUMNS),
            *(f"Closing balance — {label}" for label in wrappers.values()),
        )
    )
    for row in report.rows:
        balances = {
            entry.wrapper_id: entry.closing_balance for entry in row.wrapper_balances
        }
        writer.writerow(
            (
                row.period.start.isoformat(),
                row.period.end.isoformat(),
                names.get(str(row.person_id), str(row.person_id)),
                str(row.age_at_period_start),
                format_value(row.stage),
                str(row.year_fraction),
                *(str(amount(row).amount) for _label, amount in _FLOW_COLUMNS),
                *(
                    str(balances[wrapper_id].amount) if wrapper_id in balances else ""
                    for wrapper_id in wrappers
                ),
            )
        )
    return buffer.getvalue()


def export_cash_flow_csv(
    state: PlanState, path: Path, *, basis: ReportBasis, plan_name: str
) -> ExportOutcome:
    """Write the cash-flow CSV to ``path``, reporting the outcome.

    Follows the save transition's rule (§4.7): every failure folds
    into a status message, never an exception at a shell.
    """
    text = cash_flow_csv(state, basis=basis, plan_name=plan_name)
    if text is None:
        return ExportOutcome(saved=False, message=NOTHING_TO_EXPORT_MESSAGE)
    try:
        path.write_text(text, encoding="utf-8", newline="")
    except OSError as exc:
        return ExportOutcome(
            saved=False, message=f"{_CASH_FLOW_EXPORT_FAILED_PREFIX}{exc}"
        )
    return ExportOutcome(saved=True, message=f"Cash flow exported to {path}.")


def report_exported_message(path: Path) -> str:
    """The status line for a report the shell finished writing."""
    return f"Report exported to {path}."


def _cell(text: str) -> str:
    """One escaped table cell; multi-line values keep their lines."""
    return escape(text).replace("\n", "<br>")


def _html_table(columns: tuple[str, ...], rows: Iterable[tuple[str, ...]]) -> str:
    """Column headers and rows as an escaped rich-text table."""
    header = "".join(f"<th>{escape(column)}</th>" for column in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_cell(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (
        '<table border="1" cellspacing="0" cellpadding="4">'
        f"<tr>{header}</tr>{body}</table>"
    )


def _inputs_sections(state: PlanState) -> list[str]:
    """The report's inputs: facts, assumptions, decisions, structure (§1)."""
    inspector = build_inspector_view_model(state)
    parts = [f"<h2>{escape(_INPUTS_HEADING)}</h2>"]
    parts.append(f"<h3>{escape(inspector.facts_heading)}</h3>")
    parts.append(
        _html_table(
            inspector.facts_columns,
            (
                (row.label, row.value, row.as_of, row.recorded)
                for row in inspector.facts
            ),
        )
    )
    if inspector.roll_forwards:
        parts.append(f"<h3>{escape(inspector.roll_forwards_heading)}</h3>")
        parts.append(
            _html_table(
                inspector.roll_forwards_columns,
                (
                    (row.label, row.stated, row.as_of, row.months, row.opening)
                    for row in inspector.roll_forwards
                ),
            )
        )
    # The screen's value/default columns truncate long tables; the
    # report is the audit surface, so it prints the complete forms.
    parts.append(f"<h3>{escape(inspector.assumptions_heading)}</h3>")
    parts.append(
        _html_table(
            inspector.assumptions_columns,
            (
                (
                    row.label,
                    row.edit_text if row.structured else row.value,
                    row.default_edit_text,
                    row.status,
                    row.usage,
                    row.source,
                    row.recorded,
                )
                for row in inspector.assumptions
            ),
        )
    )
    parts.append(f"<h3>{escape(inspector.decisions_heading)}</h3>")
    parts.append(
        _html_table(
            inspector.decisions_columns,
            ((row.label, row.value, row.recorded) for row in inspector.decisions),
        )
    )
    if inspector.structure:
        parts.append(f"<h3>{escape(inspector.structure_heading)}</h3>")
        parts.append(
            _html_table(
                inspector.structure_columns,
                ((row.entity, row.setting, row.value) for row in inspector.structure),
            )
        )
    return parts


def _results_sections(state: PlanState, request: ReportRequest) -> list[str]:
    """The report's results: charts, Monte Carlo, backtest, retirement."""
    charts = build_charts_view_model(state, request.basis, request.mode)
    parts = [
        f"<h2>{escape(_RESULTS_HEADING)}</h2>",
        f"<p>Money basis: {escape(_basis_label(request.basis))}</p>",
    ]
    for index, chart in enumerate(charts.charts):
        parts.append(f"<h3>{escape(chart.title)}</h3>")
        parts.append(
            f'<img src="{chart_resource_name(index)}" width="{_REPORT_CHART_WIDTH}">'
        )
    panel = charts.monte_carlo
    if panel.metrics:
        # The seed and path count are the run's §4.6 reproducibility
        # manifest — without them the report's metrics are unattributable.
        parts.append(f"<h3>{escape(_MONTE_CARLO_HEADING)}</h3>")
        parts.append(
            _html_table(
                _METRIC_COLUMNS,
                (
                    (panel.seed_label, panel.seed_value),
                    (panel.paths_label, panel.paths_value),
                    *((row.label, row.value) for row in panel.metrics),
                ),
            )
        )
    backtest = charts.backtest
    if backtest.metrics:
        parts.append(f"<h3>{escape(backtest.heading)}</h3>")
        parts.append(
            _html_table(
                _METRIC_COLUMNS,
                tuple((row.label, row.value) for row in backtest.metrics),
            )
        )
    retirement = charts.retirement
    if retirement.answer:
        parts.append(f"<h3>{escape(retirement.heading)}</h3>")
        parts.append(f"<p>{escape(retirement.answer)}</p>")
        if retirement.detail:
            parts.append(f"<p>{escape(retirement.detail)}</p>")
    return parts


def _comparison_sections(state: PlanState, request: ReportRequest) -> list[str]:
    """The scenario comparison table, when there is one to print."""
    scenarios = build_scenarios_view_model(
        state,
        basis=request.comparison_basis,
        metric_key=request.comparison_metric_key,
    )
    if not scenarios.comparison_rows:
        return []
    metric_label = next(
        option.label
        for option in scenarios.metric_options
        if option.key == scenarios.selected_metric_key
    )
    return [
        f"<h2>{escape(scenarios.comparison_heading)}</h2>",
        (
            f"<p>{escape(metric_label)}"
            f" ({escape(_basis_label(request.comparison_basis))})</p>"
        ),
        _html_table(scenarios.comparison_columns, scenarios.comparison_rows),
    ]


def build_plan_report(state: PlanState, request: ReportRequest) -> PlanReport | None:
    """The full printable plan report, or ``None`` without a projection.

    Inputs with their provenance first, then the projection results
    with the three charts (plus Monte Carlo metrics, backtest metrics,
    and the retirement answer when held), then the scenario comparison
    when scenarios exist — the disclaimer leads the document (§1).
    """
    if state.result is None:
        return None
    charts = build_charts_view_model(state, request.basis, request.mode)
    inspector_summary = build_inspector_view_model(state)
    parts = [
        f"<h1>{escape(APP_NAME)} plan report — {escape(request.plan_name)}</h1>",
        f"<p><i>{escape(DISCLAIMER_BODY)}</i></p>",
        f"<p>{escape(inspector_summary.summary)}</p>",
    ]
    if inspector_summary.summary_detail:
        parts.append(f"<p>{escape(inspector_summary.summary_detail)}</p>")
    parts.extend(_inputs_sections(state))
    parts.extend(_results_sections(state, request))
    parts.extend(_comparison_sections(state, request))
    return PlanReport(
        html="".join(parts), charts=charts.charts, categories=charts.categories
    )


__all__ = [
    "CASH_FLOW_FILE_FILTER",
    "CASH_FLOW_FILE_SUFFIX",
    "EXPORT_CASH_FLOW_DIALOG_TITLE",
    "EXPORT_CASH_FLOW_LABEL",
    "EXPORT_REPORT_DIALOG_TITLE",
    "EXPORT_REPORT_LABEL",
    "NOTHING_TO_EXPORT_MESSAGE",
    "REPORT_EXPORT_FAILED_PREFIX",
    "REPORT_FILE_FILTER",
    "REPORT_FILE_SUFFIX",
    "REPORT_NOT_WRITTEN_MESSAGE",
    "UNSAVED_PLAN_NAME",
    "ExportOutcome",
    "PlanReport",
    "ReportRequest",
    "build_plan_report",
    "cash_flow_csv",
    "chart_resource_name",
    "export_cash_flow_csv",
    "plan_display_name",
    "report_exported_message",
]
