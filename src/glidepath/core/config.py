"""Run configuration for the projection engine (roadmap 4.1; planning §5.2).

Kept apart from the engine so the result types can carry the run's
configuration (part of the §4.6 run manifest) without an import cycle.
"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from glidepath.core.withdrawals import (
    FixedRealWithdrawalStrategy,
    TaxFreeCashStrategy,
)

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
    """Projection mode (planning §5.2).

    The same step function runs under both modes; only the return model
    differs (planning §5.2). ``MONTE_CARLO`` draws stochastic returns
    from the run's seed — one substream per path (roadmap 7.3).
    """

    DETERMINISTIC = auto()
    MONTE_CARLO = auto()


@dataclass(frozen=True, slots=True)
class RunConfig:
    """One run's configuration (planning §5.2, §4.6).

    ``today`` anchors the first period and defines "today's money" for
    the reporting layer. ``horizon_end`` defaults to the date the (v1
    single) person attains the ``horizon.planning_age`` assumption.
    ``seed`` is recorded in provenance and seeds the stochastic return
    model's substreams (roadmap 7.1/7.2); a ``MONTE_CARLO`` run
    requires it, and ``path`` names the substream the run draws from —
    the path runner (roadmap 7.3) projects path *i* under
    ``replace(config, path=i)``, so any single path is re-runnable
    from the seed and index alone (planning §4.6; a deterministic
    model ignores the index). ``withdrawal_strategy`` is
    the decumulation withdrawal decision (planning §5.2; roadmap 5.1),
    defaulting to fixed real spending — it governs decumulation
    periods only; planned outflows falling earlier are funded
    net-defined in the default tax-aware order. ``tax_free_cash`` is
    the orthogonal tax-free cash decision (roadmap 5.2), defaulting to
    the split-each-payment mode.
    """

    today: date
    horizon_end: date | None = None
    mode: RunMode = RunMode.DETERMINISTIC
    seed: int | None = None
    path: int = 0
    withdrawal_strategy: WithdrawalStrategy = _DEFAULT_STRATEGY
    tax_free_cash: TaxFreeCashStrategy = TaxFreeCashStrategy.SPLIT_EACH_PAYMENT

    def __post_init__(self) -> None:
        """Reject a backwards horizon or a negative path index."""
        if self.horizon_end is not None and self.horizon_end < self.today:
            msg = f"horizon_end {self.horizon_end} precedes today {self.today}"
            raise EngineError(msg)
        if self.path < 0:
            msg = f"path must be non-negative, got {self.path}"
            raise EngineError(msg)
