"""Dedicated tests for the ``.glidepath.json`` writer (planning §4.5).

``tests/test_persistence.py`` pins the wire format with a kitchen-sink
golden file and drives the encoder through ``dumps_plan``/``save_plan``.
This module exercises ``glidepath.persistence.encode`` directly: the
value-level helper functions and their failure paths, the POSIX
directory-flush step of the atomic save (which real Windows runs never
reach), and an encode→decode round-trip property over *structurally*
varied documents — the existing round-trip property varies scalar
values inside one fixed kitchen-sink shape, while this one varies which
optional parts of the schema exist at all.
"""

import json
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from factories import money_fact
from glidepath.core import (
    AnnuityBasis,
    AnnuityPurchase,
    AnnuityType,
    AssetAllocation,
    AssumptionKey,
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
    dumps_plan,
    loads_plan,
    save_plan,
)
from glidepath.persistence.encode import (
    _bool_value,
    _entity_id,
    _fsync_directory,
    _int_value,
)

if TYPE_CHECKING:
    from pathlib import Path

RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)
RESIDENCY = TaxResidencyId("uk.ruk")
PENSION_KIND = WrapperKindId("uk.sipp")
ISA_KIND = WrapperKindId("uk.isa")

PERSON_ID = EntityId("person-a")
PARTNER_ID = EntityId("person-b")
OUTFLOW_ID = EntityId("outflow-1")

DATA_VERSION = "test-region data v1"

FAKE_DESCRIPTOR = 4242
"""The stand-in directory descriptor the fake ``os.open`` hands out."""


def fact[T](value: T, note: str | None = None) -> Fact[T]:
    """A user-stated fact recorded at a fixed timestamp."""
    return Fact(value=value, as_of=AS_OF, recorded_on=RECORDED, note=note)


def decision[T](value: T, note: str | None = None) -> Decision[T]:
    """A user choice recorded at a fixed timestamp."""
    return Decision(value=value, recorded_on=RECORDED, note=note)


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


class TestValueHelpers:
    """The module-level value encoders, driven directly."""

    def test_int_value_passes_a_plain_whole_number(self) -> None:
        """A genuine int encodes as itself."""
        assert _int_value(65) == 65

    def test_int_value_rejects_a_smuggled_bool(self) -> None:
        """A bool is an int at runtime, but never a persisted number."""
        smuggled = True
        with pytest.raises(PersistenceError, match="not persisted whole numbers"):
            _int_value(smuggled)

    @pytest.mark.parametrize("flag", [True, False])
    def test_bool_value_is_the_identity(self, *, flag: bool) -> None:
        """A boolean decision payload passes through untouched."""
        assert _bool_value(flag) is flag

    def test_entity_id_passes_a_non_empty_id(self) -> None:
        """A non-empty id encodes as its text."""
        assert _entity_id(EntityId("person-a")) == "person-a"

    def test_entity_id_rejects_an_empty_id(self) -> None:
        """An empty id would be unloadable, so it is unwritable too."""
        empty = EntityId("")
        with pytest.raises(PersistenceError, match="entity ids must be non-empty"):
            _entity_id(empty)


class TestFsyncDirectory:
    """The POSIX directory flush of the §4.5 atomic save.

    Real Windows runs never enter the POSIX branch (directories cannot
    be opened for fsync there), so these tests fake the os-level calls
    to cover it on every platform CI runs on.
    """

    def test_posix_flush_opens_fsyncs_and_closes_the_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On POSIX the directory is opened read-only, fsynced, closed."""
        opened: list[tuple[Path, int]] = []
        synced: list[int] = []
        closed: list[int] = []

        def fake_open(path: Path, flags: int) -> int:
            opened.append((path, flags))
            return FAKE_DESCRIPTOR

        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(os, "open", fake_open)
        monkeypatch.setattr(os, "fsync", synced.append)
        monkeypatch.setattr(os, "close", closed.append)
        _fsync_directory(tmp_path)
        assert opened == [(tmp_path, os.O_RDONLY)]
        assert synced == [FAKE_DESCRIPTOR]
        assert closed == [FAKE_DESCRIPTOR]

    def test_non_posix_platforms_skip_the_flush(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Off POSIX no descriptor is ever opened (NTFS journals the rename)."""
        opened: list[Path] = []

        def fake_open(path: Path, flags: int) -> int:
            del flags
            opened.append(path)
            return FAKE_DESCRIPTOR

        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(os, "open", fake_open)
        _fsync_directory(tmp_path)
        assert opened == []

    def test_descriptor_closes_even_when_the_flush_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing fsync still releases the directory descriptor."""
        closed: list[int] = []

        def fake_open(path: Path, flags: int) -> int:
            del path, flags
            return FAKE_DESCRIPTOR

        def failing_fsync(descriptor: int) -> None:
            del descriptor
            msg = "flush failed"
            raise OSError(msg)

        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(os, "open", fake_open)
        monkeypatch.setattr(os, "fsync", failing_fsync)
        monkeypatch.setattr(os, "close", closed.append)
        with pytest.raises(OSError, match="flush failed"):
            _fsync_directory(tmp_path)
        assert closed == [FAKE_DESCRIPTOR]

    def test_save_plan_flushes_the_parent_directory_on_posix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The atomic save reaches the directory flush after the rename.

        The file-handle fsync inside the write is the first recorded
        sync; the directory descriptor's is the last. The written bytes
        stay canonical — the fakes never touch the file itself.
        """
        opened: list[tuple[Path, int]] = []
        synced: list[int] = []
        closed: list[int] = []

        def fake_open(path: Path, flags: int) -> int:
            opened.append((path, flags))
            return FAKE_DESCRIPTOR

        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(os, "open", fake_open)
        monkeypatch.setattr(os, "fsync", synced.append)
        monkeypatch.setattr(os, "close", closed.append)
        target = tmp_path / "plan.glidepath.json"
        document = minimal_document()
        save_plan(document, target)
        assert target.read_bytes() == dumps_plan(document).encode("utf-8")
        assert opened == [(tmp_path, os.O_RDONLY)]
        assert len(synced) == 2
        assert synced[-1] == FAKE_DESCRIPTOR
        assert closed == [FAKE_DESCRIPTOR]


class TestCanonicalShape:
    """Schema-shape guarantees of the writer beyond the golden file."""

    def test_schema_version_is_stamped_in(self) -> None:
        """Every written document carries the current schema version."""
        payload = json.loads(dumps_plan(minimal_document()))
        assert payload["schema_version"] == SCHEMA_VERSION

    def test_absent_optionals_are_explicit_nulls(self) -> None:
        """Optional slots are written as null keys, never omitted.

        The strict reader rejects unknown *and* missing keys, so the
        writer must emit every schema key even for a minimal document.
        """
        payload = json.loads(dumps_plan(minimal_document()))
        household = payload["household"]
        assert household["spending"] is None
        assert household["claim_marriage_allowance"] is None
        assert household["planned_outflows"] == []
        person = household["persons"][0]
        optional_keys = (
            "sex_for_longevity",
            "employment_income",
            "mpaa_triggered_on",
            "lsa_used",
            "death_age",
            "state_pension",
            "glide_path",
        )
        for key in optional_keys:
            assert person[key] is None, key
        assert person["wrappers"] == []
        assert person["db_pensions"] == []
        assert person["annuity_purchases"] == []


# --- strategies over the document's structure -------------------------------

money_amounts = st.decimals(
    min_value=0, max_value=10**6, allow_nan=False, allow_infinity=False, places=2
)

notes = st.none() | st.text(max_size=20)

money_facts = money_amounts.map(money_fact)

birth_dates = st.dates(min_value=date(1940, 1, 1), max_value=date(2000, 12, 31))

GROWTH_ALLOCATION = AssetAllocation(equity=Decimal("0.80"), bonds=Decimal("0.20"))
RETIRED_ALLOCATION = AssetAllocation(
    equity=Decimal("0.30"), bonds=Decimal("0.60"), cash=Decimal("0.10")
)
ALL_CASH_ALLOCATION = AssetAllocation(
    equity=Decimal(0), bonds=Decimal(0), cash=Decimal(1)
)

allocations = st.sampled_from(
    (GROWTH_ALLOCATION, RETIRED_ALLOCATION, ALL_CASH_ALLOCATION)
)

fee_schedules = st.sampled_from(
    (
        FeeSchedule(platform=Rate(Decimal("0.0025")), fund=Rate(Decimal("0.0010"))),
        FeeSchedule(platform=Rate(Decimal(0)), fund=Rate(Decimal("0.0050"))),
    )
)

wrapper_labels = st.none() | st.sampled_from(("Aviva SIPP", "S&S ISA", "old pot"))

stage_multiplier_tables = st.sampled_from(
    (
        None,
        {LifeStage.DECUMULATION: Decimal("1.10")},
        {
            LifeStage.GO_GO: Decimal("1.20"),
            LifeStage.SLOW_GO: Decimal("0.95"),
            LifeStage.NO_GO: Decimal("0.85"),
        },
    )
)

glide_paths = st.sampled_from(
    (
        GlidePathConfig(
            points=(
                GlidePathPoint(years_to_retirement=0, allocation=RETIRED_ALLOCATION),
            )
        ),
        GlidePathConfig(
            points=(
                GlidePathPoint(years_to_retirement=0, allocation=RETIRED_ALLOCATION),
                GlidePathPoint(years_to_retirement=10, allocation=GROWTH_ALLOCATION),
            )
        ),
    )
)

revaluation_bases = st.sampled_from(
    (
        RevaluationBasis(reference=RevaluationReference.CPI),
        RevaluationBasis(reference=RevaluationReference.CPI, cap=Rate(Decimal("0.05"))),
        RevaluationBasis(
            reference=RevaluationReference.FIXED, fixed_rate=Rate(Decimal("0.03"))
        ),
        RevaluationBasis(reference=RevaluationReference.NONE),
    )
)

SCENARIO_OVERRIDES = (
    Override(
        target=DecisionTarget(entity_id=PERSON_ID, field_path="target_retirement_age"),
        value=60,
        note="five years early",
    ),
    Override(
        target=AssumptionTarget(key=AssumptionKey.INFLATION_CPI),
        value=Decimal("0.04"),
    ),
    Override(
        target=AssumptionTarget(key=AssumptionKey.HORIZON_PLANNING_AGE),
        value=97,
    ),
)
"""Distinct-target override candidates a generated scenario draws from."""

ASSUMPTION_OVERRIDE_RECORDS = (
    AssumptionOverride(
        key=AssumptionKey.INFLATION_CPI,
        value=Decimal("0.03"),
        source="my own view",
        recorded_on=RECORDED,
    ),
    AssumptionOverride(
        key=AssumptionKey.HORIZON_PLANNING_AGE,
        value=99,
        source="family history",
        recorded_on=RECORDED,
    ),
)
"""Distinct-key stored-override candidates a generated document draws from."""


@st.composite
def contribution_schedules(draw: st.DrawFn) -> ContributionSchedule:
    """A contribution schedule with each optional field present or absent."""
    return ContributionSchedule(
        employee_amount=decision(Money(draw(money_amounts)), note=draw(notes)),
        employer_amount=draw(st.none() | money_facts),
        relief_mechanic=draw(st.none() | st.sampled_from(list(ReliefMechanic))),
        escalation=draw(st.none() | st.just(AssumptionKey.EARNINGS_GROWTH_REAL)),
    )


@st.composite
def wrapper_records(draw: st.DrawFn, wrapper_id: str, kind: WrapperKindId) -> Wrapper:
    """A wrapper with each optional field present or absent."""
    return Wrapper(
        id=EntityId(wrapper_id),
        kind=kind,
        balance=money_fact(draw(money_amounts)),
        label=draw(wrapper_labels),
        crystallised_balance=draw(st.none() | money_facts),
        contributions=draw(st.none() | contribution_schedules()),
        allocation=draw(st.none() | allocations),
        fees=draw(st.none() | fee_schedules),
    )


@st.composite
def db_pension_records(draw: st.DrawFn, pension_id: str) -> DBPension:
    """A deferred-or-active DB entitlement across every revaluation basis.

    Benefits start at the normal pension age with nothing commuted, so
    no factor table or commutation factor is required and any active
    membership's leaving age can never exceed the taken age.
    """
    normal_pension_age = draw(st.integers(min_value=55, max_value=68))
    membership = None
    if draw(st.booleans()):
        leaving_ages = st.integers(min_value=50, max_value=normal_pension_age)
        until = draw(st.none() | leaving_ages)
        membership = DBActiveMembership(
            accrual_rate=fact(Decimal("0.0125")),
            pensionable_salary=money_fact(draw(money_amounts)),
            active_until_age=None if until is None else decision(until),
        )
    return DBPension(
        id=EntityId(pension_id),
        accrued_annual_pension=money_fact(draw(money_amounts)),
        statement_date=draw(st.dates(min_value=date(2000, 1, 1), max_value=AS_OF)),
        normal_pension_age=fact(normal_pension_age),
        revaluation_basis=draw(revaluation_bases),
        early_late_factors=FactorTable(factors={}),
        commuted_fraction=decision(Decimal(0)),
        survivor_fraction=draw(st.none() | st.just(fact(Decimal("0.5")))),
        active_membership=membership,
    )


@st.composite
def annuity_purchase_records(draw: st.DrawFn, purchase_id: str) -> AnnuityPurchase:
    """An annuity purchase across both bases and every income type."""
    basis = draw(st.sampled_from(list(AnnuityBasis)))
    survivor = None
    if basis is AnnuityBasis.JOINT:
        survivor = decision(
            draw(st.sampled_from((Decimal("0.5"), Decimal("0.66"), Decimal(1))))
        )
    return AnnuityPurchase(
        id=EntityId(purchase_id),
        at_age=decision(draw(st.integers(min_value=55, max_value=80))),
        fraction_of_pot=decision(
            draw(st.sampled_from((Decimal("0.25"), Decimal("0.5"), Decimal(1))))
        ),
        annuity_type=draw(st.sampled_from(list(AnnuityType))),
        basis=basis,
        survivor_fraction=survivor,
    )


@st.composite
def state_pension_records(draw: st.DrawFn) -> StatePensionRecord:
    """A state pension record with and without the optional amounts."""
    forecast = draw(st.none() | money_facts)
    protected = None
    if forecast is not None and draw(st.booleans()):
        protected = money_fact(Decimal(0))
    deferral = draw(st.sampled_from((Decimal(0), Decimal("0.5"), Decimal(2))))
    return StatePensionRecord(
        forecast_weekly_amount=forecast,
        protected_payment=protected,
        deferral_years=decision(deferral),
    )


@st.composite
def spending_plans(draw: st.DrawFn) -> SpendingPlan:
    """A spending plan with and without stage multipliers."""
    return SpendingPlan(
        annual_spending_real=money_fact(draw(money_amounts)),
        stage_multipliers=draw(stage_multiplier_tables),
    )


@st.composite
def person_records(draw: st.DrawFn, person_id: EntityId, suffix: str) -> Person:
    """A person with every optional slot independently present or absent.

    ``suffix`` keeps generated entity ids distinct across the two
    persons a household may hold.
    """
    wrapper_kinds = (PENSION_KIND, ISA_KIND)
    wrappers = tuple(
        draw(wrapper_records(f"wrapper-{suffix}{index}", wrapper_kinds[index]))
        for index in range(draw(st.integers(min_value=0, max_value=2)))
    )
    pension = draw(st.none() | db_pension_records(f"db-{suffix}"))
    purchase = draw(st.none() | annuity_purchase_records(f"annuity-{suffix}"))
    death_age = draw(st.none() | st.integers(min_value=70, max_value=105))
    return Person(
        id=person_id,
        date_of_birth=fact(draw(birth_dates), note=draw(notes)),
        target_retirement_age=decision(
            draw(st.integers(min_value=55, max_value=70)), note=draw(notes)
        ),
        tax_residency=RESIDENCY,
        sex_for_longevity=draw(st.none() | st.sampled_from(list(Sex)).map(fact)),
        employment_income=draw(st.none() | money_facts),
        mpaa_triggered_on=draw(st.none() | st.just(fact(date(2024, 1, 15)))),
        lsa_used=draw(st.none() | money_facts),
        death_age=None if death_age is None else decision(death_age),
        wrappers=wrappers,
        db_pensions=() if pension is None else (pension,),
        annuity_purchases=() if purchase is None else (purchase,),
        state_pension=draw(st.none() | state_pension_records()),
        glide_path=draw(st.none() | glide_paths),
    )


@st.composite
def plan_documents(draw: st.DrawFn) -> PlanDocument:
    """A whole document whose optional structure varies example to example."""
    person = draw(person_records(PERSON_ID, "a"))
    partner = draw(st.none() | person_records(PARTNER_ID, "b"))
    outflows: tuple[PlannedOutflow, ...] = ()
    if draw(st.booleans()):
        outflows = (
            PlannedOutflow(
                id=OUTFLOW_ID,
                label="new roof",
                amount_real=decision(Money(draw(money_amounts)), note=draw(notes)),
                at_age_of=(PERSON_ID, draw(st.integers(min_value=0, max_value=100))),
            ),
        )
    claim = draw(st.none() | st.booleans())
    household = Household(
        persons=(person,) if partner is None else (person, partner),
        spending=draw(st.none() | spending_plans()),
        planned_outflows=outflows,
        claim_marriage_allowance=None if claim is None else decision(claim),
    )
    scenario_names = ("retire early", "do nothing")
    scenarios = tuple(
        Scenario(
            name=scenario_names[index],
            note=draw(notes),
            overrides=tuple(
                override for override in SCENARIO_OVERRIDES if draw(st.booleans())
            ),
        )
        for index in range(draw(st.integers(min_value=0, max_value=2)))
    )
    return PlanDocument(
        region="uk",
        assumptions_resolved_against=DATA_VERSION,
        household=household,
        assumption_overrides=tuple(
            record for record in ASSUMPTION_OVERRIDE_RECORDS if draw(st.booleans())
        ),
        scenarios=scenarios,
    )


class TestEncodeDecodeRoundTrip:
    """decode ∘ encode is the identity over the schema's structure."""

    @given(document=plan_documents())
    @settings(max_examples=50, deadline=None)
    def test_generated_documents_round_trip(self, document: PlanDocument) -> None:
        """Property: whatever optional structure exists survives exactly."""
        encoded = dumps_plan(document)
        decoded = loads_plan(encoded)
        assert decoded == document

    @given(document=plan_documents())
    @settings(max_examples=25, deadline=None)
    def test_generated_documents_reload_byte_stably(
        self, document: PlanDocument
    ) -> None:
        """Property: dumps ∘ loads ∘ dumps is byte-identical to dumps."""
        first = dumps_plan(document)
        second = dumps_plan(loads_plan(first))
        assert second == first
