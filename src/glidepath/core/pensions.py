"""Defined-benefit pension entitlements (roadmap 4.2, 9.6; planning §5.1).

Models deferred/accrued DB entitlements plus CARE-style active
membership (:class:`DBActiveMembership`, roadmap 9.6). Every scheme
parameter (accrued pension, normal pension age, revaluation basis,
early/late factors, commutation factor, accrual rate, pensionable
salary) is a user-entered fact: schemes vary too much to ship as data
(planning §5.1), so scheme facts drive results and nothing here is ever
guessed — taking benefits at an age the factor table does not cover is
a construction-time error, not a default.

Modelling conventions (documented in planning §5.1/§5.2):

- The scheme's one :class:`RevaluationBasis` applies both to
  revaluation in deferment and to increases in payment; splitting the
  two bases is a deferred extension (planning §2).
- Revaluation compounds per engine period from the statement date; the
  span before the run's ``today`` is not modelled period-by-period, so
  the engine folds it into a starting factor via
  :func:`revaluation_factor_for_months` — whole years compound with an
  integer exponent and the remaining whole months scale the annual
  rate linearly, both exact ``Decimal`` arithmetic (planning §4.6).
- Commutation trades pension for a lump sum at the stated factor, at
  the moment benefits start.
- Active accrual credits ``accrual_rate x pensionable_salary`` per year
  of service on top of the revalued entitlement, service ending at the
  earliest of the leave-and-defer age, the benefits start, and the
  engine's retirement gate (planning §5.1); final-salary linkage and
  member DB contributions are not modelled (planning §2).
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from typing import TYPE_CHECKING

from glidepath.core.money import Money
from glidepath.core.periods import date_age_attained

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from glidepath.core.entities import EntityId
    from glidepath.core.money import Rate
    from glidepath.core.provenance import Decision, Fact

_ZERO = Decimal(0)
_ONE = Decimal(1)
_ZERO_MONEY = Money(_ZERO)
_MONTHS_PER_YEAR = 12


class RevaluationReference(Enum):
    """What a DB scheme's revaluation and increases track (planning §5.1)."""

    CPI = auto()
    """Tracks the run's CPI path, optionally capped (e.g. CPI max 5%)."""
    FIXED = auto()
    """A fixed annual rate, whatever inflation does."""
    NONE = auto()
    """No revaluation: the pension is frozen in nominal terms."""


@dataclass(frozen=True, slots=True)
class RevaluationBasis:
    """How a DB entitlement revalues — a scheme fact (planning §5.1).

    The same basis governs deferment revaluation and increases in
    payment in v1 (module docstring).
    """

    reference: RevaluationReference
    cap: Rate | None = None
    """Annual cap on CPI-linked revaluation (CPI reference only)."""
    fixed_rate: Rate | None = None
    """The annual rate of a ``FIXED`` basis (required exactly then)."""

    def __post_init__(self) -> None:
        """Require the fields the reference uses, and only those."""
        requires_fixed = self.reference is RevaluationReference.FIXED
        if (self.fixed_rate is not None) != requires_fixed:
            msg = "RevaluationBasis.fixed_rate is required exactly for FIXED"
            raise ValueError(msg)
        if self.cap is not None and self.reference is not RevaluationReference.CPI:
            msg = "RevaluationBasis.cap applies only to a CPI reference"
            raise ValueError(msg)
        if self.cap is not None and self.cap.value < _ZERO:
            msg = "RevaluationBasis.cap must be non-negative"
            raise ValueError(msg)

    def annual_rate(self, cpi: Decimal) -> Decimal:
        """The basis's revaluation rate in a year whose CPI is ``cpi``.

        CPI-linked revaluation is floored at zero (statutory revaluation
        never reduces the entitlement) and capped when the scheme caps
        it; a fixed rate ignores CPI entirely.
        """
        if self.reference is RevaluationReference.CPI:
            rate = max(cpi, _ZERO)
            if self.cap is not None:
                rate = min(rate, self.cap.value)
            return rate
        if self.fixed_rate is not None:  # FIXED, by the __post_init__ invariant
            return self.fixed_rate.value
        return _ZERO


@dataclass(frozen=True, slots=True)
class FactorTable:
    """Early/late retirement factors — scheme facts, user-entered (§5.1).

    Maps the whole-year age benefits are taken at to the multiplier the
    scheme applies to the revalued pension (e.g. ``{58: Decimal("0.80")}``
    for a 20% early-retirement reduction at 58). Taking at the normal
    pension age needs no entry (factor 1); taking at any other age not
    in the table is rejected at :class:`DBPension` construction — scheme
    facts drive results, the model never guesses a factor.
    """

    factors: Mapping[int, Decimal]

    def __post_init__(self) -> None:
        """Require positive ages and positive factors."""
        for age, factor in self.factors.items():
            if age <= 0:
                msg = "FactorTable ages must be positive"
                raise ValueError(msg)
            if factor <= _ZERO:
                msg = "FactorTable factors must be positive"
                raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class DBActiveMembership:
    """CARE-style active membership of a DB scheme (planning §5.1, 9.6).

    ``accrual_rate`` is the fraction of pensionable salary earned as
    annual pension per year of service (a 1/60th scheme enters e.g.
    ``0.0166667``); ``pensionable_salary`` is the annual pensionable
    salary — often below total pay — escalated by the earnings-growth
    assumption within the run like employment income.
    """

    accrual_rate: Fact[Decimal]
    pensionable_salary: Fact[Money]
    active_until_age: Decision[int] | None = None
    """The age service ends with the pension left deferred; ``None``
    means service continues until benefits start (module docstring —
    the engine's retirement gate applies either way)."""

    def __post_init__(self) -> None:
        """Reject impossible scheme facts and choices."""
        if not _ZERO < self.accrual_rate.value <= _ONE:
            msg = "DBActiveMembership.accrual_rate must lie in (0, 1]"
            raise ValueError(msg)
        if self.pensionable_salary.value < _ZERO_MONEY:
            msg = "DBActiveMembership.pensionable_salary must be non-negative"
            raise ValueError(msg)
        if self.active_until_age is not None and self.active_until_age.value <= 0:
            msg = "DBActiveMembership.active_until_age must be positive"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class DBPension:
    """One DB entitlement — deferred, or active via ``active_membership``.

    ``accrued_annual_pension`` is the annual pension at the statement
    (or leaving) date; ``commuted_fraction`` is the user's decision to
    trade that fraction of the pension for a lump sum of
    ``pension given up x commutation_factor`` when benefits start
    (planning §5.1).
    """

    id: EntityId
    accrued_annual_pension: Fact[Money]
    statement_date: date
    normal_pension_age: Fact[int]
    revaluation_basis: RevaluationBasis
    early_late_factors: FactorTable
    commuted_fraction: Decision[Decimal]
    commutation_factor: Fact[Decimal] | None = None
    """Pounds of lump sum per pound of annual pension given up."""
    taken_at_age: Decision[int] | None = None
    """The age benefits start; ``None`` means the normal pension age."""
    active_membership: DBActiveMembership | None = None
    """Active CARE-style accrual; ``None`` means deferred (roadmap 9.6)."""

    def __post_init__(self) -> None:
        """Reject inconsistent scheme facts and choices loudly.

        Everything checkable without a run date is checked here, so an
        instance that exists is projectable: the engine never has to
        guess a factor or a commutation basis mid-run.
        """
        if self.accrued_annual_pension.value < _ZERO_MONEY:
            msg = "DBPension.accrued_annual_pension must be non-negative"
            raise ValueError(msg)
        if self.normal_pension_age.value <= 0:
            msg = "DBPension.normal_pension_age must be positive"
            raise ValueError(msg)
        fraction = self.commuted_fraction.value
        if not _ZERO <= fraction <= _ONE:
            msg = "DBPension.commuted_fraction must lie between 0 and 1"
            raise ValueError(msg)
        if fraction > _ZERO:
            if self.commutation_factor is None:
                msg = "DBPension.commutation_factor is required to commute pension"
                raise ValueError(msg)
            if self.commutation_factor.value <= _ZERO:
                msg = "DBPension.commutation_factor must be positive"
                raise ValueError(msg)
        taken = db_taken_age(self)
        if taken <= 0:
            msg = "DBPension.taken_at_age must be positive"
            raise ValueError(msg)
        if taken != self.normal_pension_age.value and taken not in (
            self.early_late_factors.factors
        ):
            msg = (
                f"DBPension.early_late_factors has no factor for age {taken};"
                " scheme facts drive results (planning §5.1)"
            )
            raise ValueError(msg)
        membership = self.active_membership
        if (
            membership is not None
            and membership.active_until_age is not None
            and membership.active_until_age.value > taken
        ):
            msg = (
                "DBPension.active_membership.active_until_age must not"
                " exceed the age benefits are taken (planning §5.1)"
            )
            raise ValueError(msg)


def db_taken_age(pension: DBPension) -> int:
    """The age benefits start: the taken-at decision, else the NPA."""
    if pension.taken_at_age is not None:
        return pension.taken_at_age.value
    return pension.normal_pension_age.value


def db_start_date(pension: DBPension, date_of_birth: date) -> date:
    """The exact date benefits start (an income entitlement, §4.1)."""
    return date_age_attained(date_of_birth, db_taken_age(pension))


def db_service_end_date(pension: DBPension, date_of_birth: date) -> date:
    """The date active service ends: leave-and-defer age, else benefits.

    Meaningful only for a pension with an ``active_membership``; the
    engine's retirement gate (planning §5.1) may stop accrual earlier
    still. The construction-time invariant keeps this at or before
    :func:`db_start_date`.
    """
    membership = pension.active_membership
    if membership is not None and membership.active_until_age is not None:
        return date_age_attained(date_of_birth, membership.active_until_age.value)
    return db_start_date(pension, date_of_birth)


def db_early_late_factor(pension: DBPension) -> Decimal:
    """The scheme factor applied to the revalued pension at start.

    Taking at the normal pension age defaults to a factor of 1 unless
    the table states otherwise; every other age has a table entry
    (enforced at construction).
    """
    taken = db_taken_age(pension)
    factor = pension.early_late_factors.factors.get(taken)
    if factor is not None:
        return factor
    return _ONE


def revaluation_factor_for_months(annual_rate: Decimal, months: int) -> Decimal:
    """Cumulative revaluation over ``months`` whole months at one rate.

    Whole years compound with an integer exponent; the remaining months
    scale the annual rate linearly — the §4.1/§5.2 whole-month
    convention in exact ``Decimal`` arithmetic (planning §4.6 rejects
    fractional-exponent powers). Used for the span before the run's
    ``today``, which the engine never models period-by-period.

    Raises:
        ValueError: If ``months`` is negative.
    """
    if months < 0:
        msg = "months must be non-negative"
        raise ValueError(msg)
    years, remainder = divmod(months, _MONTHS_PER_YEAR)
    factor = (_ONE + annual_rate) ** years
    return factor * (
        _ONE + annual_rate * Decimal(remainder) / Decimal(_MONTHS_PER_YEAR)
    )
