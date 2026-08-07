"""The "How much can I draw down?" card: transition and panel (9.25).

The Phase 9.25 core solver
(:func:`~glidepath.core.sustainable_income_at_age`) surfaced per
planning §4.7: a card on the charts screen asking for a retirement age
(defaulting to the plan's stated retirement-age decision) and
answering with the highest net annual income, in today's money, the
plan sustains when retiring at that age — the drawdown dual of the
"When can I retire?" card (roadmap 9.14). The search runs the plan
once per probed spending level — an explicit user action like the
Monte Carlo run (roadmap 9.13), far too slow for every keystroke —
and its basis follows the screen's run mode: the deterministic
projection, or "at least N% Monte Carlo success" over the panel's
seed and path count once a Monte Carlo basis is selected. The
transition re-anchors the whole state through
:func:`~glidepath.app.plan.replanned_state`, so a held answer can
never go stale against a changed plan.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from glidepath.app.display import format_money, format_percent
from glidepath.app.montecarlo import (
    MAX_PATHS,
    MONTE_CARLO_PATHS_MESSAGE,
    MONTE_CARLO_SEED_MESSAGE,
    path_pool,
)
from glidepath.app.plan import PlanState, region_for, replanned_state
from glidepath.app.retirement import parsed_percent
from glidepath.core import (
    AssumptionKey,
    Money,
    RunConfig,
    RunMode,
    SustainableIncomeSearch,
    age_on,
    int_assumption_value,
    sustainable_income_at_age,
)

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core import AssumptionSet, Household

_ONE = Decimal(1)
_TWO = Decimal(2)

DRAWDOWN_HEADING: Final = "How much can I draw down?"

DRAWDOWN_AGE_LABEL: Final = "Retirement age"

DRAWDOWN_SUCCESS_LABEL: Final = "Success target (%)"

DEFAULT_DRAWDOWN_SUCCESS_VALUE: Final = "90"

FIND_DRAWDOWN_LABEL: Final = "Find sustainable income"

NO_DRAWDOWN_MESSAGE: Final = (
    "No answer yet — choose a retirement age, then press Find sustainable income."
)

DRAWDOWN_NO_PLAN_MESSAGE: Final = (
    "Save facts on the Facts tab before asking how much you can draw down."
)

DRAWDOWN_AGE_MESSAGE: Final = (
    "The retirement age needs a whole number from the current age"
    " to one below the planning horizon age."
)

DRAWDOWN_SUCCESS_MESSAGE: Final = (
    "The success target needs a whole number between 1 and 100."
)

DRAWDOWN_HORIZON_MESSAGE: Final = (
    "No retirement age to test — the planning horizon age is not"
    " beyond the current age."
)

DRAWDOWN_SEARCH_MAXIMUM: Final = Money(Decimal(1_000_000))
"""The income search's upper bracket: £1,000,000 of net annual income.

Generous beyond any plan a personal modelling tool holds, so the
bracket never clips a real answer; the cost of the headroom is only
the scan-plus-bisection's logarithmic probe count
(:func:`search_probe_bound`).
"""

MAX_DRAWDOWN_PATH_RUNS: Final = 20_000
"""The most Monte Carlo path-projections one search may add up to.

The 9.13 per-run path cap bounds one Monte Carlo run; a search
multiplies its path count by the probe bound, so it carries the same
aggregate budget as the retirement-age search (roadmap 9.14).
"""

DRAWDOWN_BUDGET_MESSAGE: Final = (
    "Lower the path count — this search would project more than"
    f" {MAX_DRAWDOWN_PATH_RUNS:,} Monte Carlo paths across its probed"
    " spending levels."
)

DRAWDOWN_FAILED_PREFIX: Final = "The sustainable-income search failed: "

DRAWDOWN_RUNNING_MESSAGE: Final = "Searching for the sustainable income…"

DRAWDOWN_STALE_MESSAGE: Final = (
    "Sustainable-income answer discarded — the plan changed while it ran."
)

DRAWDOWN_ANSWER_PREFIX: Final = "Sustainable income: "

DRAWDOWN_DETERMINISTIC_BASIS: Final = (
    "Basis: deterministic projection, no unmet need in any period."
)


def search_probe_bound(search: SustainableIncomeSearch) -> int:
    """The most spending levels one income search can probe.

    One probe at the maximum, one per interior scan point and one at
    the zero floor, then the bisection's halvings of one scan step
    down to the tolerance — what the Monte Carlo budget check
    multiplies by the path count, mirroring the candidate-age count of
    the retirement-age search (roadmap 9.14).
    """
    probes = 1 + search.scan_steps
    step = search.maximum.amount / Decimal(search.scan_steps)
    while step > search.tolerance.amount:
        step = step / _TWO
        probes += 1
    return probes


@dataclass(frozen=True)
class DrawdownRequest:
    """One card submission, as the shell captured it (planning §4.7).

    ``mode`` is the charts screen's run-mode selection — the answer's
    basis. ``seed_text``, ``paths_text``, and ``success_text`` are read
    only under the Monte Carlo mode: the seed and path count come from
    the Monte Carlo panel's own controls, so the bands and the answer
    always describe the same runs.
    """

    mode: RunMode
    age_text: str
    seed_text: str = ""
    paths_text: str = ""
    success_text: str = ""


@dataclass(frozen=True)
class DrawdownAnswer:
    """One solved answer with the inputs that produced it (§4.6).

    ``income`` is the highest sustainable net annual income in today's
    money, or ``None`` when not even zero spending survives the plan's
    outflows. The rest is the manifest side: the retirement age the
    answer assumed, the searched bracket's upper bound, and the basis
    — ``seed``, ``paths``, and ``target_success_rate`` are carried
    only for a Monte Carlo basis.
    """

    income: Money | None
    age: int
    maximum: Money
    mode: RunMode
    seed: int | None = None
    paths: int | None = None
    target_success_rate: Decimal | None = None


@dataclass(frozen=True)
class DrawdownPanelViewModel:
    """The "How much can I draw down?" card (roadmap 9.25; §4.7).

    ``age_value`` echoes the held answer's actual age, or the plan's
    stated retirement-age decision before any run — blank without a
    plan. ``success_value`` echoes likewise or the default.
    ``success_visible`` shows the success-target control only under
    the Monte Carlo mode. ``answer`` is the headline; ``detail`` names
    the age, the searched bracket, and the basis; ``message`` carries
    the no-run or failure copy — blank whenever ``answer`` is
    populated, and vice versa.
    """

    heading: str
    age_label: str
    age_value: str
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

    age: int
    seed: int | None
    paths: int | None
    target_success_rate: Decimal | None


def state_with_drawdown(
    state: PlanState, request: DrawdownRequest, *, today: date
) -> PlanState:
    """The state after solving for the sustainable income (9.25).

    Anything unusable — no plan, an unparseable age or Monte Carlo
    input, an engine rejection — folds into ``drawdown_error`` on the
    returned state, never raising at a shell (the
    :mod:`glidepath.app.plan` rule). Before the search runs the whole
    state is recomputed at the same ``today`` through
    :func:`~glidepath.app.plan.replanned_state` — the answer and the
    projection it summarises must share one anchor date — which also
    drops any held Monte Carlo result rather than leaving one against
    re-anchored charts. Same inputs (and seed, under the Monte Carlo
    basis) reproduce the same answer (§4.6).
    """
    household = state.household
    if household is None:
        return _with_drawdown_error(state, DRAWDOWN_NO_PLAN_MESSAGE)
    try:
        inputs = _solver_inputs(household, state.assumptions, request, today)
    except ValueError as exc:
        return _with_drawdown_error(state, str(exc))
    base = replanned_state(
        state.assumptions,
        household,
        state.scenarios,
        today=today,
        modified=state.modified,
    )
    try:
        config, search = _config_and_search(inputs, request.mode, today)
        total_paths = search_probe_bound(search) * (inputs.paths or 0)
        with path_pool(total_paths) as parallelism:
            income = sustainable_income_at_age(
                household,
                state.assumptions,
                region_for(state.assumptions),
                config,
                age=inputs.age,
                search=search,
                parallelism=parallelism,
            )
    except Exception as exc:
        # Broad by design, exactly as in state_with_monte_carlo: a
        # process-pool failure escaping here would hold the shell's
        # in-flight guard forever, so every failure folds into the
        # state (§4.7).
        return _with_drawdown_error(base, DRAWDOWN_FAILED_PREFIX + str(exc))
    answer = DrawdownAnswer(
        income=income,
        age=inputs.age,
        maximum=DRAWDOWN_SEARCH_MAXIMUM,
        mode=request.mode,
        seed=inputs.seed,
        paths=inputs.paths,
        target_success_rate=inputs.target_success_rate,
    )
    changes: dict[str, Any] = {"drawdown": answer, "drawdown_error": None}
    return replace(base, **changes) if changes else base


def build_drawdown_panel(state: PlanState, mode: RunMode) -> DrawdownPanelViewModel:
    """The "How much can I draw down?" card for the charts screen (9.25).

    A held answer keeps showing under either run mode — its basis is
    named in the detail copy, so switching the mode never mislabels
    it; the mode governs only which controls the next search offers.
    """
    answer = state.drawdown
    headline = ""
    detail = ""
    message = ""
    if state.drawdown_error is not None:
        message = state.drawdown_error
    elif answer is None:
        message = NO_DRAWDOWN_MESSAGE
    else:
        headline = _headline(answer)
        detail = _detail(answer)
    return DrawdownPanelViewModel(
        heading=DRAWDOWN_HEADING,
        age_label=DRAWDOWN_AGE_LABEL,
        age_value=_age_echo(answer, state.household),
        success_label=DRAWDOWN_SUCCESS_LABEL,
        success_value=_success_echo(answer),
        success_visible=mode is RunMode.MONTE_CARLO,
        run_label=FIND_DRAWDOWN_LABEL,
        answer=headline,
        detail=detail,
        message=message,
    )


def _headline(answer: DrawdownAnswer) -> str:
    """The card's one-line answer."""
    if answer.income is None:
        return (
            f"No income is sustainable retiring at {answer.age} —"
            " the plan's outflows already exhaust it."
        )
    return f"{DRAWDOWN_ANSWER_PREFIX}{format_money(answer.income)} a year"


def _detail(answer: DrawdownAnswer) -> str:
    """The assumed age, searched bracket, and basis under the headline."""
    target = (
        f"Retiring at age {answer.age} — the highest net annual income"
        " the plan sustains, in today's money, searched up to"
        f" {format_money(answer.maximum)}."
    )
    basis = DRAWDOWN_DETERMINISTIC_BASIS
    if answer.mode is RunMode.MONTE_CARLO and answer.target_success_rate is not None:
        basis = (
            f"Basis: at least {format_percent(answer.target_success_rate)}"
            f" Monte Carlo success over {answer.paths} paths"
            f" (seed {answer.seed})."
        )
    return f"{target}\n{basis}"


def _age_echo(answer: DrawdownAnswer | None, household: Household | None) -> str:
    """The held answer's age, the plan's stated decision, or blank."""
    if answer is not None:
        return str(answer.age)
    if household is not None:
        return str(household.persons[0].target_retirement_age.value)
    return ""


def _success_echo(answer: DrawdownAnswer | None) -> str:
    """The held answer's success target as whole percent, or the default."""
    if answer is None or answer.target_success_rate is None:
        return DEFAULT_DRAWDOWN_SUCCESS_VALUE
    return str(int(answer.target_success_rate * Decimal(100)))


def _solver_inputs(
    household: Household,
    assumptions: AssumptionSet,
    request: DrawdownRequest,
    today: date,
) -> _SolverInputs:
    """Parse a card submission into the inputs one search runs on.

    Raises:
        ValueError: With the user-facing message, on anything unusable
            — a planning horizon the person has already reached, an
            unparseable or out-of-bracket age, an unusable Monte Carlo
            seed, path count, or success target, or a Monte Carlo
            search whose probe bound times paths would exceed the
            search budget.
    """
    person = household.persons[0]
    minimum_age = age_on(person.date_of_birth.value, today)
    planning_age = int_assumption_value(
        assumptions.get(AssumptionKey.HORIZON_PLANNING_AGE)
    )
    maximum_age = planning_age - 1
    if maximum_age < minimum_age:
        raise ValueError(DRAWDOWN_HORIZON_MESSAGE)
    try:
        age = int(request.age_text.strip(), 10)
    except ValueError:
        raise ValueError(DRAWDOWN_AGE_MESSAGE) from None
    if not minimum_age <= age <= maximum_age:
        raise ValueError(DRAWDOWN_AGE_MESSAGE)
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
        probes = search_probe_bound(
            SustainableIncomeSearch(maximum=DRAWDOWN_SEARCH_MAXIMUM)
        )
        if probes * paths > MAX_DRAWDOWN_PATH_RUNS:
            raise ValueError(DRAWDOWN_BUDGET_MESSAGE)
        success = parsed_percent(request.success_text, DRAWDOWN_SUCCESS_MESSAGE)
    return _SolverInputs(
        age=age,
        seed=seed,
        paths=paths,
        target_success_rate=success,
    )


def _config_and_search(
    inputs: _SolverInputs, mode: RunMode, today: date
) -> tuple[RunConfig, SustainableIncomeSearch]:
    """The run config and search one parsed submission denotes.

    Built inside the transition's exception boundary, so a search the
    core rejects folds into ``drawdown_error`` like any other failure
    (the :mod:`glidepath.app.plan` rule).
    """
    if mode is RunMode.MONTE_CARLO:
        return (
            RunConfig(today=today, mode=RunMode.MONTE_CARLO, seed=inputs.seed),
            SustainableIncomeSearch(
                maximum=DRAWDOWN_SEARCH_MAXIMUM,
                paths=inputs.paths or 1,
                target_success_rate=inputs.target_success_rate or _ONE,
            ),
        )
    return (
        RunConfig(today=today),
        SustainableIncomeSearch(maximum=DRAWDOWN_SEARCH_MAXIMUM),
    )


def _with_drawdown_error(state: PlanState, message: str) -> PlanState:
    """The state carrying a search failure, any held answer dropped."""
    changes: dict[str, Any] = {"drawdown": None, "drawdown_error": message}
    return replace(state, **changes) if changes else state
