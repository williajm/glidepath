"""Tests for the Household/Person skeleton (planning §4.4, issue 1.4)."""

from datetime import UTC, date, datetime

import pytest

from glidepath.core.entities import (
    EntityId,
    Household,
    Person,
    Sex,
    TaxResidencyId,
    new_entity_id,
    validate_household_v1,
)
from glidepath.core.provenance import Decision, Fact

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def make_person(person_id: EntityId | None = None) -> Person:
    """Build a minimal person for tests."""
    return Person(
        id=person_id if person_id is not None else new_entity_id(),
        date_of_birth=Fact(
            value=date(1991, 4, 5), as_of=date(2026, 8, 1), recorded_on=RECORDED
        ),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=TaxResidencyId("uk.ruk"),
        sex_for_longevity=Fact(
            value=Sex.FEMALE, as_of=date(2026, 8, 1), recorded_on=RECORDED
        ),
    )


def test_new_entity_ids_are_unique_strings() -> None:
    """Fresh ids are distinct and stringly persisted."""
    first, second = new_entity_id(), new_entity_id()
    assert first != second
    assert isinstance(first, str)


def test_single_person_household_is_valid_for_v1() -> None:
    """One person passes both the schema bound and the v1 validator."""
    household = Household(persons=(make_person(),))
    validate_household_v1(household)
    assert household.persons[0].employment_income is None


def test_two_person_household_representable_but_rejected_by_v1() -> None:
    """Planning §4.4: the schema holds couples now; v1 refuses to run them."""
    household = Household(persons=(make_person(), make_person()))
    assert len(household.persons) == 2
    with pytest.raises(ValueError, match="exactly one person"):
        validate_household_v1(household)


def test_household_rejects_zero_and_three_persons() -> None:
    """The schema bound is 1..2 persons."""
    with pytest.raises(ValueError, match="1 or 2 persons"):
        Household(persons=())
    with pytest.raises(ValueError, match="1 or 2 persons"):
        Household(persons=(make_person(), make_person(), make_person()))


def test_household_rejects_duplicate_entity_ids() -> None:
    """Stable ids are override targets; duplicates would be ambiguous."""
    shared = new_entity_id()
    with pytest.raises(ValueError, match="distinct EntityIds"):
        Household(persons=(make_person(shared), make_person(shared)))
