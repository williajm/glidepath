"""Tests for seeded randomness and substream derivation (roadmap 7.1).

The acceptance criterion of planning §4.6: same inputs + seed →
identical result, as a Hypothesis property. The derivation function is
also pinned to a known value so an accidental change to the digest
scheme — which would silently re-randomise every stored run — fails
loudly.
"""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from glidepath.core import SeededRandomSource, derive_seed

seeds = st.integers(min_value=-(2**64), max_value=2**64)
paths = st.integers(min_value=0, max_value=100_000)

_SEED_BITS = 128


class TestDeriveSeed:
    """The explicit fixed-width substream derivation of planning §4.6."""

    @given(seed=seeds, path=paths)
    def test_same_inputs_derive_the_same_seed(self, seed: int, path: int) -> None:
        """The derivation is a pure function of its inputs."""
        assert derive_seed(seed, path) == derive_seed(seed, path)

    @given(seed=seeds, path=paths)
    def test_paths_get_distinct_substreams(self, seed: int, path: int) -> None:
        """Adjacent paths must never share a substream."""
        assert derive_seed(seed, path) != derive_seed(seed, path + 1)

    @given(seed=seeds, path=paths)
    def test_derived_seed_is_a_fixed_width_non_negative_integer(
        self, seed: int, path: int
    ) -> None:
        """The digest is 128 bits, usable directly as a Random seed."""
        derived = derive_seed(seed, path)
        assert 0 <= derived < 2**_SEED_BITS

    def test_parts_are_length_prefixed_against_concatenation_collisions(self) -> None:
        """Distinct part sequences never collide by concatenation."""
        assert derive_seed(0, 1, 2) != derive_seed(0, 12)
        assert derive_seed(0, "a", "b") != derive_seed(0, "ab")

    def test_string_parts_scope_substreams(self) -> None:
        """A period label yields a different substream from the bare path."""
        assert derive_seed(7, 4, "2026-04-06") != derive_seed(7, 4)

    def test_derivation_scheme_is_pinned(self) -> None:
        """A changed digest scheme would re-randomise stored runs.

        The expected value is the BLAKE2b-128 digest of the
        length-prefixed parts ``(0, 0)``, computed once when the scheme
        was fixed (roadmap 7.1).
        """
        assert derive_seed(0, 0) == 0xF2CA0B5858EC2319842FFAACFC8E53AE


class TestSeededRandomSource:
    """The RandomSource implementation wrapping ``random.Random``."""

    @given(seed=seeds)
    def test_same_seed_yields_identical_draws(self, seed: int) -> None:
        """Reproducibility (§4.6): same seed → identical sequence."""
        first = SeededRandomSource(seed).standard_normals(8)
        second = SeededRandomSource(seed).standard_normals(8)
        assert first == second

    def test_draws_consume_the_stream_in_order(self) -> None:
        """Consecutive draws continue the stream rather than restart it."""
        source = SeededRandomSource(42)
        first = source.standard_normals(4)
        second = source.standard_normals(4)
        assert first != second
        assert (*first, *second) == SeededRandomSource(42).standard_normals(8)

    def test_draws_are_finite_decimals(self) -> None:
        """Every draw crosses the float boundary into exact Decimal."""
        draws = SeededRandomSource(1).standard_normals(16)
        assert len(draws) == 16
        assert all(isinstance(draw, Decimal) for draw in draws)
        assert all(draw.is_finite() for draw in draws)

    def test_zero_count_is_an_empty_tuple(self) -> None:
        """Zero draws is a valid request."""
        assert SeededRandomSource(1).standard_normals(0) == ()

    def test_negative_count_is_rejected(self) -> None:
        """A negative draw count is a caller bug."""
        source = SeededRandomSource(1)
        with pytest.raises(ValueError, match="non-negative"):
            source.standard_normals(-1)

    def test_draws_look_standard_normal(self) -> None:
        """Mean near 0 and variance near 1 over a fixed-seed sample."""
        count = 2000
        zero = Decimal(0)
        draws = SeededRandomSource(20260803).standard_normals(count)
        mean = sum(draws, start=zero) / count
        variance = sum(((draw - mean) ** 2 for draw in draws), start=zero) / count
        assert abs(mean) < Decimal("0.1")
        assert Decimal("0.85") < variance < Decimal("1.15")
