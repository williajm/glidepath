"""Historical backtesting over rolling windows (roadmap 9.18; planning §5.2).

:func:`run_windows` projects a plan once per rolling window of an
annual historical return series, as a complement to Monte Carlo:
rolling windows preserve the sequence-of-returns and regime behaviour
(crashes followed by recoveries, sustained inflation episodes) that
independent lognormal draws cannot reproduce, and they answer the
question "would this plan have survived starting in 1973?". Every
window is an ordinary deterministic :func:`~glidepath.core.engine.run`
— the same one step function, only the return model differs (the §5.2
design invariant) — so the whole backtest is reproducible with no
randomness involved.

The series carries **nominal** observed rates together with each
year's realised CPI; what a window replays is the year's **real**
return, recomposed with the run's assumed CPI
(:class:`HistoricalWindowModel`). Recomposition keeps the
single-inflation-truth rule intact — the engine's nominal ledger, its
tax-band dynamics, and the reporting deflators all follow the run's
one assumed CPI path, exactly as under Monte Carlo (where CPI is also
deterministic across paths) — and makes windows mutually comparable
in today's money. The accepted cost is shared with Monte Carlo:
nominal-anchored mechanics (frozen tax bands) interact with the
assumed CPI, not each window's historical inflation.

Success reporting mirrors the Monte Carlo metrics (planning §5.2):
success rate over windows, worst-window identification, and
ending-pot/balance percentiles, so the two surfaces can sit side by
side in the GUI.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from glidepath.core.config import EngineError, RunMode
from glidepath.core.engine import run
from glidepath.core.investments import AssetReturns
from glidepath.core.money import Money, Rate
from glidepath.core.montecarlo import (
    PathParallelism,
    _check_percentile,
    _interpolated_percentile,
    _merged_provenance,
    _path_chunks,
    _path_outcome,
)
from glidepath.core.provenance import AssumptionKey, decimal_assumption_value
from glidepath.core.returns import PeriodReturns, nominal_rate

if TYPE_CHECKING:
    from glidepath.core.config import RunConfig
    from glidepath.core.entities import Household
    from glidepath.core.periods import Period
    from glidepath.core.provenance import AssumptionSet, TrackedAssumptions
    from glidepath.core.region import Region
    from glidepath.core.results import RunProvenance
    from glidepath.core.returns import ReturnModelFactory

_ONE = Decimal(1)
_MINUS_ONE = Decimal(-1)


@dataclass(frozen=True, slots=True)
class HistoricalYear:
    """One year's observed nominal rates and realised CPI inflation.

    ``equity``, ``bonds`` and ``cash`` are nominal annual total
    returns (GBP terms for the shipped UK series); ``cpi`` is the
    year's realised CPI inflation. Real rates are derived, never
    stored (``(1 + nominal) / (1 + cpi) - 1``), so the shipped data
    file stays directly checkable against its sources.
    """

    year: int
    equity: Decimal
    bonds: Decimal
    cash: Decimal
    cpi: Decimal

    def __post_init__(self) -> None:
        """Reject rates at or below -100%.

        A -100% observation would zero a growth factor (or the price
        level) and cannot be recomposed into a real rate.
        """
        for name in ("equity", "bonds", "cash", "cpi"):
            if getattr(self, name) <= _MINUS_ONE:
                msg = f"HistoricalYear.{name} must be greater than -1"
                raise ValueError(msg)

    def real_rate(self, nominal: Decimal) -> Decimal:
        """Deflate a nominal rate by this year's realised CPI."""
        return (_ONE + nominal) / (_ONE + self.cpi) - _ONE


@dataclass(frozen=True, slots=True)
class HistoricalSeries:
    """A contiguous run of :class:`HistoricalYear` observations.

    The region ships one as a §5.3 data file
    (``returns_history.toml``) with ``verified_on`` + sources in its
    meta; the runner records the series it actually replayed on the
    :class:`BacktestResult`, so the manifest names the real input
    whatever series a caller supplies.
    """

    years: tuple[HistoricalYear, ...]

    def __post_init__(self) -> None:
        """Reject an empty or non-contiguous series."""
        if not self.years:
            msg = "HistoricalSeries needs at least one year"
            raise ValueError(msg)
        for previous, current in zip(self.years, self.years[1:], strict=False):
            if current.year != previous.year + 1:
                msg = (
                    "HistoricalSeries years must be contiguous and ascending:"
                    f" {previous.year} is followed by {current.year}"
                )
                raise ValueError(msg)

    @property
    def first_year(self) -> int:
        """The first observed calendar year."""
        return self.years[0].year

    @property
    def last_year(self) -> int:
        """The last observed calendar year."""
        return self.years[-1].year

    @property
    def length(self) -> int:
        """How many consecutive years the series observes."""
        return len(self.years)


@dataclass(frozen=True, slots=True)
class HistoricalWindowModel:
    """Return model replaying one rolling window of a historical series.

    The engine's period at ordinal *n* (counted from the period
    containing ``today``, whose start year is ``anchor_year``) reads
    the series observation at index ``window + n``: window 0 replays
    the series from its first year, window 1 from its second, and so
    on. Each observation's **real** rates are recomposed with the
    run's assumed CPI (module docstring), read through the tracked
    view so the CPI key lands in the run's provenance. Pure (planning
    §4.6): the returns depend only on the frozen series, the window
    offset, and the assumption view.
    """

    series: HistoricalSeries
    window: int
    anchor_year: int
    assumptions: TrackedAssumptions

    def __post_init__(self) -> None:
        """Reject a window offset outside the series."""
        if not 0 <= self.window < self.series.length:
            msg = (
                f"window must lie within the series (0 to"
                f" {self.series.length - 1}), got {self.window}"
            )
            raise ValueError(msg)

    def returns_for(self, period: Period, _path: int, /) -> PeriodReturns:
        """The window's returns for ``period``, recomposed with assumed CPI.

        Raises:
            EngineError: If the period falls before the run anchor or
                past the end of the series — the plan's horizon needs
                more years than the series holds beyond this window's
                start.
        """
        ordinal = period.start.year - self.anchor_year
        if ordinal < 0:
            msg = (
                f"period starting {period.start} precedes the run anchor"
                f" year {self.anchor_year}"
            )
            raise EngineError(msg)
        index = self.window + ordinal
        if index >= self.series.length:
            start = self.series.first_year + self.window
            msg = (
                f"the historical series ends in {self.series.last_year}:"
                f" a window starting in {start} cannot cover the run's"
                f" period at ordinal {ordinal} — the horizon needs more"
                " years than the series holds"
            )
            raise EngineError(msg)
        observed = self.series.years[index]
        cpi = decimal_assumption_value(
            self.assumptions.get(AssumptionKey.INFLATION_CPI)
        )
        equity, bonds, cash = (
            nominal_rate(observed.real_rate(nominal), cpi)
            for nominal in (observed.equity, observed.bonds, observed.cash)
        )
        return PeriodReturns(
            assets=AssetReturns(equity=equity, bonds=bonds, cash=cash),
            cpi=Rate(cpi),
        )


def historical_window_factory(
    series: HistoricalSeries, window: int, anchor_year: int
) -> ReturnModelFactory:
    """A return-model factory for one window — re-run any single window.

    The factory a :func:`run_windows` window *w* injects is exactly
    ``historical_window_factory(series, w, anchor_year)``, so any
    window is individually re-runnable through
    :func:`~glidepath.core.engine.run` (planning §4.6).
    """

    def factory(tracked: TrackedAssumptions) -> HistoricalWindowModel:
        """Build the window's model over the run's tracked view."""
        return HistoricalWindowModel(
            series=series, window=window, anchor_year=anchor_year, assumptions=tracked
        )

    return factory


@dataclass(frozen=True, slots=True)
class WindowOutcome:
    """One rolling window, reduced to its success signals.

    The mirror of :class:`~glidepath.core.montecarlo.PathOutcome`:
    ``start_year`` is the calendar year the window starts replaying
    (the "would this plan have survived starting in 1973?" year),
    ``first_shortfall_period`` the first projected period whose need
    went unmet (``None`` when the window succeeds), and
    ``closing_balances`` the household's nominal closing balance per
    period. Every window projects the same plan over the same
    periods, so shortfall periods and balances are directly
    comparable across windows.
    """

    window: int
    start_year: int
    first_shortfall_period: Period | None
    ending_balance: Money
    closing_balances: tuple[Money, ...] = ()

    def __post_init__(self) -> None:
        """Reject a negative window index or inconsistent balances."""
        if self.window < 0:
            msg = f"WindowOutcome.window must be non-negative, got {self.window}"
            raise ValueError(msg)
        if self.closing_balances and self.closing_balances[-1] != self.ending_balance:
            msg = "WindowOutcome.closing_balances must end at ending_balance"
            raise ValueError(msg)

    @property
    def ruined(self) -> bool:
        """Whether any period's need went unmet in this window."""
        return self.first_shortfall_period is not None


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Every window's outcome with the success metrics over them (§5.2).

    Mirrors :class:`~glidepath.core.montecarlo.MonteCarloResult` so
    the GUI can present the two side by side. ``config``,
    ``provenance``, and ``series`` are the §4.6 manifest side:
    :func:`run_windows` accepts any :class:`HistoricalSeries`, so the
    result must carry the series it actually replayed — two backtests
    over different series are otherwise indistinguishable from their
    manifests — and any window is re-runnable from this result plus
    the plan.
    """

    outcomes: tuple[WindowOutcome, ...]
    config: RunConfig
    provenance: RunProvenance
    series: HistoricalSeries

    def __post_init__(self) -> None:
        """Require at least one window."""
        if not self.outcomes:
            msg = "BacktestResult needs at least one window outcome"
            raise ValueError(msg)

    @property
    def window_count(self) -> int:
        """How many rolling windows were projected."""
        return len(self.outcomes)

    @property
    def failure_rate(self) -> Decimal:
        """The fraction of windows reporting a period with unmet need."""
        ruined = sum(1 for outcome in self.outcomes if outcome.ruined)
        return Decimal(ruined) / Decimal(self.window_count)

    @property
    def success_rate(self) -> Decimal:
        """The complement of :attr:`failure_rate`."""
        return _ONE - self.failure_rate

    @property
    def worst_window(self) -> WindowOutcome:
        """The worst starting year's outcome.

        A ruined window always ranks worse than a surviving one;
        among ruined windows the one falling short earliest is worst
        (every window projects the same periods, so shortfall dates
        compare directly), then the lower ending balance; among
        surviving windows, the lowest ending balance. Ties resolve to
        the earliest starting year, so the answer is deterministic.
        """

        def badness(outcome: WindowOutcome) -> tuple[int, date, Money, int]:
            shortfall = outcome.first_shortfall_period
            ruin_start = date.max if shortfall is None else shortfall.start
            return (
                0 if outcome.ruined else 1,
                ruin_start,
                outcome.ending_balance,
                outcome.start_year,
            )

        return min(self.outcomes, key=badness)

    def ending_pot_percentile(self, percentile: Decimal) -> Money:
        """The ending-balance percentile over windows, in [0, 100].

        The same order-statistic interpolation as the Monte Carlo
        metrics (planning §5.2), so the two surfaces read alike.

        Raises:
            ValueError: If ``percentile`` lies outside [0, 100].
        """
        return _interpolated_percentile(
            [outcome.ending_balance for outcome in self.outcomes], percentile
        )

    def balance_percentile(self, percentile: Decimal) -> tuple[Money, ...]:
        """The per-period closing-balance percentile over windows.

        One entry per projected period, in period order — the same
        order-statistic interpolation as the Monte Carlo bands; CPI is
        the run's one assumed path, so deflating each entry by the
        period's price level presents the band in today's money
        (module docstring). :func:`run_windows` outcomes always cover
        the same periods, but hand-built ones may not, so the
        alignment is validated like the Monte Carlo counterpart's.

        Raises:
            ValueError: If ``percentile`` lies outside [0, 100], or
                the outcomes carry differing period counts.
        """
        _check_percentile(percentile)
        lengths = {len(outcome.closing_balances) for outcome in self.outcomes}
        if len(lengths) != 1:
            msg = "balance_percentile needs every window to cover the same periods"
            raise ValueError(msg)
        return tuple(
            _interpolated_percentile(
                [outcome.closing_balances[index] for outcome in self.outcomes],
                percentile,
            )
            for index in range(lengths.pop())
        )


def run_windows(
    plan: Household,
    assumptions: AssumptionSet,
    region: Region,
    config: RunConfig,
    *,
    series: HistoricalSeries,
    parallelism: PathParallelism | None = None,
) -> BacktestResult:
    """Project ``plan`` over every rolling window of ``series`` (§5.2).

    Window *w* is ``run(plan, assumptions, region, config)`` with the
    return model :func:`historical_window_factory` builds for *w* —
    a deterministic projection whose per-period returns come from the
    series at offset *w*, mapped through the plan's own glide-path
    allocation by the one step function. The window count is however
    many complete horizons the series holds: a plan projecting *N*
    periods over an *M*-year series runs ``M - N + 1`` windows, the
    first starting at the series' first year. Results are
    reproducible: no randomness is involved, and the provenance is
    the union across windows in first-read order.

    With ``parallelism`` the remaining windows run as contiguous
    chunks on its executor after window 0 sizes the horizon; windows
    are pure functions of their offset, and chunk results recombine
    in window order, so the result is identical to the serial run.

    Raises:
        EngineError: If ``config.mode`` is not ``DETERMINISTIC`` (a
            backtest replays history; there is nothing to seed), the
            series is shorter than the plan's horizon, or any
            window's projection is rejected by the engine.
    """
    if config.mode is not RunMode.DETERMINISTIC:
        msg = "run_windows requires RunMode.DETERMINISTIC (planning §5.2)"
        raise EngineError(msg)
    anchor_year = region.calendar.period_containing(config.today).start.year
    first_outcomes, first_provenance = _run_window_range(
        plan, assumptions, region, config, series, anchor_year=anchor_year, chunk=(0, 1)
    )
    period_count = len(first_outcomes[0].closing_balances)
    window_count = series.length - period_count + 1
    remaining = window_count - 1
    if remaining == 0:
        outcomes = first_outcomes
        provenance = first_provenance
    elif parallelism is None or parallelism.workers == 1 or remaining == 1:
        rest, rest_provenance = _run_window_range(
            plan,
            assumptions,
            region,
            config,
            series,
            anchor_year=anchor_year,
            chunk=(1, window_count),
        )
        outcomes = first_outcomes + rest
        provenance = _merged_provenance([first_provenance, rest_provenance])
    else:
        futures = [
            parallelism.executor.submit(
                _run_window_range,
                plan,
                assumptions,
                region,
                config,
                series,
                anchor_year=anchor_year,
                chunk=(start + 1, stop + 1),
            )
            for start, stop in _path_chunks(remaining, parallelism.workers)
        ]
        chunk_results = [future.result() for future in futures]
        outcomes = first_outcomes + tuple(
            outcome for chunk_outcomes, _ in chunk_results for outcome in chunk_outcomes
        )
        provenance = _merged_provenance(
            [first_provenance]
            + [chunk_provenance for _, chunk_provenance in chunk_results]
        )
    return BacktestResult(
        outcomes=outcomes, config=config, provenance=provenance, series=series
    )


def _run_window_range(
    plan: Household,
    assumptions: AssumptionSet,
    region: Region,
    config: RunConfig,
    series: HistoricalSeries,
    *,
    anchor_year: int,
    chunk: tuple[int, int],
) -> tuple[tuple[WindowOutcome, ...], RunProvenance]:
    """Project the ``(start, stop)`` half-open ``chunk`` of window offsets.

    The unit of work one executor worker runs — a module-level
    function over picklable arguments returning only reduced outcomes
    and the chunk's provenance union, the exact shape of the Monte
    Carlo runner's chunk worker (planning §5.2).
    """
    start, stop = chunk
    outcomes: list[WindowOutcome] = []
    provenances: list[RunProvenance] = []
    for window in range(start, stop):
        factory = historical_window_factory(series, window, anchor_year)
        result = run(plan, assumptions, region, config, return_model_factory=factory)
        reduced = _path_outcome(window, result)
        outcomes.append(
            WindowOutcome(
                window=window,
                start_year=series.first_year + window,
                first_shortfall_period=reduced.first_shortfall_period,
                ending_balance=reduced.ending_balance,
                closing_balances=reduced.closing_balances,
            )
        )
        provenances.append(result.provenance)
    return tuple(outcomes), _merged_provenance(provenances)
