"""Period returns and the return-model boundary (roadmap 4.1; planning §5.2).

The engine applies one set of nominal asset-class returns and one CPI
rate per period — "one inflation truth per run" (planning §5.2): the
reporting layer (roadmap 4.4) deflates by the same CPI path the engine
grew nominal figures with. A :class:`ReturnModel` supplies both
together as a :class:`PeriodReturns`, so they cannot drift apart.

The same step function runs under the deterministic and Monte Carlo
modes; only the return model differs (planning §5.2, a design
invariant). :class:`DeterministicReturnModel` turns the expected
real-return assumptions plus the CPI assumption into the same nominal
returns every period and every path. :class:`StochasticReturnModel`
(roadmap 7.2) draws correlated lognormal nominal returns instead —
seeded, pure per ``(seed, path, period)``, mean-matched to the
deterministic composition — while CPI stays the assumed deterministic
path on every Monte Carlo path, keeping the single-inflation-truth
rule intact (stochastic inflation is out of v1 scope; the assumption
catalogue prices no CPI volatility).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from glidepath.core.investments import AssetReturns
from glidepath.core.money import Rate
from glidepath.core.provenance import AssumptionKey, decimal_assumption_value
from glidepath.core.randomness import RandomSource, SeededRandomSource, derive_seed

if TYPE_CHECKING:
    from collections.abc import Callable

    from glidepath.core.periods import Period
    from glidepath.core.provenance import TrackedAssumptions

_MINUS_ONE = Decimal(-1)
_ZERO = Decimal(0)
_ONE = Decimal(1)
_TWO = Decimal(2)
_ASSET_CLASS_COUNT = 3


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
        """Reject a CPI at or below -100%.

        Exactly -1 is rejected too: it would zero the cumulative
        inflation factor and turn an accepted assumption into a
        runtime failure one period later.
        """
        if self.cpi.value <= _MINUS_ONE:
            msg = "PeriodReturns.cpi must be greater than -1"
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


type ReturnModelFactory = Callable[[TrackedAssumptions], ReturnModel]
"""Builds a run's return model from the run's tracked assumption view.

The engine constructs its tracked view internally (every key read must
land in the run's provenance), so an injected model — a scripted
sequence fixture (roadmap 7.4), an alternative distribution — enters
through this factory rather than as a finished instance. A factory must
preserve the engine's purity (planning §4.6): the model it returns may
depend on nothing but the view it is given and its own frozen state.
"""


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


def cholesky_lower(
    matrix: tuple[tuple[Decimal, ...], ...],
) -> tuple[tuple[Decimal, ...], ...]:
    """Lower-triangular Cholesky factor of a symmetric matrix, in ``Decimal``.

    ``L`` such that ``L @ L.T`` reproduces ``matrix`` (to context
    precision), computed entirely in ``Decimal`` — the correlation
    transform of planning §5.2 never touches float.

    Raises:
        ValueError: If the matrix is not square, not symmetric, or not
            positive definite (a pivot fails to stay positive).
    """
    size = len(matrix)
    if any(len(row) != size for row in matrix):
        msg = "matrix must be square"
        raise ValueError(msg)
    if any(matrix[i][j] != matrix[j][i] for i in range(size) for j in range(i)):
        msg = "matrix must be symmetric"
        raise ValueError(msg)
    rows: list[list[Decimal]] = [[_ZERO] * size for _ in range(size)]
    for i in range(size):
        for j in range(i + 1):
            partial = sum((rows[i][k] * rows[j][k] for k in range(j)), start=_ZERO)
            if i == j:
                pivot = matrix[i][i] - partial
                if pivot <= _ZERO:
                    msg = "matrix is not positive definite"
                    raise ValueError(msg)
                rows[i][j] = pivot.sqrt()
            else:
                rows[i][j] = (matrix[i][j] - partial) / rows[j][j]
    return tuple(tuple(row) for row in rows)


@dataclass(frozen=True, slots=True)
class StochasticReturnModel:
    """Correlated lognormal return model for Monte Carlo (roadmap 7.2).

    Per asset class the period's nominal gross return is lognormal with
    its arithmetic mean equal to the deterministic composition
    ``(1 + real)(1 + cpi)`` — so the expected outcome of a Monte Carlo
    run agrees with the deterministic run by construction. The assumed
    volatility is read as the standard deviation of the annual
    log-return; draws are correlated across the three classes (in the
    fixed order equity, bonds, cash) through the ``Decimal`` Cholesky
    factor of the pairwise correlation assumptions. CPI stays the
    assumed deterministic value on every path (module docstring).

    Purity (planning §4.6): each ``(period, path)`` pair reads its
    draws from a private substream seeded by
    ``derive_seed(seed, path, period start)``, so results depend only
    on the arguments — call order is irrelevant, paths are
    order-independent, and any single period of any path is
    individually re-runnable.

    ``source_factory`` is the injectable :class:`RandomSource`
    boundary of planning §4.6: it builds the substream for a derived
    seed, defaulting to :class:`SeededRandomSource`. Injecting a
    factory (a test double, an alternative generator) must preserve
    purity: the source it returns may depend on nothing but the seed
    it is given.
    """

    assumptions: TrackedAssumptions
    seed: int
    source_factory: Callable[[int], RandomSource] = SeededRandomSource

    def returns_for(self, period: Period, path: int, /) -> PeriodReturns:
        """Draw the period's correlated nominal returns on ``path``.

        Raises:
            ValueError: If an assumed volatility is negative, a
                correlation falls outside [-1, 1] or the correlation
                matrix is not positive definite, or an expected
                nominal gross return is not positive (its logarithm
                would be undefined).
        """
        cpi = decimal_assumption_value(
            self.assumptions.get(AssumptionKey.INFLATION_CPI)
        )
        grosses = tuple(
            self._expected_gross(key, cpi)
            for key in (
                AssumptionKey.RETURNS_EQUITY_REAL,
                AssumptionKey.RETURNS_BONDS_REAL,
                AssumptionKey.RETURNS_CASH_REAL,
            )
        )
        sigmas = tuple(
            self._volatility(key)
            for key in (
                AssumptionKey.VOLATILITY_EQUITY,
                AssumptionKey.VOLATILITY_BONDS,
                AssumptionKey.VOLATILITY_CASH,
            )
        )
        factor = cholesky_lower(self._correlation_matrix())
        source = self.source_factory(
            derive_seed(self.seed, path, period.start.isoformat())
        )
        draws = source.standard_normals(_ASSET_CLASS_COUNT)
        correlated = (
            sum((row[k] * draws[k] for k in range(_ASSET_CLASS_COUNT)), start=_ZERO)
            for row in factor
        )
        equity, bonds, cash = (
            _lognormal_rate(gross, sigma, normal)
            for gross, sigma, normal in zip(grosses, sigmas, correlated, strict=True)
        )
        return PeriodReturns(
            assets=AssetReturns(equity=equity, bonds=bonds, cash=cash),
            cpi=Rate(cpi),
        )

    def _expected_gross(self, key: AssumptionKey, cpi: Decimal) -> Decimal:
        """The expected nominal gross return ``(1 + real)(1 + cpi)``.

        Raises:
            ValueError: If the gross is not positive — a lognormal
                cannot have a non-positive mean.
        """
        real = decimal_assumption_value(self.assumptions.get(key))
        gross = (_ONE + real) * (_ONE + cpi)
        if gross <= _ZERO:
            msg = f"expected nominal gross return for {key!r} must be positive"
            raise ValueError(msg)
        return gross

    def _volatility(self, key: AssumptionKey) -> Decimal:
        """The assumed annual log-return volatility, validated non-negative.

        Raises:
            ValueError: If the volatility is negative.
        """
        sigma = decimal_assumption_value(self.assumptions.get(key))
        if sigma < _ZERO:
            msg = f"volatility {key!r} must be non-negative"
            raise ValueError(msg)
        return sigma

    def _correlation_matrix(self) -> tuple[tuple[Decimal, ...], ...]:
        """The 3-by-3 correlation matrix in the equity, bonds, cash order.

        Raises:
            ValueError: If a pairwise correlation lies outside [-1, 1].
        """
        pairs: dict[AssumptionKey, Decimal] = {}
        for key in (
            AssumptionKey.CORRELATION_EQUITY_BONDS,
            AssumptionKey.CORRELATION_EQUITY_CASH,
            AssumptionKey.CORRELATION_BONDS_CASH,
        ):
            value = decimal_assumption_value(self.assumptions.get(key))
            if not _MINUS_ONE <= value <= _ONE:
                msg = f"correlation {key!r} must lie between -1 and 1"
                raise ValueError(msg)
            pairs[key] = value
        equity_bonds = pairs[AssumptionKey.CORRELATION_EQUITY_BONDS]
        equity_cash = pairs[AssumptionKey.CORRELATION_EQUITY_CASH]
        bonds_cash = pairs[AssumptionKey.CORRELATION_BONDS_CASH]
        return (
            (_ONE, equity_bonds, equity_cash),
            (equity_bonds, _ONE, bonds_cash),
            (equity_cash, bonds_cash, _ONE),
        )


def _lognormal_rate(gross: Decimal, sigma: Decimal, normal: Decimal) -> Rate:
    """One lognormal nominal return with arithmetic mean ``gross - 1``.

    ``mu = ln(gross) - sigma²/2`` makes ``E[exp(mu + sigma·Z)]`` exactly
    ``gross``, so with zero volatility the draw degenerates to the
    deterministic nominal rate. Exponentials keep the gross strictly
    positive: a lognormal loss can approach but never reach -100%.
    """
    mu = gross.ln() - sigma * sigma / _TWO
    return Rate((mu + sigma * normal).exp() - _ONE)
