"""Monte Carlo path runner and success metrics (roadmap 7.3; planning §5.2).

:func:`run_paths` projects a plan N times through the one engine step
function — path *i* is an ordinary :func:`~glidepath.core.engine.run`
under ``replace(config, path=i)``, so its randomness is exactly
determined by ``(config.seed, i)`` and any single path is individually
re-runnable (planning §4.6). Each path reduces to a
:class:`PathOutcome`; the full period ledgers are dropped, so a
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
  (:class:`SustainableIncomeSearch`), by bisection on the spending
  plan's real annual amount. Every probe reuses the same seed (common
  random numbers), so candidates differ only by the spending level and
  the search is reproducible. Probe plans exist only inside the
  search: the synthetic spending "fact" they carry is never part of
  any returned result.
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
    from glidepath.core.config import RunConfig
    from glidepath.core.entities import Household
    from glidepath.core.periods import Period
    from glidepath.core.provenance import AssumptionSet
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
    unmet — ``None`` on a path that never fell short. ``ending_balance``
    is the household's total closing balance in the final period,
    nominal (module docstring). Re-run the path itself with
    ``run(plan, assumptions, region, replace(config, path=path))``.
    """

    path: int
    first_shortfall_period: Period | None
    ending_balance: Money

    def __post_init__(self) -> None:
        """Reject a negative path index or ending balance."""
        if self.path < 0:
            msg = f"PathOutcome.path must be non-negative, got {self.path}"
            raise ValueError(msg)
        if self.ending_balance < _ZERO:
            msg = "PathOutcome.ending_balance must be non-negative"
            raise ValueError(msg)

    @property
    def ruined(self) -> bool:
        """Whether any period's need went unmet on this path."""
        return self.first_shortfall_period is not None


@dataclass(frozen=True, slots=True)
class MonteCarloResult:
    """N paths' outcomes with the success metrics over them (§5.2).

    ``config`` and ``provenance`` are the §4.6 manifest side: the
    seed, mode, and every assumption the paths read (identical keys on
    every path — the stochastic model draws per period, not per key),
    so the whole run is reproducible from this result plus the plan.
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
        if not Decimal(0) <= percentile <= _HUNDRED:
            msg = f"percentile must lie between 0 and 100, got {percentile}"
            raise ValueError(msg)
        balances = sorted(outcome.ending_balance for outcome in self.outcomes)
        rank = (Decimal(len(balances)) - _ONE) * percentile / _HUNDRED
        lower = int(rank)
        fraction = rank - Decimal(lower)
        value = balances[lower]
        if fraction > 0:
            value = value + (balances[lower + 1] - balances[lower]) * fraction
        return value.quantized()


def run_paths(
    plan: Household,
    assumptions: AssumptionSet,
    region: Region,
    config: RunConfig,
    *,
    paths: int,
) -> MonteCarloResult:
    """Project ``plan`` over ``paths`` seeded paths (planning §5.2).

    Path *i* is ``run(plan, assumptions, region, replace(config,
    path=i))`` — always paths 0 through ``paths - 1``, whatever
    ``config.path`` says: paths are order-independent and individually
    re-runnable from the seed alone (planning §4.6). The provenance is
    path 0's — every path reads the same assumption keys.

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
    first = run(plan, assumptions, region, replace(config, path=0))
    outcomes = [_path_outcome(0, first)]
    for index in range(1, paths):
        path_config = replace(config, path=index)
        outcomes.append(
            _path_outcome(index, run(plan, assumptions, region, path_config))
        )
    return MonteCarloResult(
        outcomes=tuple(outcomes), config=config, provenance=first.provenance
    )


@dataclass(frozen=True, slots=True)
class SustainableIncomeSearch:
    """The parameters of one sustainable-income search (planning §5.2).

    ``paths`` seeded paths are projected per candidate spending level;
    a candidate meets the target when at least ``target_success_rate``
    of them avoid ruin. The bisection covers ``[0, maximum]`` of real
    annual spending and stops once the bracket narrows to
    ``tolerance``.
    """

    paths: int
    target_success_rate: Decimal
    maximum: Money
    tolerance: Money = _DEFAULT_TOLERANCE

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


def sustainable_income(
    plan: Household,
    assumptions: AssumptionSet,
    region: Region,
    config: RunConfig,
    search: SustainableIncomeSearch,
) -> Money | None:
    """The highest starting withdrawal meeting the target, by bisection.

    Searches the spending plan's real annual amount (today's money —
    the "starting withdrawal": the engine escalates it by the run's
    CPI path) over the search's ``[0, maximum]``. Returns the highest
    probed level that met the target, within the search's tolerance of
    the true boundary — ``maximum`` itself when even that meets — or
    ``None`` when not even zero spending does (the plan's outflows
    already exhaust it). Every returned value was actually probed,
    never interpolated.

    The plan's stated spending amount is irrelevant to the search —
    only its stage multipliers and the rest of the plan carry over; a
    plan with no spending plan is probed with a bare one. Probes reuse
    ``config`` unchanged (same seed: common random numbers), keeping
    the §4.6 reproducibility guarantee over the whole search.

    Raises:
        EngineError: If ``config`` is not a seeded ``MONTE_CARLO``
            config, or a probe is rejected by the engine.
    """

    def meets(amount: Money) -> bool:
        """Whether spending ``amount`` meets the target success rate."""
        probe = _with_spending(plan, amount, config)
        result = run_paths(probe, assumptions, region, config, paths=search.paths)
        return result.success_rate >= search.target_success_rate

    high = search.maximum
    if meets(high):
        return high
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


def _path_outcome(index: int, result: ProjectionResult) -> PathOutcome:
    """Reduce one path's projection to its success signals."""
    first_shortfall = None
    for snapshot in result.snapshots:
        shortfall = _ZERO
        for person in snapshot.persons:
            shortfall = shortfall + person.shortfall
        if shortfall > _ZERO:
            first_shortfall = snapshot.period
            break
    ending = _ZERO
    for person in result.snapshots[-1].persons:
        for wrapper in person.wrappers:
            ending = ending + wrapper.closing_balance
    return PathOutcome(
        path=index, first_shortfall_period=first_shortfall, ending_balance=ending
    )


def _with_spending(plan: Household, amount: Money, config: RunConfig) -> Household:
    """The plan with its spending level replaced by a probe amount.

    An existing spending plan keeps its fact metadata and stage
    multipliers; a plan without one gains a bare probe plan whose
    "fact" is synthesized from ``config.today`` (no clock read —
    planning §4.6). Probe plans never leave the search, so the
    synthetic fact never lands in any result's provenance.
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
