"""Measure Decimal Monte Carlo performance (roadmap 7.2's measurement task).

Planning §4.6 accepts that Decimal Monte Carlo is slow and requires the
cost measured — with recorded numbers — before any optimisation is
considered. This script times the two components a Monte Carlo run
(roadmap 7.3) multiplies together:

- the stochastic draw: one ``StochasticReturnModel.returns_for`` call
  (Cholesky + three correlated lognormal draws, all ``Decimal``), and
- the engine pass: one full deterministic ``run()`` of the golden
  persona (60 annual periods through the real UK region), which is the
  per-path step cost the same step function incurs under Monte Carlo.

The projected Monte Carlo wall-clock is then
``paths x (engine pass + periods x draw)``. Record the printed numbers
in docs/planning.md §4.6 whenever they are re-measured.

Run: ``uv run --locked python scripts/measure_mc_performance.py``
"""

import platform
import time
from datetime import UTC, date, datetime
from decimal import Decimal

from glidepath.core import (
    AssumptionReadRecorder,
    ContributionSchedule,
    Decision,
    EntityId,
    Fact,
    Household,
    Money,
    Person,
    ReliefMechanic,
    RunConfig,
    SpendingPlan,
    StochasticReturnModel,
    TrackedAssumptions,
    Wrapper,
    run,
)
from glidepath.core.periods import AnnualCalendar
from glidepath.regions.uk import (
    ISA_KIND,
    RUK_RESIDENCY,
    WORKPLACE_DC_KIND,
    default_assumption_set,
    future_years_extension,
    uk_region,
)

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)

DRAW_CALLS = 5_000
ENGINE_RUNS = 10
PROJECTED_PATHS = (100, 1_000, 10_000)
PERIODS_PER_PATH = 60


def _money_fact(amount: str) -> Fact[Money]:
    """A user-stated monetary fact."""
    return Fact(value=Money(Decimal(amount)), as_of=AS_OF, recorded_on=RECORDED)


def _household() -> Household:
    """The golden persona: DC + ISA accumulator retiring at 60."""
    dc = Wrapper(
        id=EntityId("perf-dc"),
        kind=WORKPLACE_DC_KIND,
        balance=_money_fact("40000"),
        contributions=ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(5000)), recorded_on=RECORDED),
            employer_amount=_money_fact("3000"),
            relief_mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
        ),
    )
    isa = Wrapper(
        id=EntityId("perf-isa"),
        kind=ISA_KIND,
        balance=_money_fact("20000"),
        contributions=ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(5000)), recorded_on=RECORDED),
        ),
    )
    person = Person(
        id=EntityId("perf-person"),
        date_of_birth=Fact(value=date(1991, 2, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        employment_income=_money_fact("50000"),
        wrappers=(dc, isa),
    )
    spending = SpendingPlan(annual_spending_real=_money_fact("30000"))
    return Household(persons=(person,), spending=spending)


def _measure_draw_seconds() -> float:
    """Mean seconds per ``StochasticReturnModel.returns_for`` call."""
    assumptions = default_assumption_set()
    model = StochasticReturnModel(
        assumptions=TrackedAssumptions(
            assumptions=assumptions, recorder=AssumptionReadRecorder()
        ),
        seed=20260803,
    )
    calendar = AnnualCalendar(anchor_month=4, anchor_day=6)
    periods = tuple(calendar.periods(TODAY, date(2046, 4, 5)))
    started = time.perf_counter()
    for call in range(DRAW_CALLS):
        model.returns_for(periods[call % len(periods)], call)
    return (time.perf_counter() - started) / DRAW_CALLS


def _measure_engine_seconds() -> float:
    """Mean seconds per full deterministic golden-persona ``run()``."""
    assumptions = default_assumption_set()
    region = uk_region(future_years_extension(assumptions))
    household = _household()
    config = RunConfig(today=TODAY)
    run(household, assumptions, region, config)  # warm caches before timing
    started = time.perf_counter()
    for _ in range(ENGINE_RUNS):
        run(household, assumptions, region, config)
    return (time.perf_counter() - started) / ENGINE_RUNS


def main() -> None:
    """Time the components and print the projected Monte Carlo cost."""
    print(f"platform: {platform.platform()} / Python {platform.python_version()}")
    draw = _measure_draw_seconds()
    engine = _measure_engine_seconds()
    per_path = engine + PERIODS_PER_PATH * draw
    print(f"stochastic draw ({DRAW_CALLS:,} calls): {draw * 1e6:,.0f} us/call")
    print(
        f"engine pass ({ENGINE_RUNS} runs of {PERIODS_PER_PATH} periods):"
        f" {engine * 1e3:,.1f} ms/run"
    )
    print(f"projected per-path cost: {per_path * 1e3:,.1f} ms")
    for paths in PROJECTED_PATHS:
        print(f"projected {paths:,}-path Monte Carlo: {per_path * paths:,.1f} s")


if __name__ == "__main__":
    main()
