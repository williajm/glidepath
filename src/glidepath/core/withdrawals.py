"""Withdrawal strategies and plans (roadmap 5.1; planning §5.2 step 4).

A :class:`WithdrawalStrategy` decides how a decumulation period's net
spending need is met from the person's wrappers. The engine builds a
:class:`WithdrawalState` — every drawable sub-balance with its balance,
tax-free fraction, and access-gate position — and the strategy returns a
:class:`WithdrawalPlan` for the engine to execute:

- a :class:`NetWithdrawalPlan` states a **net (after-tax) target** and an
  ordered source list; the engine grosses each draw up against the
  region tax system by fixed-point iteration (planning §5.2 step 4);
- a :class:`GrossWithdrawalPlan` states exact **gross** amounts per
  source and skips the iteration entirely.

Strategies encode the wrapper ordering (planning §5.2): the tax-aware
default of :func:`tax_aware_order` draws wholly tax-free sub-balances
first, then funds already in drawdown (no fresh tax-free cash), then
uncrystallised funds whose access gate is open — which, before the
GIA/cash wrappers of roadmap 9.2 land, reduces to ISA → pension. Access
ages are respected by construction: the ordering never includes a
gate-closed source, and the engine refuses any plan that draws on one.

Everything here is region-agnostic: sources describe themselves through
the generic tax-treatment vocabulary of :mod:`glidepath.core.wrappers`,
so no account kind is ever named (planning §4.2).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from glidepath.core.money import Money, Rate

if TYPE_CHECKING:
    from collections.abc import Iterable

    from glidepath.core.entities import EntityId
    from glidepath.core.wrappers import WrapperKindId

_ZERO = Money(Decimal(0))
_ZERO_FRACTION = Decimal(0)
_ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class WithdrawalSourceId:
    """A stable reference to one drawable sub-balance.

    Pension wrappers hold two (planning §5.1): the uncrystallised pot
    and the funds already designated to drawdown. Plans reference
    sources by this key, so a strategy never touches engine internals.
    """

    wrapper_id: EntityId
    crystallised: bool


@dataclass(frozen=True, slots=True)
class WithdrawalSource:
    """One drawable sub-balance as a strategy sees it (planning §5.2).

    ``available`` is the balance at the start of the withdrawal step;
    ``tax_free_fraction`` is the share of a draw that arrives tax-free
    (1 for a wholly tax-free wrapper, the region's fraction for a
    partially tax-free pot, 0 for taxable income); ``access_open``
    follows the §4.1 gate convention — crystallised funds are always
    open (already accessed, never re-gated; planning §5.1).
    """

    id: WithdrawalSourceId
    kind: WrapperKindId
    available: Money
    tax_free_fraction: Decimal
    access_open: bool

    def __post_init__(self) -> None:
        """Reject a negative balance or a fraction outside [0, 1]."""
        if self.available < _ZERO:
            msg = "WithdrawalSource.available must be non-negative"
            raise ValueError(msg)
        if not _ZERO_FRACTION <= self.tax_free_fraction <= _ONE:
            msg = "WithdrawalSource.tax_free_fraction must lie between 0 and 1"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class WithdrawalState:
    """What a strategy may read when planning a period's withdrawals.

    ``sources`` lists every sub-balance in plan (wrapper) order —
    gate-closed sources included, flagged, so a strategy can see the
    whole pot; ``year_fraction`` is the period's active fraction
    (roadmap 4.6), by which gross-defined annual amounts scale.
    """

    sources: tuple[WithdrawalSource, ...]
    year_fraction: Decimal

    def __post_init__(self) -> None:
        """Require a fraction in [0, 1]."""
        if not _ZERO_FRACTION <= self.year_fraction <= _ONE:
            msg = "WithdrawalState.year_fraction must lie between 0 and 1"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class NetWithdrawalPlan:
    """Deliver ``target`` net cash, drawing ``order`` front to back.

    The engine grosses each draw up against the region tax system until
    the target is met or the listed sources are exhausted (planning
    §5.2 step 4); the unmet remainder is the period's shortfall.
    """

    target: Money
    order: tuple[WithdrawalSourceId, ...]

    def __post_init__(self) -> None:
        """Reject a negative target."""
        if self.target < _ZERO:
            msg = "NetWithdrawalPlan.target must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class GrossDraw:
    """One gross draw; execution caps it at the source's balance."""

    source: WithdrawalSourceId
    amount: Money

    def __post_init__(self) -> None:
        """Reject a negative amount."""
        if self.amount < _ZERO:
            msg = "GrossDraw.amount must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class GrossWithdrawalPlan:
    """Draw exact gross amounts, in order, with no net gross-up.

    The net cash delivered is whatever remains after tax; a gap between
    it and the period's need is reported as shortfall (under-draw) or
    simply spent (over-draw — income beyond the need is not banked
    until the cash/GIA wrappers of roadmap 9.2 land).
    """

    draws: tuple[GrossDraw, ...]


type WithdrawalPlan = NetWithdrawalPlan | GrossWithdrawalPlan
"""What a strategy returns: net-defined or gross-defined (planning §5.2)."""


class WithdrawalStrategy(Protocol):
    """The decumulation withdrawal decision (planning §5.1, §5.2).

    A strategy is a *decision record* — a user choice, part of the
    scenario what-if whitelist (planning §4.3) — carried on the run
    configuration (§5.2). Implementations must be pure: the same state
    and need always produce the same plan (planning §4.6).
    """

    def withdraw(self, state: WithdrawalState, need: Money) -> WithdrawalPlan:
        """Plan one period's withdrawals toward ``need`` net cash.

        ``need`` is the net (after-tax) cash still required once
        net-of-tax pension income has met what it can (planning §5.1);
        gross-defined strategies are free to ignore it.
        """
        ...


def tax_aware_order(
    sources: Iterable[WithdrawalSource],
) -> tuple[WithdrawalSource, ...]:
    """The v1 default draw order (planning §5.2), gate-closed excluded.

    Wholly tax-free sub-balances first (drawing them never wastes a
    penny of allowance), then funds already in drawdown — their
    tax-free cash is spent, so they cost only income tax — and last
    open uncrystallised pension funds, whose draws surrender future
    tax-free growth. A source whose access gate has not opened is
    excluded whatever its group: tax treatment says nothing about
    accessibility — an age-gated tax-free account (e.g. a LISA,
    roadmap 9.2) is just as ungated-by-§4.1 as a pension. Within each
    group, plan (wrapper) order is preserved. Before roadmap 9.2's
    GIA/cash wrappers this reduces to ISA → pension.
    """
    entries = tuple(entry for entry in sources if entry.access_open)
    free = [entry for entry in entries if entry.tax_free_fraction == _ONE]
    crystallised = [
        entry
        for entry in entries
        if entry.tax_free_fraction != _ONE and entry.id.crystallised
    ]
    uncrystallised = [
        entry
        for entry in entries
        if entry.tax_free_fraction != _ONE and not entry.id.crystallised
    ]
    return (*free, *crystallised, *uncrystallised)


@dataclass(frozen=True, slots=True)
class FixedRealWithdrawalStrategy:
    """Fixed real spending: meet the net need, exactly (planning §5.2).

    The need the engine passes in is already the real spending decision
    inflated by the run's CPI path (one inflation truth per run), so
    meeting it each period *is* constant real spending. Net-defined:
    the engine grosses draws up against the tax system. This is the v1
    default strategy.
    """

    def withdraw(self, state: WithdrawalState, need: Money) -> WithdrawalPlan:
        """Target the whole need over the default tax-aware order."""
        order = tuple(entry.id for entry in tax_aware_order(state.sources))
        return NetWithdrawalPlan(target=need, order=order)


@dataclass(frozen=True, slots=True)
class FixedPercentWithdrawalStrategy:
    """Fixed percentage of the pot, gross-defined (planning §5.2).

    Each period draws ``rate`` of the *accessible* pot — every source
    the default tax-aware order may touch, gate-closed funds excluded —
    scaled by the period's active fraction, allocated across sources in
    that same order. Gross-defined by declaration: the plan states
    exact gross amounts and the engine skips the net gross-up
    iteration. The net delivered therefore floats with the tax system;
    any gap to the period's need is reported as shortfall.
    """

    rate: Rate

    def __post_init__(self) -> None:
        """Require a rate in [0, 1] — a share of the pot, per year."""
        if not _ZERO_FRACTION <= self.rate.value <= _ONE:
            msg = "FixedPercentWithdrawalStrategy.rate must lie between 0 and 1"
            raise ValueError(msg)

    def withdraw(self, state: WithdrawalState, need: Money) -> WithdrawalPlan:
        """Draw the rate's share of the accessible pot, in order."""
        del need  # Gross-defined: the pot, not the need, sets the draw.
        ordered = tax_aware_order(state.sources)
        pot = _ZERO
        for entry in ordered:
            pot = pot + entry.available
        remaining = pot * (self.rate.value * state.year_fraction)
        draws: list[GrossDraw] = []
        for entry in ordered:
            if remaining <= _ZERO:
                break
            if entry.available <= _ZERO:
                continue
            amount = min(remaining, entry.available)
            draws.append(GrossDraw(source=entry.id, amount=amount))
            remaining = remaining - amount
        return GrossWithdrawalPlan(draws=tuple(draws))
