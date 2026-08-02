"""The real/nominal reporting layer (roadmap 4.4; planning §5.2).

The engine computes nominal — tax bands are nominal objects — and every
:class:`~glidepath.core.results.PeriodSnapshot` records the cumulative
CPI factor the run inflated by. This layer presents a projection in one
of two bases: **real (today's money), the default**, divides each
nominal amount by its snapshot's ``inflation_factor``; nominal presents
the ledger amounts unchanged. No inflation source of its own enters
here — the deflator is exactly the factor the engine recorded, so the
one-inflation-truth rule of planning §5.2 holds by construction.

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

    ``deflator`` is the factor divided out of the nominal snapshot
    amounts: the snapshot's cumulative ``inflation_factor`` under
    ``ReportBasis.REAL`` and 1 under ``ReportBasis.NOMINAL``.
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
    employment_income: Money
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

    Real amounts divide each nominal snapshot amount by that snapshot's
    own cumulative inflation factor: the run's single CPI path, recorded
    by the engine period by period (planning §5.2).
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
    deflator = snapshot.inflation_factor if basis is ReportBasis.REAL else _ONE

    def present(amount: Money) -> Money:
        """Deflate to the basis and quantize for presentation."""
        return Money(amount.amount / deflator).quantized()

    wrappers = person.wrappers
    return PeriodReportRow(
        period=snapshot.period,
        person_id=person.person_id,
        age_at_period_start=person.age_at_period_start,
        stage=person.stage,
        year_fraction=snapshot.year_fraction,
        deflator=deflator,
        employment_income=present(person.employment_income),
        tax_due=present(person.tax.tax_due),
        spending_need=present(person.spending_need),
        net_withdrawn=present(person.net_withdrawn),
        shortfall=present(person.shortfall),
        contributions=present(
            _total(
                entry.employee_contribution + entry.employer_contribution
                for entry in wrappers
            )
        ),
        fees=present(_total(entry.fee for entry in wrappers)),
        growth=present(_total(entry.growth for entry in wrappers)),
        withdrawals_gross=present(_total(entry.withdrawal_gross for entry in wrappers)),
        closing_balance=present(_total(entry.closing_balance for entry in wrappers)),
        wrapper_balances=tuple(
            WrapperReportBalance(
                wrapper_id=entry.wrapper_id,
                kind=entry.kind,
                closing_balance=present(entry.closing_balance),
            )
            for entry in wrappers
        ),
    )
