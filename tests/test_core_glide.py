"""Tests for life stages and glide-path allocation (issue 3.5; planning §5.1)."""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

import pytest

from glidepath.core import (
    AssetAllocation,
    AssumptionKey,
    GlidePathConfig,
    GlidePathPoint,
    LifeStage,
    Period,
    glide_path_from_shape,
    years_to_target_retirement,
)
from glidepath.regions.uk.loader import load_default_assumptions

AT_RETIREMENT = AssetAllocation(equity=Decimal("0.40"), bonds=Decimal("0.60"))
AT_START = AssetAllocation(equity=Decimal("0.80"), bonds=Decimal("0.20"))
DEFAULT_CONFIG = GlidePathConfig(
    points=(
        GlidePathPoint(years_to_retirement=0, allocation=AT_RETIREMENT),
        GlidePathPoint(years_to_retirement=15, allocation=AT_START),
    )
)


def default_shape(**overrides: object) -> dict[str, object]:
    """The §7 default shape as a mapping, with per-test overrides."""
    shape: dict[str, object] = {
        "equity_start": Decimal("0.80"),
        "derisk_years_before_retirement": 15,
        "equity_at_retirement": Decimal("0.40"),
        "transition": "linear",
        "in_drawdown": "hold",
    }
    shape.update(overrides)
    return shape


def test_allocation_holds_the_start_shape_beyond_the_window() -> None:
    """Above the highest knot the table clamps to its starting allocation."""
    assert DEFAULT_CONFIG.allocation_at(40) == AT_START
    assert DEFAULT_CONFIG.allocation_at(15) == AT_START


def test_allocation_holds_through_drawdown() -> None:
    """At and past retirement the table holds the retirement allocation."""
    assert DEFAULT_CONFIG.allocation_at(0) == AT_RETIREMENT
    assert DEFAULT_CONFIG.allocation_at(-10) == AT_RETIREMENT


def test_allocation_interpolates_linearly_between_knots() -> None:
    """Nine years out is 60% of the way from 40/60 to 80/20."""
    allocation = DEFAULT_CONFIG.allocation_at(9)
    assert allocation == AssetAllocation(equity=Decimal("0.64"), bonds=Decimal("0.36"))


def test_interpolated_weights_sum_to_exactly_one() -> None:
    """A non-terminating fraction (7/15) still yields a complete allocation."""
    allocation = DEFAULT_CONFIG.allocation_at(7)
    assert allocation.equity.quantize(Decimal("1e-9")) == Decimal("0.586666667")
    assert allocation.equity + allocation.bonds + allocation.cash == 1


def test_three_class_tables_interpolate_all_classes() -> None:
    """Cash-bearing knots interpolate to a valid allocation too."""
    config = GlidePathConfig(
        points=(
            GlidePathPoint(
                years_to_retirement=0,
                allocation=AssetAllocation(
                    equity=Decimal("0.2"), bonds=Decimal("0.5"), cash=Decimal("0.3")
                ),
            ),
            GlidePathPoint(
                years_to_retirement=12,
                allocation=AssetAllocation(
                    equity=Decimal("0.7"), bonds=Decimal("0.2"), cash=Decimal("0.1")
                ),
            ),
        )
    )
    allocation = config.allocation_at(5)
    assert allocation.equity + allocation.bonds + allocation.cash == 1
    assert allocation.cash.quantize(Decimal("1e-9")) == Decimal("0.216666667")


@pytest.mark.parametrize(
    ("years", "stage"),
    [
        (31, LifeStage.EARLY_ACCUMULATION),
        (30, LifeStage.MID_ACCUMULATION),
        (16, LifeStage.MID_ACCUMULATION),
        (15, LifeStage.PRE_RETIREMENT),
        (1, LifeStage.PRE_RETIREMENT),
        (0, LifeStage.GO_GO),
        (-5, LifeStage.GO_GO),
        (-9, LifeStage.GO_GO),
        (-10, LifeStage.SLOW_GO),
        (-19, LifeStage.SLOW_GO),
        (-20, LifeStage.NO_GO),
        (-35, LifeStage.NO_GO),
    ],
)
def test_stage_derives_from_years_to_retirement(years: int, stage: LifeStage) -> None:
    """The §5.1 stage boundaries around a 15-year de-risking window.

    Retirement splits into the go-go/slow-go/no-go sub-stages one and
    two decades in (issue #114).
    """
    assert DEFAULT_CONFIG.stage_at(years) == stage


def test_a_constant_allocation_never_derisks() -> None:
    """A single knot at 0 has no window, so PRE_RETIREMENT is unreachable."""
    config = GlidePathConfig(
        points=(GlidePathPoint(years_to_retirement=0, allocation=AT_START),)
    )
    assert config.allocation_at(30) == AT_START
    assert config.stage_at(1) == LifeStage.EARLY_ACCUMULATION
    assert config.stage_at(0) == LifeStage.GO_GO


def test_a_single_high_knot_is_still_constant() -> None:
    """A lone knot at year 15 clamps everywhere, so it never de-risks either."""
    config = GlidePathConfig(
        points=(GlidePathPoint(years_to_retirement=15, allocation=AT_START),)
    )
    assert config.derisk_window_years == 0
    assert config.stage_at(10) == LifeStage.EARLY_ACCUMULATION


def test_identical_knots_are_a_constant_table() -> None:
    """Knots that never change allocation leave the window at zero."""
    config = GlidePathConfig(
        points=(
            GlidePathPoint(years_to_retirement=0, allocation=AT_START),
            GlidePathPoint(years_to_retirement=15, allocation=AT_START),
        )
    )
    assert config.derisk_window_years == 0
    assert config.stage_at(10) == LifeStage.EARLY_ACCUMULATION


def test_the_window_starts_where_the_allocation_starts_changing() -> None:
    """A plateau above the transition does not stretch the window."""
    config = GlidePathConfig(
        points=(
            GlidePathPoint(years_to_retirement=0, allocation=AT_RETIREMENT),
            GlidePathPoint(years_to_retirement=5, allocation=AT_START),
            GlidePathPoint(years_to_retirement=15, allocation=AT_START),
        )
    )
    assert config.derisk_window_years == 5
    assert config.stage_at(5) == LifeStage.PRE_RETIREMENT
    assert config.stage_at(6) == LifeStage.MID_ACCUMULATION
    assert config.stage_at(11) == LifeStage.EARLY_ACCUMULATION


def test_config_requires_at_least_one_point() -> None:
    """An empty table answers nothing."""
    with pytest.raises(ValueError, match="at least one point"):
        GlidePathConfig(points=())


def test_config_requires_strictly_ascending_knots() -> None:
    """Duplicate or descending knots make interpolation ambiguous."""
    duplicated = (
        GlidePathPoint(years_to_retirement=10, allocation=AT_START),
        GlidePathPoint(years_to_retirement=10, allocation=AT_RETIREMENT),
    )
    with pytest.raises(ValueError, match="strictly ascend"):
        GlidePathConfig(points=duplicated)


def test_points_reject_negative_years() -> None:
    """The 0 knot already covers everything after retirement."""
    with pytest.raises(ValueError, match="must be non-negative"):
        GlidePathPoint(years_to_retirement=-1, allocation=AT_RETIREMENT)


def test_years_to_target_retirement_counts_whole_years() -> None:
    """Ten years out on a period starting before the 55th birthday."""
    period = Period(date(2026, 4, 6), date(2027, 4, 5))
    assert years_to_target_retirement(date(1970, 6, 15), 65, period) == 10


def test_retirement_period_reaches_zero_years() -> None:
    """A period opening on the 65th birthday is already at retirement."""
    period = Period(date(2035, 6, 15), date(2036, 6, 14))
    assert years_to_target_retirement(date(1970, 6, 15), 65, period) == 0


def test_shape_builds_the_two_knot_linear_config() -> None:
    """The §7 default shape becomes a 0/15-year two-knot table."""
    config = glide_path_from_shape(default_shape())
    assert config == DEFAULT_CONFIG
    assert config.derisk_window_years == 15


def test_shipped_default_shape_parses_into_a_config() -> None:
    """The assumptions data file's shape value round-trips into a config."""
    defaults = load_default_assumptions()
    value = next(
        entry.value
        for entry in defaults.defaults
        if entry.key is AssumptionKey.GLIDEPATH_DEFAULT_SHAPE
    )
    assert isinstance(value, Mapping)
    assert glide_path_from_shape(value) == DEFAULT_CONFIG


def test_shape_rejects_unknown_keys() -> None:
    """A misspelt shape key is a data error, not a default."""
    shape = default_shape(extra=1)
    with pytest.raises(ValueError, match="unknown glide-path shape keys: extra"):
        glide_path_from_shape(shape)


def test_shape_requires_every_key() -> None:
    """A shape without its transition is incomplete."""
    shape = default_shape()
    del shape["transition"]
    with pytest.raises(KeyError):
        glide_path_from_shape(shape)


def test_shape_fractions_must_be_decimal() -> None:
    """A string where a Decimal fraction belongs is a type error."""
    shape = default_shape(equity_start="0.80")
    with pytest.raises(TypeError, match="must be a Decimal fraction"):
        glide_path_from_shape(shape)


def test_shape_fractions_must_be_in_range() -> None:
    """More than 100% equity is not an allocation."""
    shape = default_shape(equity_at_retirement=Decimal("1.2"))
    with pytest.raises(ValueError, match="must lie between 0 and 1"):
        glide_path_from_shape(shape)


@pytest.mark.parametrize("key", ["transition", "in_drawdown"])
def test_shape_supports_only_the_shipped_tags(key: str) -> None:
    """Only linear de-risking held through drawdown is implemented."""
    shape = default_shape(**{key: "cliff"})
    with pytest.raises(ValueError, match=f"glide-path shape '{key}' supports only"):
        glide_path_from_shape(shape)


def test_shape_derisk_years_must_be_an_integer() -> None:
    """A boolean is not a year count."""
    shape = default_shape(derisk_years_before_retirement=True)
    with pytest.raises(TypeError, match="must be an integer"):
        glide_path_from_shape(shape)


def test_shape_derisk_years_must_be_positive() -> None:
    """A zero-year window would duplicate the retirement knot."""
    shape = default_shape(derisk_years_before_retirement=0)
    with pytest.raises(ValueError, match="at least 1"):
        glide_path_from_shape(shape)
