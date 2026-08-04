"""Household and person entities (planning §4.4, §5.1 skeleton).

The schema models ``Household{persons: 1..2}`` now — UK tax is individual,
so computation is per-person anyway, and placing shared economics at
household level avoids a schema + engine migration when couples activate
(roadmap 9.4). v1 validates exactly one person via
:func:`validate_household_v1`.

Wrappers attach to :class:`Person` as of roadmap 3.1, the glide-path
config as of 3.5, household spending as of 4.1, DB pensions and state
pension records as of 4.2/4.3, and household planned outflows as of 5.4.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from typing import TYPE_CHECKING, NewType

from glidepath.core.money import Money

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from glidepath.core.annuities import AnnuityPurchase
    from glidepath.core.glide import GlidePathConfig, LifeStage
    from glidepath.core.pensions import DBPension
    from glidepath.core.provenance import Decision, Fact
    from glidepath.core.state_pension import StatePensionRecord
    from glidepath.core.wrappers import Wrapper

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
_ZERO = Money(Decimal(0))
_ZERO_MULTIPLIER = Decimal(0)


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
    mpaa_triggered_on: Fact[date] | None = None
    """Date pension benefits were first flexibly accessed, if ever.

    A pre-plan fact (planning §5.1): once set, the region's
    money-purchase contribution limit applies from that date on
    (roadmap 3.3). An in-plan first flexible access records the
    trigger in the period results instead (roadmap 5.2); when this
    fact is present it wins.
    """
    lsa_used: Fact[Money] | None = None
    """Tax-free lump sum allowance already used before the plan.

    A pre-plan fact (planning §5.1): the run's tax-free-cash ledger is
    seeded with it, reducing the headroom under the region's lifetime
    cap (roadmap 5.2). ``None`` means none used.
    """
    wrappers: tuple[Wrapper, ...] = ()
    db_pensions: tuple[DBPension, ...] = ()
    """DB entitlements — deferred or actively accruing (roadmap 4.2, 9.6)."""
    annuity_purchases: tuple[AnnuityPurchase, ...] = ()
    """Planned annuity purchases — decision records (roadmap 5.5)."""
    state_pension: StatePensionRecord | None = None
    """This person's state pension record (roadmap 4.3).

    ``None`` means no state pension is modelled for this person.
    """
    glide_path: GlidePathConfig | None = None
    """This person's glide path, if they overrode the default.

    ``None`` means the ``glidepath.default_shape`` assumption supplies
    the factor table (planning §7, roadmap 3.5).
    """

    def __post_init__(self) -> None:
        """Require distinct entity ids (they are override targets, §4.3)."""
        ids = [wrapper.id for wrapper in self.wrappers]
        ids += [pension.id for pension in self.db_pensions]
        ids += [purchase.id for purchase in self.annuity_purchases]
        if len(set(ids)) != len(ids):
            msg = (
                "a person's wrappers, DB pensions, and annuity purchases"
                " must have distinct EntityIds"
            )
            raise ValueError(msg)
        if self.lsa_used is not None and self.lsa_used.value < _ZERO:
            msg = "Person.lsa_used must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class SpendingPlan:
    """The household's retirement spending need (planning §5.1, §5.2).

    ``annual_spending_real`` is a *net* (after-tax) need in today's
    money — the engine inflates it by the run's CPI path and grosses
    withdrawals up against the tax system (planning §5.2 step 4).
    ``stage_multipliers`` optionally scales the need per life stage
    (e.g. go-go years); an absent stage means a multiplier of 1.
    """

    annual_spending_real: Fact[Money]
    stage_multipliers: Mapping[LifeStage, Decimal] | None = None

    def __post_init__(self) -> None:
        """Reject negative spending and non-positive multipliers."""
        if self.annual_spending_real.value < _ZERO:
            msg = "SpendingPlan.annual_spending_real must be non-negative"
            raise ValueError(msg)
        multipliers = self.stage_multipliers or {}
        if any(value <= _ZERO_MULTIPLIER for value in multipliers.values()):
            msg = "SpendingPlan.stage_multipliers must be positive"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PlannedOutflow:
    """One dated one-off outflow — wholly a decision (planning §5.1).

    A mortgage payoff, gift, or purchase: a *net* cash need on top of
    the spending plan, hitting the period in which the referenced
    person attains the stated age and funded tax-aware through the
    withdrawal machinery (roadmap 5.4). ``amount_real`` is in today's
    money; the engine inflates it by the run's CPI path.
    """

    id: EntityId
    label: str
    amount_real: Decision[Money]
    at_age_of: tuple[EntityId, int]

    def __post_init__(self) -> None:
        """Reject a negative amount or age."""
        if self.amount_real.value < _ZERO:
            msg = "PlannedOutflow.amount_real must be non-negative"
            raise ValueError(msg)
        if self.at_age_of[1] < 0:
            msg = "PlannedOutflow.at_age_of age must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Household:
    """One or two persons plus shared economics (planning §4.4, §5.1).

    The 1..2 bound is the schema-level invariant (planning §4.4); the
    stricter v1 single-person rule is :func:`validate_household_v1`.
    ``spending`` is the household-level retirement spending need;
    ``None`` means decumulation spending withdrawals are not modelled.
    ``planned_outflows`` are household-level dated one-offs (roadmap
    5.4), funded through the withdrawal machinery whether or not a
    spending plan is present.
    """

    persons: tuple[Person, ...]
    spending: SpendingPlan | None = None
    planned_outflows: tuple[PlannedOutflow, ...] = ()

    def __post_init__(self) -> None:
        """Enforce the 1..2 bound, distinct entity ids, and outflow targets.

        Scenario overrides target entities by id + field path (planning
        §4.3), so ids must be unambiguous across the whole household —
        two persons' wrappers, DB pensions, annuity purchases, or
        planned outflows may not share an id, nor may any share one
        with a person. A planned outflow must reference a person in
        this household.
        """
        if not _MIN_PERSONS <= len(self.persons) <= _MAX_PERSONS:
            msg = f"a household holds 1 or 2 persons, got {len(self.persons)}"
            raise ValueError(msg)
        ids = [person.id for person in self.persons]
        ids += [wrapper.id for person in self.persons for wrapper in person.wrappers]
        ids += [pension.id for person in self.persons for pension in person.db_pensions]
        ids += [
            purchase.id
            for person in self.persons
            for purchase in person.annuity_purchases
        ]
        ids += [outflow.id for outflow in self.planned_outflows]
        if len(set(ids)) != len(ids):
            msg = (
                "household entities (persons, wrappers, DB pensions, annuity"
                " purchases, planned outflows) must have distinct EntityIds"
            )
            raise ValueError(msg)
        person_ids = {person.id for person in self.persons}
        for outflow in self.planned_outflows:
            if outflow.at_age_of[0] not in person_ids:
                msg = (
                    f"planned outflow {outflow.id} references person"
                    f" {outflow.at_age_of[0]}, who is not in this household"
                )
                raise ValueError(msg)


def validate_household_v1(household: Household) -> None:
    """Enforce the v1 single-person restriction (planning §4.4).

    Raises:
        ValueError: If the household does not hold exactly one person.
    """
    if len(household.persons) != _MIN_PERSONS:
        msg = "v1 supports exactly one person per household (planning §4.4)"
        raise ValueError(msg)
