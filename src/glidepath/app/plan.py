"""Plan session state: household, assumptions, projection (§4.7, 8.2/8.3).

The shell holds one immutable :class:`PlanState` and replaces it
through the pure transitions here: capturing a household re-runs the
projection; overriding an assumption re-stamps it ``USER_OVERRIDE``
(planning §1: value, source, and date always recorded) and re-runs.
Run failures are held as messages on the state, never raised at a
shell.
"""

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from glidepath.core import (
    Assumption,
    AssumptionKey,
    AssumptionSet,
    Household,
    ProjectionResult,
    Provenance,
    RunConfig,
    run,
)
from glidepath.regions.uk import (
    default_assumption_set,
    future_years_extension,
    state_pension_uprating,
    uk_region,
)

if TYPE_CHECKING:
    from datetime import date, datetime

OVERRIDE_SOURCE = "User override (assumptions inspector)"

_UNKNOWN_KEY_MESSAGE = "unknown assumption key"
_STRUCTURED_MESSAGE = (
    "this assumption is a structured table; it cannot be edited in place"
)
_INT_OVERRIDE_MESSAGE = "enter a whole number"
_DECIMAL_OVERRIDE_MESSAGE = "enter a plain number, e.g. 0.05"


@dataclass(frozen=True)
class PlanState:
    """Everything the shell holds between user actions."""

    assumptions: AssumptionSet
    household: Household | None = None
    result: ProjectionResult | None = None
    run_error: str | None = None


@dataclass(frozen=True)
class OverrideOutcome:
    """The state after an override attempt, or why it was rejected."""

    state: PlanState
    error: str | None = None


def initial_plan_state() -> PlanState:
    """A fresh session: shipped UK defaults, no plan yet."""
    return PlanState(assumptions=default_assumption_set())


def _projected(
    household: Household, assumptions: AssumptionSet, today: date
) -> tuple[ProjectionResult | None, str | None]:
    """Run the projection, folding any run failure into a message."""
    try:
        region = uk_region(
            future_years_extension(assumptions), state_pension_uprating(assumptions)
        )
        result = run(household, assumptions, region, RunConfig(today=today))
    except ValueError as exc:
        return None, str(exc)
    return result, None


def state_with_household(
    state: PlanState, household: Household, *, today: date
) -> PlanState:
    """The state after capturing ``household`` and re-projecting."""
    result, error = _projected(household, state.assumptions, today)
    return PlanState(
        assumptions=state.assumptions,
        household=household,
        result=result,
        run_error=error,
    )


def _parsed_override_value(base: Assumption[Any], raw: str) -> Decimal | int:
    """The typed value ``raw`` denotes for ``base``'s value shape.

    Every scalar in the shipped catalogue is a ``Decimal`` or an
    ``int``; the structured policy tables are display-only in place.

    Raises:
        ValueError: If ``raw`` does not parse as the base value's type,
            or the base value is a structured table.
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
    raise ValueError(_STRUCTURED_MESSAGE)


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
    if state.household is None:
        return OverrideOutcome(state=PlanState(assumptions=assumptions))
    result, error = _projected(state.household, assumptions, today)
    return OverrideOutcome(
        state=PlanState(
            assumptions=assumptions,
            household=state.household,
            result=result,
            run_error=error,
        )
    )


def facts_saved_message(state: PlanState) -> str:
    """The status line after a successful facts capture."""
    if state.run_error is not None:
        return f"Facts saved, but the projection failed: {state.run_error}"
    return "Facts saved and projection run — see the stated-vs-assumed view."
