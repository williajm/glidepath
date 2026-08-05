"""Monte Carlo path runner and success metrics (roadmap 7.3; planning §5.2).

:func:`run_paths` projects a plan N times through the one engine step
function — path *i* is an ordinary :func:`~glidepath.core.engine.run`
under ``replace(config, path=i)``, so its randomness is exactly
determined by ``(config.seed, i)`` and any single path is individually
re-runnable (planning §4.6). Each path reduces to a
:class:`PathOutcome` — the ruin signal, the household's closing
balance per period (what the percentile bands chart, roadmap 9.13),
and the ending balance; the full period ledgers are dropped, so a
many-path run holds one projection's snapshots at a time.

Success metrics over the outcomes (planning §5.2):

- **probability of ruin** — the fraction of paths reporting a period
  with unmet need. The engine's per-period ``shortfall`` is the ruin
  signal by design: it survives gross-defined strategies that ignore
  the need (planning §5.2), and it covers planned outflows as well as
  decumulation spending.
- **ending-pot percentiles** — order statistics of the paths' final
  nominal balances, linearly interpolated. CPI is deterministic across
  paths (the single-inflation-truth rule), so nominal and real
  percentiles rank paths identically; deflate by the final period's
  inflation factor for today's money.
- **sustainable income** (:func:`sustainable_income`) — the highest
  starting net withdrawal meeting a target success rate
  (:class:`SustainableIncomeSearch`): a descending scan over the
  bracket finds the highest succeeding scan point, then bisection
  refines upward within the scan cell above it. For a strategy whose
  success is monotone in the spending level — the default fixed-real
  strategy, and the gross-defined strategies whose draws ignore the
  need — the result is exact to the search tolerance. A strategy with
  adjustment triggers (guardrails, roadmap 5.3) can make success
  non-monotone: there the result is still never below the highest
  succeeding scan point, but a success island narrower than one scan
  step can be missed — raise ``scan_steps`` to tighten the
  resolution. Every probe reuses the same seed (common random
  numbers), so candidates differ only by the spending level and the
  search is reproducible. Probe plans exist only inside the search:
  the synthetic spending "fact" they carry is never part of any
  returned result.
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from glidepath.core.config import EngineError, RunMode
from glidepath.core.engine import run
from glidepath.core.entities import SpendingPlan
from glidepath.core.money import Money
from glidepath.core.provenance import Fact

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from concurrent.futures import Executor

    from glidepath.core.config import RunConfig
    from glidepath.core.entities import Household
    from glidepath.core.periods import Period
    from glidepath.core.provenance import Assumption, AssumptionKey, AssumptionSet
    from glidepath.core.region import Region
    from glidepath.core.results import ProjectionResult, RunProvenance

_ZERO = Money(Decimal(0))
_ONE = Decimal(1)
_TWO = Decimal(2)
_HUNDRED = Decimal(100)
_DEFAULT_TOLERANCE = Money(Decimal(100))
"""Default bisection tolerance: £100 of annual spending (planning §5.2)."""


@dataclass(frozen=True, slots=True)
class PathOutcome:
    """One Monte Carlo path, reduced to its success signals.

    ``first_shortfall_period`` is the first period whose need went
    unmet — ``None`` on a path that never fell short.
    ``closing_balances`` is the household's total closing balance per
    period in period order, nominal — what the balances chart's
    percentile bands read (roadmap 9.13) — and ``ending_balance`` is
    its final entry, kept as its own field for the ending-pot metrics.
    Re-run the path itself with ``run(plan, assumptions, region,
    replace(config, path=path))``.
    """

    path: int
    first_shortfall_period: Period | None
    ending_balance: Money
    closing_balances: tuple[Money, ...] = ()

    def __post_init__(self) -> None:
        """Reject a negative path index or inconsistent balances."""
        if self.path < 0:
            msg = f"PathOutcome.path must be non-negative, got {self.path}"
            raise ValueError(msg)
        if self.ending_balance < _ZERO:
            msg = "PathOutcome.ending_balance must be non-negative"
            raise ValueError(msg)
        if self.closing_balances and self.closing_balances[-1] != self.ending_balance:
            msg = "PathOutcome.closing_balances must end at ending_balance"
            raise ValueError(msg)

    @property
    def ruined(self) -> bool:
        """Whether any period's need went unmet on this path."""
        return self.first_shortfall_period is not None


@dataclass(frozen=True, slots=True)
class MonteCarloResult:
    """N paths' outcomes with the success metrics over them (§5.2).

    ``config`` and ``provenance`` are the §4.6 manifest side: the
    seed, mode, and every assumption any path read — the union across
    paths, since a balance-dependent read (natural-yield pricing,
    roadmap 5.3) can fire on some paths only — so the whole run is
    reproducible from this result plus the plan.
    """

    outcomes: tuple[PathOutcome, ...]
    config: RunConfig
    provenance: RunProvenance

    def __post_init__(self) -> None:
        """Require at least one path."""
        if not self.outcomes:
            msg = "MonteCarloResult needs at least one path outcome"
            raise ValueError(msg)

    @property
    def path_count(self) -> int:
        """How many paths were projected."""
        return len(self.outcomes)

    @property
    def probability_of_ruin(self) -> Decimal:
        """The fraction of paths reporting a period with unmet need."""
        ruined = sum(1 for outcome in self.outcomes if outcome.ruined)
        return Decimal(ruined) / Decimal(self.path_count)

    @property
    def success_rate(self) -> Decimal:
        """The complement of :attr:`probability_of_ruin`."""
        return _ONE - self.probability_of_ruin

    def ending_pot_percentile(self, percentile: Decimal) -> Money:
        """The ending-balance percentile over paths, in [0, 100].

        Linear interpolation between order statistics: rank
        ``(count - 1) * percentile / 100`` over the sorted balances,
        fractional ranks interpolating between the two neighbours —
        exact ``Decimal`` arithmetic, quantized as a presentation
        value (planning §4.6).

        Raises:
            ValueError: If ``percentile`` lies outside [0, 100].
        """
        return _interpolated_percentile(
            [outcome.ending_balance for outcome in self.outcomes], percentile
        )

    def balance_percentile(self, percentile: Decimal) -> tuple[Money, ...]:
        """The per-period closing-balance percentile over paths (9.13).

        One entry per projected period, in period order — the same
        order-statistic interpolation as
        :meth:`ending_pot_percentile`, applied period by period over
        the paths' nominal household balances. CPI is deterministic
        across paths (module docstring), so deflating each entry by
        the period's price level presents the band in today's money.

        Raises:
            ValueError: If ``percentile`` lies outside [0, 100], or
                the outcomes carry differing period counts.
        """
        _check_percentile(percentile)
        lengths = {len(outcome.closing_balances) for outcome in self.outcomes}
        if len(lengths) != 1:
            msg = "balance_percentile needs every path to cover the same periods"
            raise ValueError(msg)
        return tuple(
            _interpolated_percentile(
                [outcome.closing_balances[index] for outcome in self.outcomes],
                percentile,
            )
            for index in range(lengths.pop())
        )


@dataclass(frozen=True, slots=True)
class PathParallelism:
    """How :func:`run_paths` spreads its paths over workers (§5.2).

    ``executor`` runs path chunks — a ``ProcessPoolExecutor`` for real
    speedup (the engine is CPU-bound ``Decimal`` work), or any executor
    in tests. ``workers`` is the executor's worker count: paths are
    split into that many contiguous chunks, so every submitted argument
    tuple (plan, assumptions, region, config) is pickled once per
    worker, not once per path. Paths are pure and order-independent
    (planning §4.6), and chunk results are recombined in path order, so
    the result is identical to a serial run whatever the executor.

    The caller owns the executor's lifecycle: reuse one across the many
    :func:`run_paths` calls of a retirement-age search rather than
    paying process startup per candidate.
    """

    executor: Executor
    workers: int

    def __post_init__(self) -> None:
        """Reject a worker count below one."""
        if self.workers < 1:
            msg = f"workers must be positive, got {self.workers}"
            raise ValueError(msg)


def run_paths(
    plan: Household,
    assumptions: AssumptionSet,
    region: Region,
    config: RunConfig,
    *,
    paths: int,
    parallelism: PathParallelism | None = None,
) -> MonteCarloResult:
    """Project ``plan`` over ``paths`` seeded paths (planning §5.2).

    Path *i* is ``run(plan, assumptions, region, replace(config,
    path=i))`` — always paths 0 through ``paths - 1``, whatever
    ``config.path`` says: paths are order-independent and individually
    re-runnable from the seed alone (planning §4.6). The provenance is
    the union across paths in first-read order (class docstring).

    With ``parallelism`` the paths run as contiguous chunks on its
    executor; the result is identical to the serial run — same
    outcomes in path order, same provenance union — because every path
    is exactly determined by ``(config.seed, i)`` (§4.6).

    Raises:
        EngineError: If ``paths`` is not positive, ``config.mode`` is
            not ``MONTE_CARLO``, the config carries no seed, or any
            path's projection is rejected by the engine.
    """
    if paths < 1:
        msg = f"paths must be positive, got {paths}"
        raise EngineError(msg)
    if config.mode is not RunMode.MONTE_CARLO:
        msg = "run_paths requires RunMode.MONTE_CARLO (planning §5.2)"
        raise EngineError(msg)
    if parallelism is None or parallelism.workers == 1 or paths == 1:
        outcomes, provenance = _run_path_range(
            plan, assumptions, region, config, (0, paths)
        )
    else:
        futures = [
            parallelism.executor.submit(
                _run_path_range, plan, assumptions, region, config, chunk
            )
            for chunk in _path_chunks(paths, parallelism.workers)
        ]
        chunk_results = [future.result() for future in futures]
        outcomes = tuple(
            outcome for chunk_outcomes, _ in chunk_results for outcome in chunk_outcomes
        )
        provenance = _merged_provenance(
            [chunk_provenance for _, chunk_provenance in chunk_results]
        )
    return MonteCarloResult(outcomes=outcomes, config=config, provenance=provenance)


def _run_path_range(
    plan: Household,
    assumptions: AssumptionSet,
    region: Region,
    config: RunConfig,
    chunk: tuple[int, int],
) -> tuple[tuple[PathOutcome, ...], RunProvenance]:
    """Project the ``(start, stop)`` half-open ``chunk`` of path indices.

    The unit of work one executor worker runs: a module-level function
    over picklable arguments, returning only the reduced outcomes and
    the chunk's provenance union — never the paths' full ledgers, so a
    parallel run ships back kilobytes per chunk, not the projections.
    Merging the chunk unions in chunk order reproduces the serial
    provenance exactly: the union operation is associative over ordered
    concatenation, and this chunk's merged base is its first path's.
    """
    start, stop = chunk
    outcomes: list[PathOutcome] = []
    provenances: list[RunProvenance] = []
    for index in range(start, stop):
        result = run(plan, assumptions, region, _path_config(config, index))
        outcomes.append(_path_outcome(index, result))
        provenances.append(result.provenance)
    return tuple(outcomes), _merged_provenance(provenances)


def _path_chunks(paths: int, workers: int) -> Iterator[tuple[int, int]]:
    """Split ``range(paths)`` into up to ``workers`` contiguous chunks.

    Chunk sizes differ by at most one, larger chunks first — a
    deterministic tiling, so the parallel path order never depends on
    scheduling.
    """
    chunk_count = min(workers, paths)
    size, extra = divmod(paths, chunk_count)
    start = 0
    for index in range(chunk_count):
        stop = start + size + (1 if index < extra else 0)
        yield start, stop
        start = stop


@dataclass(frozen=True, slots=True)
class SustainableIncomeSearch:
    """The parameters of one sustainable-income search (planning §5.2).

    ``paths`` seeded paths are projected per candidate spending level;
    a candidate meets the target when at least ``target_success_rate``
    of them avoid ruin. The search covers ``[0, maximum]`` of real
    annual spending: a descending scan over ``scan_steps`` equal steps
    finds the highest succeeding scan point, then bisection refines
    upward within the step above it, stopping once the bracket narrows
    to ``tolerance``. The scan is what makes the search robust to a
    non-monotone success predicate (module docstring): success islands
    narrower than ``maximum / scan_steps`` can still be missed, so
    raise ``scan_steps`` when the withdrawal strategy has adjustment
    triggers; ``scan_steps=1`` is a pure bisection. Probe count is at
    most ``1 + scan_steps`` plus the bisection's
    ``log2(step / tolerance)``.
    """

    paths: int
    target_success_rate: Decimal
    maximum: Money
    tolerance: Money = _DEFAULT_TOLERANCE
    scan_steps: int = 10

    def __post_init__(self) -> None:
        """Reject an empty path count, an off-range target, or bad bounds."""
        if self.paths < 1:
            msg = f"paths must be positive, got {self.paths}"
            raise ValueError(msg)
        if not Decimal(0) < self.target_success_rate <= _ONE:
            msg = (
                "target_success_rate must lie in (0, 1],"
                f" got {self.target_success_rate}"
            )
            raise ValueError(msg)
        if self.maximum <= _ZERO:
            msg = "maximum must be positive"
            raise ValueError(msg)
        if self.tolerance <= _ZERO:
            msg = "tolerance must be positive"
            raise ValueError(msg)
        if self.scan_steps < 1:
            msg = f"scan_steps must be positive, got {self.scan_steps}"
            raise ValueError(msg)


def sustainable_income(
    plan: Household,
    assumptions: AssumptionSet,
    region: Region,
    config: RunConfig,
    search: SustainableIncomeSearch,
    *,
    parallelism: PathParallelism | None = None,
) -> Money | None:
    """The highest starting withdrawal meeting the target (scan + bisect).

    Searches the spending plan's real annual amount (today's money —
    the "starting withdrawal": the engine escalates it by the run's
    CPI path) over the search's ``[0, maximum]``: a descending scan
    over the search's ``scan_steps`` finds the highest succeeding scan
    point, then bisection refines upward within the step above it.
    Returns the highest probed level that met the target — ``maximum``
    itself when even that meets — or ``None`` when no scan point and
    not even zero spending does (the plan's outflows already exhaust
    it). Every returned value was actually probed, never interpolated.

    For a monotone success predicate (the default fixed-real strategy)
    the result is within the search's tolerance of the true boundary;
    for adjustment-trigger strategies it is never below the highest
    succeeding scan point, with the resolution caveat on
    :class:`SustainableIncomeSearch`.

    The plan's stated spending amount is irrelevant to the search —
    only its stage multipliers and the rest of the plan carry over; a
    plan with no spending plan is probed with a bare one. Probes reuse
    ``config`` unchanged (same seed: common random numbers), keeping
    the §4.6 reproducibility guarantee over the whole search.
    ``parallelism`` spreads each probe's paths over its executor
    (:class:`PathParallelism` — results identical to a serial search);
    pass one executor for the whole search so probes share it.

    Raises:
        EngineError: If ``config`` is not a seeded ``MONTE_CARLO``
            config, or a probe is rejected by the engine.
    """

    def meets(amount: Money) -> bool:
        """Whether spending ``amount`` meets the target success rate."""
        probe = probe_with_spending(plan, amount, config)
        result = run_paths(
            probe,
            assumptions,
            region,
            config,
            paths=search.paths,
            parallelism=parallelism,
        )
        return result.success_rate >= search.target_success_rate

    high = search.maximum
    if meets(high):
        return high
    low: Money | None = None
    step = search.maximum.amount / Decimal(search.scan_steps)
    for index in range(search.scan_steps - 1, 0, -1):
        candidate = Money(step * Decimal(index))
        if meets(candidate):
            low = candidate
            break
        high = candidate
    if low is None:
        low = _ZERO
        if not meets(low):
            return None
    while high - low > search.tolerance:
        midpoint = Money((low.amount + high.amount) / _TWO)
        if meets(midpoint):
            low = midpoint
        else:
            high = midpoint
    return low


def _check_percentile(percentile: Decimal) -> None:
    """Reject a percentile outside [0, 100].

    Raises:
        ValueError: If ``percentile`` lies outside [0, 100].
    """
    if not Decimal(0) <= percentile <= _HUNDRED:
        msg = f"percentile must lie between 0 and 100, got {percentile}"
        raise ValueError(msg)


def _interpolated_percentile(balances: Sequence[Money], percentile: Decimal) -> Money:
    """The ``percentile`` of ``balances`` between order statistics.

    Rank ``(count - 1) * percentile / 100`` over the sorted balances,
    fractional ranks interpolating between the two neighbours — exact
    ``Decimal`` arithmetic, quantized as a presentation value
    (planning §4.6).

    Raises:
        ValueError: If ``percentile`` lies outside [0, 100].
    """
    _check_percentile(percentile)
    ordered = sorted(balances)
    rank = (Decimal(len(ordered)) - _ONE) * percentile / _HUNDRED
    lower = int(rank)
    fraction = rank - Decimal(lower)
    value = ordered[lower]
    if fraction > 0:
        value = value + (ordered[lower + 1] - ordered[lower]) * fraction
    return value.quantized()


def _merged_provenance(provenances: Sequence[RunProvenance]) -> RunProvenance:
    """Path 0's provenance with the assumption union of every path.

    Almost every assumption read is plan- or date-driven and identical
    across paths, but a balance-dependent read — natural-yield pricing
    fires only while a source still holds money (roadmap 5.3) — can
    appear on some paths only. The union keeps the §4.6 manifest
    exhaustive: path 0's first-read order, then any later-path-only
    keys in path order. Facts, decisions, the region data version, and
    the seed are path-independent, so path 0's stand for all.
    """
    merged: dict[AssumptionKey, Assumption[Any]] = {}
    for provenance in provenances:
        for assumption in provenance.assumptions:
            merged.setdefault(assumption.key, assumption)
    changes: dict[str, Any] = {"assumptions": tuple(merged.values())}
    return replace(provenances[0], **changes) if changes else provenances[0]


def _path_config(config: RunConfig, index: int) -> RunConfig:
    """The run configuration for one path: ``config`` at ``path=index``."""
    changes: dict[str, Any] = {"path": index}
    return replace(config, **changes) if changes else config


def _path_outcome(index: int, result: ProjectionResult) -> PathOutcome:
    """Reduce one path's projection to its success signals."""
    first_shortfall = None
    balances = []
    for snapshot in result.snapshots:
        shortfall = _ZERO
        closing = _ZERO
        for person in snapshot.persons:
            shortfall = shortfall + person.shortfall
            for wrapper in person.wrappers:
                closing = closing + wrapper.closing_balance
        if shortfall > _ZERO and first_shortfall is None:
            first_shortfall = snapshot.period
        balances.append(closing)
    return PathOutcome(
        path=index,
        first_shortfall_period=first_shortfall,
        ending_balance=balances[-1],
        closing_balances=tuple(balances),
    )


def probe_with_spending(plan: Household, amount: Money, config: RunConfig) -> Household:
    """The plan with its spending level replaced by a probe amount.

    An existing spending plan keeps its fact metadata and stage
    multipliers; a plan without one gains a bare probe plan whose
    "fact" is synthesized from ``config.today`` (no clock read —
    planning §4.6). Probe plans never leave the search, so the
    synthetic fact never lands in any result's provenance. Shared by
    the sustainable-income search and the earliest-retirement-age
    solver (:mod:`glidepath.core.retirement`, roadmap 9.14); not part
    of the package API.
    """
    spending = plan.spending
    if spending is None:
        recorded = datetime.combine(config.today, time.min, tzinfo=UTC)
        probe = SpendingPlan(
            annual_spending_real=Fact(
                value=amount, as_of=config.today, recorded_on=recorded
            )
        )
    else:
        fact = replace(spending.annual_spending_real, value=amount)
        probe = replace(spending, annual_spending_real=fact)
    changes: dict[str, Any] = {"spending": probe}
    return replace(plan, **changes) if changes else plan
