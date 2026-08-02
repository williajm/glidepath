"""Tests for facts, decisions, assumptions and read-tracking (issue 1.3)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from glidepath.core.provenance import (
    Assumption,
    AssumptionKey,
    AssumptionReadRecorder,
    AssumptionSet,
    Decision,
    Fact,
    Provenance,
    TrackedAssumptions,
)

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
NAIVE = datetime(2026, 8, 1, 12, 0)  # noqa: DTZ001 -- deliberately naive input


def make_assumption(key: AssumptionKey, value: Decimal | int | str) -> Assumption[Any]:
    """Build a shipped-default assumption for tests."""
    return Assumption(
        key=key,
        value=value,
        default_value=value,
        provenance=Provenance.DEFAULT_ASSUMPTION,
        source="https://example.test/basis",
        recorded_on=RECORDED,
        description="test fixture",
    )


def test_provenance_round_trips_by_name() -> None:
    """Provenance members survive a name round-trip (persistence needs it)."""
    for member in Provenance:
        assert Provenance[member.name] is member


def test_assumption_keys_round_trip_by_dotted_id() -> None:
    """Every key resolves back from its stable dotted id."""
    for key in AssumptionKey:
        assert AssumptionKey(key.value) is key
    assert AssumptionKey("inflation.cpi") is AssumptionKey.INFLATION_CPI


def test_fact_holds_value_and_dates() -> None:
    """A fact records the value, when it was true, and when it was stated."""
    fact = Fact(value=date(1971, 4, 5), as_of=date(2026, 8, 1), recorded_on=RECORDED)
    assert fact.value == date(1971, 4, 5)
    assert fact.note is None


def test_fact_rejects_naive_recorded_on() -> None:
    """Datetimes are always tz-aware (repo rule)."""
    as_of = date(2026, 8, 1)
    with pytest.raises(ValueError, match="timezone-aware"):
        Fact(value=1, as_of=as_of, recorded_on=NAIVE)


def test_decision_rejects_naive_recorded_on() -> None:
    """Decisions carry tz-aware timestamps too."""
    with pytest.raises(ValueError, match="timezone-aware"):
        Decision(value=60, recorded_on=NAIVE)


def test_assumption_rejects_user_fact_provenance() -> None:
    """An assumption is by definition not a user-stated fact."""
    two_percent = Decimal("0.02")
    with pytest.raises(ValueError, match="USER_FACT"):
        Assumption(
            key=AssumptionKey.INFLATION_CPI,
            value=two_percent,
            default_value=two_percent,
            provenance=Provenance.USER_FACT,
            source="https://example.test",
            recorded_on=RECORDED,
            description="invalid",
        )


def test_assumption_rejects_naive_recorded_on() -> None:
    """Assumptions carry tz-aware timestamps too."""
    two_percent = Decimal("0.02")
    with pytest.raises(ValueError, match="timezone-aware"):
        Assumption(
            key=AssumptionKey.INFLATION_CPI,
            value=two_percent,
            default_value=two_percent,
            provenance=Provenance.DEFAULT_ASSUMPTION,
            source="https://example.test",
            recorded_on=NAIVE,
            description="invalid",
        )


def test_assumption_set_registers_and_returns_by_key() -> None:
    """The registry hands back the assumption for a key."""
    cpi = make_assumption(AssumptionKey.INFLATION_CPI, Decimal("0.02"))
    assumptions = AssumptionSet([cpi])
    assert assumptions.get(AssumptionKey.INFLATION_CPI) is cpi
    assert AssumptionKey.INFLATION_CPI in assumptions
    assert AssumptionKey.FEES_FUND not in assumptions
    assert assumptions.keys == frozenset({AssumptionKey.INFLATION_CPI})


def test_assumption_set_rejects_duplicate_keys() -> None:
    """One key, one assumption."""
    cpi = make_assumption(AssumptionKey.INFLATION_CPI, Decimal("0.02"))
    with pytest.raises(ValueError, match="duplicate assumption key"):
        AssumptionSet([cpi, cpi])


def test_assumption_set_raises_on_unknown_key() -> None:
    """Reading an unregistered key is an error, not a silent default."""
    assumptions = AssumptionSet([])
    with pytest.raises(KeyError, match="no assumption registered"):
        assumptions.get(AssumptionKey.FEES_FUND)


def test_tracked_reads_are_recorded_in_first_read_order() -> None:
    """Engine-side reads land in the recorder, deduplicated and ordered."""
    assumptions = AssumptionSet(
        [
            make_assumption(AssumptionKey.INFLATION_CPI, Decimal("0.02")),
            make_assumption(AssumptionKey.FEES_FUND, Decimal("0.0015")),
        ]
    )
    recorder = AssumptionReadRecorder()
    tracked = TrackedAssumptions(assumptions=assumptions, recorder=recorder)

    tracked.get(AssumptionKey.FEES_FUND)
    tracked.get(AssumptionKey.INFLATION_CPI)
    tracked.get(AssumptionKey.FEES_FUND)  # re-read must not duplicate

    assert recorder.keys_read == (
        AssumptionKey.FEES_FUND,
        AssumptionKey.INFLATION_CPI,
    )


def test_tracking_never_mutates_the_assumption_set() -> None:
    """The frozen registry is untouched; only the recorder accumulates."""
    assumptions = AssumptionSet(
        [make_assumption(AssumptionKey.INFLATION_CPI, Decimal("0.02"))]
    )
    keys_before = assumptions.keys
    tracked = TrackedAssumptions(
        assumptions=assumptions, recorder=AssumptionReadRecorder()
    )
    tracked.get(AssumptionKey.INFLATION_CPI)
    assert assumptions.keys == keys_before

    fresh_recorder = AssumptionReadRecorder()
    assert fresh_recorder.keys_read == ()
