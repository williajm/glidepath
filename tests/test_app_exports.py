"""Export tests: the cash-flow CSV and the plan report (roadmap 9.19).

The acceptance criteria: the CSV round-trips the on-screen per-year
numbers exactly, pinned against the report model the charts render;
the report carries the inputs with their provenance, the results, and
the §1 disclaimer; both fold every failure into a status message.
"""

import csv
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from html import escape
from io import StringIO
from typing import TYPE_CHECKING

from glidepath.app import (
    DEFAULT_COMPARISON_METRIC_KEY,
    DISCLAIMER_BODY,
    MONTE_CARLO_CHART_TITLE,
    NOTHING_TO_EXPORT_MESSAGE,
    PlanState,
    ReportRequest,
    RetirementAnswer,
    build_inspector_view_model,
    build_plan_report,
    cash_flow_csv,
    chart_resource_name,
    example_facts_form_data,
    export_cash_flow_csv,
    initial_plan_state,
    parse_facts_form,
    plan_display_name,
    state_with_backtest,
    state_with_household,
    state_with_monte_carlo,
    state_with_override,
    state_with_scenario_added,
)
from glidepath.app.plan import region_for
from glidepath.core import (
    Money,
    ReportBasis,
    RunConfig,
    RunMode,
    build_report,
    run,
)

if TYPE_CHECKING:
    from pathlib import Path

RECORDED = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
TODAY = RECORDED.date()

MONEY_COLUMNS = {
    "Employment income": "employment_income",
    "DB income": "db_income",
    "DB lump sum": "db_lump_sum",
    "Pension lump sum": "pension_lump_sum",
    "State pension income": "state_pension_income",
    "Annuity income": "annuity_income",
    "Annuity lump sum": "annuity_lump_sum",
    "Annuity purchases": "annuity_purchases",
    "Tax due": "tax_due",
    "Spending need": "spending_need",
    "Planned outflows": "planned_outflows",
    "Net withdrawn": "net_withdrawn",
    "Shortfall": "shortfall",
    "Contributions": "contributions",
    "Fees": "fees",
    "Growth": "growth",
    "Growth tax": "growth_tax",
    "AA charge": "aa_charge",
    "Banked": "banked",
    "Withdrawals (gross)": "withdrawals_gross",
    "Closing balance": "closing_balance",
}
"""Every money column the CSV pins, by report-model field (9.19)."""


def projected_state() -> PlanState:
    """A projected session over the launch example's household."""
    result = parse_facts_form(
        example_facts_form_data(), recorded_on=RECORDED, today=TODAY
    )
    assert result.household is not None
    return state_with_household(initial_plan_state(), result.household, today=TODAY)


def _monte_carlo_state() -> PlanState:
    """A session whose held result is a seeded one-path Monte Carlo run."""
    state = projected_state()
    assert state.household is not None
    config = RunConfig(today=TODAY, mode=RunMode.MONTE_CARLO, seed=7)
    result = run(
        state.household, state.assumptions, region_for(state.assumptions), config
    )
    return replace(state, result=result)


def _stale_balance_state() -> PlanState:
    """A projected session whose wrapper balances are a year old."""
    data = example_facts_form_data()
    stale = replace(
        data,
        wrappers=tuple(
            {**wrapper, "balances_as_of": "2025-08-03"} for wrapper in data.wrappers
        ),
    )
    result = parse_facts_form(stale, recorded_on=RECORDED, today=TODAY)
    assert result.household is not None
    return state_with_household(initial_plan_state(), result.household, today=TODAY)


def _request(
    mode: RunMode = RunMode.DETERMINISTIC, plan_name: str = "Example"
) -> ReportRequest:
    """A report request on the screens' default presentation choices."""
    return ReportRequest(
        plan_name=plan_name,
        basis=ReportBasis.REAL,
        mode=mode,
        comparison_basis=ReportBasis.REAL,
        comparison_metric_key=DEFAULT_COMPARISON_METRIC_KEY,
    )


def _parsed(text: str) -> tuple[list[str], list[list[str]]]:
    """The CSV's column header and data rows, past the header block."""
    rows = list(csv.reader(StringIO(text)))
    separator = rows.index([])
    return rows[separator + 1], rows[separator + 2 :]


class TestCashFlowCsv:
    """The CSV serialises the active run's report exactly."""

    def test_round_trips_every_money_column_exactly(self) -> None:
        """Each amount parses back to the report model's exact decimal."""
        state = projected_state()
        text = cash_flow_csv(state, basis=ReportBasis.REAL, plan_name="Example")
        assert text is not None
        columns, data = _parsed(text)
        assert state.result is not None
        report = build_report(state.result, ReportBasis.REAL)
        assert len(data) == len(report.rows)
        for label, field in MONEY_COLUMNS.items():
            index = columns.index(label)
            for cells, row in zip(data, report.rows, strict=True):
                assert Decimal(cells[index]) == getattr(row, field).amount

    def test_rows_carry_period_person_age_and_stage(self) -> None:
        """The identity columns match the report model row for row."""
        state = projected_state()
        text = cash_flow_csv(state, basis=ReportBasis.REAL, plan_name="Example")
        assert text is not None
        columns, data = _parsed(text)
        assert state.result is not None
        report = build_report(state.result, ReportBasis.REAL)
        age_index = columns.index("Age at period start")
        for cells, row in zip(data, report.rows, strict=True):
            assert cells[0] == row.period.start.isoformat()
            assert cells[1] == row.period.end.isoformat()
            assert cells[2] == "You"
            assert cells[age_index] == str(row.age_at_period_start)

    def test_wrapper_columns_round_trip_the_per_wrapper_balances(self) -> None:
        """One closing-balance column per wrapper, in first-seen order."""
        state = projected_state()
        text = cash_flow_csv(state, basis=ReportBasis.REAL, plan_name="Example")
        assert text is not None
        columns, data = _parsed(text)
        assert state.result is not None
        report = build_report(state.result, ReportBasis.REAL)
        wrapper_indexes = [
            index
            for index, label in enumerate(columns)
            if label.startswith("Closing balance — ")
        ]
        assert len(wrapper_indexes) == len(report.rows[0].wrapper_balances)
        for cells, row in zip(data, report.rows, strict=True):
            for index, entry in zip(wrapper_indexes, row.wrapper_balances, strict=True):
                assert Decimal(cells[index]) == entry.closing_balance.amount

    def test_header_block_names_plan_run_scenario_basis_disclaimer(self) -> None:
        """The header block is the issue's required manifest (9.19, §1)."""
        state = projected_state()
        text = cash_flow_csv(state, basis=ReportBasis.REAL, plan_name="My plan")
        assert text is not None
        rows = list(csv.reader(StringIO(text)))
        assert rows[0] == ["Plan", "My plan"]
        assert rows[1] == ["Run", "Deterministic"]
        assert rows[2] == ["Scenario", "Base plan"]
        assert rows[3] == ["Money basis", "Real (today's money)"]
        assert rows[4] == ["Disclaimer", DISCLAIMER_BODY]

    def test_monte_carlo_run_header_pins_the_seed(self) -> None:
        """A Monte Carlo run's header line carries its seed (§4.6).

        The seed is the reproducibility claim, so the exact text is
        pinned — the file must attribute its numbers to a rerunnable
        run, never to a bare "Monte Carlo".
        """
        text = cash_flow_csv(
            _monte_carlo_state(), basis=ReportBasis.REAL, plan_name="Example"
        )
        assert text is not None
        rows = list(csv.reader(StringIO(text)))
        assert rows[1] == ["Run", "Monte Carlo, seed 7"]

    def test_nominal_basis_serialises_the_nominal_report(self) -> None:
        """The basis toggle drives the export, same as the charts."""
        state = projected_state()
        text = cash_flow_csv(state, basis=ReportBasis.NOMINAL, plan_name="Example")
        assert text is not None
        rows = list(csv.reader(StringIO(text)))
        assert rows[3] == ["Money basis", "Nominal"]
        columns, data = _parsed(text)
        assert state.result is not None
        report = build_report(state.result, ReportBasis.NOMINAL)
        index = columns.index("Closing balance")
        last_row = report.rows[-1]
        assert Decimal(data[-1][index]) == last_row.closing_balance.amount

    def test_no_projection_yields_none(self) -> None:
        """Without a run there is no table to serialise."""
        text = cash_flow_csv(
            initial_plan_state(), basis=ReportBasis.REAL, plan_name="Example"
        )
        assert text is None


class TestExportCashFlowCsv:
    """The file-writing transition reports its outcome (§4.7)."""

    def test_writes_the_file_and_reports_the_path(self, tmp_path: Path) -> None:
        """A successful export names the file it wrote."""
        path = tmp_path / "cash-flow.csv"
        outcome = export_cash_flow_csv(
            projected_state(), path, basis=ReportBasis.REAL, plan_name="Example"
        )
        assert outcome.saved
        assert str(path) in outcome.message
        assert path.exists()
        text = cash_flow_csv(
            projected_state(), basis=ReportBasis.REAL, plan_name="Example"
        )
        assert text is not None
        with path.open(encoding="utf-8", newline="") as handle:
            assert handle.read() == text

    def test_empty_session_has_nothing_to_export(self, tmp_path: Path) -> None:
        """Without a run the export declines with the empty-state copy."""
        path = tmp_path / "cash-flow.csv"
        outcome = export_cash_flow_csv(
            initial_plan_state(), path, basis=ReportBasis.REAL, plan_name="Example"
        )
        assert not outcome.saved
        assert outcome.message == NOTHING_TO_EXPORT_MESSAGE
        assert not path.exists()

    def test_unwritable_path_reports_rather_than_raises(self, tmp_path: Path) -> None:
        """An OS failure comes back as a status message."""
        outcome = export_cash_flow_csv(
            projected_state(),
            tmp_path / "missing-dir" / "cash-flow.csv",
            basis=ReportBasis.REAL,
            plan_name="Example",
        )
        assert not outcome.saved
        assert outcome.message.startswith("Could not export the cash flow")


class TestPlanDisplayName:
    """The exported plan's display name comes from its file."""

    def test_unsaved_session_uses_the_placeholder(self) -> None:
        """No plan file yet — the exports say so rather than invent."""
        assert plan_display_name(None) == "Unsaved plan"

    def test_saved_plan_drops_the_canonical_suffix(self, tmp_path: Path) -> None:
        """The double suffix never leaks into the report title."""
        assert plan_display_name(tmp_path / "my-plan.glidepath.json") == "my-plan"


class TestPlanReport:
    """The printable report carries inputs, results, and the disclaimer."""

    def test_no_projection_yields_none(self) -> None:
        """Without a run there is nothing to report."""
        assert build_plan_report(initial_plan_state(), _request()) is None

    def test_carries_disclaimer_charts_and_provenance(self) -> None:
        """Disclaimer, three charts, and every provenance section (§1)."""
        state = projected_state()
        report = build_plan_report(state, _request(plan_name="My plan"))
        assert report is not None
        assert DISCLAIMER_BODY in report.html
        assert "My plan" in report.html
        assert len(report.charts) == 3
        for index in range(len(report.charts)):
            assert chart_resource_name(index) in report.html
        inspector = build_inspector_view_model(state)
        assert inspector.facts_heading in report.html
        assert inspector.facts[0].label in report.html
        assert inspector.assumptions_heading in report.html
        assert inspector.assumptions[0].source in report.html
        assert inspector.decisions_heading in report.html
        assert inspector.decisions[0].label in report.html
        assert inspector.structure_heading in report.html

    def test_structured_assumption_values_print_in_full(self) -> None:
        """Table values escape the screen's truncated display form.

        The report is the audit surface (9.19): every structured
        assumption prints its complete round-trippable text, never the
        120-character screen rendering.
        """
        state = projected_state()
        report = build_plan_report(state, _request())
        assert report is not None
        inspector = build_inspector_view_model(state)
        structured = [row for row in inspector.assumptions if row.structured]
        assert structured
        for row in structured:
            assert escape(row.edit_text).replace("\n", "<br>") in report.html

    def test_overridden_default_prints_beside_the_override(self) -> None:
        """An override never hides the shipped default it replaced (§1)."""
        outcome = state_with_override(
            projected_state(),
            "inflation.cpi",
            "0.031",
            recorded_on=RECORDED,
            today=TODAY,
        )
        assert outcome.error is None
        report = build_plan_report(outcome.state, _request())
        assert report is not None
        assert "<td>0.031</td>" in report.html
        assert "<td>0.02</td>" in report.html

    def test_plan_name_is_escaped_into_the_html(self) -> None:
        """User-controlled text can never inject markup."""
        report = build_plan_report(projected_state(), _request(plan_name="<b>plan</b>"))
        assert report is not None
        assert "<b>plan</b>" not in report.html
        assert "&lt;b&gt;plan&lt;/b&gt;" in report.html

    def test_scenario_comparison_included_only_when_scenarios_exist(self) -> None:
        """The comparison table prints exactly when there is one."""
        state = projected_state()
        plain = build_plan_report(state, _request())
        assert plain is not None
        assert "Comparison vs base plan" not in plain.html
        added = state_with_scenario_added(state, "Retire later", today=TODAY)
        assert added.error is None
        compared = build_plan_report(added.state, _request())
        assert compared is not None
        assert "Comparison vs base plan" in compared.html
        assert "Retire later" in compared.html

    def test_monte_carlo_metrics_ride_along_under_that_mode(self) -> None:
        """A held run adds its metrics and fan chart (9.13, 9.24)."""
        state = state_with_monte_carlo(projected_state(), "1", "3", today=TODAY)
        assert state.monte_carlo is not None
        report = build_plan_report(state, _request(mode=RunMode.MONTE_CARLO))
        assert report is not None
        assert "Success rate" in report.html
        assert report.charts[-1].title == MONTE_CARLO_CHART_TITLE
        assert report.charts[-1].fills
        assert f"<h3>{MONTE_CARLO_CHART_TITLE}</h3>" in report.html
        # The seed and path count attribute the metrics to their run (§4.6).
        assert "<td>Seed</td><td>1</td>" in report.html
        assert "<td>Paths</td><td>3</td>" in report.html

    def test_a_picked_backtest_year_prints_its_trajectory(self) -> None:
        """The report draws exactly what is on screen (9.18).

        The starting-year picker's selection rides on the request, so
        an exported PDF never silently drops a trajectory the user is
        looking at.
        """
        state = state_with_backtest(projected_state(), today=TODAY)
        assert state.backtest is not None
        outcomes = state.backtest.outcomes
        picked_year = outcomes[len(outcomes) // 2].start_year
        report = build_plan_report(
            state, replace(_request(), backtest_year=str(picked_year))
        )
        assert report is not None
        labels = [band.label for band in report.charts[0].bands]
        assert f"Start · {picked_year}" in labels
        assert labels[0].startswith("Worst start · ")

    def test_monte_carlo_provenance_names_the_seed_in_the_summary(self) -> None:
        """A Monte Carlo result's report attributes it to its seed (§4.6)."""
        report = build_plan_report(_monte_carlo_state(), _request())
        assert report is not None
        assert "Monte Carlo run, seed 7" in report.html
        assert "seed: 7" in report.html

    def test_stale_balances_print_the_roll_forward_section(self) -> None:
        """The §4.8 roll-forward table prints exactly when rows exist.

        The report is the audit surface: a year-old statement's stated
        value, date, months rolled, and opening value all print, and a
        fresh plan carries no such section at all.
        """
        state = _stale_balance_state()
        inspector = build_inspector_view_model(state)
        assert inspector.roll_forwards
        fresh = build_plan_report(projected_state(), _request())
        assert fresh is not None
        assert inspector.roll_forwards_heading not in fresh.html
        report = build_plan_report(state, _request())
        assert report is not None
        assert inspector.roll_forwards_heading in report.html
        row = inspector.roll_forwards[0]
        assert row.months == "12"
        cells = (row.label, row.stated, row.as_of, row.months, row.opening)
        rendered = "".join(f"<td>{escape(cell)}</td>" for cell in cells)
        assert rendered in report.html

    def test_a_held_retirement_answer_prints_its_section(self) -> None:
        """The "When can I retire?" answer rides on the report (9.14)."""
        state = projected_state()
        answer = RetirementAnswer(
            age=58,
            replacement_rate=Decimal("0.66"),
            employment_income=Money(Decimal(52000)),
            target_income=Money(Decimal(34320)),
            minimum_age=35,
            maximum_age=94,
            mode=RunMode.DETERMINISTIC,
        )
        held = replace(state, retirement=answer)
        report = build_plan_report(held, _request())
        assert report is not None
        assert "<h3>When can I retire?</h3>" in report.html
        assert "Earliest retirement age: 58" in report.html
        assert "Target income £34,320.00 a year" in report.html
        assert (
            "Basis: deterministic projection, no unmet need in any period."
            in report.html
        )
        plain = build_plan_report(state, _request())
        assert plain is not None
        assert "When can I retire?" not in plain.html

    def test_deterministic_mode_omits_monte_carlo(self) -> None:
        """Under the deterministic mode a held run stays off the report."""
        state = state_with_monte_carlo(projected_state(), "1", "3", today=TODAY)
        assert state.monte_carlo is not None
        report = build_plan_report(state, _request())
        assert report is not None
        assert "Success rate" not in report.html
        assert all(chart.title != MONTE_CARLO_CHART_TITLE for chart in report.charts)
        assert all(chart.fills == () for chart in report.charts)
