"""The "When can I retire?" solver (roadmap 9.14; planning §5.2).

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

Each probe replaces the (v1 single) person's retirement-age decision
with the candidate and the household's spending plan with the target
income, then runs the plan under the given config. Under a
deterministic config a candidate succeeds when no period's need goes
unmet — the same per-period ``shortfall`` ruin signal the Monte Carlo
metrics read (planning §5.2). Under a seeded Monte Carlo config the
candidate's paths run through :func:`~glidepath.core.run_paths` and
success means their success rate meets the search's target — "earliest
age with ≥ N% Monte Carlo success". Every probe reuses the same config
(common random numbers), so the search is reproducible from the seed
alone (§4.6), and probe plans never leave the search.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from glidepath.core.config import RunMode
from glidepath.core.engine import run
from glidepath.core.money import Money
from glidepath.core.montecarlo import probe_with_spending, run_paths
from glidepath.core.periods import date_age_attained, is_age_attained_by_period_start
from glidepath.core.provenance import AssumptionKey, int_assumption_value

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core.config import RunConfig
    from glidepath.core.entities import Household, Person
    from glidepath.core.provenance import AssumptionSet
    from glidepath.core.region import Region
    from glidepath.core.results import ProjectionResult

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


def earliest_retirement_age(
    plan: Household,
    assumptions: AssumptionSet,
    region: Region,
    config: RunConfig,
    search: RetirementAgeSearch,
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
    its spending reproduces the success.

    Raises:
        EngineError: If a probe is rejected by the engine — including a
            Monte Carlo config without a seed (planning §5.2).
    """
    date_of_birth = plan.persons[0].date_of_birth.value
    periods = tuple(
        region.calendar.periods(config.today, _horizon_end(plan, assumptions, config))
    )

    def has_retired_period(age: int) -> bool:
        """Whether any projected period opens the plan retired (§4.1)."""
        return any(
            is_age_attained_by_period_start(date_of_birth, age, period)
            for period in periods
        )

    def meets(age: int) -> bool:
        """Whether retiring at ``age`` sustains the target income."""
        probe = _with_retirement_age(
            probe_with_spending(plan, search.target_income, config), age
        )
        if config.mode is RunMode.MONTE_CARLO:
            result = run_paths(probe, assumptions, region, config, paths=search.paths)
            return result.success_rate >= search.target_success_rate
        return not _has_shortfall(run(probe, assumptions, region, config))

    for age in range(search.minimum_age, search.maximum_age + 1):
        if has_retired_period(age) and meets(age):
            return age
    return None


def _horizon_end(
    plan: Household, assumptions: AssumptionSet, config: RunConfig
) -> date:
    """The run's horizon end: configured, or the planning-age default.

    The same resolution the engine applies (planning §5.2), computed
    here so the exposure gate can see the periods a probe would
    project. v1 households hold one person (§4.4), whose date of birth
    anchors the default.
    """
    if config.horizon_end is not None:
        return config.horizon_end
    planning_age = int_assumption_value(
        assumptions.get(AssumptionKey.HORIZON_PLANNING_AGE)
    )
    return date_age_attained(plan.persons[0].date_of_birth.value, planning_age)


def _has_shortfall(result: ProjectionResult) -> bool:
    """Whether any period's need went unmet — the §5.2 ruin signal."""
    return any(
        person.shortfall > _ZERO
        for snapshot in result.snapshots
        for person in snapshot.persons
    )


def _with_retirement_age(plan: Household, age: int) -> Household:
    """The plan with every person's retirement-age decision at ``age``.

    v1 households hold one person (§4.4), so this is *the* person's
    decision; the decision's recorded-on metadata carries over, exactly
    as a scenario override resolves (§4.3). Couples activation (9.4)
    will need a per-person target here.
    """
    persons = tuple(_person_at_retirement_age(person, age) for person in plan.persons)
    changes: dict[str, Any] = {"persons": persons}
    return replace(plan, **changes) if changes else plan


def _person_at_retirement_age(person: Person, age: int) -> Person:
    """One person with their retirement-age decision's value replaced."""
    decision_changes: dict[str, Any] = {"value": age}
    decision = replace(person.target_retirement_age, **decision_changes)
    changes: dict[str, Any] = {"target_retirement_age": decision}
    return replace(person, **changes) if changes else person
