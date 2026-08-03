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

Draws cross the float→``Decimal`` boundary here and nowhere else: the
Mersenne Twister and its Gaussian transform are float machinery inside
the stdlib, and each draw is converted exactly to ``Decimal`` before
any glidepath arithmetic touches it (the §4.6 Decimal-end-to-end rule).
"""

import random
from decimal import Decimal
from hashlib import blake2b
from typing import Protocol

_DIGEST_SIZE = 16
"""Substream seeds are 128-bit digests — fixed width, planning §4.6."""


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
    as an integer for :class:`random.Random`. Each part is
    length-prefixed inside the digest, so distinct part sequences can
    never collide by concatenation. The Monte Carlo path runner
    (roadmap 7.3) derives path *i*'s stream as ``derive_seed(seed, i)``;
    the stochastic return model further scopes draws per period.
    """
    hasher = blake2b(digest_size=_DIGEST_SIZE)
    for part in (root_seed, *parts):
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

    def standard_normals(self, count: int, /) -> tuple[Decimal, ...]:
        """Draw ``count`` standard normals, each converted exactly.

        ``Decimal(float)`` is an exact conversion; no float arithmetic
        happens after the draw leaves the stdlib generator.

        Raises:
            ValueError: If ``count`` is negative.
        """
        if count < 0:
            msg = f"count must be non-negative, got {count}"
            raise ValueError(msg)
        return tuple(Decimal(self._stream.gauss()) for _ in range(count))
