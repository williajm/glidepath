"""Scenario comparison report (roadmap 6.3; planning §4.3).

Comparison is a per-period metrics report across scenarios, computed
from each scenario's resolved run: :func:`run_scenarios` resolves every
scenario against the base plan (raising on orphans) and projects each
through the engine; :func:`compare_scenario_results` aligns the runs
period by period — household-level totals via the reporting layer, in
real (today's money) or nominal basis — and diffs every non-base run
against the base.

Rows cover the union of the runs' periods (a scenario overriding the
planning-age assumption projects a different horizon); a run simply has
no entry in a period it never modelled, and a delta appears only where
the base modelled the period too. Metric amounts are presentation
values (quantized, planning §4.6); deltas are exact differences of
those.
"""

from dataclasses import dataclass, fields
from operator import add, sub
from typing import TYPE_CHECKING

from glidepath.core.engine import run
from glidepath.core.reporting import ReportBasis, build_report
from glidepath.core.scenarios import ScenarioError, resolve_scenario

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from glidepath.core.config import RunConfig
    from glidepath.core.entities import Household
    from glidepath.core.money import Money
    from glidepath.core.periods import Period
    from glidepath.core.provenance import AssumptionSet
    from glidepath.core.region import Region
    from glidepath.core.reporting import PeriodReportRow
    from glidepath.core.results import ProjectionResult
    from glidepath.core.scenarios import Scenario

BASE_RUN_NAME = "base"
"""The reserved name of the unmodified base plan's run."""

_MIN_RUNS = 2
"""A comparison needs a baseline plus at least one other run."""


@dataclass(frozen=True, slots=True)
class PeriodMetrics:
    """One run's household-level totals for one period (roadmap 6.3).

    ``income_total`` is income in payment (employment, DB, state
    pension, annuity); ``lump_sums`` the one-off tax-advantaged cash
    (DB commutation, up-front pension lump sums, annuity-purchase
    tax-free cash). The rest carry the reporting layer's meanings.
    Amounts follow the report's basis; a delta's amounts may be
    negative.
    """

    closing_balance: Money
    income_total: Money
    lump_sums: Money
    tax_due: Money
    contributions: Money
    withdrawals_gross: Money
    net_withdrawn: Money
    spending_need: Money
    planned_outflows: Money
    shortfall: Money

    def _combined(
        self, other: PeriodMetrics, combine: Callable[[Money, Money], Money]
    ) -> PeriodMetrics:
        """Apply ``combine`` field-wise, producing new metrics."""
        combined = {
            field.name: combine(getattr(self, field.name), getattr(other, field.name))
            for field in fields(self)
        }
        return PeriodMetrics(**combined)

    def __add__(self, other: PeriodMetrics) -> PeriodMetrics:
        """Field-wise sum — totalling persons within a period."""
        return self._combined(other, add)

    def __sub__(self, other: PeriodMetrics) -> PeriodMetrics:
        """Field-wise difference — a scenario's delta vs the base."""
        return self._combined(other, sub)


@dataclass(frozen=True, slots=True)
class ScenarioPeriodEntry:
    """One run's metrics in one period, with its delta vs the base.

    ``delta_vs_base`` is ``None`` on the base run's own entries and on
    a period the base run never modelled.
    """

    run_name: str
    metrics: PeriodMetrics
    delta_vs_base: PeriodMetrics | None


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    """One period's metrics across the runs that modelled it.

    Entries appear in run order (base first); a run without this
    period contributes no entry.
    """

    period: Period
    entries: tuple[ScenarioPeriodEntry, ...]


@dataclass(frozen=True, slots=True)
class ScenarioComparison:
    """The per-period metrics report across scenarios (planning §4.3)."""

    basis: ReportBasis
    run_names: tuple[str, ...]
    rows: tuple[ComparisonRow, ...]


def run_scenarios(
    household: Household,
    assumptions: AssumptionSet,
    scenarios: Sequence[Scenario],
    region: Region,
    config: RunConfig,
) -> tuple[tuple[str, ProjectionResult], ...]:
    """Project the base plan and every scenario's resolved inputs.

    Returns named runs — the base first under :data:`BASE_RUN_NAME`,
    then one per scenario in the given order — ready for
    :func:`compare_scenario_results`. All runs share ``config``: run
    settings are not scenario-overridable in v1.

    Raises:
        ScenarioError: If a scenario has orphaned overrides, a
            mistyped override value, a duplicate name, or the
            reserved base name.
        EngineError: If any resolved plan is not projectable.
    """
    names = [scenario.name for scenario in scenarios]
    if BASE_RUN_NAME in names or len(set(names)) != len(names):
        msg = f"scenario names must be unique and none may be {BASE_RUN_NAME!r}"
        raise ScenarioError(msg)
    runs = [(BASE_RUN_NAME, run(household, assumptions, region, config))]
    for scenario in scenarios:
        resolution = resolve_scenario(household, assumptions, scenario)
        runs.append(
            (
                scenario.name,
                run(resolution.household, resolution.assumptions, region, config),
            )
        )
    return tuple(runs)


def compare_scenario_results(
    runs: Sequence[tuple[str, ProjectionResult]],
    basis: ReportBasis = ReportBasis.REAL,
) -> ScenarioComparison:
    """Diff named runs per period — the first run is the baseline.

    Raises:
        ValueError: If fewer than two runs are given or names repeat.
    """
    if len(runs) < _MIN_RUNS:
        msg = "a scenario comparison needs at least two runs"
        raise ValueError(msg)
    names = [name for name, _ in runs]
    if len(set(names)) != len(names):
        msg = "scenario comparison run names must be unique"
        raise ValueError(msg)
    per_run = [(name, _metrics_by_period(result, basis)) for name, result in runs]
    base_metrics = per_run[0][1]
    periods = sorted({period for _, metrics in per_run for period in metrics})
    rows = []
    for period in periods:
        entries = []
        base_in_period = base_metrics.get(period)
        for index, (name, metrics) in enumerate(per_run):
            in_period = metrics.get(period)
            if in_period is None:
                continue
            delta = None
            if index > 0 and base_in_period is not None:
                delta = in_period - base_in_period
            entries.append(
                ScenarioPeriodEntry(
                    run_name=name, metrics=in_period, delta_vs_base=delta
                )
            )
        rows.append(ComparisonRow(period=period, entries=tuple(entries)))
    return ScenarioComparison(basis=basis, run_names=tuple(names), rows=tuple(rows))


def _metrics_by_period(
    result: ProjectionResult, basis: ReportBasis
) -> dict[Period, PeriodMetrics]:
    """Household-level period totals of one run, in the report's basis."""
    totals: dict[Period, PeriodMetrics] = {}
    for row in build_report(result, basis).rows:
        metrics = _row_metrics(row)
        existing = totals.get(row.period)
        totals[row.period] = metrics if existing is None else existing + metrics
    return totals


def _row_metrics(row: PeriodReportRow) -> PeriodMetrics:
    """One person's report row reduced to the comparison metrics."""
    return PeriodMetrics(
        closing_balance=row.closing_balance,
        income_total=(
            row.employment_income
            + row.db_income
            + row.state_pension_income
            + row.annuity_income
        ),
        lump_sums=row.db_lump_sum + row.pension_lump_sum + row.annuity_lump_sum,
        tax_due=row.tax_due,
        contributions=row.contributions,
        withdrawals_gross=row.withdrawals_gross,
        net_withdrawn=row.net_withdrawn,
        spending_need=row.spending_need,
        planned_outflows=row.planned_outflows,
        shortfall=row.shortfall,
    )
