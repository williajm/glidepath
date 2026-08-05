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
the base modelled the period too — over the *same* interval: a
horizon-clipped partial period (``year_fraction`` < 1) never diffs
against a whole one. Metric amounts are presentation values (quantized,
planning §4.6); deltas are exact differences of those.
"""

from dataclasses import dataclass, fields
from operator import add, sub
from typing import TYPE_CHECKING

from glidepath.core.engine import run
from glidepath.core.reporting import ReportBasis, build_report
from glidepath.core.scenarios import ScenarioError, resolve_scenario

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from decimal import Decimal

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
    tax-free cash). The pension and annuity elements of ``lump_sums``
    are column views of tax-free cash the wrappers already carry in
    ``withdrawals_gross`` — only the DB commutation arrives from
    outside the wrappers — so the two metrics overlap and must never
    be summed. The rest carry the reporting layer's meanings.
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

    ``year_fraction`` is the whole-month fraction of the period this
    run actually modelled (planning §5.2) — less than 1 when ``today``
    or the run's horizon fell mid-period. ``delta_vs_base`` is ``None``
    on the base run's own entries, on a period the base run never
    modelled, and on a period the two runs modelled over *different*
    intervals (unequal year fractions): flows over half a year minus
    flows over a whole year is not a meaningful delta.
    """

    run_name: str
    metrics: PeriodMetrics
    year_fraction: Decimal
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
    region_for: Callable[[AssumptionSet], Region],
    config: RunConfig,
) -> tuple[tuple[str, ProjectionResult], ...]:
    """Project the base plan and every scenario's resolved inputs.

    Returns named runs — the base first under :data:`BASE_RUN_NAME`,
    then one per scenario in the given order — ready for
    :func:`compare_scenario_results`.

    ``region_for`` builds the region bundle for one run from that
    run's *effective* assumption set. Regions derive data from
    assumptions at build time (e.g. the UK future-years tax extension
    and state pension uprating read ``policy.tax.future_years``,
    ``inflation.cpi``, and ``policy.state_pension.uprating``), so a
    prebuilt region shared across runs would silently pin every
    scenario to the base policy and misreport the region data version;
    the factory rebuilds it per resolved set instead. All runs share
    ``config``: run settings are not scenario-overridable in v1.

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
    base_region = region_for(assumptions)
    runs = [(BASE_RUN_NAME, run(household, assumptions, base_region, config))]
    for scenario in scenarios:
        resolution = resolve_scenario(household, assumptions, scenario)
        runs.append(
            (
                scenario.name,
                run(
                    resolution.household,
                    resolution.assumptions,
                    region_for(resolution.assumptions),
                    config,
                ),
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
    per_run = [(name, _totals_by_period(result, basis)) for name, result in runs]
    base_totals = per_run[0][1]
    periods = sorted({period for _, totals in per_run for period in totals})
    rows = []
    for period in periods:
        entries = []
        base_total = base_totals.get(period)
        for index, (name, totals) in enumerate(per_run):
            total = totals.get(period)
            if total is None:
                continue
            delta = None
            if (
                index > 0
                and base_total is not None
                and total.year_fraction == base_total.year_fraction
            ):
                delta = total.metrics - base_total.metrics
            entries.append(
                ScenarioPeriodEntry(
                    run_name=name,
                    metrics=total.metrics,
                    year_fraction=total.year_fraction,
                    delta_vs_base=delta,
                )
            )
        rows.append(ComparisonRow(period=period, entries=tuple(entries)))
    return ScenarioComparison(basis=basis, run_names=tuple(names), rows=tuple(rows))


@dataclass(frozen=True, slots=True)
class _PeriodTotals:
    """One run's household totals for one period, with its interval."""

    metrics: PeriodMetrics
    year_fraction: Decimal


def _totals_by_period(
    result: ProjectionResult, basis: ReportBasis
) -> dict[Period, _PeriodTotals]:
    """Household-level period totals of one run, in the report's basis.

    Persons within a period sum; the period's ``year_fraction`` is the
    snapshot's own (one snapshot per period, so persons share it).
    """
    totals: dict[Period, _PeriodTotals] = {}
    for row in build_report(result, basis).rows:
        metrics = _row_metrics(row)
        existing = totals.get(row.period)
        if existing is not None:
            metrics = existing.metrics + metrics
        totals[row.period] = _PeriodTotals(
            metrics=metrics, year_fraction=row.year_fraction
        )
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
