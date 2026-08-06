"""End-to-end annual-allowance charge through the real UK region (#116).

The issue's reproduction: before the wiring, a plan with an
``mpaa_triggered_on`` fact projected identically to one without — the
MPAA breach had no financial consequence. These tests run the engine
over the shipped UK region and pin the charge the breach now adds to
the period's assessment, plus the tapered-allowance case for a high
earner. Figures are hand-worked from the 2026/27 rules verified in
planning §6 (AA £60,000; MPAA £10,000; taper to £10,000 past
£200,000/£260,000; rUK bands with the relief-at-source extension).
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from glidepath.core import (
    ContributionSchedule,
    Decision,
    EntityId,
    Fact,
    Household,
    Money,
    ProjectionResult,
    ReliefMechanic,
    RunConfig,
    Wrapper,
    run,
)
from glidepath.core.entities import Person
from glidepath.regions.uk import (
    GIA_KIND,
    RUK_RESIDENCY,
    WORKPLACE_DC_KIND,
    default_assumption_set,
    future_years_extension,
    uk_region,
)

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)

ZERO = Money(Decimal(0))


def money_fact(amount: str, as_of: date = AS_OF) -> Fact[Money]:
    """A user-stated monetary fact."""
    return Fact(value=Money(Decimal(amount)), as_of=as_of, recorded_on=RECORDED)


def dc_saver(
    *,
    contribution: str,
    employer: str | None = None,
    employment: str = "60000",
    mpaa_triggered_on: date | None = None,
    gia_balance: str | None = None,
    as_of: date = AS_OF,
) -> Household:
    """A working-age DC saver contributing ``contribution`` gross per year."""
    dc = Wrapper(
        id=EntityId("aa-dc"),
        kind=WORKPLACE_DC_KIND,
        balance=money_fact("0", as_of=as_of),
        contributions=ContributionSchedule(
            employee_amount=Decision(
                value=Money(Decimal(contribution)), recorded_on=RECORDED
            ),
            employer_amount=None
            if employer is None
            else money_fact(employer, as_of=as_of),
            relief_mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
        ),
    )
    wrappers: tuple[Wrapper, ...] = (dc,)
    if gia_balance is not None:
        gia = Wrapper(
            id=EntityId("aa-gia"),
            kind=GIA_KIND,
            balance=money_fact(gia_balance, as_of=as_of),
        )
        wrappers = (dc, gia)
    person = Person(
        id=EntityId("aa-person"),
        date_of_birth=Fact(value=date(1980, 2, 1), as_of=as_of, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        employment_income=money_fact(employment, as_of=as_of),
        mpaa_triggered_on=None
        if mpaa_triggered_on is None
        else Fact(value=mpaa_triggered_on, as_of=as_of, recorded_on=RECORDED),
        wrappers=wrappers,
    )
    return Household(persons=(person,))


def one_year_run(plan: Household, today: date) -> ProjectionResult:
    """Project ``plan`` over the single 2026/27 tax year from ``today``."""
    assumptions = default_assumption_set()
    region = uk_region(future_years_extension(assumptions))
    return run(
        plan,
        assumptions,
        region,
        RunConfig(today=today, horizon_end=date(2027, 4, 5)),
    )


def test_mpaa_breach_now_charges_tax() -> None:
    """The #116 reproduction: the trigger fact changes the projected tax.

    From 2 August 2026 the 2026/27 period pro-rates the £20,000
    schedule to 8/12 — £13,333.33..., £3,333.33... over the £10,000
    MPAA. Income tax alone is unchanged (relief-extended basic band),
    and the charge prices the excess at the basic rate: 666.66 after
    the penny round-down.
    """
    today = date(2026, 8, 2)
    with_trigger = one_year_run(
        dc_saver(contribution="20000", mpaa_triggered_on=date(2025, 1, 1)), today
    )
    without_trigger = one_year_run(dc_saver(contribution="20000"), today)
    [charged] = with_trigger.snapshots[0].persons
    [uncharged] = without_trigger.snapshots[0].persons
    assert charged.tax.tax_due - uncharged.tax.tax_due == Money(Decimal("666.66"))
    charge_lines = [
        line for line in charged.tax.lines if line.band.startswith("aa_charge_")
    ]
    assert [line.band for line in charge_lines] == ["aa_charge_basic"]
    assert charge_lines[0].tax == Money(Decimal("666.66"))
    assert not any(line.band.startswith("aa_charge_") for line in uncharged.tax.lines)


def test_tapered_allowance_charges_a_high_earner() -> None:
    """£300,000 income with £60,000 in: the taper leaves £20,000 chargeable.

    Threshold income 240,000 and adjusted income 300,000 taper the
    allowance to 40,000; the whole-year £60,000 contribution leaves a
    £20,000 excess. Taxable income is 300,000 (the allowance is fully
    tapered), far past the extended higher limit of 185,140, so the
    excess prices wholly at the additional rate: 9,000.00.
    """
    rich = dc_saver(contribution="60000", employment="300000", as_of=date(2026, 4, 1))
    result = one_year_run(rich, date(2026, 4, 6))
    [charged] = result.snapshots[0].persons
    charge_lines = [
        line for line in charged.tax.lines if line.band.startswith("aa_charge_")
    ]
    assert [line.band for line in charge_lines] == ["aa_charge_additional"]
    assert charge_lines[0].tax == Money(Decimal("9000.00"))


def test_scheme_pays_debits_the_breaching_pension_pot() -> None:
    """The mandatory conditions route the whole charge to the DC pot (#124).

    An £80,000 employer contribution against £60,000 of pay leaves a
    £20,000 excess (no taper: threshold income £60,000). Taxable
    income is £47,430, so the excess prices wholly in the higher band:
    £8,000.00. The charge exceeds £2,000 and the wrapper's own input
    exceeds the standard £60,000 allowance — mandatory scheme pays
    (FA 2004 s237B) — so the pot funds all of it at close and nothing
    joins the shortfall.
    """
    plan = dc_saver(contribution="0", employer="80000", as_of=date(2026, 4, 1))
    result = one_year_run(plan, date(2026, 4, 6))
    [person] = result.snapshots[0].persons
    [dc] = person.wrappers
    charge_lines = [
        line for line in person.tax.lines if line.band.startswith("aa_charge_")
    ]
    assert [line.band for line in charge_lines] == ["aa_charge_higher"]
    assert charge_lines[0].tax == Money(Decimal("8000.00"))
    assert dc.aa_charge == Money(Decimal("8000.00"))
    assert person.shortfall == ZERO


def test_cash_route_debits_the_taxable_account() -> None:
    """A charge under £2,000 settles from the GIA, not the pension.

    The #116 MPAA repro with a £10,000 GIA alongside: the £666.66
    charge fails the mandatory scheme-pays minimum, so it is deducted
    from the taxable account at close and the pension pot keeps its
    contributions.
    """
    plan = dc_saver(
        contribution="20000",
        mpaa_triggered_on=date(2025, 1, 1),
        gia_balance="10000",
    )
    result = one_year_run(plan, date(2026, 8, 2))
    [person] = result.snapshots[0].persons
    dc, gia = person.wrappers
    assert dc.aa_charge == ZERO
    assert gia.aa_charge == Money(Decimal("666.66"))
    assert person.shortfall == ZERO


def test_an_unfundable_charge_joins_the_shortfall() -> None:
    """With no taxable account the cash route has nothing to debit.

    Same breach, no GIA: the £666.66 charge cannot come from the
    pension (conditions unmet) or from cash, so it lands in the
    person's shortfall — the ruin signal sees the breach.
    """
    plan = dc_saver(contribution="20000", mpaa_triggered_on=date(2025, 1, 1))
    result = one_year_run(plan, date(2026, 8, 2))
    [person] = result.snapshots[0].persons
    [dc] = person.wrappers
    assert dc.aa_charge == ZERO
    assert person.shortfall == Money(Decimal("666.66"))
