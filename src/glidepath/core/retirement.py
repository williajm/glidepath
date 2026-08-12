"""The retirement-question solvers (roadmap 9.14, 9.25; planning §5.2).

:func:`earliest_retirement_age` finds the earliest target retirement
age at which the plan sustains a target retirement income — the age
counterpart of :func:`~glidepath.core.montecarlo.sustainable_income`
(roadmap 7.3): the same probe-plan-per-candidate search over runs,
searching the retirement-age decision instead of the spending level.
Where the spending search bisects a continuous bracket, the age domain
is a few dozen whole years, so an ascending scan probes every
candidate: the returned age is exactly the earliest succeeding one
even when success is not monotone in age (a DB scheme's early-payment
factors or a dated outflow can make it dip), and every answer was
actually probed, never interpolated. A candidate whose retirement
date falls at or past the run's horizon has no retired period to test
the income in — it fails rather than succeeding vacuously.

Each probe replaces the *selected* person's retirement-age decision
with the candidate — a partner's decision is held fixed (planning
§4.11) — and the household's spending plan with the target income,
then runs the plan under the given config. Under a
deterministic config a candidate succeeds when no period's need goes
unmet — the same per-period ``shortfall`` ruin signal the Monte Carlo
metrics read (planning §5.2). Under a seeded Monte Carlo config the
candidate's paths run through :func:`~glidepath.core.run_paths` and
success means their success rate meets the search's target — "earliest
age with ≥ N% Monte Carlo success". Every probe reuses the same config
(common random numbers), so the search is reproducible from the seed
alone (§4.6), and probe plans never leave the search.

:func:`sustainable_income_at_age` is the same question asked the
other way around (roadmap 9.25): "how much can I draw down if I
retire at this age?" — the selected person's retirement-age decision
is fixed at the chosen age and the spending level is searched,
delegating to the
7.3 income search under the same exposure gate: an age with no
retired period inside the run's horizon has nothing to test the
income in, so it answers ``None`` rather than succeeding vacuously.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from glidepath.core.config import RunMode
from glidepath.core.engine import run
from glidepath.core.money import Money
from glidepath.core.montecarlo import (
    has_shortfall,
    probe_with_spending,
    run_paths,
    sustainable_income,
)
from glidepath.core.periods import date_age_attained, is_age_attained_by_period_start
from glidepath.core.provenance import AssumptionKey, int_assumption_value

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core.config import RunConfig
    from glidepath.core.entities import EntityId, Household, Person
    from glidepath.core.montecarlo import PathParallelism, SustainableIncomeSearch
    from glidepath.core.periods import Period
    from glidepath.core.provenance import AssumptionSet
    from glidepath.core.region import Region

_ZERO = Money(Decimal(0))
_ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class RetirementAgeSearch:
    """The parameters of one earliest-retirement-age search (9.14).

    ``target_income`` is the real (today's money) net annual retirement
    income the plan must sustain — the replacement-rate target the app
    layer derives from employment income. Candidate ages run from
    ``minimum_age`` to ``maximum_age`` inclusive; a candidate at or
    below the person's current age simply retires the plan from its
    first period (the engine's §4.1 gate convention), so "retire now"
    is an ordinary probe. ``paths`` and ``target_success_rate`` apply
    only under a Monte Carlo config: a candidate succeeds when at least
    the target fraction of its seeded paths avoid ruin. A deterministic
    probe ignores them — success is one run with no unmet need, the
    single-path equivalent of a 100% target.
    """

    target_income: Money
    minimum_age: int
    maximum_age: int
    paths: int = 1
    target_success_rate: Decimal = _ONE
    person_id: EntityId | None = None
    """Whose retirement age the search varies (planning §4.11).

    ``None`` selects the household's first person — the only person of
    a single household. A partner's retirement-age decision is held
    fixed through every probe.
    """

    def __post_init__(self) -> None:
        """Reject an empty target, a backwards bracket, or off-range knobs."""
        if self.target_income <= _ZERO:
            msg = "target_income must be positive"
            raise ValueError(msg)
        if self.minimum_age < 0:
            msg = f"minimum_age must be non-negative, got {self.minimum_age}"
            raise ValueError(msg)
        if self.maximum_age < self.minimum_age:
            msg = (
                f"maximum_age {self.maximum_age} precedes"
                f" minimum_age {self.minimum_age}"
            )
            raise ValueError(msg)
        if self.paths < 1:
            msg = f"paths must be positive, got {self.paths}"
            raise ValueError(msg)
        if not Decimal(0) < self.target_success_rate <= _ONE:
            msg = (
                "target_success_rate must lie in (0, 1],"
                f" got {self.target_success_rate}"
            )
            raise ValueError(msg)


# The §5.2 engine quartet plus keyword-only run knobs; bundling would
# obscure the run(plan, assumptions, region, config) shape.
def earliest_retirement_age(  # noqa: PLR0913
    plan: Household,
    assumptions: AssumptionSet,
    region: Region,
    config: RunConfig,
    search: RetirementAgeSearch,
    *,
    parallelism: PathParallelism | None = None,
) -> int | None:
    """The earliest retirement age sustaining the target income (9.14).

    Probes every age in the search bracket in ascending order and
    returns the first that succeeds — exactly the earliest, whatever
    the success shape over ages (module docstring) — or ``None`` when
    no age in the bracket does. A candidate with no *retirement
    exposure* — no projected period opening the plan retired under the
    §4.1 gate convention, because its retirement date falls at or past
    the run's horizon — never tests the target income at all, so it
    fails rather than succeeding vacuously; such candidates are never
    probed. The plan's stated retirement age and spending level are
    irrelevant to the search: each probe carries the candidate age and
    the target income instead, everything else unchanged, and
    re-running the plan at the returned age with the target income as
    its spending reproduces the success. ``parallelism`` spreads each
    Monte Carlo candidate's paths over its executor
    (:class:`~glidepath.core.montecarlo.PathParallelism` — results
    identical to a serial search); pass one executor for the whole
    search so candidates share it rather than paying process startup
    per age.

    Raises:
        ValueError: If the search selects a person not in the plan.
        EngineError: If a probe is rejected by the engine — including a
            Monte Carlo config without a seed (planning §5.2).
    """
    selected = _selected_person(plan, search.person_id)
    date_of_birth = selected.date_of_birth.value
    periods = _projected_periods(plan, assumptions, region, config)

    def has_retired_period(age: int) -> bool:
        """Whether any projected period opens the plan retired (§4.1)."""
        return any(
            is_age_attained_by_period_start(date_of_birth, age, period)
            for period in periods
        )

    def meets(age: int) -> bool:
        """Whether retiring at ``age`` sustains the target income."""
        probe = _with_retirement_age(
            probe_with_spending(plan, search.target_income, config), age, selected.id
        )
        if config.mode is RunMode.MONTE_CARLO:
            result = run_paths(
                probe,
                assumptions,
                region,
                config,
                paths=search.paths,
                parallelism=parallelism,
            )
            return result.success_rate >= search.target_success_rate
        return not has_shortfall(run(probe, assumptions, region, config))

    for age in range(search.minimum_age, search.maximum_age + 1):
        if has_retired_period(age) and meets(age):
            return age
    return None


# Mirrors the earliest_retirement_age signature above.
def sustainable_income_at_age(  # noqa: PLR0913
    plan: Household,
    assumptions: AssumptionSet,
    region: Region,
    config: RunConfig,
    *,
    age: int,
    search: SustainableIncomeSearch,
    person_id: EntityId | None = None,
    parallelism: PathParallelism | None = None,
) -> Money | None:
    """The highest income sustainable when retiring at ``age`` (9.25).

    The drawdown dual of :func:`earliest_retirement_age`: the selected
    person's retirement-age decision is replaced with ``age`` — a
    partner's decision is held fixed (planning §4.11); ``person_id``
    ``None`` selects the household's first person — and the spending
    level is searched through
    :func:`~glidepath.core.montecarlo.sustainable_income` — the same
    scan-plus-bisection over the search's bracket, the same success
    reading per probe (no unmet need under a deterministic config;
    "success rate ≥ target" over the seeded paths under a Monte Carlo
    one), and the same reproducibility: every probe reuses ``config``
    unchanged, so the answer is reproducible from the recorded inputs
    (and seed) alone (§4.6). The plan's stated retirement age and
    spending level are both irrelevant — the probes carry the chosen
    age and the candidate spending instead, everything else unchanged.

    An ``age`` with no *retirement exposure* — no projected period
    opening the plan retired under the §4.1 gate convention, because
    its retirement date falls at or past the run's horizon — has no
    retired period to test any income in: spending is modelled only in
    retirement, so every level would succeed vacuously. It answers
    ``None`` without probing, exactly as such candidates fail in the
    age search. ``None`` otherwise means what the income search means
    by it: not even zero spending survives the plan's outflows.

    Raises:
        ValueError: If ``age`` is negative, or ``person_id`` selects a
            person not in the plan.
        EngineError: If a probe is rejected by the engine — including
            a Monte Carlo config without a seed (planning §5.2).
    """
    if age < 0:
        msg = f"age must be non-negative, got {age}"
        raise ValueError(msg)
    selected = _selected_person(plan, person_id)
    date_of_birth = selected.date_of_birth.value
    exposed = any(
        is_age_attained_by_period_start(date_of_birth, age, period)
        for period in _projected_periods(plan, assumptions, region, config)
    )
    if not exposed:
        return None
    return sustainable_income(
        _with_retirement_age(plan, age, selected.id),
        assumptions,
        region,
        config,
        search,
        parallelism=parallelism,
    )


def _projected_periods(
    plan: Household, assumptions: AssumptionSet, region: Region, config: RunConfig
) -> tuple[Period, ...]:
    """The periods a probe under ``config`` would project (§5.2).

    What the solvers' exposure gates scan: the run's own calendar over
    its own horizon, computed exactly as the engine would.
    """
    return tuple(
        region.calendar.periods(config.today, _horizon_end(plan, assumptions, config))
    )


def _horizon_end(
    plan: Household, assumptions: AssumptionSet, config: RunConfig
) -> date:
    """The run's horizon end: configured, or the planning-age default.

    The same resolution the engine applies (planning §5.2), computed
    here so the exposure gate can see the periods a probe would
    project: the latest of the persons' planning-age dates — one
    household end (planning §4.11).
    """
    if config.horizon_end is not None:
        return config.horizon_end
    planning_age = int_assumption_value(
        assumptions.get(AssumptionKey.HORIZON_PLANNING_AGE)
    )
    return max(
        date_age_attained(person.date_of_birth.value, planning_age)
        for person in plan.persons
    )


def _selected_person(plan: Household, person_id: EntityId | None) -> Person:
    """The person a search varies: by id, or the household's first.

    Raises:
        ValueError: If ``person_id`` names nobody in the plan.
    """
    if person_id is None:
        return plan.persons[0]
    for person in plan.persons:
        if person.id == person_id:
            return person
    msg = f"person {person_id} is not in the household"
    raise ValueError(msg)


def _with_retirement_age(plan: Household, age: int, person_id: EntityId) -> Household:
    """The plan with one person's retirement-age decision at ``age``.

    Only the selected person's decision moves — a partner's is held
    fixed (planning §4.11); the decision's recorded-on metadata carries
    over, exactly as a scenario override resolves (§4.3).
    """
    persons = tuple(
        _person_at_retirement_age(person, age) if person.id == person_id else person
        for person in plan.persons
    )
    changes: dict[str, Any] = {"persons": persons}
    return replace(plan, **changes) if changes else plan


def _person_at_retirement_age(person: Person, age: int) -> Person:
    """One person with their retirement-age decision's value replaced."""
    decision_changes: dict[str, Any] = {"value": age}
    decision = replace(person.target_retirement_age, **decision_changes)
    changes: dict[str, Any] = {"target_retirement_age": decision}
    return replace(person, **changes) if changes else person
