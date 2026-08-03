"""Seeded randomness for Monte Carlo runs (roadmap 7.1; planning §4.6).

Randomness enters the engine only through the :class:`RandomSource`
protocol, wrapping :class:`random.Random` seeded from the run's
``RunConfig.seed`` — never module-level ``random`` (planning §4.6).
Monte Carlo path *i* uses a substream whose seed is derived from
``(seed, i)`` by :func:`derive_seed`, an explicit fixed-width digest:
``random.Random((seed, i))`` is a ``TypeError`` on our pinned Python
(3.11+ restricts seed types), so the derivation cannot be delegated to
the seed argument. Because the derivation is a pure function, paths are
order-independent and individually re-runnable — "re-run path 4711"
needs only the manifest's seed and the path index.

Reproducibility is manifest-level (§4.6): identical manifest →
identical output, on any runtime. Python guarantees that only
``random.Random.random()`` reproduces its sequence across versions;
the float distribution methods (``gauss`` et al.) go through libm,
whose last-bit rounding varies across platforms and versions. Draws
therefore cross the float→``Decimal`` boundary at the uniform step —
each ``random()`` output converts exactly — and the normal transform
runs in ``Decimal`` (Marsaglia's polar method; ``Decimal.ln()`` and
``Decimal.sqrt()`` are correctly rounded by specification), so the
same seed yields bit-identical draws on every platform this checkout
runs on (e.g. the shared Windows/WSL setup).
"""

import random
from decimal import Decimal
from hashlib import blake2b
from typing import Protocol

_DIGEST_SIZE = 16
"""Substream seeds are 128-bit digests — fixed width, planning §4.6."""

_ZERO = Decimal(0)
_ONE = Decimal(1)
_TWO = Decimal(2)
_MINUS_TWO = Decimal(-2)


class RandomSource(Protocol):
    """A seeded stream of random draws (planning §4.6).

    The engine and return models take their randomness only through
    this protocol, so a run's randomness is exactly determined by the
    seeds injected into it.
    """

    def standard_normals(self, count: int, /) -> tuple[Decimal, ...]:
        """Draw ``count`` independent standard-normal values in order."""
        ...


def derive_seed(root_seed: int, *parts: int | str) -> int:
    """Derive a substream seed from a root seed and identifying parts.

    The explicit derivation function of planning §4.6: a fixed-width
    (128-bit) BLAKE2b digest over the root seed and each part, returned
    as an integer for :class:`random.Random`. Each part is tagged with
    its type and length-prefixed inside the digest, so distinct part
    sequences can never collide — neither by concatenation nor by an
    integer shadowing its string spelling (``1`` vs ``"1"``). The Monte
    Carlo path runner (roadmap 7.3) derives path *i*'s stream as
    ``derive_seed(seed, i)``; the stochastic return model further
    scopes draws per period.
    """
    hasher = blake2b(digest_size=_DIGEST_SIZE)
    for part in (root_seed, *parts):
        hasher.update(b"i" if isinstance(part, int) else b"s")
        encoded = str(part).encode("utf-8")
        hasher.update(len(encoded).to_bytes(8, "big"))
        hasher.update(encoded)
    return int.from_bytes(hasher.digest(), "big")


class SeededRandomSource:
    """The :class:`RandomSource` wrapping ``random.Random(seed)`` (§4.6).

    Stateful by design: draws consume the underlying stream in order,
    and two sources built from the same seed produce identical
    sequences. Statistical use only — never security (the S311 ignore
    for this module in ``pyproject.toml``).
    """

    __slots__ = ("_stream",)

    def __init__(self, seed: int) -> None:
        """Seed a private stream; no global random state is touched."""
        self._stream = random.Random(seed)

    def _uniform_signed(self) -> Decimal:
        """One uniform draw on [-1, 1), converted exactly to Decimal.

        ``random()`` is the one generator method whose sequence Python
        guarantees stable across versions (module docstring), so it is
        the only float this class ever consumes.
        """
        return _TWO * Decimal(self._stream.random()) - _ONE

    def standard_normals(self, count: int, /) -> tuple[Decimal, ...]:
        """Draw ``count`` standard normals via the polar method.

        Marsaglia's polar method in ``Decimal``: accept a uniform pair
        ``(u, v)`` when ``s = u² + v²`` lands strictly inside the unit
        circle (excluding zero), then scale by ``sqrt(-2 ln(s) / s)``
        to yield two independent normals. No libm call is involved, so
        the draws are bit-identical across platforms (module
        docstring). An odd ``count`` discards the trailing normal of
        the last accepted pair — deterministically, since rejection
        sampling consumes the stream identically either way.

        Raises:
            ValueError: If ``count`` is negative.
        """
        if count < 0:
            msg = f"count must be non-negative, got {count}"
            raise ValueError(msg)
        draws: list[Decimal] = []
        while len(draws) < count:
            u = self._uniform_signed()
            v = self._uniform_signed()
            magnitude = u * u + v * v
            if magnitude >= _ONE or magnitude == _ZERO:
                continue
            scale = (_MINUS_TWO * magnitude.ln() / magnitude).sqrt()
            draws.append(u * scale)
            draws.append(v * scale)
        return tuple(draws[:count])
