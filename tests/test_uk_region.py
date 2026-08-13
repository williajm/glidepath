"""Tests for the UK region bundle and first end-to-end runs (issue 4.1).

The full reviewed golden scenario is roadmap 4.5; the end-to-end runs
here are smoke-level: they pin hand-checkable single figures (relief at
20%, a basic-rate gross-up) and structural invariants (net delivered
equals the need while pots last, provenance carries the UK data
version) through the real UK region.
"""

import pickle
from datetime import UTC, date, datetime
from decimal import Decimal

from factories import money_fact
from glidepath.core import (
    AssumptionKey,
    ContributionSchedule,
    Decision,
    EntityId,
    Fact,
    Household,
    Money,
    Person,
    Provenance,
    Rate,
    ReliefMechanic,
    RunConfig,
    SpendingPlan,
    Wrapper,
    run,
)
from glidepath.regions.uk import (
    ISA_KIND,
    RUK_RESIDENCY,
    SIPP_KIND,
    WORKPLACE_DC_KIND,
    FutureYearsMode,
    default_assumption_set,
    future_years_extension,
    uk_region,
)

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)


class TestDefaultAssumptionSet:
    """The shipped defaults as a core assumption set."""

    def test_every_key_ships_with_default_provenance(self) -> None:
        """All catalogue keys are present, defaulted, and sourced."""
        assumptions = default_assumption_set()
        assert assumptions.keys == frozenset(AssumptionKey)
        for key in AssumptionKey:
            entry = assumptions.get(key)
            assert entry.provenance is Provenance.DEFAULT_ASSUMPTION
            assert entry.value == entry.default_value
            assert entry.source

    def test_values_arrive_as_exact_decimals(self) -> None:
        """Numeric defaults are Decimal, never float (planning §4.6)."""
        assumptions = default_assumption_set()
        assert assumptions.get(AssumptionKey.INFLATION_CPI).value == Decimal("0.02")
        assert assumptions.get(AssumptionKey.HORIZON_PLANNING_AGE).value == 95

    def test_the_assumption_set_and_region_are_picklable(self) -> None:
        """A parallel Monte Carlo run ships both to worker processes.

        Guard: a structured default wrapped in an unpicklable view (the
        ``MappingProxyType`` this once was), or a closure smuggled into
        a ruleset, would only surface as a runtime failure inside the
        GUI's process pool (planning §5.2).
        """
        assumptions = default_assumption_set()
        region = uk_region(future_years_extension(assumptions))
        assert pickle.dumps(assumptions)
        assert pickle.dumps(region)


class TestFutureYearsExtension:
    """The extension derived from the shipped policy assumption."""

    def test_extension_reads_policy_and_cpi(self) -> None:
        """Shipped default: frozen to 2030/31, then CPI-indexed at 2%."""
        extension = future_years_extension(default_assumption_set())
        assert extension.policy.mode is FutureYearsMode.FROZEN_THEN_CPI_INDEXED
        assert extension.policy.frozen_until_start_year == 2030
        assert extension.cpi == Rate(Decimal("0.02"))


class TestUkRegionBundle:
    """The bundle and its content-version string."""

    def test_data_version_names_every_shipped_file(self) -> None:
        """The manifest string identifies the data behind a run.

        Each file carries a content digest besides its ``verified_on``
        date: two same-day revisions of a file must still produce
        distinguishable version strings (planning §4.6).
        """
        version = uk_region().data_version
        assert version.startswith("uk schema=5")
        assert "tax_year 2026/27 verified" in version
        assert "age_rules verified" in version
        assert "assumptions verified" in version
        assert version.count("sha256=") == 3
        # The returns-history file must stay out: this string doubles as
        # the saved-plan assumptions fingerprint, and the series prices
        # no assumption (a backtest result carries its series itself).
        assert "returns_history" not in version
        assert "future_years" not in version

    def test_data_version_records_the_full_extension_policy(self) -> None:
        """Mode, freeze end, and CPI all land in the version string.

        Two policies sharing a mode but differing in freeze end or CPI
        synthesize different future thresholds, so all three must be
        distinguishable from provenance.
        """
        extension = future_years_extension(default_assumption_set())
        version = uk_region(extension).data_version
        assert (
            "future_years frozen_then_cpi_indexed until=2030"
            " scot_lower_until=2026 scot_upper_until=2028 cpi=0.02"
        ) in version


def accumulator_household() -> Household:
    """A 35-year-old with a workplace DC and an ISA, retiring at 60."""
    dc_schedule = ContributionSchedule(
        employee_amount=Decision(value=Money(Decimal(5000)), recorded_on=RECORDED),
        employer_amount=money_fact("3000"),
        relief_mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
    )
    dc = Wrapper(
        id=EntityId("uk-dc"),
        kind=WORKPLACE_DC_KIND,
        balance=money_fact("40000"),
        contributions=dc_schedule,
    )
    isa_schedule = ContributionSchedule(
        employee_amount=Decision(value=Money(Decimal(5000)), recorded_on=RECORDED),
    )
    isa = Wrapper(
        id=EntityId("uk-isa"),
        kind=ISA_KIND,
        balance=money_fact("20000"),
        contributions=isa_schedule,
    )
    person = Person(
        id=EntityId("uk-person"),
        date_of_birth=Fact(value=date(1991, 2, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        employment_income=money_fact("50000"),
        wrappers=(dc, isa),
    )
    return Household(persons=(person,))


def retiree_household(spending: str, balances_as_of: date = AS_OF) -> Household:
    """A 68-year-old drawing on a crystallised SIPP and an ISA.

    ``balances_as_of`` dates the balance facts: a run whose ``today``
    is earlier than the module ``AS_OF`` must date its balances on or
    before that ``today`` (§4.8 rejects future-dated balances).
    """
    sipp = Wrapper(
        id=EntityId("uk-sipp"),
        kind=SIPP_KIND,
        balance=money_fact("0", as_of=balances_as_of),
        crystallised_balance=money_fact("150000", as_of=balances_as_of),
    )
    isa = Wrapper(
        id=EntityId("uk-isa"),
        kind=ISA_KIND,
        balance=money_fact("5000", as_of=balances_as_of),
    )
    person = Person(
        id=EntityId("uk-retiree"),
        date_of_birth=Fact(value=date(1958, 5, 10), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        wrappers=(sipp, isa),
    )
    spending_plan = SpendingPlan(annual_spending_real=money_fact(spending))
    return Household(persons=(person,), spending=spending_plan)


class TestUkEndToEnd:
    """First full-pipeline runs through the real UK region."""

    def test_accumulation_run_over_three_tax_years(self) -> None:
        """Contributions, relief, fees, and growth flow through UK rules.

        Today is 2 August 2026, so the first tax year is partial: eight
        whole months remain to 5 April 2027 (roadmap 4.6), scaling the
        50,000 salary to 33,333.33 and the 5,000 contributions to
        3,333.33 (with 20% relief at source, 666.67).
        """
        assumptions = default_assumption_set()
        region = uk_region(future_years_extension(assumptions))
        config = RunConfig(today=TODAY, horizon_end=date(2029, 4, 5))
        result = run(accumulator_household(), assumptions, region, config)

        assert len(result.snapshots) == 3
        for snapshot in result.snapshots:
            [person_result] = snapshot.persons
            dc_result, isa_result = person_result.wrappers
            assert dc_result.closing_balance > dc_result.opening_balance
            assert person_result.tax.tax_due > Money(Decimal(0))
            assert isa_result.contribution_shortfall == Money(Decimal(0))
        first_snapshot = result.snapshots[0]
        assert first_snapshot.year_fraction == Decimal(8) / Decimal(12)
        first = first_snapshot.persons[0]
        assert first.employment_income == Money(Decimal("33333.33"))
        assert first.wrappers[0].provider_relief == Money(Decimal("666.67"))
        assert first.wrappers[1].employee_contribution == Money(Decimal("3333.33"))
        assert result.provenance.region_data_version.startswith("uk schema=5")
        keys_read = {entry.key for entry in result.provenance.assumptions}
        assert AssumptionKey.INFLATION_CPI in keys_read
        assert AssumptionKey.GLIDEPATH_DEFAULT_SHAPE in keys_read

    def test_decumulation_grosses_up_against_uk_tax(self) -> None:
        """A net need is met exactly, grossed up through the UK ladder.

        Need 40,000 net: the ISA's 5,000 comes out tax-free, and the
        remaining 35,000 net comes from crystallised SIPP funds. With
        the 12,570 personal allowance and the 20% basic rate, the
        fixed point lands at 40,607.49 gross and 5,607.49 tax. The run
        covers exactly one whole tax year so the hand-worked figures
        are not pro-rated (partial periods are pinned elsewhere).
        """
        assumptions = default_assumption_set()
        region = uk_region(future_years_extension(assumptions))
        config = RunConfig(today=date(2026, 4, 6), horizon_end=date(2027, 4, 5))
        household = retiree_household("40000", balances_as_of=date(2026, 4, 6))
        result = run(household, assumptions, region, config)

        [snapshot] = result.snapshots
        [person_result] = snapshot.persons
        sipp_result, isa_result = person_result.wrappers
        assert person_result.spending_need == Money(Decimal("40000.00"))
        assert person_result.net_withdrawn == Money(Decimal("40000.00"))
        assert person_result.shortfall == Money(Decimal(0))
        assert isa_result.withdrawal_tax_free == Money(Decimal("5000.00"))
        assert sipp_result.withdrawal_taxable == Money(Decimal("40607.49"))
        assert person_result.tax.tax_due == Money(Decimal("5607.49"))

    def test_decumulation_meets_need_across_periods(self) -> None:
        """While pots last, delivery matches the CPI-inflated need."""
        assumptions = default_assumption_set()
        region = uk_region(future_years_extension(assumptions))
        config = RunConfig(today=TODAY, horizon_end=date(2028, 6, 1))
        result = run(retiree_household("20000"), assumptions, region, config)

        assert len(result.snapshots) == 3
        for snapshot in result.snapshots:
            [person_result] = snapshot.persons
            assert person_result.shortfall == Money(Decimal(0))
            assert person_result.net_withdrawn == person_result.spending_need


def couple_household(
    earner_income: str,
    *,
    earner_first: bool = True,
    claim: bool | None = None,
) -> Household:
    """One earner and one no-income partner (roadmap 9.32).

    ``earner_first`` flips the person order so the automatic direction
    pick is exercised both ways; ``claim`` records an explicit claim
    decision (``None`` keeps the default — claim when eligible).
    """
    earner = Person(
        id=EntityId("uk-earner"),
        date_of_birth=Fact(value=date(1991, 2, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        employment_income=money_fact(earner_income),
    )
    partner = Person(
        id=EntityId("uk-partner"),
        date_of_birth=Fact(value=date(1992, 3, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
    )
    persons = (earner, partner) if earner_first else (partner, earner)
    decision = None if claim is None else Decision(value=claim, recorded_on=RECORDED)
    return Household(persons=persons, claim_marriage_allowance=decision)


class TestMarriageAllowanceEndToEnd:
    """The 9.32 acceptance criteria through the real UK region.

    One whole tax year (2026/27): a £50,000 earner owes 7,486.00 at
    the basic rate, and the eligible couple's default claim takes the
    s55B reducer off the final assessment — £252 less, at no change to
    any cash flow (the §4.11 recorded simplification).
    """

    def run_couple(self, household: Household) -> tuple[Money, Money, list[str]]:
        """One-year run: earner tax, partner tax, earner line labels."""
        assumptions = default_assumption_set()
        region = uk_region(future_years_extension(assumptions))
        config = RunConfig(today=date(2026, 4, 6), horizon_end=date(2027, 4, 5))
        result = run(household, assumptions, region, config)
        [snapshot] = result.snapshots
        by_id = {person.person_id: person for person in snapshot.persons}
        earner = by_id[EntityId("uk-earner")]
        partner = by_id[EntityId("uk-partner")]
        return (
            earner.tax.tax_due,
            partner.tax.tax_due,
            [line.band for line in earner.tax.lines],
        )

    def test_eligible_couple_pays_252_less_at_basic_rate(self) -> None:
        """The default claim lands the reducer on the earner."""
        earner_tax, partner_tax, bands = self.run_couple(couple_household("50000"))
        assert earner_tax == Money(Decimal("7234.00"))
        assert partner_tax == Money(Decimal(0))
        assert bands == ["basic", "marriage_allowance"]

    def test_direction_follows_the_earner_not_the_order(self) -> None:
        """With the partner listed first the reducer still lands right."""
        earner_tax, _partner_tax, bands = self.run_couple(
            couple_household("50000", earner_first=False)
        )
        assert earner_tax == Money(Decimal("7234.00"))
        assert bands == ["basic", "marriage_allowance"]

    def test_declined_claim_leaves_the_assessment_alone(self) -> None:
        """An explicit No never calls the household adjustment."""
        earner_tax, _partner_tax, bands = self.run_couple(
            couple_household("50000", claim=False)
        )
        assert earner_tax == Money(Decimal("7486.00"))
        assert bands == ["basic"]

    def test_higher_rate_earner_is_ineligible(self) -> None:
        """60,000 reaches the 40% band: no reducer, tax unchanged."""
        earner_tax, _partner_tax, bands = self.run_couple(couple_household("60000"))
        assert earner_tax == Money(Decimal("11432.00"))
        assert bands == ["basic", "higher"]

    def test_single_person_runs_never_see_the_adjustment(self) -> None:
        """A one-person plan's assessment carries no reducer line."""
        assumptions = default_assumption_set()
        region = uk_region(future_years_extension(assumptions))
        config = RunConfig(today=TODAY, horizon_end=date(2027, 4, 5))
        result = run(accumulator_household(), assumptions, region, config)
        [snapshot] = result.snapshots
        [person_result] = snapshot.persons
        bands = [line.band for line in person_result.tax.lines]
        assert "marriage_allowance" not in bands
