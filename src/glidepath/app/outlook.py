"""The retirement outlook card: the headline forecast panel (9.27).

A held Monte Carlo run summarised as plain sentences (planning §4.7):
the likely range of the household's pots at retirement in today's
money — the middle half of paths, with the 1-in-20 tails spelled out
rather than hidden — the pension slice an annuity could be bought
with, what a whole-pot purchase would deliver under the engine's own
5.5 conventions (the tax-free cash paid out first, headroom-capped,
with the remainder buying income at the shipped level single-life
rates), and the State Pension forecast stacked on top from its
month-precise start age, quoted as the run opened it (the §4.8
roll-forward of a stale forecast). Everything is read from results
the state already holds (the base projection for the period grid,
deflators, sub-balance split, and roll-forward disclosures, the
Monte Carlo outcomes for the percentile bands), so the card needs no
run of its own and can never disagree with the fan chart beside it.

Pot values are read at the tax-year end immediately before the
household is fully retired — the last close before withdrawals can
begin (household spending starts once every person has retired,
planning §4.11), which for a couple is the later of the two target
retirement dates. A household already at or past that point on the
run date reads the first projected period's close instead, phrased
as "by the end of this tax year". The card speaks at household level
throughout (roadmap 9.31): pots are the household's pots, the annuity
quote buys each person's pension slice at their own age, and each
person's State Pension forecast stacks on separately.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from glidepath.app.plan import region_for
from glidepath.core import (
    AnnuityRateTable,
    AnnuityType,
    AssumptionKey,
    EngineError,
    Money,
    age_on,
    date_age_attained,
    decimal_assumption_value,
    whole_months_between,
)
from glidepath.regions.uk import UkAgeError, UkStatePensionError, UkStatePensionScheme

if TYPE_CHECKING:
    from datetime import date

    from glidepath.app.plan import PlanState
    from glidepath.core import (
        AssumptionSet,
        EntityId,
        MonteCarloResult,
        PeriodSnapshot,
        Person,
        PersonPeriodResult,
        ProjectionResult,
        RunProvenance,
    )

_ONE = Decimal(1)
_ZERO_MONEY = Money(Decimal(0))
_WEEKS_PER_YEAR = Decimal(52)
_MONTHS_PER_YEAR = 12

OUTLOOK_HEADING: Final = "Retirement outlook"

OUTLOOK_NO_PLAN_MESSAGE: Final = (
    "Save facts on the Facts tab before viewing the retirement outlook."
)

NO_OUTLOOK_MESSAGE: Final = (
    "Run Monte Carlo to see the retirement outlook — the likely range of"
    " your pots at retirement, and the yearly income they could buy."
)

DETERMINISTIC_BASIS_SENTENCE: Final = (
    "Based on the single deterministic projection at the assumed average"
    " returns; all figures are in today's money. Run Monte Carlo to see"
    " the likely range and the 1-in-20 tails."
)
"""The basis line of the pre-Monte-Carlo outlook (roadmap 10.4)."""

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
    ``ages`` are each person's age at the reading (household order) the
    copy names, or ``None`` for a household already fully retired —
    the "by the end of this tax year" phrasing.
    """

    index: int
    ages: tuple[int, ...] | None


@dataclass(frozen=True)
class _CardContext:
    """What the income sentences read, bundled once per build.

    All of it comes from state the panel already holds: the effective
    assumptions, the base run's provenance (for the §4.8 roll-forward
    disclosures), the household's persons, and the reading snapshot
    the pots are quoted at.
    """

    assumptions: AssumptionSet
    provenance: RunProvenance
    persons: tuple[Person, ...]
    snapshot: PeriodSnapshot
    reading: _Reading


def build_outlook_panel(state: PlanState) -> OutlookPanelViewModel:
    """The retirement outlook card for the charts screen (9.27, 10.4).

    Purely a view over held results: without a plan the card explains
    what to do; with a base projection but no held Monte Carlo run —
    or a held run whose periods no longer align with the base
    projection, the fan chart's staleness rule (roadmap 9.24) — it
    summarises the single deterministic path and invites the Monte
    Carlo run that adds the likely range.
    """
    household = state.household
    if household is None:
        return _message_panel(OUTLOOK_NO_PLAN_MESSAGE)
    result = state.result
    if result is None:
        return _message_panel(NO_OUTLOOK_MESSAGE)
    reading = _retirement_reading(result, household.persons)
    if reading is None:
        return _message_panel(NO_OUTLOOK_MESSAGE)
    context = _CardContext(
        assumptions=state.assumptions,
        provenance=result.provenance,
        persons=household.persons,
        snapshot=result.snapshots[reading.index],
        reading=reading,
    )
    monte_carlo = state.monte_carlo
    if monte_carlo is not None:
        panel = _monte_carlo_panel(context, result, monte_carlo)
        if panel is not None:
            return panel
    return _deterministic_panel(context)


def _monte_carlo_panel(
    context: _CardContext, result: ProjectionResult, monte_carlo: MonteCarloResult
) -> OutlookPanelViewModel | None:
    """The percentile-band outlook, or ``None`` for a stale held run."""
    try:
        totals = monte_carlo.balance_percentiles(_PERCENTILES)
        pensions = monte_carlo.pension_balance_percentiles(_PERCENTILES)
    except ValueError:
        return None
    periods = len(result.snapshots)
    if len(totals[0]) != periods or len(pensions[0]) != periods:
        return None
    reading = context.reading
    deflator = _balance_deflator(context.snapshot)
    total_row = _deflated(totals, reading.index, deflator)
    pension_row = _deflated(pensions, reading.index, deflator)
    lines = [_tails_sentence(total_row)]
    lines.extend(_income_sentences(context, pension_row))
    lines.append(_basis_sentence(monte_carlo))
    return OutlookPanelViewModel(
        heading=OUTLOOK_HEADING,
        answer=_pot_sentence(reading, total_row),
        detail="\n".join(lines),
        message="",
    )


def _deterministic_panel(context: _CardContext) -> OutlookPanelViewModel:
    """The single-path outlook shown before Monte Carlo runs (10.4).

    The same reading, deflator, annuity quote, and State Pension
    machinery as the percentile card, fed the base projection's own
    closing balances — so the figures agree exactly with the balance
    chart, and the basis line says what a Monte Carlo run would add.
    """
    deflator = _balance_deflator(context.snapshot)
    total = _ZERO_MONEY
    pensions = _ZERO_MONEY
    for person in context.snapshot.persons:
        pensions = pensions + _pension_closing_balance(person)
        for wrapper in person.wrappers:
            total = total + wrapper.closing_balance
    total_today = Money(total.amount / deflator)
    pension_today = Money(pensions.amount / deflator)
    lines = _deterministic_income_sentences(context, pension_today)
    lines.append(DETERMINISTIC_BASIS_SENTENCE)
    return OutlookPanelViewModel(
        heading=OUTLOOK_HEADING,
        answer=_deterministic_pot_sentence(context.reading, total_today),
        detail="\n".join(lines),
        message="",
    )


def _message_panel(message: str) -> OutlookPanelViewModel:
    """A card carrying only the no-plan or no-run copy."""
    return OutlookPanelViewModel(
        heading=OUTLOOK_HEADING, answer="", detail="", message=message
    )


def _retirement_reading(
    result: ProjectionResult, persons: tuple[Person, ...]
) -> _Reading | None:
    """The period the pots are read at, or ``None`` off the grid.

    The close immediately before the period in which the household is
    fully retired — the last balance withdrawals cannot yet have
    touched (spending starts once every person has retired, §4.11),
    so a couple reads at the *later* target retirement date, and the
    copy names each person's age at that moment. Retirement inside
    the first projected period reads that period's own close; a
    household already fully retired on the run's ``today`` (not the
    first period's start — the first tax year opens before the run
    date, so a birthday between the two is already past) reads the
    first close too, with the ages dropped from the copy (module
    docstring). ``None`` only when the projection ends before the
    retirement date — a horizon shorter than the plan's own
    decisions, which the engine normally refuses upstream.
    """
    attained = max(
        date_age_attained(
            person.date_of_birth.value, person.target_retirement_age.value
        )
        for person in persons
    )
    snapshots = result.snapshots
    if attained <= result.config.today:
        return _Reading(index=0, ages=None)
    for position, snapshot in enumerate(snapshots):
        if snapshot.period.contains(attained):
            ages = tuple(
                age_on(person.date_of_birth.value, attained) for person in persons
            )
            return _Reading(index=max(position - 1, 0), ages=ages)
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


def _ages_phrase(ages: tuple[int, ...]) -> str:
    """The reading's ages as copy: ``age 62``, or ``ages 65 and 60``."""
    if len(ages) == 1:
        return f"age {ages[0]}"
    return f"ages {ages[0]} and {ages[1]}"


def _anchor_phrase(reading: _Reading) -> str:
    """The reading's opening phrase: the ages, or this tax year."""
    if reading.ages is None:
        return "By the end of this tax year"
    return f"At {_ages_phrase(reading.ages)}"


def _pot_sentence(reading: _Reading, totals: tuple[Money, ...]) -> str:
    """The headline: the median pot with its likely range."""
    _, p25, p50, p75, _ = totals
    return (
        f"{_anchor_phrase(reading)}, your pots could be worth around"
        f" {_pounds(p50)} in today's money — likely between {_pounds(p25)}"
        f" and {_pounds(p75)}."
    )


def _deterministic_pot_sentence(reading: _Reading, total: Money) -> str:
    """The single-path headline: one figure, no range (10.4)."""
    return (
        f"{_anchor_phrase(reading)}, your pots are on course to be worth"
        f" around {_pounds(total)} in today's money."
    )


def _tails_sentence(totals: tuple[Money, ...]) -> str:
    """The 1-in-20 tails, stated rather than hidden (planning §1)."""
    p5, _, _, _, p95 = totals
    return (
        f"There is roughly a 1-in-20 chance of less than {_pounds(p5)},"
        f" and the same of more than {_pounds(p95)}."
    )


def _income_sentences(context: _CardContext, pensions: tuple[Money, ...]) -> list[str]:
    """The pension-slice, annuity, and State Pension sentences.

    Each sentence appears only when it has something true to say: the
    pension slice only when other savings sit alongside it, the
    annuity only when there is pension money to convert and the
    shipped rate table covers every purchase age, a State Pension only
    for a person holding a usable forecast. The annuity quote follows
    the engine's whole-pot purchase per person: each person's slice of
    the household pension pot pays its tax-free cash out first
    (:func:`_tax_free_cash`) and only the remainder buys income at
    that person's own age.
    """
    _, p25, p50, p75, _ = pensions
    lines: list[str] = []
    quote = None
    if p50 > _ZERO_MONEY:
        if any(
            not wrapper.pension
            for person in context.snapshot.persons
            for wrapper in person.wrappers
        ):
            lines.append(
                "Pensions alone — the money an annuity could be bought with —"
                f" could hold around {_pounds(p50)}, likely between"
                f" {_pounds(p25)} and {_pounds(p75)}."
            )
        quote = _annuity_quote(context, p50)
        if quote is not None:
            lines.append(_annuity_sentence(quote))
    annuity_income = None if quote is None else quote.income
    lines.extend(_state_pension_lines(context, annuity_income))
    return lines


def _deterministic_income_sentences(context: _CardContext, pension: Money) -> list[str]:
    """The single-path pension, annuity, and State Pension lines (10.4).

    The same appearance rules as :func:`_income_sentences`, fed one
    figure instead of a percentile row: the pension slice only when
    other savings sit alongside it, the annuity quote only when there
    is pension money the shipped rate table can price.
    """
    lines: list[str] = []
    quote = None
    if pension > _ZERO_MONEY:
        if any(
            not wrapper.pension
            for person in context.snapshot.persons
            for wrapper in person.wrappers
        ):
            lines.append(
                "Pensions alone — the money an annuity could be bought with —"
                f" are on course to hold around {_pounds(pension)}."
            )
        quote = _annuity_quote(context, pension)
        if quote is not None:
            lines.append(_annuity_sentence(quote))
    annuity_income = None if quote is None else quote.income
    lines.extend(_state_pension_lines(context, annuity_income))
    return lines


@dataclass(frozen=True)
class _AnnuityQuote:
    """A whole-pot annuity purchase: income, up-front cash, and ages."""

    income: Money
    tax_free: Money
    ages: tuple[int, ...]


def _annuity_sentence(quote: _AnnuityQuote) -> str:
    """The annuity quote as copy, single or couple phrasing."""
    if len(quote.ages) == 1:
        product = f"a level single-life annuity bought at {quote.ages[0]}"
    else:
        product = f"level single-life annuities bought at {_ages_phrase(quote.ages)}"
    sentence = (
        f"As {product}, the middle pension pot would pay about"
        f" {_pounds(quote.income)} a year before tax"
    )
    if quote.tax_free > _ZERO_MONEY:
        return (
            f"{sentence}, alongside around {_pounds(quote.tax_free)} of"
            " up-front tax-free cash."
        )
    return f"{sentence}."


def _purchase_age(context: _CardContext, position: int) -> int:
    """The person's age when the card's annuity purchase happens."""
    ages = context.reading.ages
    if ages is not None:
        return ages[position]
    person = context.persons[position]
    return age_on(person.date_of_birth.value, context.snapshot.period.end)


def _annuity_quote(context: _CardContext, pot: Money) -> _AnnuityQuote | None:
    """The household's whole-pot purchase, or ``None`` off the rate table.

    The median household pension pot splits between the persons by
    their pension balances at the reading snapshot (the base
    projection keeps the per-person split; path outcomes do not), and
    each person's slice buys a single-life annuity at their own age
    after paying out its tax-free cash. A household whose base
    snapshot holds no pension balance reads as the first person's
    whole pot — the degenerate single-person behaviour. ``None``
    whenever a purchasing person's age falls outside the shipped
    age-adjustment table — the model does not extrapolate (§5.3).
    """
    balances = tuple(
        _pension_closing_balance(_snapshot_person(context, person.id))
        for person in context.persons
    )
    total = sum(balances, _ZERO_MONEY)
    if total > _ZERO_MONEY:
        slices = tuple(
            Money(pot.amount * balance.amount / total.amount) for balance in balances
        )
    else:
        slices = (pot, *(_ZERO_MONEY,) * (len(context.persons) - 1))
    income = _ZERO_MONEY
    tax_free = _ZERO_MONEY
    ages: list[int] = []
    for position, pot_slice in enumerate(slices):
        if pot_slice <= _ZERO_MONEY and total > _ZERO_MONEY:
            continue
        age = _purchase_age(context, position)
        cash = _tax_free_cash(context, position, pot_slice)
        slice_income = _annuity_income(context.assumptions, pot_slice - cash, age)
        if slice_income is None:
            return None
        income = income + slice_income
        tax_free = tax_free + cash
        ages.append(age)
    return _AnnuityQuote(income=income, tax_free=tax_free, ages=tuple(ages))


def _snapshot_person(context: _CardContext, person_id: EntityId) -> PersonPeriodResult:
    """The reading snapshot's results for the person with ``person_id``."""
    for person in context.snapshot.persons:
        if person.person_id == person_id:
            return person
    msg = f"person {person_id} is missing from the reading snapshot"
    raise ValueError(msg)


def _pension_closing_balance(person: PersonPeriodResult) -> Money:
    """The person's pension wrappers' closing balance at the reading."""
    balances = (
        wrapper.closing_balance for wrapper in person.wrappers if wrapper.pension
    )
    return sum(balances, _ZERO_MONEY)


def _tax_free_cash(context: _CardContext, position: int, pot: Money) -> Money:
    """The tax-free cash one person's annuity slice pays out first.

    The engine's 5.5 convention: crystallising uncrystallised funds
    delivers the region's tax-free fraction as cash — capped at the
    remaining lump-sum-allowance headroom, an individual cap (§4.11)
    — and only the remainder buys income; already-crystallised funds
    annuitise whole. The uncrystallised share comes from the base
    projection's reading snapshot (path outcomes keep no sub-balance
    split), and the headroom uses the person's cumulative tax-free
    cash at that close, so the estimate follows the engine's own
    ledger.
    """
    person = _snapshot_person(context, context.persons[position].id)
    total = _ZERO_MONEY
    uncrystallised = _ZERO_MONEY
    pension_kind = None
    for wrapper in person.wrappers:
        if not wrapper.pension:
            continue
        if pension_kind is None:
            pension_kind = wrapper.kind
        total = total + wrapper.closing_balance
        uncrystallised = uncrystallised + wrapper.closing_uncrystallised
    if pension_kind is None or total <= _ZERO_MONEY or uncrystallised <= _ZERO_MONEY:
        return _ZERO_MONEY
    region = region_for(context.assumptions)
    fraction = region.wrappers.tax_treatment(
        pension_kind, context.snapshot.period
    ).tax_free_fraction
    if fraction is None:
        return _ZERO_MONEY
    share = uncrystallised.amount / total.amount
    tax_free = Money(pot.amount * share * fraction.value)
    allowance = region.wrappers.lump_sum_allowance(context.snapshot.period)
    if allowance is None:
        return tax_free
    headroom = max(allowance - person.lsa_used, _ZERO_MONEY)
    return min(tax_free, headroom)


def _annuity_income(
    assumptions: AssumptionSet, capital: Money, age: int
) -> Money | None:
    """The yearly income ``capital`` buys at the shipped level rates.

    The single-life-at-65 base rate scaled by the age-adjustment
    table — the engine's own rate composition (roadmap 5.5) over the
    capital left after the purchase's tax-free cash; ``None`` when
    the shipped table does not cover ``age`` — the model does not
    extrapolate (planning §5.3).
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
    return Money(capital.amount * rate)


_FORECAST_PREFIXES = ("Your", "Your partner's")
"""State Pension sentence openers by household position (9.31)."""


def _state_pension_lines(
    context: _CardContext, annuity_income: Money | None
) -> list[str]:
    """Each person's State Pension stacked on top, in household order.

    One sentence per person holding a usable forecast, opened "Your" /
    "Your partner's"; the closing "all told" figure lands on the last
    sentence and combines the annuity income with *every* forecast —
    the household's whole guaranteed layer (§4.11).
    """
    quotes = [
        (position, quote)
        for position, person in enumerate(context.persons)
        if (quote := _state_pension_quote(person, context.provenance)) is not None
    ]
    total = sum((annual for _, (annual, _) in quotes), _ZERO_MONEY)
    lines = []
    for count, (position, (annual, start)) in enumerate(quotes, start=1):
        prefix = _FORECAST_PREFIXES[position]
        sentence = (
            f"{prefix} State Pension forecast adds {_pounds(annual)}"
            f" a year from {start}"
        )
        if count == len(quotes) and annuity_income is not None:
            combined = annuity_income + total
            sentence = f"{sentence} — around {_pounds(combined)} a year all told"
        lines.append(f"{sentence}.")
    return lines


def _state_pension_quote(
    person: Person, provenance: RunProvenance
) -> tuple[Money, str] | None:
    """One person's (annual amount, start-age phrase), or ``None``.

    The DWP forecast states today's rates as of its own date; a stale
    forecast is quoted as the run opened it — the §4.8 roll-forward
    the engine disclosed — so the card and the projection uprate the
    same way. Deferral-uplifted and protected payment included; the
    start age keeps the timetable's month-level precision. ``None``
    without a usable forecast.
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
    weekly = _rolled_forecast_weekly(person, provenance)
    if weekly is None:
        base = entitlement.annual_amount + entitlement.cpi_uprated_annual_amount
    else:
        base = weekly * _WEEKS_PER_YEAR
    annual = base * (_ONE + entitlement.deferral_uplift)
    start = _age_phrase(person.date_of_birth.value, entitlement.start_date)
    return annual, start


def _rolled_forecast_weekly(person: Person, provenance: RunProvenance) -> Money | None:
    """The forecast's weekly rate as the run opened it, if rolled (§4.8).

    A stale forecast is uprated to the run start by the engine and the
    adjustment disclosed as a roll-forward record over the stated
    weekly rate — its blended factor already covers a protected
    slice's CPI-only handling — so the card quotes the disclosure
    rather than re-deriving the roll. ``None`` when no record exists:
    a forecast stated within a whole month of the run start is quoted
    as stated.
    """
    label = f"person[{person.id}].state_pension.forecast_weekly_amount"
    for roll in provenance.balance_roll_forwards:
        if roll.label == label:
            return roll.opening
    return None


def _age_phrase(date_of_birth: date, on: date) -> str:
    """An exact age as copy: ``age 67``, or ``age 66 and 9 months``.

    The SPA timetable and deferral both work in whole months, so a
    start age truncated to whole years could read almost a year early
    — the months are named whenever they are non-zero.
    """
    years, months = divmod(whole_months_between(date_of_birth, on), _MONTHS_PER_YEAR)
    if months == 0:
        return f"age {years}"
    unit = "month" if months == 1 else "months"
    return f"age {years} and {months} {unit}"


def _basis_sentence(monte_carlo: MonteCarloResult) -> str:
    """The manifest side: which run the card summarises (§4.6)."""
    return (
        f"Based on the Monte Carlo run's {monte_carlo.path_count:,} paths"
        f" (seed {monte_carlo.config.seed}). The likely range spans the"
        " middle half of paths (25th to 75th percentiles); all figures"
        " are in today's money."
    )


__all__ = [
    "DETERMINISTIC_BASIS_SENTENCE",
    "NO_OUTLOOK_MESSAGE",
    "OUTLOOK_HEADING",
    "OUTLOOK_NO_PLAN_MESSAGE",
    "OutlookPanelViewModel",
    "build_outlook_panel",
]
