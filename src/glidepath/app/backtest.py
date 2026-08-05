"""Historical backtest in the shell: transition and panel (roadmap 9.18).

The core window runner (:func:`~glidepath.core.run_windows`) surfaced
per planning §4.7 as a charts-screen card next to the Monte Carlo
panel: an explicit Run action replays the plan over every rolling
window of the shipped historical return series
(``returns_history.toml``, the §5.3 provenance pattern), and the
readout mirrors the Monte Carlo metrics — success rate over windows,
the worst starting year, ending-pot percentiles — so the two surfaces
read side by side. Results are reproducible: no randomness is
involved, and every plan-changing transition routes through
:func:`~glidepath.app.plan.replanned_state`, which drops the held
result, so a stale backtest can never be shown against a changed
plan.
"""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final

from glidepath.app.display import format_money, format_percent
from glidepath.app.montecarlo import BAND_SPECS, SUCCESS_RATE_LABEL
from glidepath.app.plan import PlanState, region_for, replanned_state
from glidepath.core import Money, RunConfig, run_windows
from glidepath.regions.uk import load_returns_history

if TYPE_CHECKING:
    from datetime import date
    from decimal import Decimal

    from glidepath.core import BacktestResult

BACKTEST_HEADING: Final = "Historical backtest"

RUN_BACKTEST_LABEL: Final = "Run backtest"

NO_BACKTEST_MESSAGE: Final = (
    "No backtest yet — press Run backtest to replay the plan over every"
    " historical starting year."
)

BACKTEST_NO_PLAN_MESSAGE: Final = (
    "Save facts on the Facts tab before running a backtest."
)

BACKTEST_FAILED_PREFIX: Final = "The backtest failed: "

BACKTEST_RUNNING_MESSAGE: Final = "Running historical backtest…"

BACKTEST_STALE_MESSAGE: Final = (
    "Backtest result discarded — the plan changed while it ran."
)

WINDOWS_LABEL: Final = "Windows"

WORST_WINDOW_LABEL: Final = "Worst starting year"


@dataclass(frozen=True)
class BacktestMetric:
    """One backtest readout row: a label and its formatted value."""

    label: str
    value: str


@dataclass(frozen=True)
class BacktestPanelViewModel:
    """The historical-backtest card on the charts screen (roadmap 9.18).

    ``message`` carries the no-run or failure copy; it is blank
    whenever ``metrics`` is populated. The card has no inputs — the
    windows are however many complete horizons the shipped series
    holds — so unlike the Monte Carlo panel there is nothing to echo
    back.
    """

    heading: str
    run_label: str
    metrics: tuple[BacktestMetric, ...]
    message: str


def state_with_backtest(state: PlanState, *, today: date) -> PlanState:
    """The state after replaying the plan over every historical window.

    Anything unusable — no plan, a horizon the series cannot cover, an
    engine rejection — folds into ``backtest_error`` on the returned
    state, never raising at a shell (the :mod:`glidepath.app.plan`
    rule). Before the windows run, the base projection (and the
    scenario runs with it) is recomputed at the same ``today`` through
    :func:`~glidepath.app.plan.replanned_state` — the 9.13 rule: the
    backtest bands and the deterministic chart they overlay must share
    one anchor date. The run is serial by design: a full backtest is
    around a second of work (each window is one deterministic engine
    pass), well under the process-pool threshold the Monte Carlo
    transition uses.
    """
    if state.household is None:
        return _with_backtest_error(state, BACKTEST_NO_PLAN_MESSAGE)
    base = replanned_state(
        state.assumptions, state.household, state.scenarios, today=today
    )
    config = RunConfig(today=today)
    try:
        series = load_returns_history().series
        result = run_windows(
            state.household,
            state.assumptions,
            region_for(state.assumptions),
            config,
            series=series,
        )
    except Exception as exc:
        # Broad by design, mirroring the Monte Carlo transition: an
        # escape past the shell's worker thread would leave the
        # in-flight guard held (buttons disabled, spinner running)
        # forever, so every failure folds into the state (§4.7).
        return _with_backtest_error(base, BACKTEST_FAILED_PREFIX + str(exc))
    changes: dict[str, Any] = {"backtest": result, "backtest_error": None}
    return replace(base, **changes) if changes else base


def build_backtest_panel(
    state: PlanState,
    *,
    ending_pot_deflator: Decimal | None,
    basis_suffix: str,
) -> BacktestPanelViewModel:
    """The historical-backtest card for the charts screen (9.18).

    ``ending_pot_deflator`` is the final period's balance deflator in
    the screen's basis (1 under nominal) — every window shares the
    run's one assumed CPI path (planning §5.2), so the deterministic
    report's deflator presents the window pots too; ``None`` (no
    projection to read it from) leaves the metrics unbuilt.
    ``basis_suffix`` names the basis on the money labels.
    """
    result = state.backtest
    metrics: tuple[BacktestMetric, ...] = ()
    message = ""
    if state.backtest_error is not None:
        message = state.backtest_error
    elif result is None or ending_pot_deflator is None:
        message = NO_BACKTEST_MESSAGE
    else:
        metrics = _metrics(result, ending_pot_deflator, basis_suffix)
    return BacktestPanelViewModel(
        heading=BACKTEST_HEADING,
        run_label=RUN_BACKTEST_LABEL,
        metrics=metrics,
        message=message,
    )


def _worst_window_value(
    result: BacktestResult, ending_pot_deflator: Decimal, basis_suffix: str
) -> str:
    """The worst starting year with what went wrong there.

    A ruined worst window names the plan-calendar year the money ran
    out — the year the charts' x axis carries — and a surviving one
    the lowest ending pot, so the row reads against the chart either
    way.
    """
    worst = result.worst_window
    shortfall = worst.first_shortfall_period
    if shortfall is not None:
        return f"{worst.start_year} — money ran out in {shortfall.start.year}"
    presented = Money(worst.ending_balance.amount / ending_pot_deflator).quantized()
    return (
        f"{worst.start_year} — lowest ending pot"
        f" {format_money(presented)} ({basis_suffix})"
    )


def _metrics(
    result: BacktestResult, ending_pot_deflator: Decimal, basis_suffix: str
) -> tuple[BacktestMetric, ...]:
    """The backtest readout rows, mirroring the Monte Carlo metrics."""
    first = result.outcomes[0].start_year
    last = result.outcomes[-1].start_year
    rows = [
        BacktestMetric(
            label=SUCCESS_RATE_LABEL, value=format_percent(result.success_rate)
        ),
        BacktestMetric(
            label=WINDOWS_LABEL,
            value=f"{result.window_count} starting years ({first} to {last})",
        ),
        BacktestMetric(
            label=WORST_WINDOW_LABEL,
            value=_worst_window_value(result, ending_pot_deflator, basis_suffix),
        ),
    ]
    for spec in BAND_SPECS:
        pot = result.ending_pot_percentile(spec.percentile)
        presented = Money(pot.amount / ending_pot_deflator).quantized()
        rows.append(
            BacktestMetric(
                label=f"{spec.ending_pot_label} ({basis_suffix})",
                value=format_money(presented),
            )
        )
    return tuple(rows)


def _with_backtest_error(state: PlanState, message: str) -> PlanState:
    """The state carrying a backtest failure, any held result dropped."""
    changes: dict[str, Any] = {"backtest": None, "backtest_error": message}
    return replace(state, **changes) if changes else state
