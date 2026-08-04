"""The "When can I retire?" card: transition and panel (roadmap 9.14).

The Phase 9.14 core solver
(:func:`~glidepath.core.earliest_retirement_age`) surfaced per planning
§4.7: a card on the charts screen asking for a replacement-rate target
(default 66% of current employment income, user-adjustable) and
answering with the earliest retirement age that sustains it, the target
income, and the basis the answer was computed on. The search runs the
plan once per candidate age — an explicit user action like the Monte
Carlo run (roadmap 9.13), far too slow for every keystroke — and its
basis follows the screen's run mode: the deterministic projection, or
"at least N% Monte Carlo success" over the panel's seed and path count
once a Monte Carlo basis is selected. The transition re-anchors the
whole state through :func:`~glidepath.app.plan.replanned_state`, so a
held answer can never go stale against a changed plan.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from glidepath.app.display import format_money, format_percent
from glidepath.app.montecarlo import (
    MAX_PATHS,
    MONTE_CARLO_PATHS_MESSAGE,
    MONTE_CARLO_SEED_MESSAGE,
)
from glidepath.app.plan import PlanState, region_for, replanned_state
from glidepath.core import (
    AssumptionKey,
    Money,
    RetirementAgeSearch,
    RunConfig,
    RunMode,
    age_on,
    earliest_retirement_age,
    int_assumption_value,
)

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core import AssumptionSet, Household

_ZERO = Money(Decimal(0))
_HUNDRED = Decimal(100)

RETIREMENT_HEADING: Final = "When can I retire?"

RETIREMENT_RATE_LABEL: Final = "Replacement rate (%)"

DEFAULT_RETIREMENT_RATE_VALUE: Final = "66"

RETIREMENT_SUCCESS_LABEL: Final = "Success target (%)"

DEFAULT_RETIREMENT_SUCCESS_VALUE: Final = "90"

FIND_RETIREMENT_AGE_LABEL: Final = "Find earliest age"

NO_RETIREMENT_MESSAGE: Final = (
    "No answer yet — choose a replacement rate, then press Find earliest age."
)

RETIREMENT_NO_PLAN_MESSAGE: Final = (
    "Save facts on the Facts tab before asking when you can retire."
)

RETIREMENT_NO_INCOME_MESSAGE: Final = (
    "Add employment income on the Facts tab — the target income is a share of it."
)

RETIREMENT_RATE_MESSAGE: Final = (
    "The replacement rate needs a whole number between 1 and 100."
)

RETIREMENT_SUCCESS_MESSAGE: Final = (
    "The success target needs a whole number between 1 and 100."
)

RETIREMENT_HORIZON_MESSAGE: Final = (
    "No ages to search — the planning horizon age is not beyond the current age."
)

RETIREMENT_FAILED_PREFIX: Final = "The retirement-age search failed: "

RETIREMENT_RUNNING_MESSAGE: Final = "Searching for the earliest retirement age…"

RETIREMENT_STALE_MESSAGE: Final = (
    "Retirement-age answer discarded — the plan changed while it ran."
)

RETIREMENT_ANSWER_PREFIX: Final = "Earliest retirement age: "

RETIREMENT_DETERMINISTIC_BASIS: Final = (
    "Basis: deterministic projection, no unmet need in any period."
)


@dataclass(frozen=True)
class RetirementRequest:
    """One card submission, as the shell captured it (planning §4.7).

    ``mode`` is the charts screen's run-mode selection — the answer's
    basis. ``seed_text``, ``paths_text``, and ``success_text`` are read
    only under the Monte Carlo mode: the seed and path count come from
    the Monte Carlo panel's own controls, so the bands and the answer
    always describe the same runs.
    """

    mode: RunMode
    rate_text: str
    seed_text: str = ""
    paths_text: str = ""
    success_text: str = ""


@dataclass(frozen=True)
class RetirementAnswer:
    """One solved answer with the inputs that produced it (§4.6).

    ``age`` is the earliest retirement age meeting the target, or
    ``None`` when no age in the searched bracket does. The rest is the
    manifest side: the rate and the employment income it was taken of,
    the derived target income, the searched bracket, and the basis —
    ``seed``, ``paths``, and ``target_success_rate`` are carried only
    for a Monte Carlo basis.
    """

    age: int | None
    replacement_rate: Decimal
    employment_income: Money
    target_income: Money
    minimum_age: int
    maximum_age: int
    mode: RunMode
    seed: int | None = None
    paths: int | None = None
    target_success_rate: Decimal | None = None


@dataclass(frozen=True)
class RetirementPanelViewModel:
    """The "When can I retire?" card (roadmap 9.14; planning §4.7).

    ``rate_value`` and ``success_value`` echo the held answer's actual
    inputs or the defaults before any run. ``success_visible`` shows
    the success-target control only under the Monte Carlo mode.
    ``answer`` is the headline; ``detail`` names the target income and
    the basis; ``message`` carries the no-run or failure copy — blank
    whenever ``answer`` is populated, and vice versa.
    """

    heading: str
    rate_label: str
    rate_value: str
    success_label: str
    success_value: str
    success_visible: bool
    run_label: str
    answer: str
    detail: str
    message: str


@dataclass(frozen=True)
class _SolverInputs:
    """The parsed and derived inputs one search runs on."""

    replacement_rate: Decimal
    employment_income: Money
    target_income: Money
    minimum_age: int
    maximum_age: int
    seed: int | None
    paths: int | None
    target_success_rate: Decimal | None


def state_with_retirement(
    state: PlanState, request: RetirementRequest, *, today: date
) -> PlanState:
    """The state after solving for the earliest retirement age (9.14).

    Anything unusable — no plan, no employment income, an unparseable
    rate or Monte Carlo input, an engine rejection — folds into
    ``retirement_error`` on the returned state, never raising at a
    shell (the :mod:`glidepath.app.plan` rule). Before the search runs
    the whole state is recomputed at the same ``today`` through
    :func:`~glidepath.app.plan.replanned_state` — the answer and the
    projection it summarises must share one anchor date — which also
    drops any held Monte Carlo result rather than leaving one against
    re-anchored charts. Same inputs (and seed, under the Monte Carlo
    basis) reproduce the same answer (§4.6).
    """
    household = state.household
    if household is None:
        return _with_retirement_error(state, RETIREMENT_NO_PLAN_MESSAGE)
    try:
        inputs = _solver_inputs(household, state.assumptions, request, today)
    except ValueError as exc:
        return _with_retirement_error(state, str(exc))
    base = replanned_state(state.assumptions, household, state.scenarios, today=today)
    config = RunConfig(today=today)
    search = RetirementAgeSearch(
        target_income=inputs.target_income,
        minimum_age=inputs.minimum_age,
        maximum_age=inputs.maximum_age,
    )
    if request.mode is RunMode.MONTE_CARLO:
        config = RunConfig(today=today, mode=RunMode.MONTE_CARLO, seed=inputs.seed)
        search = RetirementAgeSearch(
            target_income=inputs.target_income,
            minimum_age=inputs.minimum_age,
            maximum_age=inputs.maximum_age,
            paths=inputs.paths or 1,
            target_success_rate=inputs.target_success_rate or Decimal(1),
        )
    try:
        age = earliest_retirement_age(
            household, state.assumptions, region_for(state.assumptions), config, search
        )
    except ValueError as exc:
        return _with_retirement_error(base, RETIREMENT_FAILED_PREFIX + str(exc))
    answer = RetirementAnswer(
        age=age,
        replacement_rate=inputs.replacement_rate,
        employment_income=inputs.employment_income,
        target_income=inputs.target_income,
        minimum_age=inputs.minimum_age,
        maximum_age=inputs.maximum_age,
        mode=request.mode,
        seed=inputs.seed,
        paths=inputs.paths,
        target_success_rate=inputs.target_success_rate,
    )
    changes: dict[str, Any] = {"retirement": answer, "retirement_error": None}
    return replace(base, **changes) if changes else base


def build_retirement_panel(state: PlanState, mode: RunMode) -> RetirementPanelViewModel:
    """The "When can I retire?" card for the charts screen (9.14).

    A held answer keeps showing under either run mode — its basis is
    named in the detail copy, so switching the mode never mislabels
    it; the mode governs only which controls the next search offers.
    """
    answer = state.retirement
    headline = ""
    detail = ""
    message = ""
    if state.retirement_error is not None:
        message = state.retirement_error
    elif answer is None:
        message = NO_RETIREMENT_MESSAGE
    else:
        headline = _headline(answer)
        detail = _detail(answer)
    return RetirementPanelViewModel(
        heading=RETIREMENT_HEADING,
        rate_label=RETIREMENT_RATE_LABEL,
        rate_value=_rate_echo(answer),
        success_label=RETIREMENT_SUCCESS_LABEL,
        success_value=_success_echo(answer),
        success_visible=mode is RunMode.MONTE_CARLO,
        run_label=FIND_RETIREMENT_AGE_LABEL,
        answer=headline,
        detail=detail,
        message=message,
    )


def _headline(answer: RetirementAnswer) -> str:
    """The card's one-line answer."""
    if answer.age is None:
        return (
            f"No age from {answer.minimum_age} to {answer.maximum_age}"
            " meets the target."
        )
    return f"{RETIREMENT_ANSWER_PREFIX}{answer.age}"


def _detail(answer: RetirementAnswer) -> str:
    """The target income and basis under the headline."""
    target = (
        f"Target income {format_money(answer.target_income)} a year"
        f" — {format_percent(answer.replacement_rate)} of employment income"
        f" {format_money(answer.employment_income)} — searched ages"
        f" {answer.minimum_age} to {answer.maximum_age}."
    )
    basis = RETIREMENT_DETERMINISTIC_BASIS
    if answer.mode is RunMode.MONTE_CARLO and answer.target_success_rate is not None:
        basis = (
            f"Basis: at least {format_percent(answer.target_success_rate)}"
            f" Monte Carlo success over {answer.paths} paths"
            f" (seed {answer.seed})."
        )
    return f"{target}\n{basis}"


def _rate_echo(answer: RetirementAnswer | None) -> str:
    """The held answer's rate as whole percent, or the default."""
    if answer is None:
        return DEFAULT_RETIREMENT_RATE_VALUE
    return str(int(answer.replacement_rate * _HUNDRED))


def _success_echo(answer: RetirementAnswer | None) -> str:
    """The held answer's success target as whole percent, or the default."""
    if answer is None or answer.target_success_rate is None:
        return DEFAULT_RETIREMENT_SUCCESS_VALUE
    return str(int(answer.target_success_rate * _HUNDRED))


def _parsed_percent(text: str, message: str) -> Decimal:
    """A whole-number percentage in [1, 100] as a fraction.

    Raises:
        ValueError: With ``message``, if ``text`` is not a whole
            number from 1 to 100.
    """
    try:
        percent = int(text.strip(), 10)
    except ValueError:
        raise ValueError(message) from None
    if not 1 <= percent <= _HUNDRED:
        raise ValueError(message)
    return Decimal(percent) / _HUNDRED


def _solver_inputs(
    household: Household,
    assumptions: AssumptionSet,
    request: RetirementRequest,
    today: date,
) -> _SolverInputs:
    """Parse a card submission into the inputs one search runs on.

    Raises:
        ValueError: With the user-facing message, on anything unusable
            — no (positive) employment income, an unparseable rate, an
            empty age bracket, or an unusable Monte Carlo seed, path
            count, or success target.
    """
    person = household.persons[0]
    employment = person.employment_income
    if employment is None or employment.value <= _ZERO:
        raise ValueError(RETIREMENT_NO_INCOME_MESSAGE)
    rate = _parsed_percent(request.rate_text, RETIREMENT_RATE_MESSAGE)
    minimum_age = age_on(person.date_of_birth.value, today)
    planning_age = int_assumption_value(
        assumptions.get(AssumptionKey.HORIZON_PLANNING_AGE)
    )
    maximum_age = planning_age - 1
    if maximum_age < minimum_age:
        raise ValueError(RETIREMENT_HORIZON_MESSAGE)
    seed: int | None = None
    paths: int | None = None
    success: Decimal | None = None
    if request.mode is RunMode.MONTE_CARLO:
        try:
            seed = int(request.seed_text.strip(), 10)
        except ValueError:
            raise ValueError(MONTE_CARLO_SEED_MESSAGE) from None
        try:
            paths = int(request.paths_text.strip(), 10)
        except ValueError:
            raise ValueError(MONTE_CARLO_PATHS_MESSAGE) from None
        if not 1 <= paths <= MAX_PATHS:
            raise ValueError(MONTE_CARLO_PATHS_MESSAGE)
        success = _parsed_percent(request.success_text, RETIREMENT_SUCCESS_MESSAGE)
    return _SolverInputs(
        replacement_rate=rate,
        employment_income=employment.value,
        target_income=(employment.value * rate).quantized(),
        minimum_age=minimum_age,
        maximum_age=maximum_age,
        seed=seed,
        paths=paths,
        target_success_rate=success,
    )


def _with_retirement_error(state: PlanState, message: str) -> PlanState:
    """The state carrying a search failure, any held answer dropped."""
    changes: dict[str, Any] = {"retirement": None, "retirement_error": message}
    return replace(state, **changes) if changes else state
