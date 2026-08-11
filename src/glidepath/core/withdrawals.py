"""Withdrawal strategies and plans (roadmap 5.1; planning §5.2 step 4).

A :class:`WithdrawalStrategy` decides how a decumulation period's net
spending need is met from the household's wrappers — one pooled step
per period, every person's sources in one state (planning §4.11). The
engine builds a :class:`WithdrawalState` — every drawable sub-balance
with its owner, balance, tax-free fraction, and access-gate position —
and the strategy returns a :class:`WithdrawalPlan` for the engine to
execute:

- a :class:`NetWithdrawalPlan` states a **net (after-tax) target** and an
  ordered source list; the engine grosses each draw up against the
  region tax system by fixed-point iteration (planning §5.2 step 4);
- a :class:`GrossWithdrawalPlan` states exact **gross** amounts per
  source and skips the iteration entirely.

Strategies encode the wrapper ordering (planning §5.2): the tax-aware
default of :func:`tax_aware_order` draws taxable-growth accounts
(GIA/cash) first, then wholly tax-free sub-balances, then funds
already in drawdown (no fresh tax-free cash), then uncrystallised
funds whose access gate is open — the full GIA/cash → ISA → pension
default. Access ages are respected by construction: the ordering never
includes a gate-closed source, and the engine refuses any plan that
draws on one.

Everything here is region-agnostic: sources describe themselves through
the generic tax-treatment vocabulary of :mod:`glidepath.core.wrappers`,
so no account kind is ever named (planning §4.2).
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto
from typing import TYPE_CHECKING, ClassVar, Protocol

from glidepath.core.money import Money, Rate

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from glidepath.core.entities import EntityId
    from glidepath.core.wrappers import WrapperKindId

_ZERO = Money(Decimal(0))
_ZERO_FRACTION = Decimal(0)
_ONE = Decimal(1)
_DEFAULT_UPPER_GUARDRAIL = Rate(Decimal("0.06"))
_DEFAULT_LOWER_GUARDRAIL = Rate(Decimal("0.04"))
_DEFAULT_ADJUSTMENT = Decimal("0.1")


class TaxFreeCashStrategy(Enum):
    """How pension tax-free cash is taken (planning §5.2, roadmap 5.2).

    A decision record on the run configuration, orthogonal to the
    withdrawal strategy — any combination of the two is valid. The
    names are generic (planning §4.2); the region's tax treatment
    supplies the tax-free fraction and the lifetime cap
    (:meth:`~glidepath.core.wrappers.WrapperRuleset.lump_sum_allowance`).
    Gross-defined plans resolve every mode as
    :attr:`SPLIT_EACH_PAYMENT` — an exact gross amount is a payment
    instruction, not a designation (planning §5.2).
    """

    SPLIT_EACH_PAYMENT = auto()
    """Every uncrystallised draw carries the tax-free fraction (UK: UFPLS).

    The default. The remainder of each payment arrives as taxable
    income, so the first payment marks flexible access.
    """

    LUMP_SUM_AS_NEEDED = auto()
    """Tax-free cash first, designating the rest (UK: phased FAD).

    An uncrystallised draw delivers tax-free cash only, moving the
    crystallised remainder into the wrapper's drawdown sub-balance,
    which stays invested; taxable income is drawn only once tax-free
    cash cannot meet the remaining need — so flexible access is not
    marked until taxable income actually flows.
    """

    UP_FRONT_LUMP_SUM = auto()
    """Full crystallisation at first open access (UK: PCLS up front).

    In the first decumulation period whose access gate is open, each
    uncrystallised pension pot crystallises whole: the capped tax-free
    lump sum joins the period's income offset and the remainder moves
    to the crystallised sub-balance. Lump-sum cash beyond the period's
    need banks into the person's first uncapped taxable wrapper
    (GIA/cash, roadmap 9.2); with none it is spent (planning §5.2).
    """


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
    ``tax_free_fraction`` is the *nominal* share of a draw that
    arrives tax-free (1 for a wholly tax-free wrapper, the region's
    fraction for a partially tax-free pot, 0 for taxable income) —
    for pension sources the tax-free element is additionally capped by
    the remaining lifetime headroom
    (:attr:`WithdrawalState.tax_free_cash_headroom`), so a draw past
    the cap delivers less than the fraction alone promises (roadmap
    5.2); ``access_open`` follows the §4.1 gate convention —
    crystallised funds are always open (already accessed, never
    re-gated; planning §5.1).

    ``natural_yield`` is the income this sub-balance throws off over
    the period — its balance at the shipped per-asset yield
    assumptions through the wrapper's allocation, scaled by the
    period's active fraction (roadmap 5.3). The engine prices it only
    for strategies declaring
    :attr:`WithdrawalStrategy.uses_natural_yield`, so runs that never
    read the yield assumptions never record them in provenance; other
    strategies see zero.

    ``person_id`` names the person whose wrapper holds this
    sub-balance (planning §4.11): the household withdrawal step pools
    every person's sources into one state, and a draw prices against —
    and updates — the owner's own tax position, lump-sum-allowance
    headroom, and flexible-access marker.
    """

    id: WithdrawalSourceId
    kind: WrapperKindId
    available: Money
    tax_free_fraction: Decimal
    access_open: bool
    person_id: EntityId
    natural_yield: Money = _ZERO
    growth_taxable: bool = False
    """Whether the wrapper's growth is taxed as it arises (roadmap 9.2).

    Drawing a taxable-growth account (a GIA or cash account) first
    stops future income tax accruing on what it holds, so the default
    ordering spends these before tax-sheltered accounts — the core
    reads the flag from the generic tax-treatment vocabulary, never
    from the kind (planning §4.2).
    """

    def __post_init__(self) -> None:
        """Reject a negative balance, yield, or fraction outside [0, 1]."""
        if self.available < _ZERO:
            msg = "WithdrawalSource.available must be non-negative"
            raise ValueError(msg)
        if not _ZERO_FRACTION <= self.tax_free_fraction <= _ONE:
            msg = "WithdrawalSource.tax_free_fraction must lie between 0 and 1"
            raise ValueError(msg)
        if self.natural_yield < _ZERO:
            msg = "WithdrawalSource.natural_yield must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class WithdrawalState:
    """What a strategy may read when planning a period's withdrawals.

    ``sources`` lists every sub-balance of every person in the
    household — person order, then plan (wrapper) order (planning
    §4.11) — gate-closed sources included, flagged, so a strategy can
    see the whole household pot; ``year_fraction`` is the period's
    active fraction (roadmap 4.6), by which gross-defined annual
    amounts scale. ``tax_free_cash_headroom`` maps each person's id to
    the tax-free cash still allowed under the region's lifetime cap as
    the withdrawal step opens — the cap is an individual one (planning
    §4.11), with cumulative usage (the ``lsa_used`` fact plus
    everything this run has paid, income lump sums included) already
    deducted — or ``None`` where the region has no cap (roadmap 5.2),
    so a tax-aware strategy can size pension draws against the caps
    the engine will actually enforce.
    """

    sources: tuple[WithdrawalSource, ...]
    year_fraction: Decimal
    tax_free_cash_headroom: Mapping[EntityId, Money | None] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        """Require a fraction in [0, 1] and non-negative headrooms."""
        if not _ZERO_FRACTION <= self.year_fraction <= _ONE:
            msg = "WithdrawalState.year_fraction must lie between 0 and 1"
            raise ValueError(msg)
        if any(
            headroom is not None and headroom < _ZERO
            for headroom in self.tax_free_cash_headroom.values()
        ):
            msg = "WithdrawalState.tax_free_cash_headroom must be non-negative"
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
    it and the period's need is reported as shortfall (under-draw),
    while an over-draw banks into the person's first uncapped taxable
    wrapper — spent only when they hold none (roadmap 9.2).
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

    A strategy that spends portfolio income may additionally declare a
    class-level ``uses_natural_yield = True`` (see
    :class:`NaturalYieldWithdrawalStrategy`): the engine then prices
    each source's natural yield from the ``yield.*`` assumption keys
    (planning §7). The marker is deliberately *not* part of this
    protocol — pricing is opt-in, so an absent marker simply means
    ``False`` and a strategy that only implements ``withdraw`` keeps
    working (roadmap 5.3).
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
    """The default draw order (planning §5.2), gate-closed excluded.

    The full ordering is GIA/cash → ISA → pension: taxable-growth
    accounts first — every pound left in them keeps accruing income
    tax, so spending them shelters the rest — then wholly tax-free
    sub-balances (drawing them never wastes a penny of allowance),
    then funds already in drawdown — their tax-free cash is spent, so
    they cost only income tax — and last open uncrystallised pension
    funds, whose draws surrender future tax-free growth. A source
    whose access gate has not opened is excluded whatever its group:
    tax treatment says nothing about accessibility — an age-gated
    tax-free account (a LISA) is just as ungated-by-§4.1 as a
    pension. Within each group, plan (wrapper) order is preserved.
    """
    entries = tuple(entry for entry in sources if entry.access_open)
    taxable_growth = [
        entry
        for entry in entries
        if entry.tax_free_fraction == _ONE and entry.growth_taxable
    ]
    free = [
        entry
        for entry in entries
        if entry.tax_free_fraction == _ONE and not entry.growth_taxable
    ]
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
    return (*taxable_growth, *free, *crystallised, *uncrystallised)


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
    the default tax-aware order may touch, across the whole household
    (the §4.11 meaning change from "my pot" to "our pot"), gate-closed
    funds excluded —
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


@dataclass(frozen=True, slots=True)
class GuardrailsWithdrawalStrategy:
    """Guyton-Klinger-style guardrails, net-defined (roadmap 5.3).

    The need the engine passes in — the CPI-inflated spending decision,
    net of pension income — is the baseline; the strategy annualises
    the withdrawal rate it implies (need over the period's active
    fraction, over the accessible household pot — planning §4.11) and
    adjusts spending when that
    rate crosses a configured guardrail: above ``upper_guardrail`` the
    target is cut by ``cut_fraction`` (the capital-preservation rule),
    below ``lower_guardrail`` it rises by ``rise_fraction`` (the
    prosperity rule). The defaults are the conventional
    Guyton-Klinger parameters: guardrails at 6%/4% around an implied
    5% initial rate, adjusting spending by 10%.

    The protocol is pure — the same state and need always produce the
    same plan — so each period is judged afresh from the pot alone and
    adjustments never compound across periods (planning §5.2). A cut's
    unspent remainder is reported as shortfall, exactly as a
    gross-defined under-draw is: the roadmap-7.3 metrics read spending
    cuts from there. A rise is genuinely spent: the engine treats the
    adjusted target as the period's net need, so the roadmap-9.2
    sweep banks only delivery beyond it — never the rise itself.
    """

    upper_guardrail: Rate = _DEFAULT_UPPER_GUARDRAIL
    lower_guardrail: Rate = _DEFAULT_LOWER_GUARDRAIL
    cut_fraction: Decimal = _DEFAULT_ADJUSTMENT
    rise_fraction: Decimal = _DEFAULT_ADJUSTMENT

    def __post_init__(self) -> None:
        """Require ordered positive guardrails and fractions in [0, 1]."""
        if not _ZERO_FRACTION < self.lower_guardrail.value < self.upper_guardrail.value:
            msg = (
                "GuardrailsWithdrawalStrategy guardrails must satisfy"
                " 0 < lower_guardrail < upper_guardrail"
            )
            raise ValueError(msg)
        for name, fraction in (
            ("cut_fraction", self.cut_fraction),
            ("rise_fraction", self.rise_fraction),
        ):
            if not _ZERO_FRACTION <= fraction <= _ONE:
                msg = f"GuardrailsWithdrawalStrategy.{name} must lie between 0 and 1"
                raise ValueError(msg)

    def withdraw(self, state: WithdrawalState, need: Money) -> WithdrawalPlan:
        """Target the need, adjusted on a guardrail crossing."""
        ordered = tax_aware_order(state.sources)
        order = tuple(entry.id for entry in ordered)
        pot = _ZERO
        for entry in ordered:
            pot = pot + entry.available
        target = need
        if need > _ZERO and pot > _ZERO and state.year_fraction > _ZERO_FRACTION:
            annualised = need.amount / state.year_fraction / pot.amount
            if annualised > self.upper_guardrail.value:
                target = need * (_ONE - self.cut_fraction)
            elif annualised < self.lower_guardrail.value:
                target = need * (_ONE + self.rise_fraction)
        return NetWithdrawalPlan(target=target, order=order)


@dataclass(frozen=True, slots=True)
class NaturalYieldWithdrawalStrategy:
    """Spend the portfolio's income, never its capital (roadmap 5.3).

    Gross-defined: each accessible source is drawn by exactly its
    period natural yield (:attr:`WithdrawalSource.natural_yield`) —
    the income its balance throws off at the shipped per-asset yield
    assumptions, which the engine prices only because this strategy
    declares ``uses_natural_yield`` (an opt-in marker, not a protocol
    member — see :class:`WithdrawalStrategy`). Draws follow the default
    tax-aware order, and a yield taken from an uncrystallised pension
    pot resolves through the normal payment machinery (in the model an
    income draw is a withdrawal, so its tax follows the wrapper's
    rules). The net delivered floats with the pot and the tax system;
    any gap to the period's need is reported as shortfall (planning
    §5.2).
    """

    uses_natural_yield: ClassVar[bool] = True

    def withdraw(self, state: WithdrawalState, need: Money) -> WithdrawalPlan:
        """Draw every accessible source's natural yield, in order."""
        del need  # Gross-defined: the yield, not the need, sets the draw.
        draws = tuple(
            GrossDraw(source=entry.id, amount=min(entry.natural_yield, entry.available))
            for entry in tax_aware_order(state.sources)
            if entry.natural_yield > _ZERO and entry.available > _ZERO
        )
        return GrossWithdrawalPlan(draws=draws)
