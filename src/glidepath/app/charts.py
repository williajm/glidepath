"""Projection chart view models (roadmap 8.4; planning §5.2, §4.7).

Chart series for the three projection surfaces — wrapper balances,
income composition, and tax over the horizon — presented through the
core reporting layer (roadmap 4.4) in either money basis, plus the
Monte Carlo fan chart a held run adds under the Monte Carlo mode
(roadmap 9.24). **Real (today's money) is the default presentation**;
the nominal toggle re-presents the same ledger, never a second
inflation source (planning §5.2). Amounts stay ``Decimal`` here:
converting them to plot coordinates is shell mechanics (§4.7).
"""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from glidepath.app.backtest import (
    BacktestPanelViewModel,
    build_backtest_panel,
    selected_window,
)
from glidepath.app.display import format_money, format_share, format_wrapper_kind
from glidepath.app.drawdown import (
    DrawdownPanelViewModel,
    build_drawdown_panel,
)
from glidepath.app.montecarlo import (
    DEFAULT_RUN_MODE,
    FAN_MEDIAN_LABEL,
    FAN_SPECS,
    MonteCarloPanelViewModel,
    build_monte_carlo_panel,
)
from glidepath.app.retirement import (
    RetirementPanelViewModel,
    build_retirement_panel,
)
from glidepath.core import (
    AssumptionKey,
    Money,
    Provenance,
    ReportBasis,
    RunMode,
    build_report,
    mapping_assumption_value,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from glidepath.app.plan import PlanState
    from glidepath.core import (
        AssetAllocation,
        BacktestResult,
        EntityId,
        MonteCarloResult,
        Period,
        PeriodReportRow,
        ProjectionReport,
        WindowOutcome,
    )

_ZERO = Money(Decimal(0))
_MIN_AXIS_MAX = Decimal(1)
_MEDIAN_PERCENTILE = Decimal(50)

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

MONTE_CARLO_CHART_TITLE: Final = "Monte Carlo"

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
    """One overlay line over the categories (roadmap 9.13, 9.18)."""

    label: str
    values: tuple[Decimal, ...]


@dataclass(frozen=True)
class ChartFill:
    """One filled band between two per-category value runs (9.24).

    The Monte Carlo fan's building block: ``lower`` and ``upper``
    bound one inter-percentile region, one value per period category
    each. Shells fill between them; the tooltip copy comes from
    :func:`fill_tooltip` over these exact ``Decimal`` amounts.
    """

    label: str
    lower: tuple[Decimal, ...]
    upper: tuple[Decimal, ...]


@dataclass(frozen=True)
class ChartSpec:
    """One chart, ready for a shell to bind to a plotting widget.

    ``bands`` are overlay lines over any stacked bars — backtest
    trajectories on the balances chart (roadmap 9.18), the median on
    the Monte Carlo fan chart (9.24). ``fills`` are the fan chart's
    nested inter-percentile regions, outermost first — empty on every
    other chart. ``y_axis_max`` is the largest stacked total, band, or
    fill value across the periods (never below 1, so an all-zero chart
    still renders a visible axis); every value here is non-negative,
    so the y range is always ``[0, y_axis_max]``.
    """

    title: str
    y_axis_label: str
    y_axis_max: Decimal
    series: tuple[ChartSeries, ...]
    bands: tuple[ChartBand, ...] = ()
    fills: tuple[ChartFill, ...] = ()


@dataclass(frozen=True)
class ChartsViewModel:
    """The projection charts screen (roadmap 8.4).

    ``categories`` labels the shared x axis — one label per projected
    period: the period-start year with the person's age at period
    start alongside (roadmap 9.11; year alone until couples activate,
    9.4 — a two-person period has no single age to show). ``message``
    carries the empty-state copy when there is nothing to chart; it
    is blank whenever ``charts`` is populated. ``allocation_note``
    states the asset allocation each wrapper actually ran — stated
    equity split, pinned cash, or the glide path with its provenance —
    so a projection can never silently model a mix the user does not
    hold.
    """

    basis_heading: str
    basis_options: tuple[ChartBasisOption, ...]
    selected_basis_key: str
    categories: tuple[str, ...]
    charts: tuple[ChartSpec, ...]
    message: str
    allocation_note: str
    monte_carlo: MonteCarloPanelViewModel
    retirement: RetirementPanelViewModel
    drawdown: DrawdownPanelViewModel
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
    """The hover copy for one bar segment or overlay-line point (§4.7).

    Shells bind this to their plotting widget's hover affordance, so
    the copy — series or line label, period, and the amount in pounds
    and pence — stays app-layer like every other label on the chart
    (roadmap 9.23 extends it from the bar segments to the overlay
    lines).
    """
    return f"{series_label}\n{category}: {format_money(Money(value))}"


def fill_tooltip(category: str, label: str, lower: Decimal, upper: Decimal) -> str:
    """The hover copy for one fan fill at one period (9.23, 9.24).

    The fill's interval statement made concrete for the hovered
    period: its label with the low and high amounts, from the same
    exact ``Decimal`` values the fill draws.
    """
    low = format_money(Money(lower))
    high = format_money(Money(upper))
    return f"{label}\n{category}: {low} to {high}"


def build_charts_view_model(
    state: PlanState,
    basis: ReportBasis = DEFAULT_CHART_BASIS,
    mode: RunMode = DEFAULT_RUN_MODE,
    *,
    backtest_year: str = "",
) -> ChartsViewModel:
    """The charts screen for ``state``, presented in ``basis``.

    Real (today's money) is the default; the deflators come from the
    run's own CPI path via the core reporting layer (planning §5.2).
    ``mode`` is the screen's run-mode selection (roadmap 9.13): under
    ``MONTE_CARLO`` a held Monte Carlo run adds its fan chart as a
    fourth chart (9.24) and its metrics to the panel.
    ``backtest_year`` is the backtest card's starting-year picker as
    raw text (presentation state the shell holds, like the basis and
    mode): with a held backtest it adds that starting year's actual
    trajectory to the balances chart alongside the worst and best
    ones. Without a projection the screen carries only the
    empty-state message — the no-run copy, or the run failure held on
    the state.
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
            allocation_note="",
            monte_carlo=build_monte_carlo_panel(
                state, mode, ending_pot_deflator=None, basis_suffix=suffix
            ),
            retirement=build_retirement_panel(state, mode),
            drawdown=build_drawdown_panel(state, mode),
            backtest=build_backtest_panel(
                state,
                ending_pot_deflator=None,
                basis_suffix=suffix,
                year_text=backtest_year,
            ),
        )
    grouped = _rows_by_period(build_report(state.result, basis))
    bands = _chart_bands(state, grouped, backtest_year)
    final_rows = next(reversed(grouped.values()))
    charts = [
        _balances_chart(grouped, suffix, bands),
        _income_chart(grouped, suffix),
        _tax_chart(grouped, suffix),
    ]
    if mode is RunMode.MONTE_CARLO and state.monte_carlo is not None:
        fan = _fan_chart(state.monte_carlo, grouped, suffix)
        if fan is not None:
            charts.append(fan)
    return ChartsViewModel(
        basis_heading=BASIS_HEADING,
        basis_options=options,
        selected_basis_key=selected_key,
        categories=tuple(
            _category_label(period, rows) for period, rows in grouped.items()
        ),
        charts=tuple(charts),
        message="",
        allocation_note=_allocation_note(state, grouped),
        monte_carlo=build_monte_carlo_panel(
            state,
            mode,
            ending_pot_deflator=final_rows[0].balance_deflator,
            basis_suffix=suffix,
        ),
        retirement=build_retirement_panel(state, mode),
        drawdown=build_drawdown_panel(state, mode),
        backtest=build_backtest_panel(
            state,
            ending_pot_deflator=final_rows[0].balance_deflator,
            basis_suffix=suffix,
            year_text=backtest_year,
        ),
    )


def _chart_bands(
    state: PlanState,
    grouped: dict[Period, list[PeriodReportRow]],
    backtest_year: str,
) -> tuple[ChartBand, ...]:
    """The overlay lines the balances chart draws, if any.

    A held backtest supplies actual window trajectories in either run
    mode (roadmap 9.18). A held Monte Carlo run no longer overlays
    the balances chart — its percentiles moved to the fan chart's own
    tab (9.24) so neither surface crowds the other.
    """
    if state.backtest is not None:
        return _backtest_trajectories(state.backtest, grouped, backtest_year)
    return ()


def _glide_note(state: PlanState) -> str:
    """The glide path summarised with its provenance (planning §5.1).

    The flat-shape check compares the shape's raw values — numeric
    equality, so an overridden ``1`` and ``1.0`` are the same
    allocation whatever their text.
    """
    assumption = state.assumptions.get(AssumptionKey.GLIDEPATH_DEFAULT_SHAPE)
    shape = mapping_assumption_value(assumption)
    start = shape["equity_start"]
    at_retirement = shape["equity_at_retirement"]
    years = shape["derisk_years_before_retirement"]
    provenance = (
        "shipped default"
        if assumption.provenance is Provenance.DEFAULT_ASSUMPTION
        else "your override"
    )
    if start == at_retirement:
        return (
            f"glide path — {format_share(Decimal(str(start)))} equity"
            f" throughout ({provenance})"
        )
    return (
        f"glide path — {format_share(Decimal(str(start)))} equity de-risking"
        f" to {format_share(Decimal(str(at_retirement)))} over the {years}"
        f" years before retirement ({provenance})"
    )


def _allocation_text(allocation: AssetAllocation) -> str:
    """One stated allocation as copy: ``70% equity, 30% bonds``.

    Zero slices are dropped — the weights sum to one, so at least one
    always remains.
    """
    slices = (
        (allocation.equity, "equity"),
        (allocation.bonds, "bonds"),
        (allocation.cash, "cash"),
    )
    return ", ".join(
        f"{format_share(value)} {name}" for value, name in slices if value != Decimal(0)
    )


def _allocation_note(
    state: PlanState, grouped: dict[Period, list[PeriodReportRow]]
) -> str:
    """What each wrapper is invested in, as one status line (§5.1).

    Every projection surface — deterministic, Monte Carlo, backtest —
    runs this allocation, so the line sits with the charts it
    explains. A stated equity split reads ``(stated)``; a cash
    account's pinned allocation is a rule and carries no suffix; a
    wrapper with nothing stated names the glide path and whether it
    is the shipped default or an override. A household holding no
    wrappers at all (DB and state pension income only) has nothing to
    invest, so the note is empty rather than a dangling prefix.
    """
    household = state.household
    if household is None:
        return ""
    labels = wrapper_display_labels(row for rows in grouped.values() for row in rows)
    glide = _glide_note(state)
    parts = []
    for person in household.persons:
        for wrapper in person.wrappers:
            label = labels.get(wrapper.id, format_wrapper_kind(wrapper.kind))
            allocation = wrapper.allocation
            if allocation is None:
                parts.append(f"{label}: {glide}")
            elif allocation.cash == Decimal(1):
                parts.append(f"{label}: {_allocation_text(allocation)}")
            else:
                parts.append(f"{label}: {_allocation_text(allocation)} (stated)")
    if not parts:
        return ""
    return "Invested as — " + "; ".join(parts)


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
    fills: tuple[ChartFill, ...] = (),
) -> ChartSpec:
    """A chart spec with its y axis covering stack, bands, and fills."""
    stacked: dict[int, Decimal] = {}
    for entry in series:
        for index, value in enumerate(entry.values):
            stacked[index] = stacked.get(index, Decimal(0)) + value
    band_values = [value for band in bands for value in band.values]
    fill_values = [value for fill in fills for value in fill.upper]
    return ChartSpec(
        title=title,
        y_axis_label=y_axis_label,
        y_axis_max=max([*stacked.values(), *band_values, *fill_values, _MIN_AXIS_MAX]),
        series=series,
        bands=bands,
        fills=fills,
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


def _deflated(
    values: tuple[Money, ...], deflators: tuple[Decimal, ...]
) -> tuple[Decimal, ...]:
    """Nominal per-period amounts presented by the report's deflators.

    CPI is deterministic across Monte Carlo paths and backtest windows
    alike (planning §5.2), so each period's nominal amount deflates by
    the same balance deflator the deterministic report rows carry — 1
    under the nominal basis.
    """
    return tuple(
        Money(value.amount / deflator).quantized().amount
        for value, deflator in zip(values, deflators, strict=True)
    )


def _fan_chart(
    result: MonteCarloResult,
    grouped: dict[Period, list[PeriodReportRow]],
    suffix: str,
) -> ChartSpec | None:
    """The Monte Carlo fan chart, or ``None`` if it cannot align (9.24).

    Nested inter-percentile fills (outermost first, per ``FAN_SPECS``)
    with the median as the single overlay line — each fill a genuine
    interval statement over the paths' per-period closing balances,
    deflated like every Monte Carlo amount (:func:`_deflated`). All
    nine percentiles come from one ``balance_percentiles`` batch — the
    view model is built on the GUI thread, and per-percentile calls
    would re-sort a 10,000-path result nine times over. A held result
    whose period count differs from the projection's (the runs
    straddled a calendar day) draws no fan rather than a fan against
    the wrong periods.
    """
    deflators = tuple(rows[0].balance_deflator for rows in grouped.values())
    count = len(FAN_SPECS)
    requested = (
        *(spec.lower for spec in FAN_SPECS),
        *(spec.upper for spec in FAN_SPECS),
        _MEDIAN_PERCENTILE,
    )
    percentiles = result.balance_percentiles(requested)
    if len(percentiles[0]) != len(deflators):
        return None
    fills = tuple(
        ChartFill(
            label=spec.label,
            lower=_deflated(lower, deflators),
            upper=_deflated(upper, deflators),
        )
        for spec, lower, upper in zip(
            FAN_SPECS, percentiles[:count], percentiles[count : 2 * count], strict=True
        )
    )
    median = ChartBand(
        label=FAN_MEDIAN_LABEL,
        values=_deflated(percentiles[-1], deflators),
    )
    return _chart(
        MONTE_CARLO_CHART_TITLE,
        f"Closing balance, £ ({suffix})",
        (),
        bands=(median,),
        fills=fills,
    )


def _trajectory_band(
    outcome: WindowOutcome, label_prefix: str, deflators: tuple[Decimal, ...]
) -> ChartBand:
    """One window's actual balance path as a chart line (9.18).

    Labelled with its starting year (`Worst start · 1907`), so the
    legend names the history being replayed; the nominal balances
    deflate by the report rows' own deflators like every band.
    """
    return ChartBand(
        label=f"{label_prefix} · {outcome.start_year}",
        values=_deflated(outcome.closing_balances, deflators),
    )


def _backtest_trajectories(
    result: BacktestResult,
    grouped: dict[Period, list[PeriodReportRow]],
    year_text: str,
) -> tuple[ChartBand, ...]:
    """The worst, best, and picked starting years' actual paths (9.18).

    Unlike the Monte Carlo percentile bands — pointwise order
    statistics that follow no single path — each backtest line is one
    starting year's real trajectory: the worst and best windows
    always, plus whichever starting year the picker's raw text names
    (:func:`~glidepath.app.backtest.selected_window`; a miss draws
    nothing and the card says why). Any drawn outcome whose period
    count differs from the projection's (the runs straddled a
    calendar day, or a hand-built result misaligned its windows)
    draws no lines rather than lines against the wrong periods.
    """
    deflators = tuple(rows[0].balance_deflator for rows in grouped.values())
    picked, _ = selected_window(result, year_text)
    lines = [
        (result.worst_window, "Worst start"),
        (result.best_window, "Best start"),
    ]
    if picked is not None:
        lines.append((picked, "Start"))
    if any(len(outcome.closing_balances) != len(deflators) for outcome, _ in lines):
        return ()
    return tuple(
        _trajectory_band(outcome, prefix, deflators) for outcome, prefix in lines
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
