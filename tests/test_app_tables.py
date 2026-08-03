"""Table-override text form tests (issue #71, planning §1).

Structured defaults edit as ``key = value`` text: the form must
round-trip exactly over every shipped table shape and reject
malformed lines before any policy parser runs.
"""

from decimal import Decimal

import pytest

from glidepath.app.tables import parse_table_text, table_edit_text
from glidepath.core import AssumptionKey
from glidepath.regions.uk import default_assumption_set

STRUCTURED_KEYS = (
    AssumptionKey.GLIDEPATH_DEFAULT_SHAPE,
    AssumptionKey.POLICY_STATE_PENSION_UPRATING,
    AssumptionKey.POLICY_TAX_FUTURE_YEARS,
    AssumptionKey.ANNUITY_AGE_ADJUSTMENT,
)


class TestRoundTrip:
    """Serialised text parses back to the identical table."""

    @pytest.mark.parametrize("key", STRUCTURED_KEYS)
    def test_shipped_default_round_trips(self, key: AssumptionKey) -> None:
        """Every shipped structured default survives the text form."""
        value = default_assumption_set().get(key).value
        round_tripped = parse_table_text(table_edit_text(value))
        assert round_tripped == value

    def test_scalar_types_infer_like_the_catalogue(self) -> None:
        """Whole numbers are ints, other numerics Decimal, the rest tags."""
        parsed = parse_table_text("years = 15\nrate = 0.80\nmode = linear")
        years = parsed["years"]
        assert years == 15
        assert isinstance(years, int)
        rate = parsed["rate"]
        assert rate == Decimal("0.80")
        assert isinstance(rate, Decimal)
        assert parsed["mode"] == "linear"

    def test_non_finite_numerics_stay_tags(self) -> None:
        """Infinity never becomes a figure; it stays text for the parsers."""
        parsed = parse_table_text("rate = Infinity")
        assert parsed["rate"] == "Infinity"

    def test_dotted_keys_nest(self) -> None:
        """Dotted keys rebuild the nested sub-tables."""
        parsed = parse_table_text("level.55 = 0.846\nlevel.65 = 1.0")
        assert parsed == {"level": {"55": Decimal("0.846"), "65": Decimal("1.0")}}

    def test_blank_lines_are_ignored(self) -> None:
        """Blank and whitespace-only lines carry no meaning."""
        parsed = parse_table_text("\nrule = cpi\n\n   \n")
        assert parsed == {"rule": "cpi"}


class TestParseErrors:
    """Malformed text is rejected with a locating message."""

    def test_line_without_equals_is_rejected(self) -> None:
        """A bare value has no key to land on."""
        with pytest.raises(ValueError, match="key = value"):
            parse_table_text("cpi")

    def test_missing_value_is_rejected(self) -> None:
        """A key with nothing after ``=`` is incomplete, not a default."""
        with pytest.raises(ValueError, match="missing value"):
            parse_table_text("rule =")

    def test_empty_key_segment_is_rejected(self) -> None:
        """A dotted path may not contain an empty segment."""
        with pytest.raises(ValueError, match="empty key"):
            parse_table_text(".rule = cpi")

    def test_duplicate_key_is_rejected(self) -> None:
        """The same key twice is ambiguous, so it fails loudly."""
        with pytest.raises(ValueError, match="duplicate"):
            parse_table_text("rule = cpi\nrule = triple_lock")

    def test_value_and_table_conflict_is_rejected(self) -> None:
        """One key may not hold both a figure and a nested table."""
        with pytest.raises(ValueError, match="both a value and a table"):
            parse_table_text("level = 1.0\nlevel.55 = 0.8")
