"""The retirement outlook card: the headline forecast panel (9.27).

A held Monte Carlo run summarised as plain sentences (planning §4.7):
the likely range of the household's pots at retirement in today's
money — the middle half of paths, with the 1-in-20 tails spelled out
rather than hidden — the pension slice an annuity could be bought
with, that annuity's yearly income at the shipped level single-life
rates, and the State Pension forecast stacked on top from its own
start age. Everything is read from results the state already holds
(the base projection for the period grid and deflators, the Monte
Carlo outcomes for the percentile bands), so the card needs no run of
its own and can never disagree with the fan chart beside it.

Pot values are read at the tax-year end immediately before the
retirement age is attained — the last close before withdrawals can
begin. A person already at or past their target retirement age reads
the first projected period's close instead, phrased as "by the end of
this tax year".
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from glidepath.core import (
    AnnuityRateTable,
    AnnuityType,
    AssumptionKey,
    EngineError,
    Money,
    age_on,
    date_age_attained,
    decimal_assumption_value,
)
from glidepath.regions.uk import UkAgeError, UkStatePensionError, UkStatePensionScheme

if TYPE_CHECKING:
    from glidepath.app.plan import PlanState
    from glidepath.core import (
        AssumptionSet,
        MonteCarloResult,
        PeriodSnapshot,
        Person,
        ProjectionResult,
    )

_ONE = Decimal(1)
_ZERO_MONEY = Money(Decimal(0))

OUTLOOK_HEADING: Final = "Retirement outlook"

OUTLOOK_NO_PLAN_MESSAGE: Final = (
    "Save facts on the Facts tab before viewing the retirement outlook."
)

NO_OUTLOOK_MESSAGE: Final = (
    "Run Monte Carlo to see the retirement outlook — the likely range of"
    " your pots at retirement, and the yearly income they could buy."
)

_PERCENTILES: Final = (
    Decimal(5),
    Decimal(25),
    Decimal(50),
    Decimal(75),
    Decimal(95),
)
"""The five order statistics the card reads: the 1-in-20 tails, the
likely (middle-half) bounds, and the median headline."""


@dataclass(frozen=True)
class OutlookPanelViewModel:
    """The retirement outlook card (roadmap 9.27; planning §4.7).

    ``answer`` is the headline pot sentence; ``detail`` carries the
    supporting sentences (tails, pension slice, annuity income, State
    Pension, basis), one per line. ``message`` carries the no-plan or
    no-run copy — blank whenever ``answer`` is populated, and vice
    versa.
    """

    heading: str
    answer: str
    detail: str
    message: str


@dataclass(frozen=True)
class _Reading:
    """Where on the period grid the card reads the pots.

    ``index`` is the snapshot whose closing balances are quoted;
    ``age`` is the retirement age the copy names, or ``None`` for a
    person already at or past it — the "by the end of this tax year"
    phrasing.
    """

    index: int
    age: int | None


def build_outlook_panel(state: PlanState) -> OutlookPanelViewModel:
    """The retirement outlook card for the charts screen (9.27).

    Purely a view over held results: without a plan or a held Monte
    Carlo run the card explains what to do, and a held run whose
    periods no longer align with the base projection reads as no run
    — exactly the fan chart's staleness rule (roadmap 9.24).
    """
    household = state.household
    if household is None:
        return _message_panel(OUTLOOK_NO_PLAN_MESSAGE)
    result = state.result
    monte_carlo = state.monte_carlo
    if result is None or monte_carlo is None:
        return _message_panel(NO_OUTLOOK_MESSAGE)
    person = household.persons[0]
    reading = _retirement_reading(result, person)
    try:
        totals = monte_carlo.balance_percentiles(_PERCENTILES)
        pensions = monte_carlo.pension_balance_percentiles(_PERCENTILES)
    except ValueError:
        return _message_panel(NO_OUTLOOK_MESSAGE)
    periods = len(result.snapshots)
    if reading is None or len(totals[0]) != periods or len(pensions[0]) != periods:
        return _message_panel(NO_OUTLOOK_MESSAGE)
    snapshot = result.snapshots[reading.index]
    deflator = _balance_deflator(snapshot)
    total_row = _deflated(totals, reading.index, deflator)
    pension_row = _deflated(pensions, reading.index, deflator)
    lines = [_tails_sentence(total_row)]
    lines.extend(
        _income_sentences(state.assumptions, person, snapshot, reading, pension_row)
    )
    lines.append(_basis_sentence(monte_carlo))
    return OutlookPanelViewModel(
        heading=OUTLOOK_HEADING,
        answer=_pot_sentence(reading, total_row),
        detail="\n".join(lines),
        message="",
    )


def _message_panel(message: str) -> OutlookPanelViewModel:
    """A card carrying only the no-plan or no-run copy."""
    return OutlookPanelViewModel(
        heading=OUTLOOK_HEADING, answer="", detail="", message=message
    )


def _retirement_reading(result: ProjectionResult, person: Person) -> _Reading | None:
    """The period the pots are read at, or ``None`` off the grid.

    The close immediately before the period in which the target
    retirement age is attained — the last balance withdrawals cannot
    yet have touched. Retirement inside the first projected period
    reads that period's own close; a retirement age already attained
    reads the first close too, with the age dropped from the copy
    (module docstring). ``None`` only when the projection ends before
    the retirement age — a horizon shorter than the plan's own
    decisions, which the engine normally refuses upstream.
    """
    retirement_age = person.target_retirement_age.value
    attained = date_age_attained(person.date_of_birth.value, retirement_age)
    snapshots = result.snapshots
    if attained <= snapshots[0].period.start:
        return _Reading(index=0, age=None)
    for position, snapshot in enumerate(snapshots):
        if snapshot.period.contains(attained):
            return _Reading(index=max(position - 1, 0), age=retirement_age)
    return None


def _balance_deflator(snapshot: PeriodSnapshot) -> Decimal:
    """The price level at the snapshot's modelled end (planning §5.2).

    The closing-balance deflator of the core reporting layer: the
    cumulative period-start factor carried forward through the
    period's own CPI, so a closing balance presents in today's money.
    CPI is deterministic across paths (the single-inflation-truth
    rule), so the base projection's deflator presents the Monte Carlo
    percentiles too.
    """
    return snapshot.inflation_factor * (
        _ONE + snapshot.returns.cpi.value * snapshot.year_fraction
    )


def _deflated(
    rows: tuple[tuple[Money, ...], ...], index: int, deflator: Decimal
) -> tuple[Money, ...]:
    """One period's percentile column, presented in today's money."""
    return tuple(Money(row[index].amount / deflator) for row in rows)


def _pounds(value: Money) -> str:
    """A projected amount as whole pounds, e.g. ``£928,513``.

    Percentiles of simulated paths carry no meaningful pence; quoting
    them would read as precision the model does not have.
    """
    return f"£{value.amount:,.0f}"


def _pot_sentence(reading: _Reading, totals: tuple[Money, ...]) -> str:
    """The headline: the median pot with its likely range."""
    _, p25, p50, p75, _ = totals
    anchor = (
        "By the end of this tax year"
        if reading.age is None
        else f"At age {reading.age}"
    )
    return (
        f"{anchor}, your pots could be worth around {_pounds(p50)} in"
        f" today's money — likely between {_pounds(p25)} and {_pounds(p75)}."
    )


def _tails_sentence(totals: tuple[Money, ...]) -> str:
    """The 1-in-20 tails, stated rather than hidden (planning §1)."""
    p5, _, _, _, p95 = totals
    return (
        f"There is roughly a 1-in-20 chance of less than {_pounds(p5)},"
        f" and the same of more than {_pounds(p95)}."
    )


def _income_sentences(
    assumptions: AssumptionSet,
    person: Person,
    snapshot: PeriodSnapshot,
    reading: _Reading,
    pensions: tuple[Money, ...],
) -> list[str]:
    """The pension-slice, annuity, and State Pension sentences.

    Each sentence appears only when it has something true to say: the
    pension slice only when other savings sit alongside it, the
    annuity only when there is pension money to convert and the
    shipped rate table covers the purchase age, the State Pension only
    when the person holds a usable forecast.
    """
    _, p25, p50, p75, _ = pensions
    lines: list[str] = []
    if p50 <= _ZERO_MONEY:
        annuity_income = None
    else:
        if any(not wrapper.pension for wrapper in snapshot.persons[0].wrappers):
            lines.append(
                "Pensions alone — the money an annuity could be bought with —"
                f" could hold around {_pounds(p50)}, likely between"
                f" {_pounds(p25)} and {_pounds(p75)}."
            )
        purchase_age = (
            reading.age
            if reading.age is not None
            else age_on(person.date_of_birth.value, snapshot.period.end)
        )
        annuity_income = _annuity_income(assumptions, p50, purchase_age)
        if annuity_income is not None:
            lines.append(
                f"As a level single-life annuity bought at {purchase_age}, the"
                f" middle pension pot would pay about {_pounds(annuity_income)}"
                " a year before tax."
            )
    state_pension = _state_pension_sentence(person, annuity_income)
    if state_pension is not None:
        lines.append(state_pension)
    return lines


def _annuity_income(assumptions: AssumptionSet, pot: Money, age: int) -> Money | None:
    """The yearly income ``pot`` buys at the shipped level rates.

    The single-life-at-65 base rate scaled by the age-adjustment
    table, exactly as the engine prices a purchase (roadmap 5.5);
    ``None`` when the shipped table does not cover ``age`` — the
    model does not extrapolate (planning §5.3).
    """
    try:
        table = AnnuityRateTable.from_assumption_value(
            assumptions.get(AssumptionKey.ANNUITY_AGE_ADJUSTMENT).value
        )
        rate = decimal_assumption_value(
            assumptions.get(AssumptionKey.ANNUITY_LEVEL_SINGLE_65)
        ) * table.age_multiplier(AnnuityType.LEVEL, age)
    except EngineError:
        return None
    return Money(pot.amount * rate)


def _state_pension_sentence(person: Person, annuity_income: Money | None) -> str | None:
    """The State Pension stacked on top, or ``None`` without a forecast.

    The stated DWP forecast (deferral-uplifted, protected payment
    included) is already today's money — the §5.1 rule that the
    official forecast is the fact — so it adds directly onto the
    annuity's today's-money income.
    """
    record = person.state_pension
    if record is None:
        return None
    try:
        entitlement = UkStatePensionScheme.from_shipped_data().entitlement(
            record, person.date_of_birth.value
        )
    except UkAgeError, UkStatePensionError:
        return None
    annual = (entitlement.annual_amount + entitlement.cpi_uprated_annual_amount) * (
        _ONE + entitlement.deferral_uplift
    )
    start_age = age_on(person.date_of_birth.value, entitlement.start_date)
    if annuity_income is None:
        return (
            f"Your State Pension forecast adds {_pounds(annual)} a year"
            f" from age {start_age}."
        )
    combined = annuity_income + annual
    return (
        f"Your State Pension forecast adds {_pounds(annual)} a year from"
        f" age {start_age} — around {_pounds(combined)} a year all told."
    )


def _basis_sentence(monte_carlo: MonteCarloResult) -> str:
    """The manifest side: which run the card summarises (§4.6)."""
    return (
        f"Based on the Monte Carlo run's {monte_carlo.path_count:,} paths"
        f" (seed {monte_carlo.config.seed}). The likely range spans the"
        " middle half of paths (25th to 75th percentiles); all figures"
        " are in today's money."
    )


__all__ = [
    "NO_OUTLOOK_MESSAGE",
    "OUTLOOK_HEADING",
    "OUTLOOK_NO_PLAN_MESSAGE",
    "OutlookPanelViewModel",
    "build_outlook_panel",
]
