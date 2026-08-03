"""Tests for annuity decision records and pricing (roadmap 5.5).

The engine-side purchase execution is covered in ``test_engine.py``;
here the :class:`AnnuityPurchase` record and the
:class:`AnnuityRateTable` — strict parsing, per-age interpolation, the
joint factor — are pinned in isolation.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.core import (
    AnnuityBasis,
    AnnuityPurchase,
    AnnuityRateTable,
    AnnuityType,
    AssumptionKey,
    Decision,
    EngineError,
    EntityId,
    annuity_base_rate_key,
    annuity_start_date,
)

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def purchase_of(
    at_age: int = 65,
    fraction: str = "0.5",
    annuity_type: AnnuityType = AnnuityType.LEVEL,
    basis: AnnuityBasis = AnnuityBasis.SINGLE,
) -> AnnuityPurchase:
    """One annuity purchase decision record."""
    return AnnuityPurchase(
        id=EntityId("annuity-1"),
        at_age=Decision(value=at_age, recorded_on=RECORDED),
        fraction_of_pot=Decision(value=Decimal(fraction), recorded_on=RECORDED),
        annuity_type=annuity_type,
        basis=basis,
    )


def table_value(
    level: dict[str, Decimal] | None = None,
    escalation: str = "0.03",
    joint_factor: str = "0.9",
) -> dict[str, object]:
    """A well-formed age-adjustment assumption value."""
    knots: dict[str, Decimal] = (
        {"60": Decimal("0.9"), "65": Decimal("1.0"), "70": Decimal("1.2")}
        if level is None
        else level
    )
    return {
        "escalation": Decimal(escalation),
        "joint_factor": Decimal(joint_factor),
        "level": knots,
        "escalating3": dict(knots),
        "inflation_linked": dict(knots),
    }


class TestAnnuityPurchase:
    """The decision record's construction-time validation."""

    def test_valid_purchase_constructs(self) -> None:
        """A whole-pot purchase at a positive age is accepted."""
        purchase = purchase_of(at_age=65, fraction="1")
        assert purchase.fraction_of_pot.value == Decimal(1)

    def test_start_date_is_the_birthday(self) -> None:
        """Income starts the exact day the person attains the age (§4.1)."""
        purchase = purchase_of(at_age=67)
        assert annuity_start_date(purchase, date(1960, 3, 15)) == date(2027, 3, 15)

    def test_zero_fraction_is_rejected(self) -> None:
        """A purchase must convert some capital."""
        with pytest.raises(ValueError, match="fraction_of_pot"):
            purchase_of(fraction="0")

    def test_fraction_above_one_is_rejected(self) -> None:
        """The pot cannot be over-annuitised."""
        with pytest.raises(ValueError, match="fraction_of_pot"):
            purchase_of(fraction="1.5")

    def test_non_positive_age_is_rejected(self) -> None:
        """A purchase age must be positive."""
        with pytest.raises(ValueError, match="at_age"):
            purchase_of(at_age=0)


class TestBaseRateKeys:
    """Each product type prices from its own §7 base rate."""

    def test_each_type_maps_to_its_key(self) -> None:
        """The three product types cover the three shipped base rates."""
        assert (
            annuity_base_rate_key(AnnuityType.LEVEL)
            is AssumptionKey.ANNUITY_LEVEL_SINGLE_65
        )
        assert (
            annuity_base_rate_key(AnnuityType.ESCALATING)
            is AssumptionKey.ANNUITY_ESCALATING3_SINGLE_65
        )
        assert (
            annuity_base_rate_key(AnnuityType.INFLATION_LINKED)
            is AssumptionKey.ANNUITY_INFLATION_LINKED_SINGLE_65
        )


class TestRateTableParsing:
    """Strict parsing of the ``annuity.age_adjustment`` value."""

    def test_parses_a_well_formed_table(self) -> None:
        """Escalation, joint factor, and per-type knots all land."""
        table = AnnuityRateTable.from_assumption_value(table_value())
        assert table.escalation.value == Decimal("0.03")
        assert table.joint_factor == Decimal("0.9")
        assert table.multipliers[AnnuityType.LEVEL][65] == Decimal("1.0")

    def test_non_table_value_is_rejected(self) -> None:
        """A bare number is not a rate table."""
        bare_number = Decimal("0.9")
        with pytest.raises(EngineError, match="expected a table"):
            AnnuityRateTable.from_assumption_value(bare_number)

    def test_missing_product_table_is_rejected(self) -> None:
        """Every product tag must be present."""
        value = table_value()
        del value["escalating3"]
        with pytest.raises(EngineError, match="escalating3"):
            AnnuityRateTable.from_assumption_value(value)

    def test_unknown_keys_are_rejected(self) -> None:
        """Unknown keys fail loudly, matching the data-file loader."""
        value = table_value()
        value["surprise"] = Decimal(1)
        with pytest.raises(EngineError, match="unknown keys: surprise"):
            AnnuityRateTable.from_assumption_value(value)

    def test_missing_escalation_is_rejected(self) -> None:
        """The escalating product's rate is required."""
        value = table_value()
        del value["escalation"]
        with pytest.raises(EngineError, match="escalation"):
            AnnuityRateTable.from_assumption_value(value)

    def test_non_decimal_multiplier_is_rejected(self) -> None:
        """Multipliers must be decimal figures."""
        value = table_value(level={"65": Decimal(1), "70": "1.2"})  # type: ignore[dict-item]
        with pytest.raises(EngineError, match="positive decimal"):
            AnnuityRateTable.from_assumption_value(value)

    def test_non_integer_age_is_rejected(self) -> None:
        """Knot ages are whole years."""
        value = table_value(level={"sixty": Decimal(1)})
        with pytest.raises(EngineError, match="whole years"):
            AnnuityRateTable.from_assumption_value(value)

    def test_empty_age_table_is_rejected(self) -> None:
        """A product with no knots cannot price anything."""
        value = table_value(level={})
        with pytest.raises(EngineError, match="must not be empty"):
            AnnuityRateTable.from_assumption_value(value)

    def test_negative_joint_factor_is_rejected(self) -> None:
        """The joint factor scales a price, so it must be positive."""
        value = table_value(joint_factor="-0.9")
        with pytest.raises(EngineError, match="joint_factor"):
            AnnuityRateTable.from_assumption_value(value)


class TestAgeMultiplier:
    """Per-age lookup: exact knots, interpolation, hard edges."""

    def test_exact_knot_returns_its_value(self) -> None:
        """A purchase at a knot age uses the knot's multiplier."""
        table = AnnuityRateTable.from_assumption_value(table_value())
        assert table.age_multiplier(AnnuityType.LEVEL, 60) == Decimal("0.9")

    def test_between_knots_interpolates_linearly(self) -> None:
        """67 sits two fifths of the way from 1.0 (65) to 1.2 (70)."""
        table = AnnuityRateTable.from_assumption_value(table_value())
        assert table.age_multiplier(AnnuityType.LEVEL, 67) == Decimal("1.08")

    def test_below_the_table_is_an_error(self) -> None:
        """No extrapolation below the lowest knot (planning §5.3)."""
        table = AnnuityRateTable.from_assumption_value(table_value())
        with pytest.raises(EngineError, match="covers ages 60-70"):
            table.age_multiplier(AnnuityType.LEVEL, 59)

    def test_above_the_table_is_an_error(self) -> None:
        """No extrapolation above the highest knot either."""
        table = AnnuityRateTable.from_assumption_value(table_value())
        with pytest.raises(EngineError, match="covers ages 60-70"):
            table.age_multiplier(AnnuityType.LEVEL, 71)

    def test_basis_factor_scales_joint_only(self) -> None:
        """Single life prices at 1; joint life at the table's factor."""
        table = AnnuityRateTable.from_assumption_value(table_value())
        assert table.basis_factor(AnnuityBasis.SINGLE) == Decimal(1)
        assert table.basis_factor(AnnuityBasis.JOINT) == Decimal("0.9")
