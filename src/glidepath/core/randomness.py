"""Seeded randomness for Monte Carlo runs (roadmap 7.1; planning §4.6).

Randomness enters the engine only through the :class:`RandomSource`
protocol, wrapping a seeded numpy generator — never module-level
``random`` (planning §4.6). Monte Carlo path *i* uses a substream
whose seed is derived from ``(seed, i)`` by :func:`derive_seed`, an
explicit fixed-width digest, so the derivation is a pure function:
paths are order-independent and individually re-runnable — "re-run
path 4711" needs only the manifest's seed and the path index.

Reproducibility is manifest-level (§4.6): identical manifest →
identical output on the locked dependency set. Draws come from
numpy's ``default_rng`` (PCG64), whose seeded streams are stable for
a given numpy version — the version the lockfile pins on every
platform this checkout runs on (e.g. the shared Windows/WSL setup).
Each float64 draw crosses the float→``Decimal`` boundary exactly
(binary floats convert to ``Decimal`` without rounding), so the
engine's ledger arithmetic downstream stays pure ``Decimal``.
"""

from decimal import Decimal
from hashlib import blake2b
from typing import Protocol

import numpy as np

_DIGEST_SIZE = 16
"""Substream seeds are 128-bit digests — fixed width, planning §4.6."""

_SEED_SPACE = 1 << (_DIGEST_SIZE * 8)
"""Seeds are canonicalized into the digest's non-negative 128-bit space.

``numpy.random.default_rng`` rejects negative seeds, which
:class:`random.Random` used to accept; reducing modulo the digest
space keeps every historical seed value usable and deterministic.
"""


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
    as an integer seed. Each part is tagged with its type and
    length-prefixed inside the digest, so distinct part sequences can
    never collide — neither by concatenation nor by an integer
    shadowing its string spelling (``1`` vs ``"1"``). The Monte Carlo
    path runner (roadmap 7.3) derives path *i*'s stream as
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
    """The :class:`RandomSource` wrapping a seeded numpy PCG64 stream.

    Stateful by design: draws consume the underlying stream in order,
    and two sources built from the same seed produce identical
    sequences. Statistical use only — never security material.
    """

    __slots__ = ("_stream",)

    def __init__(self, seed: int) -> None:
        """Seed a private stream; no global random state is touched."""
        self._stream = np.random.default_rng(seed % _SEED_SPACE)

    def standard_normals(self, count: int, /) -> tuple[Decimal, ...]:
        """Draw ``count`` standard normals from the numpy generator.

        The float64 draws convert exactly to ``Decimal`` — binary
        floats are decimally representable — so everything downstream
        of this boundary stays pure ``Decimal`` arithmetic (module
        docstring).

        Raises:
            ValueError: If ``count`` is negative.
        """
        if count < 0:
            msg = f"count must be non-negative, got {count}"
            raise ValueError(msg)
        draws = self._stream.standard_normal(count)
        return tuple(Decimal(draw) for draw in draws.tolist())
