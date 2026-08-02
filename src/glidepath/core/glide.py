"""Life stages and glide-path allocation (roadmap 3.5; planning §5.1, §7).

The projection moves a person through ``EARLY_ACCUMULATION →
MID_ACCUMULATION → PRE_RETIREMENT → DECUMULATION``. Stage is *derived*
each period from years-to-target-retirement — never stored — and the
glide path maps years-to-retirement to an asset allocation by
interpolating a factor table (:class:`GlidePathConfig`).

Stage boundaries (planning §5.1): ``DECUMULATION`` once the target
retirement age is attained by the period's first day (years-to-retirement
≤ 0, matching the §4.1 gate convention); ``PRE_RETIREMENT`` inside the
table's de-risking window (its highest knot); the ``EARLY`` / ``MID``
split falls at twice that window.

The default shape ships as the ``glidepath.default_shape`` assumption
(planning §7), overridable per person; :func:`glide_path_from_shape`
turns that structured value into a config.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from enum import Enum, auto
from itertools import pairwise
from typing import TYPE_CHECKING

from glidepath.core.investments import AssetAllocation
from glidepath.core.periods import age_on

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from glidepath.core.periods import Period

_ZERO = Decimal(0)
_ONE = Decimal(1)

_FRACTION_EXPONENT = Decimal("1e-9")
"""Quantization for the interpolation fraction between two knots.

A short fraction keeps every interpolated weight product exact in
``Decimal`` arithmetic, so interpolated allocations still sum to exactly
1; a billionth of the span between knots is far below any planning
significance. Config resolution is not ledger arithmetic, so this does
not breach the never-quantize-rates policy (planning §4.6).
"""

_SHAPE_LINEAR = "linear"
_SHAPE_HOLD = "hold"


class LifeStage(Enum):
    """The stages a person moves through (planning §5.1); always derived."""

    EARLY_ACCUMULATION = auto()
    MID_ACCUMULATION = auto()
    PRE_RETIREMENT = auto()
    """Inside the glide path's de-risking window."""
    DECUMULATION = auto()
    """Target retirement age attained by the period's first day."""


@dataclass(frozen=True, slots=True)
class GlidePathPoint:
    """One knot of the factor table: the allocation held at this distance."""

    years_to_retirement: int
    allocation: AssetAllocation

    def __post_init__(self) -> None:
        """Reject knots after retirement; the 0 knot holds through drawdown."""
        if self.years_to_retirement < 0:
            msg = "GlidePathPoint.years_to_retirement must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class GlidePathConfig:
    """A years-to-retirement → allocation factor table (planning §5.1).

    Knots are strictly ascending by ``years_to_retirement``. Between
    knots the allocation interpolates linearly per asset class; beyond
    the highest knot it holds that knot's allocation, and at or past
    retirement it holds the lowest knot's — the "held through drawdown"
    behaviour of the default shape (planning §7).
    """

    points: tuple[GlidePathPoint, ...]

    def __post_init__(self) -> None:
        """Require at least one knot, strictly ascending."""
        if not self.points:
            msg = "GlidePathConfig requires at least one point"
            raise ValueError(msg)
        ascending = all(
            lower.years_to_retirement < upper.years_to_retirement
            for lower, upper in pairwise(self.points)
        )
        if not ascending:
            msg = "GlidePathConfig points must strictly ascend by years_to_retirement"
            raise ValueError(msg)

    @property
    def derisk_window_years(self) -> int:
        """Years before retirement at which de-risking begins (highest knot)."""
        return self.points[-1].years_to_retirement

    def allocation_at(self, years_to_retirement: int) -> AssetAllocation:
        """The allocation held at ``years_to_retirement`` (may be negative).

        Clamps beyond the table at both ends; interpolates linearly
        between knots.
        """
        first, last = self.points[0], self.points[-1]
        if years_to_retirement <= first.years_to_retirement:
            return first.allocation
        if years_to_retirement >= last.years_to_retirement:
            return last.allocation
        lower, upper = next(
            pair
            for pair in pairwise(self.points)
            if years_to_retirement <= pair[1].years_to_retirement
        )
        return _interpolate(lower, upper, years_to_retirement)

    def stage_at(self, years_to_retirement: int) -> LifeStage:
        """Derive the life stage at ``years_to_retirement`` (planning §5.1).

        A constant-allocation table (single knot, or a knot only at 0)
        never de-risks, so ``PRE_RETIREMENT`` is unreachable and
        accumulation runs straight into ``DECUMULATION``.
        """
        if years_to_retirement <= 0:
            return LifeStage.DECUMULATION
        window = self.derisk_window_years
        if years_to_retirement <= window:
            return LifeStage.PRE_RETIREMENT
        if years_to_retirement <= 2 * window:
            return LifeStage.MID_ACCUMULATION
        return LifeStage.EARLY_ACCUMULATION


def _interpolate(
    lower: GlidePathPoint, upper: GlidePathPoint, years_to_retirement: int
) -> AssetAllocation:
    """Linearly interpolate between two knots at an interior year."""
    span = upper.years_to_retirement - lower.years_to_retirement
    offset = years_to_retirement - lower.years_to_retirement
    fraction = (Decimal(offset) / Decimal(span)).quantize(
        _FRACTION_EXPONENT, rounding=ROUND_HALF_EVEN
    )

    def weight(start: Decimal, end: Decimal) -> Decimal:
        """One class's weight at ``fraction`` of the way from lower to upper."""
        return start + (end - start) * fraction

    return AssetAllocation(
        equity=weight(lower.allocation.equity, upper.allocation.equity),
        bonds=weight(lower.allocation.bonds, upper.allocation.bonds),
        cash=weight(lower.allocation.cash, upper.allocation.cash),
    )


def years_to_target_retirement(
    date_of_birth: date, target_retirement_age: int, period: Period
) -> int:
    """Whole years from ``period``'s first day to the target retirement age.

    Zero or negative once the age is attained by the period's first day
    — the same gate convention as §4.1, so the retirement period itself
    is already ``DECUMULATION``.
    """
    return target_retirement_age - age_on(date_of_birth, period.start)


def _shape_fraction(shape: Mapping[str, object], key: str) -> Decimal:
    """Take a required fraction in [0, 1] from the shape mapping."""
    raw = shape[key]
    if not isinstance(raw, Decimal):
        msg = f"glide-path shape {key!r} must be a Decimal fraction"
        raise TypeError(msg)
    if not _ZERO <= raw <= _ONE:
        msg = f"glide-path shape {key!r} must lie between 0 and 1"
        raise ValueError(msg)
    return raw


def _shape_tag(shape: Mapping[str, object], key: str, supported: str) -> None:
    """Require the shape tag ``key`` to hold the one supported value."""
    raw = shape[key]
    if raw != supported:
        msg = f"glide-path shape {key!r} supports only {supported!r}, got {raw!r}"
        raise ValueError(msg)


def glide_path_from_shape(shape: Mapping[str, object]) -> GlidePathConfig:
    """Build a config from the ``glidepath.default_shape`` value (planning §7).

    The shape is the structured assumption value: ``equity_start`` held
    until ``derisk_years_before_retirement`` years out, then a
    ``linear`` transition to ``equity_at_retirement``, ``hold`` through
    drawdown — the remainder of each allocation in bonds. Only those
    tags are supported; anything else here is a data error, not a
    default.

    Raises:
        KeyError: If a required shape key is missing.
        TypeError: If a shape value has the wrong type.
        ValueError: If keys are unknown or values out of range.
    """
    expected = {
        "equity_start",
        "derisk_years_before_retirement",
        "equity_at_retirement",
        "transition",
        "in_drawdown",
    }
    unknown = set(shape) - expected
    if unknown:
        msg = f"unknown glide-path shape keys: {', '.join(sorted(unknown))}"
        raise ValueError(msg)
    equity_start = _shape_fraction(shape, "equity_start")
    equity_at_retirement = _shape_fraction(shape, "equity_at_retirement")
    _shape_tag(shape, "transition", _SHAPE_LINEAR)
    _shape_tag(shape, "in_drawdown", _SHAPE_HOLD)
    derisk_years = shape["derisk_years_before_retirement"]
    if isinstance(derisk_years, bool) or not isinstance(derisk_years, int):
        msg = "glide-path shape 'derisk_years_before_retirement' must be an integer"
        raise TypeError(msg)
    if derisk_years < 1:
        msg = "glide-path shape 'derisk_years_before_retirement' must be at least 1"
        raise ValueError(msg)
    return GlidePathConfig(
        points=(
            GlidePathPoint(
                years_to_retirement=0,
                allocation=AssetAllocation(
                    equity=equity_at_retirement, bonds=_ONE - equity_at_retirement
                ),
            ),
            GlidePathPoint(
                years_to_retirement=derisk_years,
                allocation=AssetAllocation(
                    equity=equity_start, bonds=_ONE - equity_start
                ),
            ),
        )
    )
