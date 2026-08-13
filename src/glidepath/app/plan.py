"""Plan session state: household, assumptions, projection (§4.7, 8.2/8.3).

The shell holds one immutable :class:`PlanState` and replaces it
through the pure transitions here: capturing a household re-runs the
projection; overriding an assumption re-stamps it ``USER_OVERRIDE``
(planning §1: value, source, and date always recorded) and re-runs.
Run failures are held as messages on the state, never raised at a
shell. The scenario-editing transitions live in
:mod:`glidepath.app.scenarios` (roadmap 8.5); every transition here
keeps the state's scenario runs in step with the base plan they diff
against.
"""

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from glidepath.app.tables import parse_table_text
from glidepath.core import (
    AnnuityRateTable,
    Assumption,
    AssumptionKey,
    AssumptionSet,
    Household,
    ProjectionResult,
    Provenance,
    RunConfig,
    RunMode,
    Scenario,
    StatePensionUprating,
    glide_path_from_shape,
    is_scenario_valid,
    run,
    run_scenarios,
)
from glidepath.regions.uk import (
    FutureYearsPolicy,
    default_assumption_set,
    future_years_extension,
    uk_region,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date, datetime

    from glidepath.app.drawdown import DrawdownAnswer
    from glidepath.app.retirement import RetirementAnswer
    from glidepath.core import BacktestResult, MonteCarloResult, Region
    from glidepath.regions.uk import AssumptionValue

OVERRIDE_SOURCE = "User override (assumptions inspector)"

_UNKNOWN_KEY_MESSAGE = "unknown assumption key"
_INT_OVERRIDE_MESSAGE = "enter a whole number"
_DECIMAL_OVERRIDE_MESSAGE = "enter a plain number, e.g. 0.05"


@dataclass(frozen=True)
class PlanState:
    """Everything the shell holds between user actions.

    ``scenario_runs`` holds the named runs behind the comparison
    report — the base first, then every *valid* scenario (an orphaned
    scenario is flagged on the scenarios screen and excluded, §4.3) —
    or ``None`` when there is nothing to compare. ``scenario_run_error``
    carries a scenario run failure as a message, mirroring
    ``run_error``. ``monte_carlo`` holds the explicit Monte Carlo run
    over the same plan (roadmap 9.13), with ``monte_carlo_error``
    mirroring ``run_error``; ``retirement`` holds the explicit "When
    can I retire?" answer (roadmap 9.14), with ``retirement_error``
    alongside; ``drawdown`` holds the explicit "How much can I draw
    down?" answer (roadmap 9.25), with ``drawdown_error`` alongside;
    ``backtest`` holds the explicit historical backtest
    (roadmap 9.18), with ``backtest_error`` alongside. All of them
    reset whenever the state is recomputed through
    :func:`replanned_state`, so a held result can never go stale
    against a changed plan.

    ``modified`` says whether a plan-mutating transition (facts
    capture, assumption override, scenario edit) has touched the state
    since the last save or load — the shell's unsaved-changes signal
    (issue #136). The slow-run transitions (Monte Carlo, retirement,
    drawdown, backtest) carry it through unchanged: a run reads the
    plan, it does not edit it.
    """

    assumptions: AssumptionSet
    household: Household | None = None
    result: ProjectionResult | None = None
    run_error: str | None = None
    scenarios: tuple[Scenario, ...] = ()
    scenario_runs: tuple[tuple[str, ProjectionResult], ...] | None = None
    scenario_run_error: str | None = None
    monte_carlo: MonteCarloResult | None = None
    monte_carlo_error: str | None = None
    retirement: RetirementAnswer | None = None
    retirement_error: str | None = None
    drawdown: DrawdownAnswer | None = None
    drawdown_error: str | None = None
    backtest: BacktestResult | None = None
    backtest_error: str | None = None
    modified: bool = False


@dataclass(frozen=True)
class OverrideOutcome:
    """The state after an override attempt, or why it was rejected."""

    state: PlanState
    error: str | None = None


def initial_plan_state() -> PlanState:
    """A fresh session: shipped UK defaults, no plan yet."""
    return PlanState(assumptions=default_assumption_set())


def plan_run_config(
    household: Household | None,
    *,
    today: date,
    mode: RunMode = RunMode.DETERMINISTIC,
    seed: int | None = None,
) -> RunConfig:
    """The run configuration the plan's own decisions imply (roadmap 10.3).

    The household's withdrawal-strategy decision (planning §5.1) rides
    into every projection of the plan — the base run, scenario runs,
    Monte Carlo, and the backtest — so the charts always show the
    strategy the user chose. ``None`` (no household, or no explicit
    choice) keeps the engine default: fixed real spending. The "When
    can I retire?" and "How much can I draw down?" cards deliberately
    stay on fixed real spending — each answers a fixed-real-income
    question by construction (roadmap 9.14, 9.25).
    """
    rule = None if household is None else household.withdrawal_strategy
    if rule is None:
        return RunConfig(today=today, mode=mode, seed=seed)
    return RunConfig(
        today=today, mode=mode, seed=seed, withdrawal_strategy=rule.value.strategy()
    )


def region_for(assumptions: AssumptionSet) -> Region:
    """The region bundle one run's *effective* assumption set implies.

    Rebuilt per run rather than shared: the UK future-years tax
    extension is derived from assumptions at build time, so a scenario
    overriding those needs its own region (see
    :func:`~glidepath.core.run_scenarios`).
    """
    return uk_region(future_years_extension(assumptions))


def _projected(
    household: Household, assumptions: AssumptionSet, today: date
) -> tuple[ProjectionResult | None, str | None]:
    """Run the projection, folding any run failure into a message."""
    try:
        config = plan_run_config(household, today=today)
        result = run(household, assumptions, region_for(assumptions), config)
    except ValueError as exc:
        return None, str(exc)
    return result, None


def _scenario_runs(
    household: Household | None,
    assumptions: AssumptionSet,
    scenarios: tuple[Scenario, ...],
    today: date,
) -> tuple[tuple[tuple[str, ProjectionResult], ...] | None, str | None]:
    """Run the base and every valid scenario, folding failures into a message.

    Orphaned scenarios are left out — the scenarios screen flags them
    (§4.3) — so one broken what-if never blocks the comparison.
    """
    if household is None:
        return None, None
    valid = tuple(
        scenario
        for scenario in scenarios
        if is_scenario_valid(scenario, household, assumptions)
    )
    if not valid:
        return None, None
    try:
        runs = run_scenarios(
            household,
            assumptions,
            valid,
            region_for,
            plan_run_config(household, today=today),
        )
    except ValueError as exc:
        return None, str(exc)
    return runs, None


def replanned_state(
    assumptions: AssumptionSet,
    household: Household | None,
    scenarios: tuple[Scenario, ...],
    *,
    today: date,
    modified: bool,
) -> PlanState:
    """A state recomputed from its inputs: base run plus scenario runs.

    The one route every transition takes, so the projection and the
    scenario comparison can never drift out of step with the inputs.
    ``modified`` is the unsaved-changes flag the recomputed state
    carries: True from the plan-mutating transitions, False from a
    load, and the incoming state's own flag from the slow-run
    transitions (issue #136).
    """
    if household is None:
        return PlanState(
            assumptions=assumptions, scenarios=scenarios, modified=modified
        )
    result, error = _projected(household, assumptions, today)
    runs, runs_error = _scenario_runs(household, assumptions, scenarios, today)
    return PlanState(
        assumptions=assumptions,
        household=household,
        result=result,
        run_error=error,
        scenarios=scenarios,
        scenario_runs=runs,
        scenario_run_error=runs_error,
        modified=modified,
    )


def state_with_household(
    state: PlanState, household: Household, *, today: date
) -> PlanState:
    """The state after capturing ``household`` and re-projecting."""
    return replanned_state(
        state.assumptions, household, state.scenarios, today=today, modified=True
    )


def state_with_scenarios(
    state: PlanState, scenarios: tuple[Scenario, ...], *, today: date
) -> PlanState:
    """The state after replacing the scenario list and re-running the diffs.

    Scenarios never mutate the base plan (§4.3), but the base run is
    recomputed alongside the scenario runs so both always share one
    ``today`` — in a session left open across a date boundary, the
    comparison's base and the displayed projection must not diverge.
    """
    return replanned_state(
        state.assumptions, state.household, scenarios, today=today, modified=True
    )


def state_marked_saved(state: PlanState) -> PlanState:
    """The state with its unsaved-changes flag cleared (issue #136).

    Shells apply this after a successful save — and after projecting
    the launch example, which is shipped demo data, not user edits.
    """
    changes: dict[str, Any] = {"modified": False} if state.modified else {}
    return replace(state, **changes) if changes else state


def _parsed_override_value(
    base: Assumption[Any], raw: str
) -> Decimal | int | dict[str, AssumptionValue]:
    """The typed value ``raw`` denotes for ``base``'s value shape.

    Every scalar in the shipped catalogue is a ``Decimal`` or an
    ``int``; a structured table parses from ``key = value`` lines and
    is vetted by its policy parser before it can reach the assumption
    set (issue #71).

    Raises:
        ValueError: If ``raw`` does not parse as the base value's
            shape, or a parsed table fails its policy parser.
    """
    if isinstance(base.value, Decimal):
        try:
            value = Decimal(raw)
        except InvalidOperation:
            raise ValueError(_DECIMAL_OVERRIDE_MESSAGE) from None
        if not value.is_finite():
            raise ValueError(_DECIMAL_OVERRIDE_MESSAGE)
        return value
    if isinstance(base.value, int):
        try:
            return int(raw, 10)
        except ValueError:
            raise ValueError(_INT_OVERRIDE_MESSAGE) from None
    table = parse_table_text(raw)
    check_table_override(base.key, table)
    return table


def check_table_override(
    key: AssumptionKey, table: Mapping[str, AssumptionValue]
) -> None:
    """Vet a table override through its policy parser (issue #71).

    A shape check alone would accept nonsense — any mapping passes —
    so the parser that consumes the table at run time is the contract:
    a table it rejects never enters the state. The parsers raise
    ``ValueError`` subclasses except the glide-shape builder, whose
    ``KeyError``/``TypeError`` are folded into the same channel.

    Raises:
        ValueError: If the table fails its policy parser.
    """
    try:
        if key is AssumptionKey.GLIDEPATH_DEFAULT_SHAPE:
            glide_path_from_shape(table)
        elif key is AssumptionKey.POLICY_STATE_PENSION_UPRATING:
            StatePensionUprating.from_assumption_value(table)
        elif key is AssumptionKey.POLICY_TAX_FUTURE_YEARS:
            FutureYearsPolicy.from_assumption_value(table)
        elif key is AssumptionKey.ANNUITY_AGE_ADJUSTMENT:
            AnnuityRateTable.from_assumption_value(table)
    except KeyError as exc:
        msg = f"missing required key {exc.args[0]!r}"
        raise ValueError(msg) from exc
    except TypeError as exc:
        raise ValueError(str(exc)) from exc


def _with_assumption(state: PlanState, assumption: Assumption[Any]) -> AssumptionSet:
    """A new set with ``assumption`` in place of its key's entry."""
    return AssumptionSet(
        assumption if key == assumption.key else state.assumptions.get(key)
        for key in state.assumptions.keys
    )


def state_with_override(
    state: PlanState,
    key: str,
    raw_value: str,
    *,
    recorded_on: datetime,
    today: date,
) -> OverrideOutcome:
    """Override one assumption in place and re-project (roadmap 8.3).

    A blank ``raw_value`` restores the shipped default. A value that
    does not parse leaves the state untouched and reports why.
    """
    try:
        assumption_key = AssumptionKey(key)
    except ValueError:
        return OverrideOutcome(state=state, error=_UNKNOWN_KEY_MESSAGE)
    base = state.assumptions.get(assumption_key)
    text = raw_value.strip()
    if not text:
        changed = default_assumption_set().get(assumption_key)
    else:
        try:
            value = _parsed_override_value(base, text)
        except ValueError as exc:
            return OverrideOutcome(state=state, error=str(exc))
        changed = replace(
            base,
            value=value,
            provenance=Provenance.USER_OVERRIDE,
            source=OVERRIDE_SOURCE,
            recorded_on=recorded_on,
        )
    assumptions = _with_assumption(state, changed)
    return OverrideOutcome(
        state=replanned_state(
            assumptions, state.household, state.scenarios, today=today, modified=True
        )
    )


def facts_saved_message(state: PlanState) -> str:
    """The status line after a successful facts capture."""
    if state.run_error is not None:
        return f"Facts saved, but the projection failed: {state.run_error}"
    return "Facts saved and projection run — see the stated-vs-assumed view."
