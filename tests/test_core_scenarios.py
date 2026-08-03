"""Tests for the scenario/override model and resolution (issue 6.1, §4.3).

The plan here carries one of every override-targetable entity — a
wrapper with a contribution schedule, a DB pension with a taken-at-age
decision, an annuity purchase, a state pension record, and a planned
outflow — so every field path in the decision whitelist is exercised,
along with orphan detection and the type-enforced facts boundary.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from glidepath.core import (
    AnnuityBasis,
    AnnuityPurchase,
    AnnuityType,
    Assumption,
    AssumptionKey,
    AssumptionSet,
    AssumptionTarget,
    ContributionSchedule,
    DBPension,
    Decision,
    DecisionTarget,
    EntityId,
    Fact,
    FactorTable,
    Household,
    Money,
    Override,
    Person,
    PlannedOutflow,
    Provenance,
    ReliefMechanic,
    RevaluationBasis,
    RevaluationReference,
    Scenario,
    ScenarioError,
    SpendingPlan,
    StatePensionRecord,
    TaxResidencyId,
    Wrapper,
    WrapperKindId,
    is_scenario_valid,
    resolve_scenario,
    scenario_orphans,
)

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)
RESIDENCY = TaxResidencyId("test.main")
KIND = WrapperKindId("test.pension")

PERSON_ID = EntityId("person-1")
WRAPPER_ID = EntityId("wrapper-1")
BARE_WRAPPER_ID = EntityId("wrapper-2")
DB_ID = EntityId("db-1")
UNTAKEN_DB_ID = EntityId("db-2")
ANNUITY_ID = EntityId("annuity-1")
OUTFLOW_ID = EntityId("outflow-1")


def money_fact(amount: str) -> Fact[Money]:
    """A user-stated monetary fact."""
    return Fact(value=Money(Decimal(amount)), as_of=AS_OF, recorded_on=RECORDED)


def decision[T](value: T, note: str | None = None) -> Decision[T]:
    """A user choice recorded at the fixed test timestamp."""
    return Decision(value=value, recorded_on=RECORDED, note=note)


def db_pension_of(pension_id: EntityId, *, taken_at: int | None) -> DBPension:
    """A deferred DB pension that never revalues.

    The factor table covers every early age a test takes it at, and the
    commutation factor permits any commuted-fraction what-if.
    """
    factors = {62: Decimal("0.85"), 64: Decimal("0.95")}
    return DBPension(
        id=pension_id,
        accrued_annual_pension=money_fact("8000"),
        statement_date=date(2026, 1, 1),
        normal_pension_age=Fact(value=65, as_of=AS_OF, recorded_on=RECORDED),
        revaluation_basis=RevaluationBasis(reference=RevaluationReference.NONE),
        early_late_factors=FactorTable(factors=factors),
        commuted_fraction=Decision(value=Decimal(0), recorded_on=RECORDED),
        commutation_factor=Fact(value=Decimal(12), as_of=AS_OF, recorded_on=RECORDED),
        taken_at_age=None if taken_at is None else decision(taken_at),
    )


def base_household(*, with_state_pension: bool = True) -> Household:
    """One of every override-targetable entity, under a single person."""
    schedule = ContributionSchedule(
        employee_amount=decision(Money(Decimal(5000))),
        employer_amount=None,
        relief_mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
    )
    wrappers = (
        Wrapper(
            id=WRAPPER_ID,
            kind=KIND,
            balance=money_fact("100000"),
            crystallised_balance=None,
            contributions=schedule,
            allocation=None,
            fees=None,
        ),
        Wrapper(
            id=BARE_WRAPPER_ID,
            kind=KIND,
            balance=money_fact("50000"),
            crystallised_balance=None,
            contributions=None,
            allocation=None,
            fees=None,
        ),
    )
    state_pension = None
    if with_state_pension:
        state_pension = StatePensionRecord(
            forecast_weekly_amount=money_fact("230"),
            protected_payment=None,
            ni_record_start=None,
            qualifying_years=None,
            planned_extra_years=decision(0),
            deferral_years=decision(Decimal(0)),
        )
    purchase = AnnuityPurchase(
        id=ANNUITY_ID,
        at_age=decision(70),
        fraction_of_pot=decision(Decimal("0.5")),
        annuity_type=AnnuityType.LEVEL,
        basis=AnnuityBasis.SINGLE,
    )
    person = Person(
        id=PERSON_ID,
        date_of_birth=Fact(value=date(1970, 6, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=decision(65),
        tax_residency=RESIDENCY,
        wrappers=wrappers,
        db_pensions=(
            db_pension_of(DB_ID, taken_at=64),
            db_pension_of(UNTAKEN_DB_ID, taken_at=None),
        ),
        annuity_purchases=(purchase,),
        state_pension=state_pension,
    )
    outflow = PlannedOutflow(
        id=OUTFLOW_ID,
        label="new roof",
        amount_real=decision(Money(Decimal(20000))),
        at_age_of=(PERSON_ID, 66),
    )
    spending = SpendingPlan(annual_spending_real=money_fact("24000"))
    return Household(persons=(person,), spending=spending, planned_outflows=(outflow,))


def base_assumptions() -> AssumptionSet:
    """A small default-provenance assumption set."""
    entries = {
        AssumptionKey.INFLATION_CPI: Decimal("0.02"),
        AssumptionKey.RETURNS_EQUITY_REAL: Decimal("0.05"),
        AssumptionKey.HORIZON_PLANNING_AGE: 95,
    }
    return AssumptionSet(
        Assumption(
            key=key,
            value=value,
            default_value=value,
            provenance=Provenance.DEFAULT_ASSUMPTION,
            source="test basis",
            recorded_on=RECORDED,
            description="test assumption",
        )
        for key, value in entries.items()
    )


def decision_override(
    entity_id: EntityId, field_path: str, value: object, note: str | None = None
) -> Override:
    """An override on a decision variable."""
    return Override(
        target=DecisionTarget(entity_id=entity_id, field_path=field_path),
        value=value,
        note=note,
    )


def assumption_override(key: AssumptionKey, value: object) -> Override:
    """An override on an assumption key."""
    return Override(target=AssumptionTarget(key=key), value=value)


class TestScenarioModel:
    """Construction-time invariants of the scenario dataclasses."""

    def test_scenario_rejects_empty_name(self) -> None:
        """A scenario is addressed by name, so the name must exist."""
        with pytest.raises(ValueError, match="name must be non-empty"):
            Scenario(name="")

    def test_scenario_rejects_duplicate_targets(self) -> None:
        """Two overrides on one target would be ambiguous."""
        overrides = (
            decision_override(PERSON_ID, "target_retirement_age", 60),
            decision_override(PERSON_ID, "target_retirement_age", 62),
        )
        with pytest.raises(ValueError, match="two overrides on one target"):
            Scenario(name="clash", overrides=overrides)

    def test_decision_target_rejects_empty_field_path(self) -> None:
        """A decision target must name a field."""
        with pytest.raises(ValueError, match="field_path must be non-empty"):
            DecisionTarget(entity_id=PERSON_ID, field_path="")

    def test_empty_scenario_is_allowed(self) -> None:
        """A scenario with no overrides is the base plan, validly."""
        scenario = Scenario(name="as-is")
        assert scenario.overrides == ()


class TestOrphans:
    """Orphan detection flags the scenario invalid (planning §4.3)."""

    def test_unknown_entity_is_orphaned(self) -> None:
        """A target whose entity no longer exists is an orphan."""
        override = decision_override(EntityId("gone"), "target_retirement_age", 60)
        scenario = Scenario(name="orphan", overrides=(override,))
        household = base_household()
        assumptions = base_assumptions()
        assert scenario_orphans(scenario, household, assumptions) == (override,)
        assert not is_scenario_valid(scenario, household, assumptions)

    def test_unknown_field_path_is_orphaned(self) -> None:
        """A path outside the decision whitelist cannot be addressed."""
        override = decision_override(PERSON_ID, "date_of_birth", date(1980, 1, 1))
        scenario = Scenario(name="facts-are-safe", overrides=(override,))
        orphans = scenario_orphans(scenario, base_household(), base_assumptions())
        assert orphans == (override,)

    def test_absent_state_pension_record_is_orphaned(self) -> None:
        """A path through an absent optional record is unaddressable."""
        override = decision_override(
            PERSON_ID, "state_pension.deferral_years", Decimal(1)
        )
        scenario = Scenario(name="defer", overrides=(override,))
        household = base_household(with_state_pension=False)
        assert scenario_orphans(scenario, household, base_assumptions()) == (override,)

    def test_absent_contribution_schedule_is_orphaned(self) -> None:
        """A wrapper without a schedule has no employee amount to override."""
        amount = Money(Decimal(9000))
        override = decision_override(
            BARE_WRAPPER_ID, "contributions.employee_amount", amount
        )
        scenario = Scenario(name="save-more", overrides=(override,))
        orphans = scenario_orphans(scenario, base_household(), base_assumptions())
        assert orphans == (override,)

    def test_absent_taken_at_age_is_orphaned(self) -> None:
        """Overriding can replace a decision, never create one."""
        override = decision_override(UNTAKEN_DB_ID, "taken_at_age", 60)
        scenario = Scenario(name="take-early", overrides=(override,))
        orphans = scenario_orphans(scenario, base_household(), base_assumptions())
        assert orphans == (override,)

    def test_unregistered_assumption_key_is_orphaned(self) -> None:
        """An assumption override needs a registered base assumption."""
        override = assumption_override(AssumptionKey.FEES_PLATFORM, Decimal("0.01"))
        scenario = Scenario(name="fees", overrides=(override,))
        orphans = scenario_orphans(scenario, base_household(), base_assumptions())
        assert orphans == (override,)

    def test_valid_scenario_has_no_orphans(self) -> None:
        """Every whitelisted target on a present entity is addressable."""
        scenario = Scenario(
            name="valid",
            overrides=(
                decision_override(PERSON_ID, "target_retirement_age", 60),
                assumption_override(AssumptionKey.INFLATION_CPI, Decimal("0.03")),
            ),
        )
        household = base_household()
        assumptions = base_assumptions()
        assert scenario_orphans(scenario, household, assumptions) == ()
        assert is_scenario_valid(scenario, household, assumptions)

    def test_resolve_refuses_orphaned_scenario(self) -> None:
        """Resolution of an invalid scenario fails loudly, naming targets."""
        override = decision_override(EntityId("gone"), "target_retirement_age", 60)
        scenario = Scenario(name="orphan", overrides=(override,))
        household = base_household()
        assumptions = base_assumptions()
        with pytest.raises(ScenarioError, match=r"orphaned overrides.*gone"):
            resolve_scenario(household, assumptions, scenario)


class TestResolution:
    """base ⊕ overrides with SCENARIO_OVERRIDE provenance (§4.3)."""

    def test_assumption_override_carries_scenario_provenance(self) -> None:
        """The resolved assumption is re-stamped; the default survives."""
        scenario = Scenario(
            name="high-inflation",
            overrides=(
                assumption_override(AssumptionKey.INFLATION_CPI, Decimal("0.05")),
            ),
        )
        resolution = resolve_scenario(base_household(), base_assumptions(), scenario)
        resolved = resolution.assumptions.get(AssumptionKey.INFLATION_CPI)
        assert resolved.value == Decimal("0.05")
        assert resolved.default_value == Decimal("0.02")
        assert resolved.provenance is Provenance.SCENARIO_OVERRIDE
        assert resolved.source == "test basis"
        untouched = resolution.assumptions.get(AssumptionKey.RETURNS_EQUITY_REAL)
        assert untouched.provenance is Provenance.DEFAULT_ASSUMPTION

    def test_base_inputs_are_never_mutated(self) -> None:
        """Resolution builds new inputs; the base objects are untouched."""
        household = base_household()
        assumptions = base_assumptions()
        scenario = Scenario(
            name="retire-early",
            overrides=(
                decision_override(PERSON_ID, "target_retirement_age", 60),
                assumption_override(AssumptionKey.INFLATION_CPI, Decimal("0.05")),
            ),
        )
        resolve_scenario(household, assumptions, scenario)
        assert household.persons[0].target_retirement_age.value == 65
        base_cpi = assumptions.get(AssumptionKey.INFLATION_CPI)
        assert base_cpi.value == Decimal("0.02")
        assert base_cpi.provenance is Provenance.DEFAULT_ASSUMPTION

    def test_retirement_age_override(self) -> None:
        """The resolved person carries the what-if value and the note."""
        override = decision_override(
            PERSON_ID, "target_retirement_age", 60, note="what if I retire at 60"
        )
        scenario = Scenario(name="retire-early", overrides=(override,))
        resolution = resolve_scenario(base_household(), base_assumptions(), scenario)
        resolved = resolution.household.persons[0].target_retirement_age
        assert resolved.value == 60
        assert resolved.note == "what if I retire at 60"
        assert resolved.recorded_on == RECORDED

    def test_wrapper_contribution_override(self) -> None:
        """The wrapper's employee amount is replaced in place."""
        amount = Money(Decimal(9000))
        override = decision_override(
            WRAPPER_ID, "contributions.employee_amount", amount
        )
        scenario = Scenario(name="save-more", overrides=(override,))
        resolution = resolve_scenario(base_household(), base_assumptions(), scenario)
        wrappers = resolution.household.persons[0].wrappers
        schedule = wrappers[0].contributions
        assert schedule is not None
        assert schedule.employee_amount.value == amount
        assert wrappers[1].contributions is None

    def test_db_pension_overrides(self) -> None:
        """Taken-at-age and commuted-fraction replace on the right pension."""
        scenario = Scenario(
            name="db-choices",
            overrides=(
                decision_override(DB_ID, "taken_at_age", 62),
                decision_override(DB_ID, "commuted_fraction", Decimal("0.25")),
            ),
        )
        resolution = resolve_scenario(base_household(), base_assumptions(), scenario)
        taken, untaken = resolution.household.persons[0].db_pensions
        assert taken.taken_at_age is not None
        assert taken.taken_at_age.value == 62
        assert taken.commuted_fraction.value == Decimal("0.25")
        assert untaken.taken_at_age is None
        assert untaken.commuted_fraction.value == Decimal(0)

    def test_annuity_purchase_overrides(self) -> None:
        """Both annuity decisions replace on the purchase record."""
        scenario = Scenario(
            name="annuitise-later",
            overrides=(
                decision_override(ANNUITY_ID, "at_age", 75),
                decision_override(ANNUITY_ID, "fraction_of_pot", Decimal("0.75")),
            ),
        )
        resolution = resolve_scenario(base_household(), base_assumptions(), scenario)
        purchase = resolution.household.persons[0].annuity_purchases[0]
        assert purchase.at_age.value == 75
        assert purchase.fraction_of_pot.value == Decimal("0.75")

    def test_state_pension_overrides(self) -> None:
        """Both state pension decisions replace on the person's record."""
        scenario = Scenario(
            name="defer-sp",
            overrides=(
                decision_override(PERSON_ID, "state_pension.planned_extra_years", 3),
                decision_override(
                    PERSON_ID, "state_pension.deferral_years", Decimal("1.5")
                ),
            ),
        )
        resolution = resolve_scenario(base_household(), base_assumptions(), scenario)
        record = resolution.household.persons[0].state_pension
        assert record is not None
        assert record.planned_extra_years.value == 3
        assert record.deferral_years.value == Decimal("1.5")

    def test_planned_outflow_override(self) -> None:
        """The household-level outflow amount is replaced."""
        amount = Money(Decimal(30000))
        override = decision_override(OUTFLOW_ID, "amount_real", amount)
        scenario = Scenario(name="bigger-roof", overrides=(override,))
        resolution = resolve_scenario(base_household(), base_assumptions(), scenario)
        outflow = resolution.household.planned_outflows[0]
        assert outflow.amount_real.value == amount
        assert outflow.label == "new roof"

    def test_applied_records_carry_labels_and_provenance(self) -> None:
        """Every override lands in ``applied`` at its stable label (§4.3)."""
        scenario = Scenario(
            name="mixed",
            overrides=(
                assumption_override(AssumptionKey.INFLATION_CPI, Decimal("0.05")),
                decision_override(PERSON_ID, "target_retirement_age", 60),
                decision_override(OUTFLOW_ID, "amount_real", Money(Decimal(30000))),
            ),
        )
        resolution = resolve_scenario(base_household(), base_assumptions(), scenario)
        labels = [applied.label for applied in resolution.applied]
        assert labels == [
            "inflation.cpi",
            f"person[{PERSON_ID}].target_retirement_age",
            f"planned_outflow[{OUTFLOW_ID}].amount_real",
        ]
        assert all(
            applied.provenance is Provenance.SCENARIO_OVERRIDE
            for applied in resolution.applied
        )

    def test_empty_scenario_resolves_to_base_inputs(self) -> None:
        """No overrides means the resolved inputs are the base objects."""
        household = base_household()
        assumptions = base_assumptions()
        resolution = resolve_scenario(household, assumptions, Scenario(name="as-is"))
        assert resolution.household is household
        assert resolution.assumptions is assumptions
        assert resolution.applied == ()

    def test_facts_survive_resolution_untouched(self) -> None:
        """Facts are never overridable; resolution carries them as-is."""
        household = base_household()
        scenario = Scenario(
            name="retire-early",
            overrides=(decision_override(PERSON_ID, "target_retirement_age", 60),),
        )
        resolution = resolve_scenario(household, base_assumptions(), scenario)
        base_person = household.persons[0]
        resolved_person = resolution.household.persons[0]
        assert resolved_person.date_of_birth is base_person.date_of_birth
        assert resolved_person.wrappers[0].balance is base_person.wrappers[0].balance


class TestTypeEnforcement:
    """Override values must match the base value's runtime type."""

    def test_mistyped_decision_value_is_rejected(self) -> None:
        """A Decimal cannot replace an int retirement age."""
        override = decision_override(PERSON_ID, "target_retirement_age", Decimal(60))
        scenario = Scenario(name="mistyped", overrides=(override,))
        household = base_household()
        assumptions = base_assumptions()
        with pytest.raises(ScenarioError, match="must hold a int, got Decimal"):
            resolve_scenario(household, assumptions, scenario)

    def test_bool_cannot_replace_int(self) -> None:
        """Bool is an int subtype but never a number here (§4.6 spirit)."""
        override = decision_override(PERSON_ID, "target_retirement_age", True)  # noqa: FBT003
        scenario = Scenario(name="bool", overrides=(override,))
        household = base_household()
        assumptions = base_assumptions()
        with pytest.raises(ScenarioError, match="must hold a int, got bool"):
            resolve_scenario(household, assumptions, scenario)

    def test_mistyped_assumption_value_is_rejected(self) -> None:
        """An int cannot replace a Decimal assumption."""
        override = assumption_override(AssumptionKey.INFLATION_CPI, 5)
        scenario = Scenario(name="mistyped", overrides=(override,))
        household = base_household()
        assumptions = base_assumptions()
        with pytest.raises(ScenarioError, match="must hold a Decimal, got int"):
            resolve_scenario(household, assumptions, scenario)

    def test_entity_invariants_still_apply(self) -> None:
        """A well-typed but invalid value fails the entity's own checks."""
        amount = Money(Decimal(-1))
        override = decision_override(OUTFLOW_ID, "amount_real", amount)
        scenario = Scenario(name="negative", overrides=(override,))
        household = base_household()
        assumptions = base_assumptions()
        with pytest.raises(ValueError, match="must be non-negative"):
            resolve_scenario(household, assumptions, scenario)
