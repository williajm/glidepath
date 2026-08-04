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
    state_pension_uprating,
    uk_region,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date, datetime

    from glidepath.core import Region
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
    ``run_error``.
    """

    assumptions: AssumptionSet
    household: Household | None = None
    result: ProjectionResult | None = None
    run_error: str | None = None
    scenarios: tuple[Scenario, ...] = ()
    scenario_runs: tuple[tuple[str, ProjectionResult], ...] | None = None
    scenario_run_error: str | None = None


@dataclass(frozen=True)
class OverrideOutcome:
    """The state after an override attempt, or why it was rejected."""

    state: PlanState
    error: str | None = None


def initial_plan_state() -> PlanState:
    """A fresh session: shipped UK defaults, no plan yet."""
    return PlanState(assumptions=default_assumption_set())


def region_for(assumptions: AssumptionSet) -> Region:
    """The region bundle one run's *effective* assumption set implies.

    Rebuilt per run rather than shared: the UK future-years tax
    extension and state pension uprating are derived from assumptions
    at build time, so a scenario overriding them needs its own region
    (see :func:`~glidepath.core.run_scenarios`).
    """
    return uk_region(
        future_years_extension(assumptions), state_pension_uprating(assumptions)
    )


def _projected(
    household: Household, assumptions: AssumptionSet, today: date
) -> tuple[ProjectionResult | None, str | None]:
    """Run the projection, folding any run failure into a message."""
    try:
        config = RunConfig(today=today)
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
            household, assumptions, valid, region_for, RunConfig(today=today)
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
) -> PlanState:
    """A state recomputed from its inputs: base run plus scenario runs.

    The one route every transition takes, so the projection and the
    scenario comparison can never drift out of step with the inputs.
    """
    if household is None:
        return PlanState(assumptions=assumptions, scenarios=scenarios)
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
    )


def state_with_household(
    state: PlanState, household: Household, *, today: date
) -> PlanState:
    """The state after capturing ``household`` and re-projecting."""
    return replanned_state(state.assumptions, household, state.scenarios, today=today)


def state_with_scenarios(
    state: PlanState, scenarios: tuple[Scenario, ...], *, today: date
) -> PlanState:
    """The state after replacing the scenario list and re-running the diffs.

    Scenarios never mutate the base plan (§4.3), so the base
    projection is untouched; only the scenario runs recompute.
    """
    runs, error = _scenario_runs(state.household, state.assumptions, scenarios, today)
    return PlanState(
        assumptions=state.assumptions,
        household=state.household,
        result=state.result,
        run_error=state.run_error,
        scenarios=scenarios,
        scenario_runs=runs,
        scenario_run_error=error,
    )


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
    _check_table(base.key, table)
    return table


def _check_table(key: AssumptionKey, table: Mapping[str, AssumptionValue]) -> None:
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
            assumptions, state.household, state.scenarios, today=today
        )
    )


def facts_saved_message(state: PlanState) -> str:
    """The status line after a successful facts capture."""
    if state.run_error is not None:
        return f"Facts saved, but the projection failed: {state.run_error}"
    return "Facts saved and projection run — see the stated-vs-assumed view."
