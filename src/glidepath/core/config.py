"""Run configuration for the projection engine (roadmap 4.1; planning §5.2).

Kept apart from the engine so the result types can carry the run's
configuration (part of the §4.6 run manifest) without an import cycle.
"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from glidepath.core.withdrawals import FixedRealWithdrawalStrategy

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core.withdrawals import WithdrawalStrategy

_DEFAULT_STRATEGY = FixedRealWithdrawalStrategy()
"""The v1 default withdrawal decision (planning §5.2): fixed real.

A frozen, stateless instance, so sharing one default across configs is
safe.
"""


class EngineError(ValueError):
    """A projection request the engine cannot honour."""


class RunMode(Enum):
    """Projection mode (planning §5.2). Monte Carlo lands in Phase 7."""

    DETERMINISTIC = auto()


@dataclass(frozen=True, slots=True)
class RunConfig:
    """One run's configuration (planning §5.2, §4.6).

    ``today`` anchors the first period and defines "today's money" for
    the reporting layer. ``horizon_end`` defaults to the date the (v1
    single) person attains the ``horizon.planning_age`` assumption.
    ``seed`` is recorded in provenance now and drives the random source
    once Monte Carlo lands (roadmap 7.1). ``withdrawal_strategy`` is
    the decumulation withdrawal decision (planning §5.2; roadmap 5.1),
    defaulting to fixed real spending — it governs decumulation
    periods only; planned outflows falling earlier are funded
    net-defined in the default tax-aware order.
    """

    today: date
    horizon_end: date | None = None
    mode: RunMode = RunMode.DETERMINISTIC
    seed: int | None = None
    withdrawal_strategy: WithdrawalStrategy = _DEFAULT_STRATEGY

    def __post_init__(self) -> None:
        """Reject a horizon that ends before it starts."""
        if self.horizon_end is not None and self.horizon_end < self.today:
            msg = f"horizon_end {self.horizon_end} precedes today {self.today}"
            raise EngineError(msg)
