"""Monte Carlo in the shell: run mode, transition, panel (roadmap 9.13).

The Phase 7 core (:func:`~glidepath.core.run_paths`) surfaced per
planning §4.7: a run-mode control (deterministic | Monte Carlo with
paths + seed), the success-metrics readout, and the percentile-band
specs the balances chart draws (the bands themselves are composed in
:mod:`glidepath.app.charts`). The Monte Carlo run is an explicit user
action — even at the recorded 9.15 per-path cost (planning §4.6) it is
far too slow to re-run on every keystroke — and every plan-changing
transition routes through :func:`~glidepath.app.plan.replanned_state`, which
drops the held result, so a stale Monte Carlo surface can never be
shown against a changed plan. Same seed + inputs reproduce identical
results (§4.6): the transition passes the parsed seed straight to the
seeded path runner.
"""

import os
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from glidepath.app.display import format_money, format_percent
from glidepath.app.plan import PlanState, region_for, replanned_state
from glidepath.core import Money, PathParallelism, RunConfig, RunMode, run_paths

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from datetime import date

    from glidepath.core import MonteCarloResult

DEFAULT_RUN_MODE: Final = RunMode.DETERMINISTIC

RUN_MODE_HEADING: Final = "Run mode"

SEED_LABEL: Final = "Seed"

PATHS_LABEL: Final = "Paths"

RUN_MONTE_CARLO_LABEL: Final = "Run Monte Carlo"

DEFAULT_SEED_VALUE: Final = "1"

DEFAULT_PATHS_VALUE: Final = "100"

MAX_PATHS: Final = 10_000

PARALLEL_PATHS_MIN: Final = 100
"""Fewest path-projections a run must total before a pool pays off.

Spawning worker processes costs around a second (each re-imports the
package); below this many paths the serial run finishes comparably
fast, so small runs — and the app-layer test suite — stay in-process.
"""

MAX_POOL_WORKERS: Final = 61
"""Most workers a pool may hold — Windows' wait-handle ceiling.

``ProcessPoolExecutor`` rejects more than 61 workers on Windows (the
64-object ``WaitForMultipleObjects`` limit minus bookkeeping handles);
without the cap, every qualifying run on a 63+-logical-CPU Windows
machine would fail at pool construction. Capped on every platform so
the policy stays uniform.
"""

NO_MONTE_CARLO_MESSAGE: Final = (
    "No Monte Carlo run yet — choose the paths and seed, then press Run Monte Carlo."
)

MONTE_CARLO_NO_PLAN_MESSAGE: Final = (
    "Save facts on the Facts tab before running Monte Carlo."
)

MONTE_CARLO_SEED_MESSAGE: Final = "Monte Carlo needs a whole-number seed."

MONTE_CARLO_PATHS_MESSAGE: Final = (
    "Monte Carlo needs a whole-number path count between 1 and 10,000."
)

MONTE_CARLO_FAILED_PREFIX: Final = "The Monte Carlo run failed: "

MONTE_CARLO_RUNNING_MESSAGE: Final = "Running Monte Carlo…"


def monte_carlo_running_status(paths_text: str) -> str:
    """The busy status line for a Monte Carlo run just started (9.16).

    Names the path count when the raw text the shell captured parses
    to a usable one — "Running Monte Carlo — 1,000 paths…" — and falls
    back to the plain running message otherwise; the transition itself
    reports unusable input when it delivers (planning §4.7: the shell
    never parses).
    """
    try:
        paths = int(paths_text.strip(), 10)
    except ValueError:
        return MONTE_CARLO_RUNNING_MESSAGE
    if not 1 <= paths <= MAX_PATHS:
        return MONTE_CARLO_RUNNING_MESSAGE
    return f"Running Monte Carlo — {paths:,} paths…"


MONTE_CARLO_STALE_MESSAGE: Final = (
    "Monte Carlo result discarded — the plan changed while it ran."
)

SUCCESS_RATE_LABEL: Final = "Success rate"

RUIN_LABEL: Final = "Probability of ruin"

_MODE_BY_KEY: Final[Mapping[str, RunMode]] = {
    "deterministic": RunMode.DETERMINISTIC,
    "monte_carlo": RunMode.MONTE_CARLO,
}

_KEY_BY_MODE: Final[Mapping[RunMode, str]] = {
    mode: key for key, mode in _MODE_BY_KEY.items()
}

_MODE_LABELS: Final[Mapping[str, str]] = {
    "deterministic": "Deterministic",
    "monte_carlo": "Monte Carlo",
}

_UNKNOWN_MODE_MESSAGE: Final = "unknown run mode key"


@dataclass(frozen=True)
class BandSpec:
    """One percentile the Monte Carlo surfaces chart and report (§5.2)."""

    band_label: str
    ending_pot_label: str
    percentile: Decimal


BAND_SPECS: Final[tuple[BandSpec, ...]] = (
    BandSpec(
        band_label="10th percentile",
        ending_pot_label="Ending pot, 10th percentile",
        percentile=Decimal(10),
    ),
    BandSpec(
        band_label="Median",
        ending_pot_label="Ending pot, median",
        percentile=Decimal(50),
    ),
    BandSpec(
        band_label="90th percentile",
        ending_pot_label="Ending pot, 90th percentile",
        percentile=Decimal(90),
    ),
)
"""The 10/50/90 bands of roadmap 9.13, chart labels and metric labels."""


@dataclass(frozen=True)
class RunModeOption:
    """One run-mode choice for the charts screen (roadmap 9.13)."""

    key: str
    label: str


@dataclass(frozen=True)
class MonteCarloMetric:
    """One success-metric readout row: a label and its formatted value."""

    label: str
    value: str


@dataclass(frozen=True)
class MonteCarloPanelViewModel:
    """The run-mode control and Monte Carlo readout (roadmap 9.13).

    ``seed_value`` and ``paths_value`` echo the held result's actual
    inputs (the §4.6 manifest side) or the defaults before any run.
    ``controls_visible`` hides the seed, paths, run action, metrics,
    and message under the deterministic mode. ``message`` carries the
    no-run or failure copy; it is blank whenever ``metrics`` is
    populated.
    """

    heading: str
    mode_options: tuple[RunModeOption, ...]
    selected_mode_key: str
    seed_label: str
    seed_value: str
    paths_label: str
    paths_value: str
    run_label: str
    controls_visible: bool
    metrics: tuple[MonteCarloMetric, ...]
    message: str


@contextmanager
def path_pool(total_paths: int) -> Iterator[PathParallelism | None]:
    """A worker-process pool for ``total_paths`` path-projections, or ``None``.

    The app layer's parallelism policy (planning §5.2): paths are pure
    CPU-bound ``Decimal`` work, so a run big enough to amortize process
    startup (``PARALLEL_PATHS_MIN``) gets a process pool sized to the
    machine — every available core but one, so the GUI thread keeps a
    core, capped at ``MAX_POOL_WORKERS`` — and anything smaller runs
    serially (``None``). One pool
    serves a whole transition: a retirement-age search passes it to
    every candidate's paths rather than re-spawning per age.
    """
    spare_cores = max(1, (os.process_cpu_count() or 1) - 1)
    workers = min(spare_cores, total_paths, MAX_POOL_WORKERS)
    if workers <= 1 or total_paths < PARALLEL_PATHS_MIN:
        yield None
        return
    with ProcessPoolExecutor(max_workers=workers) as executor:
        yield PathParallelism(executor=executor, workers=workers)


def run_mode_from_key(key: str) -> RunMode:
    """The run mode a shell-selected option key denotes.

    Raises:
        ValueError: If ``key`` is not a known run-mode option key.
    """
    mode = _MODE_BY_KEY.get(key)
    if mode is None:
        raise ValueError(_UNKNOWN_MODE_MESSAGE)
    return mode


def run_mode_key(mode: RunMode) -> str:
    """The option key denoting ``mode`` — the inverse of ``run_mode_from_key``."""
    return _KEY_BY_MODE[mode]


def run_mode_options() -> tuple[RunModeOption, ...]:
    """The run-mode choices the charts screen offers (roadmap 9.13)."""
    return tuple(
        RunModeOption(key=key, label=_MODE_LABELS[key]) for key in _MODE_BY_KEY
    )


def state_with_monte_carlo(
    state: PlanState, seed_text: str, paths_text: str, *, today: date
) -> PlanState:
    """The state after running the plan over seeded Monte Carlo paths.

    ``seed_text`` and ``paths_text`` arrive as the shell captured
    them; anything unusable — no plan, an unparseable seed or path
    count, an engine rejection — folds into ``monte_carlo_error`` on
    the returned state, never raising at a shell (the
    :mod:`glidepath.app.plan` rule). Before the paths run, the base
    projection (and the scenario runs with it) is recomputed at the
    same ``today`` through :func:`~glidepath.app.plan.replanned_state`
    — the :func:`~glidepath.app.plan.state_with_scenarios` rule: in a
    session left open across a date boundary, the bands and the
    deterministic chart they overlay must share one anchor date. Same
    seed + inputs reproduce identical results (§4.6).
    """
    if state.household is None:
        return _with_monte_carlo_error(state, MONTE_CARLO_NO_PLAN_MESSAGE)
    try:
        seed = int(seed_text.strip(), 10)
    except ValueError:
        return _with_monte_carlo_error(state, MONTE_CARLO_SEED_MESSAGE)
    try:
        paths = int(paths_text.strip(), 10)
    except ValueError:
        return _with_monte_carlo_error(state, MONTE_CARLO_PATHS_MESSAGE)
    if not 1 <= paths <= MAX_PATHS:
        return _with_monte_carlo_error(state, MONTE_CARLO_PATHS_MESSAGE)
    base = replanned_state(
        state.assumptions,
        state.household,
        state.scenarios,
        today=today,
        modified=state.modified,
    )
    config = RunConfig(today=today, mode=RunMode.MONTE_CARLO, seed=seed)
    try:
        with path_pool(paths) as parallelism:
            result = run_paths(
                state.household,
                state.assumptions,
                region_for(state.assumptions),
                config,
                paths=paths,
                parallelism=parallelism,
            )
    except Exception as exc:
        # Broad by design: beyond the engine's ValueErrors, the process
        # pool can raise OSError at spawn, pickling TypeErrors, or a
        # BrokenExecutor — an escape here would leave the shell's
        # in-flight guard held (buttons disabled, spinner running)
        # forever, so every failure folds into the state (§4.7).
        return _with_monte_carlo_error(base, MONTE_CARLO_FAILED_PREFIX + str(exc))
    changes: dict[str, Any] = {"monte_carlo": result, "monte_carlo_error": None}
    return replace(base, **changes) if changes else base


def build_monte_carlo_panel(
    state: PlanState,
    mode: RunMode,
    *,
    ending_pot_deflator: Decimal | None,
    basis_suffix: str,
) -> MonteCarloPanelViewModel:
    """The run-mode control and readout for the charts screen (9.13).

    ``ending_pot_deflator`` is the final period's balance deflator in
    the screen's basis (1 under nominal) — CPI is deterministic across
    paths (planning §5.2), so the deterministic report's deflator
    presents the Monte Carlo pots too; ``None`` (no projection to read
    it from) leaves the metrics unbuilt. ``basis_suffix`` names the
    basis on the ending-pot labels.
    """
    result = state.monte_carlo
    metrics: tuple[MonteCarloMetric, ...] = ()
    message = ""
    controls_visible = mode is RunMode.MONTE_CARLO
    if controls_visible:
        if state.monte_carlo_error is not None:
            message = state.monte_carlo_error
        elif result is None or ending_pot_deflator is None:
            message = NO_MONTE_CARLO_MESSAGE
        else:
            metrics = _metrics(result, ending_pot_deflator, basis_suffix)
    return MonteCarloPanelViewModel(
        heading=RUN_MODE_HEADING,
        mode_options=run_mode_options(),
        selected_mode_key=run_mode_key(mode),
        seed_label=SEED_LABEL,
        seed_value=DEFAULT_SEED_VALUE if result is None else str(result.config.seed),
        paths_label=PATHS_LABEL,
        paths_value=DEFAULT_PATHS_VALUE if result is None else str(result.path_count),
        run_label=RUN_MONTE_CARLO_LABEL,
        controls_visible=controls_visible,
        metrics=metrics,
        message=message,
    )


def _metrics(
    result: MonteCarloResult, ending_pot_deflator: Decimal, basis_suffix: str
) -> tuple[MonteCarloMetric, ...]:
    """The success-metrics readout rows (planning §5.2)."""
    rows = [
        MonteCarloMetric(
            label=SUCCESS_RATE_LABEL, value=format_percent(result.success_rate)
        ),
        MonteCarloMetric(
            label=RUIN_LABEL, value=format_percent(result.probability_of_ruin)
        ),
    ]
    for spec in BAND_SPECS:
        pot = result.ending_pot_percentile(spec.percentile)
        presented = Money(pot.amount / ending_pot_deflator).quantized()
        rows.append(
            MonteCarloMetric(
                label=f"{spec.ending_pot_label} ({basis_suffix})",
                value=format_money(presented),
            )
        )
    return tuple(rows)


def _with_monte_carlo_error(state: PlanState, message: str) -> PlanState:
    """The state carrying a Monte Carlo failure, any held result dropped."""
    changes: dict[str, Any] = {"monte_carlo": None, "monte_carlo_error": message}
    return replace(state, **changes) if changes else state
