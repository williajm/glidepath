"""Wrapper entity and the wrapper-rules boundary (roadmap 3.1; planning §4.2, §5.1).

A :class:`Wrapper` is one tax-advantaged (or, later, taxable) account a
person holds. Wrapper *kinds* are opaque region-defined ids
(``"uk.workplace_dc"``, ``"uk.sipp"``, ``"uk.isa"``): the core never
enumerates account types — the region's :class:`WrapperRuleset` maps a
kind to its rules (limits, relief mechanics, access gates, and the tax
treatment of money going in, growing, and coming out), so nothing
region-specific leaks into the core model (planning §4.2).

The tax-treatment vocabulary here is deliberately generic — relieved vs
taxed on the way in, tax-free vs taxable growth, and tax-free, taxable,
or partially tax-free on the way out — so any region can describe an
EET pension or a TEE savings account without the core naming either.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from typing import TYPE_CHECKING, NewType, Protocol

from glidepath.core.money import Money

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core.contributions import ContributionSchedule
    from glidepath.core.entities import EntityId
    from glidepath.core.money import Rate
    from glidepath.core.periods import Period
    from glidepath.core.provenance import Fact

WrapperKindId = NewType("WrapperKindId", str)
"""Opaque region-defined wrapper kind id (e.g. ``"uk.sipp"``).

The core never interprets it; the region's :class:`WrapperRuleset` does
(planning §4.2) — the same pattern as ``TaxResidencyId``.
"""

_ZERO = Money(Decimal(0))
_ONE = Decimal(1)


class ContributionTaxTreatment(Enum):
    """How money going *into* a wrapper is treated (planning §4.2)."""

    TAX_RELIEVED = auto()
    """Contributions attract tax relief (e.g. a pension: EET in)."""

    FROM_TAXED_INCOME = auto()
    """Contributions are paid from post-tax income with no relief."""


class GrowthTaxTreatment(Enum):
    """How growth *inside* a wrapper is treated (planning §4.2)."""

    TAX_FREE = auto()
    TAXABLE = auto()
    """Growth is taxable as it arises (e.g. a bare account, roadmap 9.2)."""


class WithdrawalTaxTreatment(Enum):
    """How money coming *out of* a wrapper is treated (planning §4.2)."""

    TAX_FREE = auto()
    TAXABLE_INCOME = auto()
    PARTIALLY_TAX_FREE = auto()
    """A ``tax_free_fraction`` is free; the remainder is taxable income."""


class ReliefMechanic(Enum):
    """How contribution tax relief is delivered (planning §5.1).

    ``RELIEF_AT_SOURCE``: contributions leave taxed pay and the provider
    reclaims basic-rate relief; higher rates arrive via assessment.
    ``NET_PAY``: contributions leave pay before tax, so full marginal
    relief is immediate. Which mechanics a wrapper kind may use is the
    region's call (:meth:`WrapperRuleset.permitted_relief_mechanics`);
    the mechanics themselves land with ``ContributionSchedule``
    (roadmap 3.2).
    """

    RELIEF_AT_SOURCE = auto()
    NET_PAY = auto()


@dataclass(frozen=True, slots=True)
class WrapperTaxTreatment:
    """One wrapper kind's in/during/out tax treatment (planning §4.2).

    ``tax_free_fraction`` is required exactly when withdrawals are
    ``PARTIALLY_TAX_FREE`` and must be strictly between 0 and 1 — a
    fraction of 0 or 1 is just ``TAXABLE_INCOME`` or ``TAX_FREE`` and
    must be stated as such.
    """

    contributions: ContributionTaxTreatment
    growth: GrowthTaxTreatment
    withdrawals: WithdrawalTaxTreatment
    tax_free_fraction: Rate | None = None

    def __post_init__(self) -> None:
        """Require the fraction exactly when the treatment uses one."""
        partial = self.withdrawals is WithdrawalTaxTreatment.PARTIALLY_TAX_FREE
        if partial and self.tax_free_fraction is None:
            msg = "PARTIALLY_TAX_FREE withdrawals require a tax_free_fraction"
            raise ValueError(msg)
        if not partial and self.tax_free_fraction is not None:
            msg = f"{self.withdrawals.name} withdrawals do not take a tax_free_fraction"
            raise ValueError(msg)
        if partial and self.tax_free_fraction is not None:
            fraction = self.tax_free_fraction.value
            if not 0 < fraction < _ONE:
                msg = "tax_free_fraction must be strictly between 0 and 1"
                raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Wrapper:
    """One account a person holds, of an opaque region-defined kind.

    Balances are user-stated facts (planning §5.1). For pension kinds,
    ``balance`` is the *uncrystallised* value and ``crystallised_balance``
    holds funds already designated to drawdown — making an
    already-in-drawdown user modellable (no fresh tax-free cash on
    crystallised funds, planning §5.1); non-pension kinds leave it
    ``None``. ``contributions`` is the wrapper's planned contribution
    schedule, if any (roadmap 3.2); allocation (3.5) and fees (3.4)
    attach in later roadmap items.
    """

    id: EntityId
    kind: WrapperKindId
    balance: Fact[Money]
    crystallised_balance: Fact[Money] | None = None
    contributions: ContributionSchedule | None = None

    def __post_init__(self) -> None:
        """Reject negative balances."""
        if self.balance.value < _ZERO:
            msg = "Wrapper.balance must be non-negative"
            raise ValueError(msg)
        if (
            self.crystallised_balance is not None
            and self.crystallised_balance.value < _ZERO
        ):
            msg = "Wrapper.crystallised_balance must be non-negative"
            raise ValueError(msg)


class WrapperRuleset(Protocol):
    """Region-supplied wrapper rules (planning §4.2).

    Maps an opaque :data:`WrapperKindId` to its rules: contribution
    limits, permitted relief mechanics, access gates, and in/during/out
    tax treatment. Every period-based query resolves the region's data
    for that period, so a query outside data coverage fails loudly
    rather than answering from the wrong year. An unknown kind is an
    error, never a default.
    """

    def tax_treatment(self, kind: WrapperKindId, period: Period) -> WrapperTaxTreatment:
        """The in/during/out tax treatment of ``kind`` during ``period``."""
        ...

    def annual_contribution_limit(
        self, kind: WrapperKindId, period: Period
    ) -> Money | None:
        """The per-person cap on contributions to ``kind`` for ``period``.

        ``None`` means no per-kind cap applies here (any cross-wrapper
        measure, like a pension annual allowance, is the region's
        contribution-checking concern — roadmap 3.3). The cap is shared
        across all of a person's wrappers of the kind; enforcement lands
        with contribution schedules (roadmap 3.2).
        """
        ...

    def permitted_relief_mechanics(
        self, kind: WrapperKindId
    ) -> frozenset[ReliefMechanic]:
        """The relief mechanics ``kind`` may operate (empty: no relief)."""
        ...

    def is_access_open(
        self, kind: WrapperKindId, date_of_birth: date, period: Period
    ) -> bool:
        """Whether *new* access to ``kind`` may open in ``period``.

        Follows the §4.1 access-gate convention: open only if any access
        age is attained on or before the period's first day. This gates
        new access only — e.g. crystallising pension funds; funds the
        person has already accessed (a wrapper's crystallised balance)
        are never re-gated by a later rise in the access age (planning
        §5.1) — withdrawals from those are the decumulation logic's
        concern (§5.2).
        """
        ...
