"""Annuity purchases and pricing (roadmap 5.5; planning §5.1, §7).

An :class:`AnnuityPurchase` is wholly a decision record (planning §5.1):
at a chosen age, a chosen fraction of the pension pot converts into
lifetime income of a chosen type and basis. Pricing is
assumption-driven — the §7 single-life-at-65 base rates
(``annuity.level.single.65`` and friends) shaped by the
``annuity.age_adjustment`` table (:class:`AnnuityRateTable`): per-age
multipliers with linear interpolation between whole-year knots, a
joint-life factor, and the escalating product's fixed annual increase.
Nothing here is region-specific: the rates are economic estimates in
the assumption catalogue, not policy figures (planning §4.2).

The engine executes the purchase (roadmap 5.5): capital leaves the
pension wrappers at the purchase date, tax-free cash arrives alongside
per the region's rules, and the resulting income joins the §5.2 income
step pro-rated from its exact start date.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from typing import TYPE_CHECKING

from glidepath.core.config import EngineError
from glidepath.core.money import Rate
from glidepath.core.periods import date_age_attained
from glidepath.core.provenance import AssumptionKey

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core.entities import EntityId
    from glidepath.core.provenance import Decision

_ZERO = Decimal(0)
_ONE = Decimal(1)
_TABLE_CONTEXT = "annuity.age_adjustment assumption"


class AnnuityType(Enum):
    """The income shape an annuity pays (planning §5.1)."""

    LEVEL = auto()
    """Constant nominal income for life."""
    ESCALATING = auto()
    """Income rising by the table's fixed escalation rate each year."""
    INFLATION_LINKED = auto()
    """Income tracking the run's CPI path (uncapped, unfloored)."""


class AnnuityBasis(Enum):
    """Whose lives the annuity covers (planning §5.1)."""

    SINGLE = auto()
    JOINT = auto()
    """Continues (at the priced survivor fraction) to a partner."""


@dataclass(frozen=True, slots=True)
class AnnuityPurchase:
    """One planned annuity purchase — wholly a decision (planning §5.1).

    At the period containing the date the person attains ``at_age``,
    ``fraction_of_pot`` of each pension wrapper's balance (both
    sub-balances, as they stand at that date) converts into lifetime
    income priced from the annuity-rate assumptions (roadmap 5.5).
    Partial annuitisation mid-drawdown is the ``fraction_of_pot < 1``
    case; several purchases at different ages annuitise in stages.
    """

    id: EntityId
    at_age: Decision[int]
    fraction_of_pot: Decision[Decimal]
    annuity_type: AnnuityType = AnnuityType.LEVEL
    basis: AnnuityBasis = AnnuityBasis.SINGLE

    def __post_init__(self) -> None:
        """Reject a non-positive age or a fraction outside (0, 1]."""
        if self.at_age.value <= 0:
            msg = "AnnuityPurchase.at_age must be positive"
            raise ValueError(msg)
        if not _ZERO < self.fraction_of_pot.value <= _ONE:
            msg = "AnnuityPurchase.fraction_of_pot must lie in (0, 1]"
            raise ValueError(msg)


def annuity_start_date(purchase: AnnuityPurchase, date_of_birth: date) -> date:
    """The exact date the purchase fires and income starts (§4.1)."""
    return date_age_attained(date_of_birth, purchase.at_age.value)


def annuity_base_rate_key(annuity_type: AnnuityType) -> AssumptionKey:
    """The §7 single-life-at-65 base rate key for ``annuity_type``."""
    match annuity_type:
        case AnnuityType.LEVEL:
            return AssumptionKey.ANNUITY_LEVEL_SINGLE_65
        case AnnuityType.ESCALATING:
            return AssumptionKey.ANNUITY_ESCALATING3_SINGLE_65
        case AnnuityType.INFLATION_LINKED:
            return AssumptionKey.ANNUITY_INFLATION_LINKED_SINGLE_65


_TYPE_TABLE_KEYS: tuple[tuple[str, AnnuityType], ...] = (
    ("level", AnnuityType.LEVEL),
    ("escalating3", AnnuityType.ESCALATING),
    ("inflation_linked", AnnuityType.INFLATION_LINKED),
)


@dataclass(frozen=True, slots=True)
class AnnuityRateTable:
    """The parsed ``annuity.age_adjustment`` assumption (planning §7).

    ``multipliers`` maps each annuity type to whole-year age knots and
    the multiplier each applies to that type's single-life-at-65 base
    rate; ``joint_factor`` scales any type down to its joint-life
    price; ``escalation`` is the escalating product's fixed annual
    income increase (the base key names it: ``escalating3`` is the
    3%/yr product).
    """

    escalation: Rate
    joint_factor: Decimal
    multipliers: Mapping[AnnuityType, Mapping[int, Decimal]]

    def age_multiplier(self, annuity_type: AnnuityType, age: int) -> Decimal:
        """The multiplier on the base rate for a purchase at ``age``.

        Linear interpolation between whole-year knots — exact
        ``Decimal`` arithmetic per planning §4.6. An age outside the
        table's span is an error, never an extrapolation: shipped data
        drives results, the model does not guess (planning §5.3).

        Raises:
            EngineError: If ``age`` lies outside the table's knots.
        """
        knots = self.multipliers[annuity_type]
        ages = sorted(knots)
        if age < ages[0] or age > ages[-1]:
            msg = (
                f"{_TABLE_CONTEXT}: no multiplier for a purchase at age {age};"
                f" the table covers ages {ages[0]}-{ages[-1]}"
            )
            raise EngineError(msg)
        exact = knots.get(age)
        if exact is not None:
            return exact
        below = max(knot for knot in ages if knot < age)
        above = min(knot for knot in ages if knot > age)
        share = Decimal(age - below) / Decimal(above - below)
        return knots[below] + (knots[above] - knots[below]) * share

    def basis_factor(self, basis: AnnuityBasis) -> Decimal:
        """The price factor for ``basis``: 1 single, ``joint_factor`` joint."""
        if basis is AnnuityBasis.JOINT:
            return self.joint_factor
        return _ONE

    @classmethod
    def from_assumption_value(cls, value: object) -> AnnuityRateTable:
        """Parse the assumption's structured value, strictly.

        Expects ``escalation`` and ``joint_factor`` figures plus one
        age table per product tag (``level``, ``escalating3``,
        ``inflation_linked``); anything else — missing keys, unknown
        keys, non-``Decimal`` figures — fails loudly.

        Raises:
            EngineError: If the value has any other shape.
        """
        entries = _table_entries(value, _TABLE_CONTEXT)
        escalation = Rate(_take_decimal(entries, "escalation"))
        joint_factor = _take_decimal(entries, "joint_factor")
        multipliers = {
            annuity_type: _age_knots(entries, tag)
            for tag, annuity_type in _TYPE_TABLE_KEYS
        }
        if entries:
            unknown = ", ".join(sorted(entries))
            msg = f"{_TABLE_CONTEXT}: unknown keys: {unknown}"
            raise EngineError(msg)
        if not _ZERO <= escalation.value <= _ONE:
            msg = f"{_TABLE_CONTEXT}: escalation must lie between 0 and 1"
            raise EngineError(msg)
        if joint_factor <= _ZERO:
            msg = f"{_TABLE_CONTEXT}: joint_factor must be positive"
            raise EngineError(msg)
        return cls(
            escalation=escalation, joint_factor=joint_factor, multipliers=multipliers
        )


def _table_entries(value: object, context: str) -> dict[str, object]:
    """The assumption value as a consumable key/value dictionary."""
    if not isinstance(value, Mapping):
        msg = f"{context}: expected a table value, got {type(value).__name__}"
        raise EngineError(msg)
    return {str(key): entry for key, entry in value.items()}


def _take_decimal(entries: dict[str, object], key: str) -> Decimal:
    """Pop a required ``Decimal`` figure from the table."""
    raw = entries.pop(key, None)
    if not isinstance(raw, Decimal):
        found = "missing" if raw is None else type(raw).__name__
        msg = f"{_TABLE_CONTEXT}: {key!r} must be a decimal figure ({found})"
        raise EngineError(msg)
    return raw


def _age_knots(entries: dict[str, object], tag: str) -> dict[int, Decimal]:
    """Pop one product's age table: whole-year ages to positive factors."""
    raw = entries.pop(tag, None)
    if raw is None:
        msg = f"{_TABLE_CONTEXT}: missing required key {tag!r}"
        raise EngineError(msg)
    table = _table_entries(raw, f"{_TABLE_CONTEXT}.{tag}")
    if not table:
        msg = f"{_TABLE_CONTEXT}.{tag}: age table must not be empty"
        raise EngineError(msg)
    knots: dict[int, Decimal] = {}
    for age_text, factor in table.items():
        try:
            age = int(age_text)
        except ValueError:
            msg = f"{_TABLE_CONTEXT}.{tag}: ages must be whole years, got {age_text!r}"
            raise EngineError(msg) from None
        if age <= 0:
            msg = f"{_TABLE_CONTEXT}.{tag}: ages must be positive"
            raise EngineError(msg)
        if not isinstance(factor, Decimal) or factor <= _ZERO:
            msg = (
                f"{_TABLE_CONTEXT}.{tag}[{age}]: multipliers must be"
                " positive decimal figures"
            )
            raise EngineError(msg)
        knots[age] = factor
    return knots
