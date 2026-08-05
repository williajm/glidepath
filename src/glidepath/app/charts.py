"""Projection chart view models (roadmap 8.4; planning §5.2, §4.7).

Chart series for the three projection surfaces — wrapper balances,
income composition, and tax over the horizon — presented through the
core reporting layer (roadmap 4.4) in either money basis. **Real
(today's money) is the default presentation**; the nominal toggle
re-presents the same ledger, never a second inflation source (planning
§5.2). Amounts stay ``Decimal`` here: converting them to plot
coordinates is shell mechanics (§4.7).
"""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from glidepath.app.backtest import BacktestPanelViewModel, build_backtest_panel
from glidepath.app.display import format_money, format_wrapper_kind
from glidepath.app.montecarlo import (
    BAND_SPECS,
    DEFAULT_RUN_MODE,
    MonteCarloPanelViewModel,
    build_monte_carlo_panel,
)
from glidepath.app.retirement import (
    RetirementPanelViewModel,
    build_retirement_panel,
)
from glidepath.core import Money, ReportBasis, RunMode, build_report

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from glidepath.app.plan import PlanState
    from glidepath.core import (
        BacktestResult,
        EntityId,
        MonteCarloResult,
        Period,
        PeriodReportRow,
        ProjectionReport,
    )

_ZERO = Money(Decimal(0))
_MIN_AXIS_MAX = Decimal(1)

DEFAULT_CHART_BASIS: Final = ReportBasis.REAL

BASIS_HEADING: Final = "Money basis"

NO_PROJECTION_MESSAGE: Final = (
    "No projection yet — save facts on the Facts tab to see charts."
)

RUN_FAILED_PREFIX: Final = "The projection failed: "

BALANCES_CHART_TITLE: Final = "Wrapper balances"

INCOME_CHART_TITLE: Final = "Income composition"

TAX_CHART_TITLE: Final = "Tax"

TAX_SERIES_LABEL: Final = "Tax due"

_BASIS_BY_KEY: Final[Mapping[str, ReportBasis]] = {
    "real": ReportBasis.REAL,
    "nominal": ReportBasis.NOMINAL,
}

_KEY_BY_BASIS: Final[Mapping[ReportBasis, str]] = {
    basis: key for key, basis in _BASIS_BY_KEY.items()
}

_BASIS_LABELS: Final[Mapping[str, str]] = {
    "real": "Real (today's money)",
    "nominal": "Nominal",
}

_BASIS_SUFFIXES: Final[Mapping[str, str]] = {
    "real": "today's money",
    "nominal": "nominal",
}

_UNKNOWN_BASIS_MESSAGE: Final = "unknown chart basis key"

# The stacked sources must not overlap: the report's pension_lump_sum
# and annuity_lump_sum are column views of tax-free cash the wrappers
# already carry in withdrawal_tax_free, so they are already inside
# withdrawals_gross. Only the DB commutation lump sum is cash from
# outside the wrappers.
_INCOME_SOURCES: Final[tuple[tuple[str, Callable[[PeriodReportRow], Money]], ...]] = (
    ("Employment", lambda row: row.employment_income),
    ("DB pension", lambda row: row.db_income),
    ("DB lump sum", lambda row: row.db_lump_sum),
    ("State pension", lambda row: row.state_pension_income),
    ("Annuity income", lambda row: row.annuity_income),
    ("Withdrawals (gross)", lambda row: row.withdrawals_gross),
)


@dataclass(frozen=True)
class ChartBasisOption:
    """One money-basis choice for the charts screen (planning §5.2)."""

    key: str
    label: str


@dataclass(frozen=True)
class ChartSeries:
    """One stacked series: a label and a value per period category."""

    label: str
    values: tuple[Decimal, ...]


@dataclass(frozen=True)
class ChartBand:
    """One percentile line over the categories (roadmap 9.13)."""

    label: str
    values: tuple[Decimal, ...]


@dataclass(frozen=True)
class ChartSpec:
    """One chart, ready for a shell to bind to a plotting widget.

    ``bands`` are the Monte Carlo percentile lines drawn over the
    stacked bars (roadmap 9.13) — empty outside a Monte Carlo
    presentation. ``y_axis_max`` is the largest stacked total or band
    value across the periods (never below 1, so an all-zero chart
    still renders a visible axis); every value here is non-negative,
    so the y range is always ``[0, y_axis_max]``.
    """

    title: str
    y_axis_label: str
    y_axis_max: Decimal
    series: tuple[ChartSeries, ...]
    bands: tuple[ChartBand, ...] = ()


@dataclass(frozen=True)
class ChartsViewModel:
    """The projection charts screen (roadmap 8.4).

    ``categories`` labels the shared x axis — one label per projected
    period: the period-start year with the person's age at period
    start alongside (roadmap 9.11; year alone until couples activate,
    9.4 — a two-person period has no single age to show). ``message``
    carries the empty-state copy when there is nothing to chart; it
    is blank whenever ``charts`` is populated.
    """

    basis_heading: str
    basis_options: tuple[ChartBasisOption, ...]
    selected_basis_key: str
    categories: tuple[str, ...]
    charts: tuple[ChartSpec, ...]
    message: str
    monte_carlo: MonteCarloPanelViewModel
    retirement: RetirementPanelViewModel
    backtest: BacktestPanelViewModel


def basis_from_key(key: str) -> ReportBasis:
    """The report basis a shell-selected option key denotes.

    Raises:
        ValueError: If ``key`` is not a known basis option key.
    """
    basis = _BASIS_BY_KEY.get(key)
    if basis is None:
        raise ValueError(_UNKNOWN_BASIS_MESSAGE)
    return basis


def basis_key(basis: ReportBasis) -> str:
    """The option key denoting ``basis`` — the inverse of ``basis_from_key``."""
    return _KEY_BY_BASIS[basis]


def basis_options() -> tuple[ChartBasisOption, ...]:
    """The money-basis choices every basis toggle offers (planning §5.2)."""
    return tuple(
        ChartBasisOption(key=key, label=_BASIS_LABELS[key]) for key in _BASIS_BY_KEY
    )


def basis_suffix(basis: ReportBasis) -> str:
    """The axis-label suffix naming ``basis`` (e.g. ``today's money``)."""
    return _BASIS_SUFFIXES[_KEY_BY_BASIS[basis]]


def bar_tooltip(category: str, series_label: str, value: Decimal) -> str:
    """The hover copy for one bar segment (planning §4.7).

    Shells bind this to their plotting widget's hover affordance, so
    the copy — series, period, and the amount in pounds and pence —
    stays app-layer like every other label on the chart.
    """
    return f"{series_label}\n{category}: {format_money(Money(value))}"


def build_charts_view_model(
    state: PlanState,
    basis: ReportBasis = DEFAULT_CHART_BASIS,
    mode: RunMode = DEFAULT_RUN_MODE,
) -> ChartsViewModel:
    """The charts screen for ``state``, presented in ``basis``.

    Real (today's money) is the default; the deflators come from the
    run's own CPI path via the core reporting layer (planning §5.2).
    ``mode`` is the screen's run-mode selection (roadmap 9.13): under
    ``MONTE_CARLO`` a held Monte Carlo run adds its percentile bands
    to the balances chart and its metrics to the panel. Without a
    projection the screen carries only the empty-state message — the
    no-run copy, or the run failure held on the state.
    """
    selected_key = basis_key(basis)
    options = basis_options()
    suffix = basis_suffix(basis)
    if state.result is None:
        message = (
            NO_PROJECTION_MESSAGE
            if state.run_error is None
            else RUN_FAILED_PREFIX + state.run_error
        )
        return ChartsViewModel(
            basis_heading=BASIS_HEADING,
            basis_options=options,
            selected_basis_key=selected_key,
            categories=(),
            charts=(),
            message=message,
            monte_carlo=build_monte_carlo_panel(
                state, mode, ending_pot_deflator=None, basis_suffix=suffix
            ),
            retirement=build_retirement_panel(state, mode),
            backtest=build_backtest_panel(
                state, ending_pot_deflator=None, basis_suffix=suffix
            ),
        )
    grouped = _rows_by_period(build_report(state.result, basis))
    bands = _chart_bands(state, mode, grouped)
    final_rows = next(reversed(grouped.values()))
    return ChartsViewModel(
        basis_heading=BASIS_HEADING,
        basis_options=options,
        selected_basis_key=selected_key,
        categories=tuple(
            _category_label(period, rows) for period, rows in grouped.items()
        ),
        charts=(
            _balances_chart(grouped, suffix, bands),
            _income_chart(grouped, suffix),
            _tax_chart(grouped, suffix),
        ),
        message="",
        monte_carlo=build_monte_carlo_panel(
            state,
            mode,
            ending_pot_deflator=final_rows[0].balance_deflator,
            basis_suffix=suffix,
        ),
        retirement=build_retirement_panel(state, mode),
        backtest=build_backtest_panel(
            state,
            ending_pot_deflator=final_rows[0].balance_deflator,
            basis_suffix=suffix,
        ),
    )


def _chart_bands(
    state: PlanState,
    mode: RunMode,
    grouped: dict[Period, list[PeriodReportRow]],
) -> tuple[ChartBand, ...]:
    """The percentile bands the balances chart draws, if any.

    A held Monte Carlo run supplies them under the Monte Carlo mode
    (roadmap 9.13); a held backtest supplies them in either mode
    (roadmap 9.18) — the two can never be held together, since each
    slow-run transition re-anchors through
    :func:`~glidepath.app.plan.replanned_state` and so drops the
    other's result.
    """
    if mode is RunMode.MONTE_CARLO and state.monte_carlo is not None:
        return _balance_bands(state.monte_carlo, grouped)
    if state.backtest is not None:
        return _balance_bands(state.backtest, grouped)
    return ()


def _category_label(period: Period, rows: list[PeriodReportRow]) -> str:
    """One period's x-axis label: its start year, with the person's age.

    ``2032 · 60`` reads the horizon in ages as well as calendar years
    (roadmap 9.11). Only a single-person period carries an age — a
    two-person household has no one age to label with (revisit with
    couples activation, 9.4).
    """
    year = str(period.start.year)
    if len(rows) == 1:
        return f"{year} · {rows[0].age_at_period_start}"
    return year


def _rows_by_period(
    report: ProjectionReport,
) -> dict[Period, list[PeriodReportRow]]:
    """Report rows grouped by period, in period order.

    With a multi-person household each period holds one row per
    person; the charts aggregate them to household level.
    """
    grouped: dict[Period, list[PeriodReportRow]] = {}
    for row in report.rows:
        grouped.setdefault(row.period, []).append(row)
    return grouped


def _period_total(
    rows: list[PeriodReportRow], amount: Callable[[PeriodReportRow], Money]
) -> Decimal:
    """One period's household total of a per-person amount."""
    total = _ZERO
    for row in rows:
        total = total + amount(row)
    return total.amount


def _chart(
    title: str,
    y_axis_label: str,
    series: tuple[ChartSeries, ...],
    bands: tuple[ChartBand, ...] = (),
) -> ChartSpec:
    """A chart spec with its y-axis maximum covering stack and bands."""
    stacked: dict[int, Decimal] = {}
    for entry in series:
        for index, value in enumerate(entry.values):
            stacked[index] = stacked.get(index, Decimal(0)) + value
    band_values = [value for band in bands for value in band.values]
    return ChartSpec(
        title=title,
        y_axis_label=y_axis_label,
        y_axis_max=max([*stacked.values(), *band_values, _MIN_AXIS_MAX]),
        series=series,
        bands=bands,
    )


def wrapper_display_labels(rows: Iterable[PeriodReportRow]) -> dict[EntityId, str]:
    """A display label per wrapper, in first-seen order.

    The wrapper's kind name alone when unique; numbered in first-seen
    order when the household holds several of one kind (entity ids are
    generated UUIDs, so they are never shown as copy). Shared with the
    cash-flow export (9.19), which columns its balances the same way
    the balances chart stacks them.
    """
    kinds: dict[EntityId, str] = {}
    for row in rows:
        for entry in row.wrapper_balances:
            kinds.setdefault(entry.wrapper_id, format_wrapper_kind(entry.kind))
    counts = Counter(kinds.values())
    numbered: Counter[str] = Counter()
    labels: dict[EntityId, str] = {}
    for wrapper_id, kind in kinds.items():
        if counts[kind] == 1:
            labels[wrapper_id] = kind
        else:
            numbered[kind] += 1
            labels[wrapper_id] = f"{kind} {numbered[kind]}"
    return labels


def _balance_bands(
    result: MonteCarloResult | BacktestResult,
    grouped: dict[Period, list[PeriodReportRow]],
) -> tuple[ChartBand, ...]:
    """The 10/50/90 percentile bands over the balances chart (9.13, 9.18).

    Both slow-run results answer the same ``balance_percentile``
    reduction, so one composition serves paths and windows alike. The
    balances are nominal; CPI is deterministic across paths and
    windows (planning §5.2), so each period's band value deflates by
    the same balance deflator the deterministic report rows carry — 1
    under the nominal basis. A held result whose period count differs
    from the projection's (the runs straddled a calendar day) draws no
    bands rather than bands against the wrong periods.
    """
    deflators = tuple(rows[0].balance_deflator for rows in grouped.values())
    if len(result.balance_percentile(BAND_SPECS[0].percentile)) != len(deflators):
        return ()
    return tuple(
        ChartBand(
            label=spec.band_label,
            values=tuple(
                Money(value.amount / deflator).quantized().amount
                for value, deflator in zip(
                    result.balance_percentile(spec.percentile), deflators, strict=True
                )
            ),
        )
        for spec in BAND_SPECS
    )


def _balances_chart(
    grouped: dict[Period, list[PeriodReportRow]],
    suffix: str,
    bands: tuple[ChartBand, ...],
) -> ChartSpec:
    """Closing balance per wrapper per period, stacked to the total."""
    series = []
    labels = wrapper_display_labels(row for rows in grouped.values() for row in rows)
    for wrapper_id, label in labels.items():
        values = []
        for rows in grouped.values():
            total = _ZERO
            for row in rows:
                for entry in row.wrapper_balances:
                    if entry.wrapper_id == wrapper_id:
                        total = total + entry.closing_balance
            values.append(total.amount)
        series.append(ChartSeries(label=label, values=tuple(values)))
    return _chart(
        BALANCES_CHART_TITLE, f"Closing balance, £ ({suffix})", tuple(series), bands
    )


def _income_chart(
    grouped: dict[Period, list[PeriodReportRow]], suffix: str
) -> ChartSpec:
    """Income by source per period; sources never drawn on are dropped."""
    series = []
    for label, amount in _INCOME_SOURCES:
        values = tuple(_period_total(rows, amount) for rows in grouped.values())
        if any(value != 0 for value in values):
            series.append(ChartSeries(label=label, values=values))
    return _chart(INCOME_CHART_TITLE, f"Income, £ per period ({suffix})", tuple(series))


def _tax_chart(grouped: dict[Period, list[PeriodReportRow]], suffix: str) -> ChartSpec:
    """Tax due per period across the household."""
    values = tuple(
        _period_total(rows, lambda row: row.tax_due) for rows in grouped.values()
    )
    return _chart(
        TAX_CHART_TITLE,
        f"Tax due, £ per period ({suffix})",
        (ChartSeries(label=TAX_SERIES_LABEL, values=values),),
    )
