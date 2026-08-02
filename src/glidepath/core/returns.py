"""Period returns and the return-model boundary (roadmap 4.1; planning §5.2).

The engine applies one set of nominal asset-class returns and one CPI
rate per period — "one inflation truth per run" (planning §5.2): the
reporting layer (roadmap 4.4) deflates by the same CPI path the engine
grew nominal figures with. A :class:`ReturnModel` supplies both
together as a :class:`PeriodReturns`, so they cannot drift apart.

The same step function runs under the deterministic and Monte Carlo
modes; only the return model differs (planning §5.2, a design
invariant). :class:`DeterministicReturnModel` — the only v1
implementation — turns the expected real-return assumptions plus the
CPI assumption into the same nominal returns every period and every
path; the stochastic implementation lands with Monte Carlo
(roadmap 7.2).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from glidepath.core.investments import AssetReturns
from glidepath.core.money import Rate
from glidepath.core.provenance import AssumptionKey, decimal_assumption_value

if TYPE_CHECKING:
    from glidepath.core.periods import Period
    from glidepath.core.provenance import TrackedAssumptions

_MINUS_ONE = Decimal(-1)
_ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class PeriodReturns:
    """One period's nominal asset returns and CPI rate, together.

    Keeping the two in one value enforces the single-inflation-truth
    rule (planning §5.2): the CPI that built the nominal returns is the
    CPI the reporting layer deflates by.
    """

    assets: AssetReturns
    cpi: Rate

    def __post_init__(self) -> None:
        """Reject a CPI rate below -100% (prices cannot go negative)."""
        if self.cpi.value < _MINUS_ONE:
            msg = "PeriodReturns.cpi must be at least -1"
            raise ValueError(msg)


class ReturnModel(Protocol):
    """Supplies each period's returns (planning §5.2).

    The engine step function is mode-agnostic: deterministic and Monte
    Carlo runs differ only in which implementation they inject. Data
    parameters are positional-only so implementations that need neither
    (the deterministic model) remain protocol-compatible.
    """

    def returns_for(self, period: Period, path: int, /) -> PeriodReturns:
        """The nominal returns and CPI for ``period`` on ``path``."""
        ...


def nominal_rate(real: Decimal, cpi: Decimal) -> Rate:
    """Compose a real rate with CPI into a nominal rate (planning §5.2).

    ``(1 + real) * (1 + cpi) - 1`` — the exact Fisher composition, kept
    unquantized like every rate (planning §4.6).
    """
    return Rate((_ONE + real) * (_ONE + cpi) - _ONE)


@dataclass(frozen=True, slots=True)
class DeterministicReturnModel:
    """Expected-return model: the same nominal returns every period.

    Reads the expected real returns per asset class and the CPI
    assumption through the run's tracked view (so every key lands in
    the run's provenance) and composes them into nominal rates. Every
    period and every path sees the same value (planning §5.2).
    """

    assumptions: TrackedAssumptions

    def returns_for(self, _period: Period, _path: int, /) -> PeriodReturns:
        """The nominal returns and CPI (identical for every argument)."""
        cpi = decimal_assumption_value(
            self.assumptions.get(AssumptionKey.INFLATION_CPI)
        )
        real_rates = (
            decimal_assumption_value(self.assumptions.get(key))
            for key in (
                AssumptionKey.RETURNS_EQUITY_REAL,
                AssumptionKey.RETURNS_BONDS_REAL,
                AssumptionKey.RETURNS_CASH_REAL,
            )
        )
        equity, bonds, cash = (nominal_rate(real, cpi) for real in real_rates)
        return PeriodReturns(
            assets=AssetReturns(equity=equity, bonds=bonds, cash=cash),
            cpi=Rate(cpi),
        )
