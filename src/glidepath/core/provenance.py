"""Facts, decisions, assumptions, and provenance (planning §1, §5.1).

Every number in a plan is exactly one of three kinds:

- a :class:`Fact` the user stated (DOB, balances, accrued DB entitlement),
- a :class:`Decision` the user chose (retirement age, contributions) — the
  only scenario-overridable plan fields (planning §4.3),
- an :class:`Assumption` the app defaulted or estimated (returns,
  inflation, longevity) — always overridable, always carrying its source.

The engine may not read a tunable number any way other than through an
:class:`AssumptionSet`; per-run reads are recorded by an
:class:`AssumptionReadRecorder` without mutating the frozen set, so
``ProjectionResult.provenance`` can list every assumption actually used.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, StrEnum, auto
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date, datetime


class Provenance(Enum):
    """Where a value came from (planning §5.1)."""

    USER_FACT = auto()
    DEFAULT_ASSUMPTION = auto()
    USER_OVERRIDE = auto()
    SCENARIO_OVERRIDE = auto()


def _require_tz_aware(moment: datetime, field_name: str) -> None:
    """Reject naive datetimes (repo rule: datetimes are always tz-aware)."""
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        msg = f"{field_name} must be timezone-aware"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Fact[T]:
    """A value the user stated (planning §5.1).

    Facts are never scenario-overridable (planning §4.3): a different
    balance is a different plan.
    """

    value: T
    as_of: date
    recorded_on: datetime
    note: str | None = None

    def __post_init__(self) -> None:
        """Reject naive timestamps."""
        _require_tz_aware(self.recorded_on, "Fact.recorded_on")


@dataclass(frozen=True, slots=True)
class Decision[T]:
    """A user choice — neither a fact about the world nor an estimate.

    Decision variables are exactly the scenario what-if whitelist
    (planning §4.3, §5.1).
    """

    value: T
    recorded_on: datetime
    note: str | None = None

    def __post_init__(self) -> None:
        """Reject naive timestamps."""
        _require_tz_aware(self.recorded_on, "Decision.recorded_on")


class AssumptionKey(StrEnum):
    """Stable dotted ids for every tunable number (planning §5.1, §7).

    Values are persisted in user files and targeted by scenario overrides,
    so they must never change meaning; retire a key by adding a new one.
    """

    INFLATION_CPI = "inflation.cpi"
    EARNINGS_GROWTH_REAL = "earnings.growth.real"
    RETURNS_EQUITY_REAL = "returns.equity.real"
    RETURNS_BONDS_REAL = "returns.bonds.real"
    RETURNS_CASH_REAL = "returns.cash.real"
    VOLATILITY_EQUITY = "volatility.equity"
    VOLATILITY_BONDS = "volatility.bonds"
    VOLATILITY_CASH = "volatility.cash"
    CORRELATION_EQUITY_BONDS = "correlation.equity_bonds"
    CORRELATION_EQUITY_CASH = "correlation.equity_cash"
    CORRELATION_BONDS_CASH = "correlation.bonds_cash"
    FEES_PLATFORM = "fees.platform"
    FEES_FUND = "fees.fund"
    YIELD_EQUITY = "yield.equity"
    YIELD_BONDS = "yield.bonds"
    YIELD_CASH = "yield.cash"
    HORIZON_PLANNING_AGE = "horizon.planning_age"
    GLIDEPATH_DEFAULT_SHAPE = "glidepath.default_shape"
    POLICY_STATE_PENSION_UPRATING = "policy.state_pension.uprating"
    POLICY_TAX_FUTURE_YEARS = "policy.tax.future_years"
    ANNUITY_LEVEL_SINGLE_65 = "annuity.level.single.65"
    ANNUITY_ESCALATING3_SINGLE_65 = "annuity.escalating3.single.65"
    ANNUITY_INFLATION_LINKED_SINGLE_65 = "annuity.inflation_linked.single.65"
    ANNUITY_AGE_ADJUSTMENT = "annuity.age_adjustment"


@dataclass(frozen=True, slots=True)
class Assumption[T]:
    """A value the app defaulted or estimated. Always overridable.

    Carries the shipped default alongside the effective value, plus the
    source its default is based on, so the UI can always answer "which of
    these numbers did I state, and which did you assume?" (planning §1).
    """

    key: AssumptionKey
    value: T
    default_value: T
    provenance: Provenance
    source: str
    recorded_on: datetime
    description: str

    def __post_init__(self) -> None:
        """Reject impossible provenance and naive timestamps."""
        if self.provenance is Provenance.USER_FACT:
            msg = "an Assumption cannot carry USER_FACT provenance"
            raise ValueError(msg)
        _require_tz_aware(self.recorded_on, "Assumption.recorded_on")


class AssumptionSet:
    """Immutable typed registry of assumptions, keyed by dotted id.

    The engine may not read a tunable number any other way (planning
    §5.1): step functions receive only ``Plan`` + ``AssumptionSet`` +
    ``Region`` + ``RunConfig``.
    """

    __slots__ = ("_entries",)

    def __init__(self, assumptions: Iterable[Assumption[Any]]) -> None:
        """Build the registry, rejecting duplicate keys."""
        entries: dict[AssumptionKey, Assumption[Any]] = {}
        for assumption in assumptions:
            if assumption.key in entries:
                msg = f"duplicate assumption key: {assumption.key!r}"
                raise ValueError(msg)
            entries[assumption.key] = assumption
        self._entries = entries

    def get(self, key: AssumptionKey) -> Assumption[Any]:
        """Return the assumption registered for ``key``.

        Raises:
            KeyError: If no assumption is registered for ``key``.
        """
        try:
            return self._entries[key]
        except KeyError:
            msg = f"no assumption registered for key {key!r}"
            raise KeyError(msg) from None

    def __contains__(self, key: AssumptionKey) -> bool:
        """Whether an assumption is registered for ``key``."""
        return key in self._entries

    @property
    def keys(self) -> frozenset[AssumptionKey]:
        """The keys with a registered assumption."""
        return frozenset(self._entries)


class AssumptionReadRecorder:
    """Mutable per-run recorder of assumption reads (planning §5.1).

    ``run()`` creates one per run and returns its contents as part of the
    result; the frozen :class:`AssumptionSet` itself is never mutated.
    """

    __slots__ = ("_keys_in_order",)

    def __init__(self) -> None:
        """Start with no reads recorded."""
        self._keys_in_order: list[AssumptionKey] = []

    def record(self, key: AssumptionKey) -> None:
        """Note that ``key`` was read (first read wins; later reads dedup)."""
        if key not in self._keys_in_order:
            self._keys_in_order.append(key)

    @property
    def keys_read(self) -> tuple[AssumptionKey, ...]:
        """Keys read so far, deduplicated, in first-read order."""
        return tuple(self._keys_in_order)


def decimal_assumption_value(assumption: Assumption[Any]) -> Decimal:
    """The assumption's value as an exact ``Decimal`` rate or factor.

    The engine reads tunable numbers only through assumptions (planning
    §5.1); a value of the wrong shape is a configuration error and must
    fail loudly, never coerce — ``Decimal`` end-to-end is the §4.6 rule
    (a bool is rejected explicitly: it is an ``int`` subtype but never
    a number here).

    Raises:
        TypeError: If the value is not a ``Decimal``.
    """
    value = assumption.value
    if not isinstance(value, Decimal):
        msg = (
            f"assumption {assumption.key!r} must hold a Decimal value,"
            f" got {type(value).__name__}"
        )
        raise TypeError(msg)
    return value


def int_assumption_value(assumption: Assumption[Any]) -> int:
    """The assumption's value as a whole number (e.g. a planning age).

    Raises:
        TypeError: If the value is not an ``int`` (``bool`` rejected).
    """
    value = assumption.value
    if isinstance(value, bool) or not isinstance(value, int):
        msg = (
            f"assumption {assumption.key!r} must hold an integer value,"
            f" got {type(value).__name__}"
        )
        raise TypeError(msg)
    return value


def mapping_assumption_value(assumption: Assumption[Any]) -> Mapping[str, Any]:
    """The assumption's value as a structured table (e.g. a glide shape).

    Raises:
        TypeError: If the value is not a mapping.
    """
    value = assumption.value
    if not isinstance(value, Mapping):
        msg = (
            f"assumption {assumption.key!r} must hold a table value,"
            f" got {type(value).__name__}"
        )
        raise TypeError(msg)
    return value


@dataclass(frozen=True, slots=True)
class TrackedAssumptions:
    """Read-through view pairing an :class:`AssumptionSet` with a recorder.

    The engine reads assumptions through this view so every read lands in
    the run's provenance record with no engine-side bookkeeping.
    """

    assumptions: AssumptionSet
    recorder: AssumptionReadRecorder

    def get(self, key: AssumptionKey) -> Assumption[Any]:
        """Record the read, then return the assumption."""
        self.recorder.record(key)
        return self.assumptions.get(key)
