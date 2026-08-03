"""Tests for the stochastic return model and Decimal Cholesky (roadmap 7.2).

The §4.6 reproducibility criterion as a Hypothesis property (same
assumptions + seed + path → identical returns), the purity guarantees
(path/period/call-order independence), the zero-volatility degeneration
to the deterministic model, and the correlation transform.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from glidepath.core import (
    Assumption,
    AssumptionKey,
    AssumptionReadRecorder,
    AssumptionSet,
    DeterministicReturnModel,
    Period,
    Provenance,
    Rate,
    StochasticReturnModel,
    TrackedAssumptions,
    cholesky_lower,
)

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
PERIOD = Period(date(2026, 4, 6), date(2027, 4, 5))
NEXT_PERIOD = Period(date(2027, 4, 6), date(2028, 4, 5))

DEFAULTS: dict[AssumptionKey, Decimal] = {
    AssumptionKey.INFLATION_CPI: Decimal("0.02"),
    AssumptionKey.RETURNS_EQUITY_REAL: Decimal("0.04"),
    AssumptionKey.RETURNS_BONDS_REAL: Decimal("0.005"),
    AssumptionKey.RETURNS_CASH_REAL: Decimal("-0.005"),
    AssumptionKey.VOLATILITY_EQUITY: Decimal("0.18"),
    AssumptionKey.VOLATILITY_BONDS: Decimal("0.07"),
    AssumptionKey.VOLATILITY_CASH: Decimal("0.01"),
    AssumptionKey.CORRELATION_EQUITY_BONDS: Decimal("0.2"),
    AssumptionKey.CORRELATION_EQUITY_CASH: Decimal("0.0"),
    AssumptionKey.CORRELATION_BONDS_CASH: Decimal("0.2"),
}

seeds = st.integers(min_value=-(2**64), max_value=2**64)
paths = st.integers(min_value=0, max_value=10_000)

_ZERO = Decimal(0)
_ONE = Decimal(1)


def tracked(
    overrides: dict[AssumptionKey, Decimal] | None = None,
    recorder: AssumptionReadRecorder | None = None,
) -> TrackedAssumptions:
    """A tracked view over the §7 defaults with per-key overrides."""
    values = DEFAULTS | (overrides or {})
    assumptions = AssumptionSet(
        Assumption(
            key=key,
            value=value,
            default_value=value,
            provenance=Provenance.DEFAULT_ASSUMPTION,
            source="test basis",
            recorded_on=RECORDED,
            description="test assumption",
        )
        for key, value in values.items()
    )
    return TrackedAssumptions(
        assumptions=assumptions, recorder=recorder or AssumptionReadRecorder()
    )


def model(
    seed: int = 1,
    overrides: dict[AssumptionKey, Decimal] | None = None,
    recorder: AssumptionReadRecorder | None = None,
) -> StochasticReturnModel:
    """A stochastic model over the test defaults."""
    return StochasticReturnModel(assumptions=tracked(overrides, recorder), seed=seed)


def _pearson(xs: list[Decimal], ys: list[Decimal]) -> Decimal:
    """Sample Pearson correlation, computed entirely in Decimal."""
    count = Decimal(len(xs))
    mean_x = sum(xs, start=_ZERO) / count
    mean_y = sum(ys, start=_ZERO) / count
    covariance = sum(
        ((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)),
        start=_ZERO,
    )
    variance_x = sum(((x - mean_x) ** 2 for x in xs), start=_ZERO)
    variance_y = sum(((y - mean_y) ** 2 for y in ys), start=_ZERO)
    return covariance / (variance_x.sqrt() * variance_y.sqrt())


class TestCholeskyLower:
    """The Decimal Cholesky factorisation behind the correlation transform."""

    def test_identity_factors_to_identity(self) -> None:
        """An identity correlation matrix has an identity factor."""
        identity = (
            (_ONE, _ZERO, _ZERO),
            (_ZERO, _ONE, _ZERO),
            (_ZERO, _ZERO, _ONE),
        )
        assert cholesky_lower(identity) == identity

    def test_factor_reconstructs_the_matrix(self) -> None:
        """L·Lᵀ reproduces the input to context precision."""
        matrix = (
            (_ONE, Decimal("0.2"), Decimal("0.0")),
            (Decimal("0.2"), _ONE, Decimal("0.2")),
            (Decimal("0.0"), Decimal("0.2"), _ONE),
        )
        factor = cholesky_lower(matrix)
        for i in range(3):
            for j in range(3):
                rebuilt = sum(
                    (factor[i][k] * factor[j][k] for k in range(3)), start=_ZERO
                )
                assert abs(rebuilt - matrix[i][j]) < Decimal("1e-25")

    def test_factor_is_lower_triangular(self) -> None:
        """Entries above the diagonal are exactly zero."""
        matrix = (
            (_ONE, Decimal("0.5")),
            (Decimal("0.5"), _ONE),
        )
        factor = cholesky_lower(matrix)
        assert factor[0][1] == _ZERO

    def test_rejects_a_non_square_matrix(self) -> None:
        """Row lengths must all equal the matrix size."""
        ragged = ((_ONE,), (_ZERO, _ONE))
        with pytest.raises(ValueError, match="square"):
            cholesky_lower(ragged)

    def test_rejects_an_asymmetric_matrix(self) -> None:
        """A correlation matrix is symmetric by definition."""
        asymmetric = (
            (_ONE, Decimal("0.2")),
            (Decimal("0.3"), _ONE),
        )
        with pytest.raises(ValueError, match="symmetric"):
            cholesky_lower(asymmetric)

    def test_rejects_a_non_positive_definite_matrix(self) -> None:
        """Inconsistent pairwise correlations fail loudly."""
        impossible = (
            (_ONE, Decimal("0.9"), Decimal("-0.9")),
            (Decimal("0.9"), _ONE, Decimal("0.9")),
            (Decimal("-0.9"), Decimal("0.9"), _ONE),
        )
        with pytest.raises(ValueError, match="positive definite"):
            cholesky_lower(impossible)

    def test_rejects_a_singular_matrix(self) -> None:
        """Perfect correlation gives a zero pivot, not a factor."""
        singular = ((_ONE, _ONE), (_ONE, _ONE))
        with pytest.raises(ValueError, match="positive definite"):
            cholesky_lower(singular)


class TestStochasticReturnModelReproducibility:
    """The §4.6 acceptance criterion: same inputs + seed → same result."""

    @given(seed=seeds, path=paths)
    def test_same_seed_and_path_reproduce_the_returns(
        self, seed: int, path: int
    ) -> None:
        """Two independent models with the same inputs agree exactly."""
        assert model(seed).returns_for(PERIOD, path) == model(seed).returns_for(
            PERIOD, path
        )

    def test_paths_are_order_independent(self) -> None:
        """Draw order across paths never changes a path's returns."""
        forward = model(3)
        first_then_second = (
            forward.returns_for(PERIOD, 0),
            forward.returns_for(PERIOD, 1),
        )
        backward = model(3)
        second_then_first = (
            backward.returns_for(PERIOD, 1),
            backward.returns_for(PERIOD, 0),
        )
        assert first_then_second == tuple(reversed(second_then_first))

    def test_periods_are_order_independent(self) -> None:
        """A single period of a path is individually re-runnable."""
        forward = model(3)
        early = forward.returns_for(PERIOD, 0)
        late = forward.returns_for(NEXT_PERIOD, 0)
        backward = model(3)
        assert backward.returns_for(NEXT_PERIOD, 0) == late
        assert backward.returns_for(PERIOD, 0) == early

    def test_different_paths_draw_different_returns(self) -> None:
        """Substreams differ by path (planning §4.6)."""
        instance = model(3)
        assert instance.returns_for(PERIOD, 0) != instance.returns_for(PERIOD, 1)

    def test_different_periods_draw_different_returns(self) -> None:
        """Substreams differ by period within one path."""
        instance = model(3)
        assert instance.returns_for(PERIOD, 0) != instance.returns_for(NEXT_PERIOD, 0)

    def test_different_seeds_draw_different_returns(self) -> None:
        """The run seed drives every substream."""
        assert model(3).returns_for(PERIOD, 0) != model(4).returns_for(PERIOD, 0)


class TestStochasticReturnModelBehaviour:
    """Distributional shape, CPI handling, provenance, and validation."""

    def test_cpi_stays_the_assumed_path(self) -> None:
        """One inflation truth per run: CPI is not stochastic (§5.2)."""
        instance = model(9)
        for path in range(5):
            returns = instance.returns_for(PERIOD, path)
            assert returns.cpi == Rate(Decimal("0.02"))

    def test_zero_volatility_degenerates_to_the_deterministic_model(self) -> None:
        """With zero sigma every draw is the expected nominal rate."""
        no_volatility = {
            AssumptionKey.VOLATILITY_EQUITY: _ZERO,
            AssumptionKey.VOLATILITY_BONDS: _ZERO,
            AssumptionKey.VOLATILITY_CASH: _ZERO,
        }
        stochastic = model(5, overrides=no_volatility).returns_for(PERIOD, 7)
        deterministic = DeterministicReturnModel(assumptions=tracked()).returns_for(
            PERIOD, 7
        )
        precision = Decimal("1e-20")
        pairs = (
            (stochastic.assets.equity, deterministic.assets.equity),
            (stochastic.assets.bonds, deterministic.assets.bonds),
            (stochastic.assets.cash, deterministic.assets.cash),
        )
        for drawn, expected in pairs:
            assert drawn.value.quantize(precision) == expected.value.quantize(precision)

    def test_sample_mean_matches_the_expected_nominal_return(self) -> None:
        """The lognormal is mean-matched to (1 + real)(1 + cpi) - 1."""
        instance = model(11)
        count = 500
        total = sum(
            (
                instance.returns_for(PERIOD, path).assets.equity.value
                for path in range(count)
            ),
            start=_ZERO,
        )
        expected = Decimal("0.0608")
        assert abs(total / count - expected) < Decimal("0.05")

    def test_high_correlation_shows_in_the_draws(self) -> None:
        """Correlated substreams move together (Cholesky transform)."""
        overrides = {
            AssumptionKey.CORRELATION_EQUITY_BONDS: Decimal("0.9"),
            AssumptionKey.CORRELATION_BONDS_CASH: Decimal("0.0"),
        }
        instance = model(13, overrides=overrides)
        equity_logs: list[Decimal] = []
        bond_logs: list[Decimal] = []
        for path in range(400):
            returns = instance.returns_for(PERIOD, path)
            equity_logs.append((_ONE + returns.assets.equity.value).ln())
            bond_logs.append((_ONE + returns.assets.bonds.value).ln())
        assert _pearson(equity_logs, bond_logs) > Decimal("0.6")

    def test_every_assumption_read_is_recorded(self) -> None:
        """All ten stochastic inputs land in the run's provenance."""
        recorder = AssumptionReadRecorder()
        model(1, recorder=recorder).returns_for(PERIOD, 0)
        assert set(recorder.keys_read) == set(DEFAULTS)

    def test_negative_volatility_is_rejected(self) -> None:
        """A negative standard deviation is a configuration error."""
        overrides = {AssumptionKey.VOLATILITY_BONDS: Decimal("-0.01")}
        instance = model(1, overrides=overrides)
        with pytest.raises(ValueError, match="non-negative"):
            instance.returns_for(PERIOD, 0)

    def test_out_of_range_correlation_is_rejected(self) -> None:
        """Correlations live in [-1, 1]."""
        overrides = {AssumptionKey.CORRELATION_EQUITY_CASH: Decimal("1.5")}
        instance = model(1, overrides=overrides)
        with pytest.raises(ValueError, match="between -1 and 1"):
            instance.returns_for(PERIOD, 0)

    def test_inconsistent_correlations_are_rejected(self) -> None:
        """A non-positive-definite correlation matrix fails loudly."""
        overrides = {
            AssumptionKey.CORRELATION_EQUITY_BONDS: Decimal("0.9"),
            AssumptionKey.CORRELATION_EQUITY_CASH: Decimal("-0.9"),
            AssumptionKey.CORRELATION_BONDS_CASH: Decimal("0.9"),
        }
        instance = model(1, overrides=overrides)
        with pytest.raises(ValueError, match="positive definite"):
            instance.returns_for(PERIOD, 0)

    def test_non_positive_expected_gross_is_rejected(self) -> None:
        """A lognormal cannot centre on a non-positive gross return."""
        overrides = {AssumptionKey.RETURNS_EQUITY_REAL: Decimal("-1.5")}
        instance = model(1, overrides=overrides)
        with pytest.raises(ValueError, match="must be positive"):
            instance.returns_for(PERIOD, 0)
