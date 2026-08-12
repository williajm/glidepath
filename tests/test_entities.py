"""Tests for the Household/Person skeleton (planning §4.4, issue 1.4)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.core.entities import (
    EntityId,
    Household,
    Person,
    PlannedOutflow,
    Sex,
    TaxResidencyId,
    new_entity_id,
)
from glidepath.core.glide import GlidePathConfig, GlidePathPoint
from glidepath.core.investments import AssetAllocation
from glidepath.core.money import Money
from glidepath.core.pensions import (
    DBPension,
    FactorTable,
    RevaluationBasis,
    RevaluationReference,
)
from glidepath.core.provenance import Decision, Fact
from glidepath.core.wrappers import Wrapper, WrapperKindId

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def make_person(
    person_id: EntityId | None = None, wrappers: tuple[Wrapper, ...] = ()
) -> Person:
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
        wrappers=wrappers,
    )


def make_wrapper(wrapper_id: EntityId | None = None) -> Wrapper:
    """Build a minimal wrapper for tests."""
    return Wrapper(
        id=wrapper_id if wrapper_id is not None else new_entity_id(),
        kind=WrapperKindId("test.dc"),
        balance=Fact(
            value=Money(Decimal(10000)), as_of=date(2026, 8, 1), recorded_on=RECORDED
        ),
    )


def make_db_pension(pension_id: EntityId | None = None) -> DBPension:
    """Build a minimal deferred DB pension for tests."""
    return DBPension(
        id=pension_id if pension_id is not None else new_entity_id(),
        accrued_annual_pension=Fact(
            value=Money(Decimal(8000)), as_of=date(2026, 8, 1), recorded_on=RECORDED
        ),
        statement_date=date(2024, 8, 1),
        normal_pension_age=Fact(value=65, as_of=date(2026, 8, 1), recorded_on=RECORDED),
        revaluation_basis=RevaluationBasis(reference=RevaluationReference.NONE),
        early_late_factors=FactorTable(factors={}),
        commuted_fraction=Decision(value=Decimal(0), recorded_on=RECORDED),
    )


def test_person_defaults_to_no_mpaa_trigger() -> None:
    """Most users have never flexibly accessed a pension."""
    assert make_person().mpaa_triggered_on is None


def test_person_carries_mpaa_trigger_fact() -> None:
    """Pre-plan flexible access is a stated fact (planning §5.1, issue 3.3)."""
    triggered = Fact(
        value=date(2024, 6, 1), as_of=date(2026, 8, 1), recorded_on=RECORDED
    )
    person = Person(
        id=new_entity_id(),
        date_of_birth=Fact(
            value=date(1966, 4, 5), as_of=date(2026, 8, 1), recorded_on=RECORDED
        ),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=TaxResidencyId("uk.ruk"),
        mpaa_triggered_on=triggered,
    )
    assert person.mpaa_triggered_on is triggered


def test_person_defaults_to_the_default_glide_path() -> None:
    """No stated glide path means the default-shape assumption supplies it."""
    assert make_person().glide_path is None


def test_person_defaults_to_alive_to_the_horizon() -> None:
    """No stated death age means no survivor modelling (planning §4.11)."""
    assert make_person().death_age is None


def test_person_rejects_a_non_positive_death_age() -> None:
    """A death age of zero is a data error, never a modelling input."""
    person = make_person()
    death_age = Decision(value=0, recorded_on=RECORDED)
    with pytest.raises(ValueError, match="death_age must be positive"):
        Person(
            id=person.id,
            date_of_birth=person.date_of_birth,
            target_retirement_age=person.target_retirement_age,
            tax_residency=person.tax_residency,
            death_age=death_age,
        )


def test_person_carries_a_glide_path_override() -> None:
    """A per-person glide path overrides the default shape (roadmap 3.5)."""
    config = GlidePathConfig(
        points=(
            GlidePathPoint(
                years_to_retirement=0,
                allocation=AssetAllocation(equity=Decimal("0.5"), bonds=Decimal("0.5")),
            ),
        )
    )
    person = Person(
        id=new_entity_id(),
        date_of_birth=Fact(
            value=date(1991, 4, 5), as_of=date(2026, 8, 1), recorded_on=RECORDED
        ),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=TaxResidencyId("uk.ruk"),
        glide_path=config,
    )
    assert person.glide_path is config


def test_new_entity_ids_are_unique_strings() -> None:
    """Fresh ids are distinct and stringly persisted."""
    first, second = new_entity_id(), new_entity_id()
    assert first != second
    assert isinstance(first, str)


def test_single_person_household_is_valid() -> None:
    """One person satisfies the schema bound."""
    household = Household(persons=(make_person(),))
    assert household.persons[0].employment_income is None


def test_two_person_household_is_valid() -> None:
    """Planning §4.4, §4.11: the schema holds couples, valid everywhere."""
    household = Household(persons=(make_person(), make_person()))
    assert len(household.persons) == 2


def test_household_rejects_zero_and_three_persons() -> None:
    """The schema bound is 1..2 persons."""
    with pytest.raises(ValueError, match="1 or 2 persons"):
        Household(persons=())
    three = (make_person(), make_person(), make_person())
    with pytest.raises(ValueError, match="1 or 2 persons"):
        Household(persons=three)


def test_household_rejects_duplicate_entity_ids() -> None:
    """Stable ids are override targets; duplicates would be ambiguous."""
    shared = new_entity_id()
    duplicates = (make_person(shared), make_person(shared))
    with pytest.raises(ValueError, match="distinct EntityIds"):
        Household(persons=duplicates)


def test_person_holds_wrappers() -> None:
    """Wrappers attach to a person (roadmap 3.1); default is none."""
    person = make_person(wrappers=(make_wrapper(), make_wrapper()))
    assert len(person.wrappers) == 2
    assert make_person().wrappers == ()


def test_person_rejects_duplicate_wrapper_ids() -> None:
    """Wrapper ids are override targets too (planning §4.3)."""
    shared = new_entity_id()
    duplicates = (make_wrapper(shared), make_wrapper(shared))
    with pytest.raises(ValueError, match="distinct EntityIds"):
        make_person(wrappers=duplicates)


def test_household_rejects_wrapper_ids_shared_across_persons() -> None:
    """Ids must be unambiguous across the whole aggregate (planning §4.3)."""
    shared = new_entity_id()
    persons = (
        make_person(wrappers=(make_wrapper(shared),)),
        make_person(wrappers=(make_wrapper(shared),)),
    )
    with pytest.raises(ValueError, match="distinct EntityIds"):
        Household(persons=persons)


def test_household_rejects_wrapper_id_colliding_with_person_id() -> None:
    """A wrapper may not reuse a person's id either."""
    person_id = new_entity_id()
    persons = (
        make_person(person_id),
        make_person(wrappers=(make_wrapper(person_id),)),
    )
    with pytest.raises(ValueError, match="distinct EntityIds"):
        Household(persons=persons)


def test_person_rejects_db_pension_id_colliding_with_wrapper_id() -> None:
    """DB pensions are override targets too (planning §4.3, roadmap 4.2)."""
    shared = new_entity_id()
    person_id = new_entity_id()
    wrappers = (make_wrapper(shared),)
    pensions = (make_db_pension(shared),)
    date_of_birth = Fact(
        value=date(1991, 4, 5), as_of=date(2026, 8, 1), recorded_on=RECORDED
    )
    retirement = Decision(value=60, recorded_on=RECORDED)
    residency = TaxResidencyId("uk.ruk")
    with pytest.raises(ValueError, match="distinct EntityIds"):
        Person(
            id=person_id,
            date_of_birth=date_of_birth,
            target_retirement_age=retirement,
            tax_residency=residency,
            wrappers=wrappers,
            db_pensions=pensions,
        )


def make_outflow(
    for_person: EntityId,
    outflow_id: EntityId | None = None,
    *,
    amount: str = "15000",
    age: int = 55,
) -> PlannedOutflow:
    """Build a planned outflow decision for tests (roadmap 5.4)."""
    return PlannedOutflow(
        id=outflow_id if outflow_id is not None else new_entity_id(),
        label="mortgage payoff",
        amount_real=Decision(value=Money(Decimal(amount)), recorded_on=RECORDED),
        at_age_of=(for_person, age),
    )


def test_household_holds_planned_outflows() -> None:
    """Outflows are household-level decisions (planning §5.1, roadmap 5.4)."""
    person = make_person()
    outflow = make_outflow(person.id)
    household = Household(persons=(person,), planned_outflows=(outflow,))
    assert household.planned_outflows == (outflow,)
    assert Household(persons=(make_person(),)).planned_outflows == ()


def test_planned_outflow_rejects_negative_amount() -> None:
    """A negative outflow would be an inflow; there is no such wrapper."""
    person = make_person()
    with pytest.raises(ValueError, match="non-negative"):
        make_outflow(person.id, amount="-1")


def test_planned_outflow_rejects_negative_age() -> None:
    """The occurrence age must be a real age."""
    person = make_person()
    with pytest.raises(ValueError, match="age must be non-negative"):
        make_outflow(person.id, age=-1)


def test_household_rejects_outflow_for_unknown_person() -> None:
    """An outflow must reference a person in the household."""
    person = make_person()
    stranger = make_outflow(new_entity_id())
    with pytest.raises(ValueError, match="not in this household"):
        Household(persons=(person,), planned_outflows=(stranger,))


def test_household_rejects_outflow_id_colliding_with_wrapper_id() -> None:
    """Outflow ids are override targets too (planning §4.3)."""
    shared = new_entity_id()
    person = make_person(wrappers=(make_wrapper(shared),))
    outflow = make_outflow(person.id, shared)
    with pytest.raises(ValueError, match="distinct EntityIds"):
        Household(persons=(person,), planned_outflows=(outflow,))


def test_household_rejects_db_pension_ids_shared_across_persons() -> None:
    """DB pension ids must be unambiguous across the aggregate."""
    shared = new_entity_id()
    first = make_person()
    second_id = new_entity_id()
    second = Person(
        id=second_id,
        date_of_birth=first.date_of_birth,
        target_retirement_age=first.target_retirement_age,
        tax_residency=TaxResidencyId("uk.ruk"),
        db_pensions=(make_db_pension(shared),),
    )
    third = Person(
        id=new_entity_id(),
        date_of_birth=first.date_of_birth,
        target_retirement_age=first.target_retirement_age,
        tax_residency=TaxResidencyId("uk.ruk"),
        db_pensions=(make_db_pension(shared),),
    )
    with pytest.raises(ValueError, match="distinct EntityIds"):
        Household(persons=(second, third))
