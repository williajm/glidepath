"""Tests for ``.glidepath.json`` reading and writing (issue 6.2, §4.5).

The kitchen-sink document here carries one of everything the schema can
hold — every optional field populated, every enum token, every tagged
value kind — so the canonical golden file pins the wire format and the
round-trip tests exercise every codec branch. Determinism is asserted
at the byte level: a given document always serializes to the same
bytes and load→save round trips are byte-stable, while value
representations are preserved rather than normalized (planning §4.5,
§5.4).

Regenerate the golden file after a reviewed format change with::

    uv run --locked pytest --no-cov tests/test_persistence.py --update-golden
"""

import json
import os
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from factories import money_fact
from glidepath.core import (
    AnnuityBasis,
    AnnuityPurchase,
    AnnuityType,
    AssetAllocation,
    Assumption,
    AssumptionKey,
    AssumptionSet,
    AssumptionTarget,
    ContributionSchedule,
    DBActiveMembership,
    DBPension,
    Decision,
    DecisionTarget,
    EntityId,
    Fact,
    FactorTable,
    FeeSchedule,
    GlidePathConfig,
    GlidePathPoint,
    Household,
    LifeStage,
    Money,
    Override,
    Person,
    PlannedOutflow,
    Provenance,
    Rate,
    ReliefMechanic,
    RevaluationBasis,
    RevaluationReference,
    Scenario,
    Sex,
    SpendingPlan,
    StatePensionRecord,
    TaxResidencyId,
    Wrapper,
    WrapperKindId,
)
from glidepath.persistence import (
    SCHEMA_VERSION,
    AssumptionOverride,
    PersistenceError,
    PlanDocument,
    assumption_overrides_from,
    dumps_plan,
    load_plan,
    loads_plan,
    resolve_assumptions,
    save_plan,
)
from glidepath.persistence.values import decode_value, encode_value
from glidepath.regions.uk import default_assumption_set

if TYPE_CHECKING:
    from collections.abc import Callable

GOLDEN_PATH = Path(__file__).parent / "golden" / "kitchen_sink.glidepath.json"

V1_GOLDEN_PATH = Path(__file__).parent / "golden" / "kitchen_sink_v1.glidepath.json"

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)
RESIDENCY = TaxResidencyId("uk.ruk")
PENSION_KIND = WrapperKindId("uk.sipp")
ISA_KIND = WrapperKindId("uk.isa")

PERSON_ID = EntityId("person-1")
PARTNER_ID = EntityId("person-2")
WRAPPER_ID = EntityId("wrapper-1")
BARE_WRAPPER_ID = EntityId("wrapper-2")
DB_CPI_ID = EntityId("db-1")
DB_FIXED_ID = EntityId("db-2")
DB_FROZEN_ID = EntityId("db-3")
ANNUITY_LEVEL_ID = EntityId("annuity-1")
ANNUITY_LINKED_ID = EntityId("annuity-2")
ANNUITY_ESCALATING_ID = EntityId("annuity-3")
OUTFLOW_ID = EntityId("outflow-1")

DATA_VERSION = "test-region data v1"


def fact[T](value: T, recorded: datetime = RECORDED) -> Fact[T]:
    """A user-stated fact recorded at a fixed timestamp."""
    return Fact(value=value, as_of=AS_OF, recorded_on=recorded)


def decision[T](
    value: T, note: str | None = None, recorded: datetime = RECORDED
) -> Decision[T]:
    """A user choice recorded at a fixed timestamp."""
    return Decision(value=value, recorded_on=recorded, note=note)


def full_wrapper(balance: str = "100000.00", fee: str = "0.0035") -> Wrapper:
    """A wrapper with every optional field populated."""
    schedule = ContributionSchedule(
        employee_amount=decision(Money(Decimal(5000)), note="salary sacrifice soon"),
        employer_amount=money_fact("3000"),
        relief_mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
        escalation=AssumptionKey.EARNINGS_GROWTH_REAL,
    )
    return Wrapper(
        id=WRAPPER_ID,
        kind=PENSION_KIND,
        balance=money_fact(balance),
        label="Aviva SIPP",
        crystallised_balance=money_fact("25000.50"),
        contributions=schedule,
        allocation=AssetAllocation(
            equity=Decimal("0.60"), bonds=Decimal("0.30"), cash=Decimal("0.10")
        ),
        fees=FeeSchedule(platform=Rate(Decimal(fee)), fund=Rate(Decimal("0.0010"))),
    )


def db_pensions() -> tuple[DBPension, ...]:
    """Three DB entitlements covering every revaluation reference.

    The CPI-capped one is an active membership with every optional
    field populated (roadmap 9.6); the other two are deferred.
    """
    factors = FactorTable(factors={62: Decimal("0.85"), 64: Decimal("0.95")})
    cpi_capped = DBPension(
        id=DB_CPI_ID,
        accrued_annual_pension=money_fact("8000"),
        statement_date=date(2026, 1, 1),
        normal_pension_age=fact(65),
        revaluation_basis=RevaluationBasis(
            reference=RevaluationReference.CPI, cap=Rate(Decimal("0.05"))
        ),
        early_late_factors=factors,
        commuted_fraction=decision(Decimal("0.25")),
        commutation_factor=fact(Decimal(12)),
        taken_at_age=decision(62, note="what if I go early"),
        survivor_fraction=fact(Decimal("0.6667")),
        active_membership=DBActiveMembership(
            accrual_rate=fact(Decimal("0.0166667")),
            pensionable_salary=money_fact("42000"),
            active_until_age=decision(60, note="stepping back early"),
        ),
    )
    fixed = DBPension(
        id=DB_FIXED_ID,
        accrued_annual_pension=money_fact("2500"),
        statement_date=date(2020, 4, 6),
        normal_pension_age=fact(60),
        revaluation_basis=RevaluationBasis(
            reference=RevaluationReference.FIXED, fixed_rate=Rate(Decimal("0.03"))
        ),
        early_late_factors=FactorTable(factors={}),
        commuted_fraction=decision(Decimal(0)),
    )
    frozen = DBPension(
        id=DB_FROZEN_ID,
        accrued_annual_pension=money_fact("1200"),
        statement_date=date(2015, 1, 1),
        normal_pension_age=fact(65),
        revaluation_basis=RevaluationBasis(reference=RevaluationReference.NONE),
        early_late_factors=FactorTable(factors={}),
        commuted_fraction=decision(Decimal(0)),
    )
    return (cpi_capped, fixed, frozen)


def annuity_purchases() -> tuple[AnnuityPurchase, ...]:
    """Purchases covering every annuity type token and both bases."""
    return (
        AnnuityPurchase(
            id=ANNUITY_LEVEL_ID,
            at_age=decision(70),
            fraction_of_pot=decision(Decimal("0.5")),
            annuity_type=AnnuityType.LEVEL,
            basis=AnnuityBasis.SINGLE,
        ),
        AnnuityPurchase(
            id=ANNUITY_LINKED_ID,
            at_age=decision(75),
            fraction_of_pot=decision(Decimal("0.25")),
            annuity_type=AnnuityType.INFLATION_LINKED,
            basis=AnnuityBasis.JOINT,
        ),
        AnnuityPurchase(
            id=ANNUITY_ESCALATING_ID,
            at_age=decision(80),
            fraction_of_pot=decision(Decimal(1)),
            annuity_type=AnnuityType.ESCALATING,
            basis=AnnuityBasis.SINGLE,
        ),
    )


def full_person(
    dob: date = date(1970, 6, 1),
    balance: str = "100000.00",
    fee: str = "0.0035",
    deferral: str = "0.5",
    note: str | None = "from my statement",
) -> Person:
    """A person with every optional field populated."""
    state_pension = StatePensionRecord(
        forecast_weekly_amount=money_fact("230.25"),
        protected_payment=money_fact("20.10"),
        deferral_years=decision(Decimal(deferral)),
    )
    glide = GlidePathConfig(
        points=(
            GlidePathPoint(
                years_to_retirement=0,
                allocation=AssetAllocation(
                    equity=Decimal("0.30"), bonds=Decimal("0.70")
                ),
            ),
            GlidePathPoint(
                years_to_retirement=10,
                allocation=AssetAllocation(
                    equity=Decimal("0.80"), bonds=Decimal("0.20")
                ),
            ),
        )
    )
    bare_wrapper = Wrapper(
        id=BARE_WRAPPER_ID, kind=ISA_KIND, balance=money_fact("50000")
    )
    return Person(
        id=PERSON_ID,
        date_of_birth=Fact(value=dob, as_of=AS_OF, recorded_on=RECORDED, note=note),
        target_retirement_age=decision(65, note="maybe 60"),
        tax_residency=RESIDENCY,
        sex_for_longevity=fact(Sex.FEMALE),
        employment_income=money_fact("52000"),
        mpaa_triggered_on=fact(date(2024, 1, 15)),
        lsa_used=money_fact("10000"),
        death_age=decision(88, note="modelling an earlier death"),
        wrappers=(full_wrapper(balance=balance, fee=fee), bare_wrapper),
        db_pensions=db_pensions(),
        annuity_purchases=annuity_purchases(),
        state_pension=state_pension,
        glide_path=glide,
    )


def minimal_partner() -> Person:
    """A second person with almost nothing optional (covers the MALE token)."""
    return Person(
        id=PARTNER_ID,
        date_of_birth=fact(date(1972, 3, 15)),
        target_retirement_age=decision(67),
        tax_residency=RESIDENCY,
        sex_for_longevity=fact(Sex.MALE),
    )


def kitchen_sink_household(
    dob: date = date(1970, 6, 1),
    balance: str = "100000.00",
    fee: str = "0.0035",
    deferral: str = "0.5",
    note: str | None = "from my statement",
) -> Household:
    """Two persons plus shared economics, one of everything."""
    spending = SpendingPlan(
        annual_spending_real=money_fact("24000"),
        stage_multipliers={
            LifeStage.GO_GO: Decimal("1.20"),
            LifeStage.SLOW_GO: Decimal("0.95"),
            LifeStage.NO_GO: Decimal("0.85"),
            LifeStage.DECUMULATION: Decimal("1.10"),
        },
    )
    outflow = PlannedOutflow(
        id=OUTFLOW_ID,
        label="new roof",
        amount_real=decision(Money(Decimal(20000)), note="quoted 2026"),
        at_age_of=(PERSON_ID, 66),
    )
    return Household(
        persons=(
            full_person(
                dob=dob, balance=balance, fee=fee, deferral=deferral, note=note
            ),
            minimal_partner(),
        ),
        spending=spending,
        planned_outflows=(outflow,),
        claim_marriage_allowance=decision(value=False, note="we cohabit"),
    )


def kitchen_sink_scenarios() -> tuple[Scenario, ...]:
    """Scenarios exercising every override target and value kind."""
    what_if = Scenario(
        name="retire at 60",
        note="the big question",
        overrides=(
            Override(
                target=DecisionTarget(
                    entity_id=PERSON_ID, field_path="target_retirement_age"
                ),
                value=60,
                note="five years early",
            ),
            Override(
                target=DecisionTarget(
                    entity_id=WRAPPER_ID,
                    field_path="contributions.employee_amount",
                ),
                value=Money(Decimal(8000)),
            ),
            Override(
                target=DecisionTarget(
                    entity_id=DB_CPI_ID, field_path="commuted_fraction"
                ),
                value=Decimal("0.1"),
            ),
            Override(
                target=DecisionTarget(
                    entity_id=ANNUITY_LEVEL_ID, field_path="annuity_type"
                ),
                value=AnnuityType.INFLATION_LINKED,
            ),
            Override(
                target=DecisionTarget(entity_id=ANNUITY_LEVEL_ID, field_path="basis"),
                value=AnnuityBasis.JOINT,
            ),
            Override(
                target=AssumptionTarget(key=AssumptionKey.INFLATION_CPI),
                value=Decimal("0.04"),
            ),
            Override(
                target=AssumptionTarget(key=AssumptionKey.GLIDEPATH_DEFAULT_SHAPE),
                value={
                    "equity_start": Decimal("0.9"),
                    "derisk_years_before_retirement": 15,
                    "transition": "linear",
                },
            ),
        ),
    )
    return (what_if, Scenario(name="do nothing"))


def kitchen_sink_document(
    *,
    dob: date = date(1970, 6, 1),
    balance: str = "100000.00",
    fee: str = "0.0035",
    deferral: str = "0.5",
    note: str | None = "from my statement",
    recorded: datetime = RECORDED,
) -> PlanDocument:
    """One of everything the schema can hold."""
    overrides = (
        AssumptionOverride(
            key=AssumptionKey.INFLATION_CPI,
            value=Decimal("0.03"),
            source="my own view",
            recorded_on=recorded,
        ),
        AssumptionOverride(
            key=AssumptionKey.HORIZON_PLANNING_AGE,
            value=99,
            source="family history",
            recorded_on=recorded,
        ),
        AssumptionOverride(
            key=AssumptionKey.POLICY_STATE_PENSION_UPRATING,
            value="cpi",
            source="pessimism",
            recorded_on=recorded,
        ),
        AssumptionOverride(
            key=AssumptionKey.GLIDEPATH_DEFAULT_SHAPE,
            value={
                "equity_start": Decimal("0.85"),
                "derisk_years_before_retirement": 12,
                "transition": "linear",
                "in_drawdown": "hold",
                "equity_at_retirement": Decimal("0.35"),
            },
            source="my IFA's shape",
            recorded_on=recorded,
        ),
    )
    return PlanDocument(
        region="uk",
        assumptions_resolved_against=DATA_VERSION,
        household=kitchen_sink_household(
            dob=dob, balance=balance, fee=fee, deferral=deferral, note=note
        ),
        assumption_overrides=overrides,
        scenarios=kitchen_sink_scenarios(),
    )


def minimal_document() -> PlanDocument:
    """The smallest valid document: one bare person, nothing optional."""
    person = Person(
        id=PERSON_ID,
        date_of_birth=fact(date(1980, 1, 1)),
        target_retirement_age=decision(68),
        tax_residency=RESIDENCY,
    )
    return PlanDocument(
        region="uk",
        assumptions_resolved_against=DATA_VERSION,
        household=Household(persons=(person,)),
    )


def payload_of(document: PlanDocument) -> dict[str, Any]:
    """The document's canonical text parsed back to a mutable payload."""
    return cast("dict[str, Any]", json.loads(dumps_plan(document)))


def loads_payload(payload: dict[str, Any]) -> PlanDocument:
    """Re-serialize a (mutated) payload and decode it."""
    return loads_plan(json.dumps(payload))


class TestCanonicalForm:
    """The §4.5 byte-level guarantees of the writer."""

    def test_matches_checked_in_golden(self, request: pytest.FixtureRequest) -> None:
        """The kitchen-sink document reproduces the reviewed golden file."""
        rendered = dumps_plan(kitchen_sink_document())
        if request.config.getoption("--update-golden"):
            GOLDEN_PATH.parent.mkdir(exist_ok=True)
            GOLDEN_PATH.write_text(rendered, encoding="utf-8", newline="\n")
        stored = GOLDEN_PATH.read_text(encoding="utf-8")
        assert stored == rendered, (
            "canonical output shifted — regenerate with `uv run --locked pytest"
            " --no-cov tests/test_persistence.py --update-golden`, review the"
            " diff, and explain it in the pull request"
        )

    def test_output_is_canonical_json(self) -> None:
        """Sorted keys, 2-space indent, one trailing LF, no CRLF."""
        text = dumps_plan(kitchen_sink_document())
        assert text.endswith("}\n")
        assert "\r" not in text
        reference = (
            json.dumps(json.loads(text), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )
        assert text == reference

    def test_equal_documents_serialize_byte_identically(self) -> None:
        """Two independently built equal documents give identical text."""
        first = kitchen_sink_document()
        second = kitchen_sink_document()
        assert first is not second
        assert dumps_plan(first) == dumps_plan(second)

    def test_decimals_travel_as_strings(self) -> None:
        """No JSON floats anywhere in the output (planning §4.6)."""
        text = dumps_plan(kitchen_sink_document())
        assert '"100000.00"' in text
        assert '"0.0035"' in text

    def test_save_writes_lf_bytes(self, tmp_path: Path) -> None:
        """Saved files carry LF endings even on Windows."""
        target = tmp_path / "plan.glidepath.json"
        document = kitchen_sink_document()
        save_plan(document, target)
        data = target.read_bytes()
        assert b"\r" not in data
        assert data == dumps_plan(document).encode("utf-8")

    def test_save_then_load_round_trips(self, tmp_path: Path) -> None:
        """A saved file loads back to an equal document."""
        target = tmp_path / "plan.glidepath.json"
        document = kitchen_sink_document()
        save_plan(document, target)
        assert load_plan(target) == document

    def test_failed_save_leaves_the_existing_file_intact(self, tmp_path: Path) -> None:
        """A document that cannot be encoded never truncates the target."""
        target = tmp_path / "plan.glidepath.json"
        save_plan(minimal_document(), target)
        before = target.read_bytes()
        unsavable = _document_with_override_value(object())
        with pytest.raises(PersistenceError, match="cannot persist"):
            save_plan(unsavable, target)
        assert target.read_bytes() == before

    def test_save_leaves_no_temporary_file_behind(self, tmp_path: Path) -> None:
        """The atomic-write staging file is gone after a clean save."""
        target = tmp_path / "plan.glidepath.json"
        save_plan(minimal_document(), target)
        assert list(tmp_path.iterdir()) == [target]

    def test_failed_write_never_truncates_the_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A mid-write failure leaves the last saved plan untouched.

        The write goes to a sibling temporary file that only replaces
        the target after closing cleanly; a failure at the replace step
        stands in for disk-full or power loss mid-write.
        """
        target = tmp_path / "plan.glidepath.json"
        save_plan(minimal_document(), target)
        before = target.read_bytes()

        def refuse(_src: object, _dst: object) -> None:
            msg = "disk full"
            raise OSError(msg)

        monkeypatch.setattr(os, "replace", refuse)
        document = kitchen_sink_document()
        with pytest.raises(OSError, match="disk full"):
            save_plan(document, target)
        assert target.read_bytes() == before
        assert list(tmp_path.iterdir()) == [target]

    def test_decimal_representations_are_preserved_not_normalized(self) -> None:
        """Equal-but-differently-written decimals keep their spellings.

        `Decimal("1.0")` and `Decimal("1.00")` compare equal, so two
        documents differing only in exponent are equal too — but their
        bytes intentionally differ: the writer preserves the stated
        representation rather than normalizing it (planning §5.4). The
        guarantee is per-representation determinism plus byte-stable
        round trips, not byte-identity across equal values.
        """
        precise = _document_with_override_value(Decimal("1.00"))
        loose = _document_with_override_value(Decimal("1.0"))
        assert precise == loose
        assert '"1.00"' in dumps_plan(precise)
        assert '"1.0"' in dumps_plan(loose)
        assert loads_plan(dumps_plan(precise)) == precise


class TestRoundTrip:
    """loads ∘ dumps is the identity (issue 6.2's acceptance)."""

    def test_kitchen_sink_round_trips(self) -> None:
        """Every populated field survives the round trip exactly."""
        document = kitchen_sink_document()
        assert loads_plan(dumps_plan(document)) == document

    def test_minimal_document_round_trips(self) -> None:
        """Every absent optional survives as absent."""
        document = minimal_document()
        assert loads_plan(dumps_plan(document)) == document

    def test_reload_is_byte_stable(self) -> None:
        """Dumps ∘ loads ∘ dumps is byte-identical to dumps."""
        text = dumps_plan(kitchen_sink_document())
        assert dumps_plan(loads_plan(text)) == text

    def test_checked_in_v1_file_loads_through_the_full_chain(self) -> None:
        """A genuine v1 file upgrades 1→2→3→4 on load (§4.5).

        The fixture is the kitchen-sink golden as the v1 build wrote
        it — never synthesized from a current document. Its DB
        pensions predate ``active_membership``, its state pension
        record still carries the retired derivation fields, and its
        stage multipliers carry the accumulation-stage tokens the v1
        build accepted (retired by the #129 migration).
        """
        raw = json.loads(V1_GOLDEN_PATH.read_text(encoding="utf-8"))
        assert raw["schema_version"] == 1
        stored_person = raw["household"]["persons"][0]
        assert "active_membership" not in stored_person["db_pensions"][0]
        stored_record = stored_person["state_pension"]
        assert "ni_record_start" in stored_record
        assert "qualifying_years" in stored_record
        assert "planned_extra_years" in stored_record
        stored_multipliers = raw["household"]["spending"]["stage_multipliers"]
        assert "early_accumulation" in stored_multipliers
        assert "mid_accumulation" in stored_multipliers
        assert "pre_retirement" in stored_multipliers
        loaded = load_plan(V1_GOLDEN_PATH)
        persons = loaded.household.persons
        assert [person.id for person in persons] == [PERSON_ID, PARTNER_ID]
        pensions = [pension for person in persons for pension in person.db_pensions]
        assert [pension.id for pension in pensions] == [
            DB_CPI_ID,
            DB_FIXED_ID,
            DB_FROZEN_ID,
        ]
        assert all(pension.active_membership is None for pension in pensions)
        record = persons[0].state_pension
        assert record is not None
        assert record.forecast_weekly_amount is not None
        assert record.forecast_weekly_amount.value == Money(Decimal("230.25"))
        assert record.protected_payment is not None
        assert record.protected_payment.value == Money(Decimal("20.10"))
        assert record.deferral_years.value == Decimal("0.5")
        spending = loaded.household.spending
        assert spending is not None
        assert spending.stage_multipliers == {LifeStage.DECUMULATION: Decimal("1.10")}
        assert [scenario.name for scenario in loaded.scenarios] == [
            "retire at 60",
            "do nothing",
        ]
        assert loaded.assumption_overrides[0].key is AssumptionKey.INFLATION_CPI
        assert loaded.assumption_overrides[0].value == Decimal("0.03")
        rewritten = json.loads(dumps_plan(loaded))
        assert rewritten["schema_version"] == SCHEMA_VERSION

    def test_v1_file_loads_with_every_pension_deferred(self) -> None:
        """The 9.6 migration upgrades a v1 file on load (§4.5)."""
        payload = payload_of(kitchen_sink_document())
        for person in payload["household"]["persons"]:
            for pension in person["db_pensions"]:
                del pension["active_membership"]
        payload["schema_version"] = 1
        loaded = loads_plan(json.dumps(payload))
        pensions = [
            pension
            for person in loaded.household.persons
            for pension in person.db_pensions
        ]
        assert len(pensions) == 3
        assert all(pension.active_membership is None for pension in pensions)

    def test_v2_file_drops_the_qualifying_years_fields(self) -> None:
        """The #97 migration retires the derivation-only fields on load."""
        payload = payload_of(kitchen_sink_document())
        record = payload["household"]["persons"][0]["state_pension"]
        record["ni_record_start"] = {
            "value": "2016-04-06",
            "as_of": "2026-08-01",
            "recorded_on": "2026-08-01T12:00:00+00:00",
            "note": None,
        }
        record["qualifying_years"] = {
            "value": 20,
            "as_of": "2026-08-01",
            "recorded_on": "2026-08-01T12:00:00+00:00",
            "note": None,
        }
        record["planned_extra_years"] = {
            "value": 2,
            "recorded_on": "2026-08-01T12:00:00+00:00",
            "note": None,
        }
        payload["schema_version"] = 2
        loaded = loads_plan(json.dumps(payload))
        migrated = loaded.household.persons[0].state_pension
        assert migrated is not None
        assert migrated.forecast_weekly_amount is not None
        assert migrated.deferral_years.value == Decimal("0.5")

    def test_v2_record_without_a_forecast_still_loads(self) -> None:
        """A derivation-only v2 record keeps its deferral choice (#97).

        Projection later refuses it with a demand for a forecast — the
        load itself must not lose the plan.
        """
        payload = payload_of(kitchen_sink_document())
        record = payload["household"]["persons"][0]["state_pension"]
        record["forecast_weekly_amount"] = None
        record["protected_payment"] = None
        record["qualifying_years"] = {
            "value": 20,
            "as_of": "2026-08-01",
            "recorded_on": "2026-08-01T12:00:00+00:00",
            "note": None,
        }
        payload["schema_version"] = 2
        loaded = loads_plan(json.dumps(payload))
        migrated = loaded.household.persons[0].state_pension
        assert migrated is not None
        assert migrated.forecast_weekly_amount is None
        assert migrated.deferral_years.value == Decimal("0.5")

    def test_v3_file_drops_the_accumulation_stage_multipliers(self) -> None:
        """The #129 migration retires the accumulation-stage keys on load."""
        payload = payload_of(kitchen_sink_document())
        payload["household"]["spending"]["stage_multipliers"] = {
            "decumulation": "1.10",
            "early_accumulation": "1.00",
            "mid_accumulation": "1.05",
            "pre_retirement": "0.95",
        }
        payload["schema_version"] = 3
        loaded = loads_plan(json.dumps(payload))
        spending = loaded.household.spending
        assert spending is not None
        assert spending.stage_multipliers == {LifeStage.DECUMULATION: Decimal("1.10")}

    def test_v4_file_loads_with_unnamed_wrappers(self) -> None:
        """The 9.28 migration adds the wrapper ``label`` key on load."""
        payload = payload_of(kitchen_sink_document())
        for person in payload["household"]["persons"]:
            for wrapper in person["wrappers"]:
                del wrapper["label"]
        payload["schema_version"] = 4
        loaded = loads_plan(json.dumps(payload))
        wrappers = loaded.household.persons[0].wrappers
        assert wrappers
        assert all(wrapper.label is None for wrapper in wrappers)

    def test_v5_file_loads_with_the_default_marriage_allowance_claim(self) -> None:
        """The 9.32 migration adds the household claim key on load."""
        payload = payload_of(kitchen_sink_document())
        del payload["household"]["claim_marriage_allowance"]
        payload["schema_version"] = 5
        loaded = loads_plan(json.dumps(payload))
        assert loaded.household.claim_marriage_allowance is None

    @given(
        balance=st.decimals(
            min_value=0,
            max_value=10**9,
            allow_nan=False,
            allow_infinity=False,
            places=2,
        ),
        fee=st.decimals(
            min_value=0,
            max_value=Decimal("0.05"),
            allow_nan=False,
            allow_infinity=False,
            places=4,
        ),
        dob=st.dates(min_value=date(1940, 1, 1), max_value=date(2000, 12, 31)),
        deferral=st.sampled_from(["0", "0.25", "0.5", "1", "1.5", "2"]),
        note=st.none() | st.text(max_size=40),
        recorded=st.datetimes(
            # Naive bounds are the strategy's contract; it applies the tz.
            min_value=datetime(1990, 1, 1),  # noqa: DTZ001
            max_value=datetime(2050, 1, 1),  # noqa: DTZ001
            timezones=st.sampled_from(
                (
                    UTC,
                    timezone(timedelta(hours=5, minutes=30)),
                    timezone(-timedelta(hours=8)),
                )
            ),
        ),
    )
    def test_documents_round_trip(
        self,
        *,
        balance: Decimal,
        fee: Decimal,
        dob: date,
        deferral: str,
        note: str | None,
        recorded: datetime,
    ) -> None:
        """Property: any valid document round-trips exactly (§4.5)."""
        document = kitchen_sink_document(
            dob=dob,
            balance=str(balance),
            fee=str(fee),
            deferral=deferral,
            note=note,
            recorded=recorded,
        )
        assert loads_plan(dumps_plan(document)) == document

    @given(
        value=st.recursive(
            st.one_of(
                st.integers(min_value=-(10**9), max_value=10**9),
                st.text(max_size=30),
                st.decimals(
                    min_value=-(10**9),
                    max_value=10**9,
                    allow_nan=False,
                    allow_infinity=False,
                ),
                st.decimals(
                    min_value=0, max_value=10**9, allow_nan=False, allow_infinity=False
                ).map(Money),
                st.sampled_from(list(AnnuityType)),
                st.sampled_from(list(AnnuityBasis)),
            ),
            lambda children: st.dictionaries(
                st.text(max_size=10), children, max_size=4
            ),
            max_leaves=10,
        )
    )
    def test_tagged_values_round_trip(self, value: object) -> None:
        """Property: the polymorphic value codec preserves exact types."""
        encoded = encode_value(value, "value")
        text = json.dumps(encoded, ensure_ascii=False, indent=2, sort_keys=True)
        assert decode_value(json.loads(text), "value") == value


class TestDocumentModel:
    """PlanDocument and AssumptionOverride invariants."""

    def test_rejects_empty_region(self) -> None:
        """A document must name its region."""
        household = minimal_document().household
        with pytest.raises(ValueError, match="region must be non-empty"):
            PlanDocument(
                region="",
                assumptions_resolved_against=DATA_VERSION,
                household=household,
            )

    def test_rejects_empty_data_version(self) -> None:
        """A document must record what it was resolved against (§4.5)."""
        household = minimal_document().household
        with pytest.raises(ValueError, match="assumptions_resolved_against"):
            PlanDocument(
                region="uk", assumptions_resolved_against="", household=household
            )

    def test_rejects_duplicate_override_keys(self) -> None:
        """Two stored overrides may not target one assumption."""
        household = minimal_document().household
        override = AssumptionOverride(
            key=AssumptionKey.INFLATION_CPI,
            value=Decimal("0.03"),
            source="view",
            recorded_on=RECORDED,
        )
        with pytest.raises(ValueError, match="distinct keys"):
            PlanDocument(
                region="uk",
                assumptions_resolved_against=DATA_VERSION,
                household=household,
                assumption_overrides=(override, override),
            )

    def test_rejects_duplicate_scenario_names(self) -> None:
        """Scenario names identify scenarios, so they must be unique."""
        household = minimal_document().household
        scenarios = (Scenario(name="same"), Scenario(name="same"))
        with pytest.raises(ValueError, match="distinct names"):
            PlanDocument(
                region="uk",
                assumptions_resolved_against=DATA_VERSION,
                household=household,
                scenarios=scenarios,
            )

    def test_override_rejects_empty_source(self) -> None:
        """A user override must state its basis."""
        value = Decimal("0.03")
        with pytest.raises(ValueError, match="source must be non-empty"):
            AssumptionOverride(
                key=AssumptionKey.INFLATION_CPI,
                value=value,
                source="",
                recorded_on=RECORDED,
            )

    def test_override_rejects_naive_timestamp(self) -> None:
        """Datetimes are always timezone-aware (repo rule)."""
        value = Decimal("0.03")
        # The naive timestamp is exactly the rejected input under test.
        naive = datetime(2026, 8, 1, 12, 0)  # noqa: DTZ001
        with pytest.raises(ValueError, match="timezone-aware"):
            AssumptionOverride(
                key=AssumptionKey.INFLATION_CPI,
                value=value,
                source="view",
                recorded_on=naive,
            )


def _drop_region(payload: dict[str, Any]) -> None:
    """Remove the required region key."""
    del payload["region"]


def _add_unknown_key(payload: dict[str, Any]) -> None:
    """Add a key the schema does not define."""
    payload["surprise"] = 1


def _person(payload: dict[str, Any]) -> dict[str, Any]:
    """The first person's payload."""
    persons: list[dict[str, Any]] = payload["household"]["persons"]
    return persons[0]


def _unknown_person_key(payload: dict[str, Any]) -> None:
    """Add an unknown key to a person."""
    _person(payload)["surprise"] = 1


def _bad_sex_token(payload: dict[str, Any]) -> None:
    """Use a token outside the sex vocabulary."""
    _person(payload)["sex_for_longevity"]["value"] = "other"


def _naive_recorded_on(payload: dict[str, Any]) -> None:
    """Strip the offset from a stored timestamp."""
    _person(payload)["date_of_birth"]["recorded_on"] = "2026-08-01T12:00:00"


def _bad_date(payload: dict[str, Any]) -> None:
    """Corrupt a stored date."""
    _person(payload)["date_of_birth"]["as_of"] = "not-a-date"


def _bad_datetime(payload: dict[str, Any]) -> None:
    """Corrupt a stored datetime."""
    _person(payload)["date_of_birth"]["recorded_on"] = "not-a-time"


def _negative_balance(payload: dict[str, Any]) -> None:
    """Violate the wrapper's non-negative balance invariant."""
    _person(payload)["wrappers"][0]["balance"]["value"] = "-1"


def _unbalanced_allocation(payload: dict[str, Any]) -> None:
    """Violate the allocation's sum-to-one invariant."""
    _person(payload)["wrappers"][0]["allocation"]["equity"] = "0.90"


def _bad_factor_age(payload: dict[str, Any]) -> None:
    """Use a non-numeric age key in a factor table."""
    _person(payload)["db_pensions"][0]["early_late_factors"] = {"abc": "1"}


def _bad_stage_token(payload: dict[str, Any]) -> None:
    """Use a token outside the life-stage vocabulary."""
    payload["household"]["spending"]["stage_multipliers"] = {"party_years": "1.2"}


def _bad_relief_mechanic(payload: dict[str, Any]) -> None:
    """Use a token outside the relief-mechanic vocabulary."""
    _person(payload)["wrappers"][0]["contributions"]["relief_mechanic"] = "magic"


def _bad_revaluation_reference(payload: dict[str, Any]) -> None:
    """Use a token outside the revaluation vocabulary."""
    _person(payload)["db_pensions"][0]["revaluation_basis"]["reference"] = "rpi"


def _empty_entity_id(payload: dict[str, Any]) -> None:
    """Use an empty entity id."""
    _person(payload)["wrappers"][0]["id"] = ""


def _wrong_schema_version_type(payload: dict[str, Any]) -> None:
    """Store the schema version as text."""
    payload["schema_version"] = "one"


def _unknown_assumption_key(payload: dict[str, Any]) -> None:
    """Reference a key outside the stable catalogue."""
    payload["assumption_overrides"][0]["key"] = "inflation.rpi"


def _unknown_value_kind(payload: dict[str, Any]) -> None:
    """Use a value tag outside the closed vocabulary."""
    payload["assumption_overrides"][0]["value"] = {"kind": "float", "value": 1}


def _malformed_tagged_value(payload: dict[str, Any]) -> None:
    """Drop the value half of a tagged value."""
    payload["assumption_overrides"][0]["value"] = {"kind": "int"}


def _bool_as_int(payload: dict[str, Any]) -> None:
    """Store a boolean where a whole number belongs."""
    payload["assumption_overrides"][0]["value"] = {"kind": "int", "value": True}


def _bad_target_kind(payload: dict[str, Any]) -> None:
    """Use an override target kind outside the vocabulary."""
    payload["scenarios"][0]["overrides"][0]["target"] = {"kind": "fact", "key": "x"}


def _null_required_field(payload: dict[str, Any]) -> None:
    """Store null where the schema requires a value."""
    _person(payload)["tax_residency"] = None


def _list_person(payload: dict[str, Any]) -> None:
    """Store an array where the schema requires an object."""
    payload["household"]["persons"] = [[]]


def _object_persons(payload: dict[str, Any]) -> None:
    """Store an object where the schema requires an array."""
    payload["household"]["persons"] = {}


def _text_tagged_table(payload: dict[str, Any]) -> None:
    """Store text where a tagged table's entries belong."""
    payload["assumption_overrides"][0]["value"] = {"kind": "table", "value": "flat"}


def _text_factor_table(payload: dict[str, Any]) -> None:
    """Store text where a factor-table object belongs."""
    _person(payload)["db_pensions"][0]["early_late_factors"] = "none"


def _array_stage_multipliers(payload: dict[str, Any]) -> None:
    """Store an array where the stage-multiplier object belongs."""
    payload["household"]["spending"]["stage_multipliers"] = ["go_go"]


def _non_finite_decimal(payload: dict[str, Any]) -> None:
    """Store a non-finite decimal string."""
    payload["assumption_overrides"][0]["value"] = {"kind": "decimal", "value": "NaN"}


def _non_decimal_string(payload: dict[str, Any]) -> None:
    """Store an unparseable decimal string."""
    payload["assumption_overrides"][0]["value"] = {"kind": "decimal", "value": "12p"}


class TestDecodeErrors:
    """The reader fails loudly, with a document path (issue 6.2)."""

    @pytest.mark.parametrize(
        ("mutate", "message"),
        [
            (_drop_region, "missing required key 'region'"),
            (_add_unknown_key, "unknown keys: surprise"),
            (_unknown_person_key, "unknown keys: surprise"),
            (_bad_sex_token, "unknown token 'other'"),
            (_naive_recorded_on, "timezone-aware"),
            (_bad_date, "not an ISO-8601 date"),
            (_bad_datetime, "not an ISO-8601 datetime"),
            (_negative_balance, "must be non-negative"),
            (_unbalanced_allocation, "sum to exactly 1"),
            (_bad_factor_age, "ages must be whole years"),
            (_bad_stage_token, "unknown token 'party_years'"),
            (_bad_relief_mechanic, "unknown token 'magic'"),
            (_bad_revaluation_reference, "unknown token 'rpi'"),
            (_empty_entity_id, "entity ids must be non-empty"),
            (_wrong_schema_version_type, "must be a whole number"),
            (_unknown_assumption_key, "unknown assumption key"),
            (_unknown_value_kind, "unknown value kind 'float'"),
            (_malformed_tagged_value, "tagged value object"),
            (_bool_as_int, "expected a whole number"),
            (_bad_target_kind, "expected 'assumption' or 'decision'"),
            (_null_required_field, "expected a string"),
            (_list_person, "expected an object"),
            (_object_persons, "expected an array"),
            (_text_tagged_table, "expected a table object, got str"),
            (_text_factor_table, "early_late_factors: expected an object, got str"),
            (_array_stage_multipliers, "stage_multipliers: expected an object"),
            (_non_finite_decimal, "must be finite"),
            (_non_decimal_string, "not a decimal number"),
        ],
    )
    def test_mutated_payload_is_rejected(
        self, mutate: Callable[[dict[str, Any]], None], message: str
    ) -> None:
        """Each schema violation raises with a pointed message."""
        payload = payload_of(kitchen_sink_document())
        mutate(payload)
        with pytest.raises(PersistenceError, match=message):
            loads_payload(payload)

    def test_rejects_invalid_json(self) -> None:
        """Unparseable text is a persistence error, not a JSONDecodeError."""
        with pytest.raises(PersistenceError, match="not valid JSON"):
            loads_plan("{nope")

    def test_rejects_non_object_root(self) -> None:
        """The document root must be an object."""
        with pytest.raises(PersistenceError, match="root must be an object"):
            loads_plan("[]")

    def test_rejects_json_floats(self) -> None:
        """Floats never enter the plan (planning §4.6)."""
        payload = payload_of(minimal_document())
        text = json.dumps(payload).replace(
            f'"schema_version": {SCHEMA_VERSION}', '"schema_version": 1.5'
        )
        with pytest.raises(PersistenceError, match="floats are not permitted"):
            loads_plan(text)

    def test_rejects_a_truncated_document(self) -> None:
        """A file cut off mid-JSON (a crashed writer) fails as invalid JSON.

        The §4.5 atomic save makes this shape unreachable through
        glidepath itself, but a copy truncated by a failing disk or
        transfer must still fail loudly, never half-load.
        """
        text = dumps_plan(minimal_document())
        truncated = text[: len(text) // 2]
        with pytest.raises(PersistenceError, match="not valid JSON"):
            loads_plan(truncated)

    def test_rejects_a_utf8_bom(self, tmp_path: Path) -> None:
        """A BOM-prefixed file is rejected as invalid JSON.

        The canonical writer never emits a byte-order mark and the
        reader decodes strict UTF-8 (not ``utf-8-sig``), so a plan
        re-saved by a BOM-adding editor fails loudly at the JSON layer
        rather than parsing with an invisible extra character.
        """
        target = tmp_path / "plan.glidepath.json"
        body = dumps_plan(minimal_document()).encode("utf-8")
        target.write_bytes(b"\xef\xbb\xbf" + body)
        with pytest.raises(PersistenceError, match="not valid JSON"):
            load_plan(target)

    def test_rejects_a_version_the_migration_chain_failed_to_reach(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The decoder pins the post-migration version (defence in depth).

        ``apply_migrations`` already guarantees it returns the current
        version, so the decoder's own check is unreachable through the
        real registry — a neutered migration layer stands in for a
        future bug that hands the strict decoder a stale document.
        """
        monkeypatch.setattr(
            "glidepath.persistence.decode.apply_migrations", lambda raw: raw
        )
        payload = payload_of(minimal_document())
        payload["schema_version"] = SCHEMA_VERSION - 1
        with pytest.raises(
            PersistenceError,
            match=f"expected {SCHEMA_VERSION} after migration",
        ):
            loads_payload(payload)


class TestEncodeErrors:
    """The writer refuses values outside the closed vocabulary."""

    def test_rejects_unsupported_override_value(self) -> None:
        """An unpersistable override value fails at save, not load."""
        document = _document_with_override_value(object())
        with pytest.raises(PersistenceError, match="cannot persist a value"):
            dumps_plan(document)

    def test_rejects_boolean_override_value(self) -> None:
        """Booleans are not in the §4.5 value vocabulary."""
        document = _document_with_override_value(value=True)
        with pytest.raises(PersistenceError, match="booleans are not"):
            dumps_plan(document)

    def test_rejects_non_string_table_keys(self) -> None:
        """Table keys must be strings to survive JSON."""
        document = _document_with_override_value({1: Decimal("0.5")})
        with pytest.raises(PersistenceError, match="table keys must be strings"):
            dumps_plan(document)

    @pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
    def test_rejects_non_finite_decimal_values(self, raw: str) -> None:
        """The writer refuses the non-finite values its reader refuses."""
        document = _document_with_override_value(Decimal(raw))
        with pytest.raises(PersistenceError, match="must be finite"):
            dumps_plan(document)

    def test_rejects_non_finite_decimals_inside_tables(self) -> None:
        """The finiteness rule recurses through table values."""
        document = _document_with_override_value({"rate": Decimal("Infinity")})
        with pytest.raises(PersistenceError, match="must be finite"):
            dumps_plan(document)

    def test_rejects_a_boolean_in_a_whole_number_field(self) -> None:
        """A bool smuggled past the type checker cannot be written."""
        smuggled = True
        person = replace(
            minimal_document().household.persons[0],
            target_retirement_age=decision(cast("int", smuggled)),
        )
        document = _document_with_household(Household(persons=(person,)))
        with pytest.raises(PersistenceError, match="not persisted whole numbers"):
            dumps_plan(document)

    def test_rejects_an_empty_entity_id(self) -> None:
        """An empty id would be unloadable, so it is unsavable too."""
        person = replace(minimal_document().household.persons[0], id=EntityId(""))
        document = _document_with_household(Household(persons=(person,)))
        with pytest.raises(PersistenceError, match="entity ids must be non-empty"):
            dumps_plan(document)


def _document_with_household(household: Household) -> PlanDocument:
    """A minimal document around the given household."""
    return PlanDocument(
        region="uk",
        assumptions_resolved_against=DATA_VERSION,
        household=household,
    )


def _document_with_override_value(value: object) -> PlanDocument:
    """A minimal document whose one scenario override holds ``value``."""
    base = minimal_document()
    scenario = Scenario(
        name="probe",
        overrides=(
            Override(
                target=AssumptionTarget(key=AssumptionKey.INFLATION_CPI), value=value
            ),
        ),
    )
    return PlanDocument(
        region=base.region,
        assumptions_resolved_against=base.assumptions_resolved_against,
        household=base.household,
        scenarios=(scenario,),
    )


def _with_text_value(entry: Assumption[Any]) -> Assumption[Any]:
    """The entry with its value re-typed to text (a drifted default)."""
    return Assumption(
        key=entry.key,
        value=str(entry.value),
        default_value=entry.default_value,
        provenance=entry.provenance,
        source=entry.source,
        recorded_on=entry.recorded_on,
        description=entry.description,
    )


def base_assumptions() -> AssumptionSet:
    """A small default-provenance assumption set to resolve against."""
    entries: dict[AssumptionKey, object] = {
        AssumptionKey.INFLATION_CPI: Decimal("0.02"),
        AssumptionKey.HORIZON_PLANNING_AGE: 95,
        AssumptionKey.POLICY_STATE_PENSION_UPRATING: "triple_lock",
        AssumptionKey.GLIDEPATH_DEFAULT_SHAPE: {"equity_start": Decimal("0.8")},
    }
    return AssumptionSet(
        Assumption(
            key=key,
            value=value,
            default_value=value,
            provenance=Provenance.DEFAULT_ASSUMPTION,
            source="shipped basis",
            recorded_on=RECORDED,
            description=f"shipped default for {key.value}",
        )
        for key, value in entries.items()
    )


class TestResolveAssumptions:
    """Defaults ⊕ stored overrides on load (planning §4.5)."""

    def test_overrides_apply_with_user_override_provenance(self) -> None:
        """A stored override becomes a USER_OVERRIDE assumption."""
        document = kitchen_sink_document()
        resolved = resolve_assumptions(document, base_assumptions())
        entry = resolved.get(AssumptionKey.INFLATION_CPI)
        assert entry.value == Decimal("0.03")
        assert entry.provenance is Provenance.USER_OVERRIDE
        assert entry.source == "my own view"
        assert entry.recorded_on == RECORDED

    def test_overrides_keep_the_shipped_default_alongside(self) -> None:
        """The UI can always answer "what would this have been?"."""
        document = kitchen_sink_document()
        resolved = resolve_assumptions(document, base_assumptions())
        entry = resolved.get(AssumptionKey.HORIZON_PLANNING_AGE)
        assert entry.value == 99
        assert entry.default_value == 95
        assert entry.description == "shipped default for horizon.planning_age"

    def test_untouched_defaults_pass_through_unchanged(self) -> None:
        """Keys without a stored override stay the shipped entries."""
        defaults = base_assumptions()
        resolved = resolve_assumptions(minimal_document(), defaults)
        for key in defaults.keys:
            assert resolved.get(key) == defaults.get(key)

    def test_mapping_override_accepts_any_mapping(self) -> None:
        """A table override lands on a table default (§4.3 rule)."""
        document = kitchen_sink_document()
        resolved = resolve_assumptions(document, base_assumptions())
        entry = resolved.get(AssumptionKey.GLIDEPATH_DEFAULT_SHAPE)
        assert entry.provenance is Provenance.USER_OVERRIDE
        assert entry.value["equity_start"] == Decimal("0.85")

    def test_rejects_override_on_unregistered_key(self) -> None:
        """An override with no shipped default fails loudly."""
        document = kitchen_sink_document()
        defaults = AssumptionSet([base_assumptions().get(AssumptionKey.INFLATION_CPI)])
        with pytest.raises(PersistenceError, match="unregistered keys"):
            resolve_assumptions(document, defaults)

    def test_rejects_override_whose_shape_drifted(self) -> None:
        """A default that changed type since the save fails loudly."""
        document = kitchen_sink_document()
        defaults = base_assumptions()
        drifted = AssumptionSet(
            _with_text_value(entry)
            if entry.key is AssumptionKey.INFLATION_CPI
            else entry
            for entry in (defaults.get(key) for key in defaults.keys)
        )
        with pytest.raises(PersistenceError, match="must hold a str"):
            resolve_assumptions(document, drifted)

    def test_rejects_scalar_where_mapping_expected(self) -> None:
        """A scalar override cannot land on a table default."""
        base = minimal_document()
        document = PlanDocument(
            region=base.region,
            assumptions_resolved_against=base.assumptions_resolved_against,
            household=base.household,
            assumption_overrides=(
                AssumptionOverride(
                    key=AssumptionKey.GLIDEPATH_DEFAULT_SHAPE,
                    value=Decimal("0.8"),
                    source="oops",
                    recorded_on=RECORDED,
                ),
            ),
        )
        defaults = base_assumptions()
        with pytest.raises(PersistenceError, match="must hold a mapping"):
            resolve_assumptions(document, defaults)

    def test_extracting_overrides_inverts_resolution(self) -> None:
        """assumption_overrides_from recovers exactly the stored records."""
        document = kitchen_sink_document()
        resolved = resolve_assumptions(document, base_assumptions())
        extracted = assumption_overrides_from(resolved)
        assert list(extracted) == sorted(
            document.assumption_overrides, key=lambda record: record.key.value
        )

    def test_extracts_nothing_from_pure_defaults(self) -> None:
        """A default-only set stores no overrides (§4.5)."""
        assert assumption_overrides_from(base_assumptions()) == ()


class TestUkIntegration:
    """The document resolves against the real shipped UK defaults."""

    def test_overrides_resolve_against_shipped_uk_defaults(self) -> None:
        """Stored overrides land on the shipped UK assumption set.

        The shipped ``policy.state_pension.uprating`` default is a
        table, so this document overrides it in table form — the §4.3
        shape rule holds for stored overrides too.
        """
        base = kitchen_sink_document()
        document = PlanDocument(
            region=base.region,
            assumptions_resolved_against=base.assumptions_resolved_against,
            household=base.household,
            assumption_overrides=(
                *(
                    record
                    for record in base.assumption_overrides
                    if record.key is not AssumptionKey.POLICY_STATE_PENSION_UPRATING
                ),
                AssumptionOverride(
                    key=AssumptionKey.POLICY_STATE_PENSION_UPRATING,
                    value={"rule": "cpi"},
                    source="pessimism",
                    recorded_on=RECORDED,
                ),
            ),
            scenarios=base.scenarios,
        )
        resolved = resolve_assumptions(document, default_assumption_set())
        assert resolved.get(AssumptionKey.INFLATION_CPI).value == Decimal("0.03")
        assert (
            resolved.get(AssumptionKey.INFLATION_CPI).provenance
            is Provenance.USER_OVERRIDE
        )
        untouched = resolved.get(AssumptionKey.RETURNS_EQUITY_REAL)
        assert untouched.provenance is Provenance.DEFAULT_ASSUMPTION
