"""The real/nominal reporting layer (roadmap 4.4; planning §5.2).

The engine computes nominal — tax bands are nominal objects — and every
:class:`~glidepath.core.results.PeriodSnapshot` records the cumulative
CPI factor the run inflated by. This layer presents a projection in one
of two bases: **real (today's money), the default**, deflates by the
run's own CPI path; nominal presents the ledger amounts unchanged. No
inflation source of its own enters here — the deflators come exactly
from what the engine recorded, so the one-inflation-truth rule of
planning §5.2 holds by construction.

Two deflators per row under the real basis, matching what each amount
is: *flows* divide by the snapshot's ``inflation_factor`` — the
period-start price level the engine inflated them with — while
*closing balances* divide by the level at the period's modelled end,
``inflation_factor * (1 + cpi * year_fraction)`` (the linear
partial-period convention of §5.2), because a closing balance already
contains the period's own nominal growth. Presented totals are sums of
the presented per-wrapper amounts, so a report table is internally
consistent after rounding.

Report amounts are quantized for presentation (they are derived views,
not ledger writes); the snapshots remain the exact ledger record.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from typing import TYPE_CHECKING

from glidepath.core.money import Money

if TYPE_CHECKING:
    from collections.abc import Iterable

    from glidepath.core.entities import EntityId
    from glidepath.core.glide import LifeStage
    from glidepath.core.periods import Period
    from glidepath.core.results import (
        PeriodSnapshot,
        PersonPeriodResult,
        ProjectionResult,
    )
    from glidepath.core.wrappers import WrapperKindId

_ONE = Decimal(1)
_ZERO = Money(Decimal(0))


class ReportBasis(Enum):
    """The money basis a report presents (planning §5.2).

    ``REAL`` is today's money — the default presentation; ``NOMINAL``
    is the engine's ledger basis.
    """

    REAL = auto()
    NOMINAL = auto()


@dataclass(frozen=True, slots=True)
class WrapperReportBalance:
    """One wrapper's closing balance, in the report's basis."""

    wrapper_id: EntityId
    kind: WrapperKindId
    closing_balance: Money


@dataclass(frozen=True, slots=True)
class PeriodReportRow:
    """One person's period figures, in the report's basis.

    ``deflator`` is the factor divided out of the nominal flow amounts:
    the snapshot's cumulative ``inflation_factor`` (the period-start
    price level) under ``ReportBasis.REAL`` and 1 under
    ``ReportBasis.NOMINAL``. ``balance_deflator`` is the factor divided
    out of the closing balances — the price level at the period's
    modelled end, since a closing balance embeds the period's own
    nominal growth. ``closing_balance`` is the sum of the presented
    ``wrapper_balances``, so the row stays consistent after rounding.
    ``contributions`` totals what landed in the pots (employee gross,
    including provider relief, plus employer); ``withdrawals_gross``
    totals the tax-free and taxable draws across wrappers.
    """

    period: Period
    person_id: EntityId
    age_at_period_start: int
    stage: LifeStage
    year_fraction: Decimal
    deflator: Decimal
    balance_deflator: Decimal
    employment_income: Money
    db_income: Money
    db_lump_sum: Money
    state_pension_income: Money
    tax_due: Money
    spending_need: Money
    net_withdrawn: Money
    shortfall: Money
    contributions: Money
    fees: Money
    growth: Money
    withdrawals_gross: Money
    closing_balance: Money
    wrapper_balances: tuple[WrapperReportBalance, ...]


@dataclass(frozen=True, slots=True)
class ProjectionReport:
    """A projection presented in one money basis (roadmap 4.4).

    Rows appear in period order; with a multi-person household each
    period contributes one row per person, in plan order.
    """

    basis: ReportBasis
    rows: tuple[PeriodReportRow, ...]


def build_report(
    result: ProjectionResult, basis: ReportBasis = ReportBasis.REAL
) -> ProjectionReport:
    """Present ``result`` in ``basis`` — real (today's money) by default.

    Real amounts deflate by the run's single CPI path as the engine
    recorded it period by period (planning §5.2): flows by the
    period-start level, closing balances by the level at the period's
    modelled end (see the module docstring).
    """
    rows = tuple(
        _person_row(snapshot, person, basis)
        for snapshot in result.snapshots
        for person in snapshot.persons
    )
    return ProjectionReport(basis=basis, rows=rows)


def _total(amounts: Iterable[Money]) -> Money:
    """Sum a stream of amounts exactly."""
    total = _ZERO
    for amount in amounts:
        total = total + amount
    return total


def _person_row(
    snapshot: PeriodSnapshot, person: PersonPeriodResult, basis: ReportBasis
) -> PeriodReportRow:
    """One report row: the person's snapshot figures deflated to ``basis``."""
    deflator = _ONE
    balance_deflator = _ONE
    if basis is ReportBasis.REAL:
        deflator = snapshot.inflation_factor
        balance_deflator = snapshot.inflation_factor * (
            _ONE + snapshot.returns.cpi.value * snapshot.year_fraction
        )

    def flow(amount: Money) -> Money:
        """Deflate a flow by the period-start level and quantize."""
        return Money(amount.amount / deflator).quantized()

    def balance(amount: Money) -> Money:
        """Deflate a closing balance by the period-end level and quantize."""
        return Money(amount.amount / balance_deflator).quantized()

    wrappers = person.wrappers
    wrapper_balances = tuple(
        WrapperReportBalance(
            wrapper_id=entry.wrapper_id,
            kind=entry.kind,
            closing_balance=balance(entry.closing_balance),
        )
        for entry in wrappers
    )
    return PeriodReportRow(
        period=snapshot.period,
        person_id=person.person_id,
        age_at_period_start=person.age_at_period_start,
        stage=person.stage,
        year_fraction=snapshot.year_fraction,
        deflator=deflator,
        balance_deflator=balance_deflator,
        employment_income=flow(person.employment_income),
        db_income=flow(person.db_income),
        db_lump_sum=flow(person.db_lump_sum),
        state_pension_income=flow(person.state_pension_income),
        tax_due=flow(person.tax.tax_due),
        spending_need=flow(person.spending_need),
        net_withdrawn=flow(person.net_withdrawn),
        shortfall=flow(person.shortfall),
        contributions=flow(
            _total(
                entry.employee_contribution + entry.employer_contribution
                for entry in wrappers
            )
        ),
        fees=flow(_total(entry.fee for entry in wrappers)),
        growth=flow(_total(entry.growth for entry in wrappers)),
        withdrawals_gross=flow(_total(entry.withdrawal_gross for entry in wrappers)),
        closing_balance=_total(entry.closing_balance for entry in wrapper_balances),
        wrapper_balances=wrapper_balances,
    )
