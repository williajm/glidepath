"""Household and person entities (planning §4.4, §5.1 skeleton).

The schema models ``Household{persons: 1..2}`` now — UK tax is individual,
so computation is per-person anyway, and placing shared economics at
household level avoids a schema + engine migration when couples activate
(roadmap 9.4). v1 validates exactly one person via
:func:`validate_household_v1`.

Wrappers, DB pensions, state pension records and glide-path config attach
to :class:`Person` in later phases (roadmap Phases 3-4).
"""

import uuid
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, NewType

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core.money import Money
    from glidepath.core.provenance import Decision, Fact

EntityId = NewType("EntityId", str)
"""Stable persisted identifier.

Scenario overrides target entities by id + field path (planning §4.3), so
ids must survive reordering and insertion; couples support needs them too.
"""

TaxResidencyId = NewType("TaxResidencyId", str)
"""Opaque region-defined residency id (e.g. ``"uk.ruk"``, ``"uk.scotland"``).

The core never interprets it; the region's tax system does (planning §4.2).
"""

_MIN_PERSONS = 1
_MAX_PERSONS = 2


def new_entity_id() -> EntityId:
    """Generate a fresh stable id.

    For plan-edit time only — the engine itself never creates entities
    during a run (planning §4.6 purity).
    """
    return EntityId(str(uuid.uuid4()))


class Sex(Enum):
    """Sex used solely for longevity defaults (planning §5.1)."""

    FEMALE = auto()
    MALE = auto()


@dataclass(frozen=True, slots=True)
class Person:
    """One person in a household (planning §5.1, Phase 1 skeleton).

    Everything taxed or age-gated hangs off a person; shared economics
    hang off the household (planning §4.4).
    """

    id: EntityId
    date_of_birth: Fact[date]
    target_retirement_age: Decision[int]
    tax_residency: TaxResidencyId
    sex_for_longevity: Fact[Sex] | None = None
    employment_income: Fact[Money] | None = None


@dataclass(frozen=True, slots=True)
class Household:
    """One or two persons plus (in later phases) shared economics.

    The 1..2 bound is the schema-level invariant (planning §4.4); the
    stricter v1 single-person rule is :func:`validate_household_v1`.
    """

    persons: tuple[Person, ...]

    def __post_init__(self) -> None:
        """Enforce the 1..2 bound and distinct ids."""
        if not _MIN_PERSONS <= len(self.persons) <= _MAX_PERSONS:
            msg = f"a household holds 1 or 2 persons, got {len(self.persons)}"
            raise ValueError(msg)
        ids = [person.id for person in self.persons]
        if len(set(ids)) != len(ids):
            msg = "household persons must have distinct EntityIds"
            raise ValueError(msg)


def validate_household_v1(household: Household) -> None:
    """Enforce the v1 single-person restriction (planning §4.4).

    Raises:
        ValueError: If the household does not hold exactly one person.
    """
    if len(household.persons) != _MIN_PERSONS:
        msg = "v1 supports exactly one person per household (planning §4.4)"
        raise ValueError(msg)
