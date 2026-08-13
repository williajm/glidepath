"""Property-based tests over the engine (issues #132, #201; planning §5.2).

Hypothesis generates modest households — one person, a few wrappers of
distinct kinds, optional spending, contributions, and a planned
outflow — and projects them through the real UK region over short
horizons. The invariants are the structural identities the golden
scenarios pin for two hand-reviewed plans, asserted here over the
generated space:

- no closing balance goes negative;
- the wrapper ledger identity reconciles every period;
- every retired period's need is met, banked, or reported as
  shortfall — nothing is silently dropped;
- the per-band tax lines sum to the assessed tax;
- reordering a person's wrapper listing changes nothing;
- the chunked parallel Monte Carlo runner reproduces the serial run.

``modest_households`` deliberately keeps kinds distinct and persons
single (its docstring); issue #201 widens alongside it rather than in
place: ``couple_households`` generates two-person households — pooled
withdrawals, repeated wrapper kinds, mixed residencies, and optional
DB pensions, state pension records, and annuity purchases — and the
same invariants are asserted over that space at household level.

Generation stays modest (balances to a million, horizons to five
years) to keep each engine run cheap; the golden tests remain the
anchors for exact hand-computed figures.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from factories import money_fact
from glidepath.core import (
    JOINT_SURVIVOR_FRACTIONS,
    AnnuityBasis,
    AnnuityPurchase,
    AnnuityType,
    ContributionSchedule,
    DBPension,
    Decision,
    EntityId,
    Fact,
    FactorTable,
    Household,
    Money,
    PathParallelism,
    Person,
    PersonPeriodResult,
    PlannedOutflow,
    ProjectionResult,
    Rate,
    ReliefMechanic,
    RevaluationBasis,
    RevaluationReference,
    RunConfig,
    RunMode,
    SpendingPlan,
    StatePensionRecord,
    Wrapper,
    WrapperKindId,
    run,
    run_paths,
)
from glidepath.regions.uk import (
    CASH_KIND,
    GIA_KIND,
    ISA_KIND,
    MARRIAGE_ALLOWANCE_BAND,
    RUK_RESIDENCY,
    SCOTLAND_RESIDENCY,
    SIPP_KIND,
    default_assumption_set,
    future_years_extension,
    uk_region,
)

pytestmark = pytest.mark.slow

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)

ZERO = Money(Decimal(0))

ASSUMPTIONS = default_assumption_set()
REGION = uk_region(future_years_extension(ASSUMPTIONS))

KINDS = (ISA_KIND, SIPP_KIND, GIA_KIND, CASH_KIND)

IDENTITY_TOLERANCE = Decimal("0.10")
"""Every snapshot field is independently quantized at period close
(planning §4.6), so an identity over a dozen fields can drift by up to
half a penny per field — never more. Real defects show up in pounds."""

balances = st.decimals(
    min_value=0, max_value=1_000_000, places=2, allow_nan=False, allow_infinity=False
)
spending_amounts = st.decimals(
    min_value=0, max_value=80_000, places=2, allow_nan=False, allow_infinity=False
)
contribution_amounts = st.decimals(
    min_value=0, max_value=15_000, places=2, allow_nan=False, allow_infinity=False
)
outflow_amounts = st.decimals(
    min_value=0, max_value=30_000, places=2, allow_nan=False, allow_infinity=False
)
income_amounts = st.decimals(
    min_value=0, max_value=120_000, places=2, allow_nan=False, allow_infinity=False
)
horizon_years = st.integers(min_value=1, max_value=5)
db_pension_amounts = st.decimals(
    min_value=0, max_value=20_000, places=2, allow_nan=False, allow_infinity=False
)
weekly_amounts = st.decimals(
    min_value=0, max_value=250, places=2, allow_nan=False, allow_infinity=False
)

RESIDENCIES = (RUK_RESIDENCY, SCOTLAND_RESIDENCY)
REVALUATION_BASES = (
    RevaluationBasis(reference=RevaluationReference.NONE),
    RevaluationBasis(reference=RevaluationReference.CPI),
    RevaluationBasis(reference=RevaluationReference.CPI, cap=Rate(Decimal("0.025"))),
    RevaluationBasis(
        reference=RevaluationReference.FIXED, fixed_rate=Rate(Decimal("0.03"))
    ),
)
COMMUTED_FRACTIONS = (Decimal(0), Decimal("0.25"))
POT_FRACTIONS = (Decimal("0.25"), Decimal("0.5"), Decimal(1))

MAX_STATE_PENSION_AGE = 71
"""Oldest generated age that may carry a state pension record: the
shipped SPA timetable covers births from 1954-10-06, so a Jan-1
birthday must fall in 1955 or later (UkAgeError below that)."""

MAX_ANNUITY_PURCHASE_AGE = 75
"""The shipped annuity rate table's last knot: a purchase at an age
outside 55-75 is a modelling error, never an extrapolation (§7)."""


def wrapper_of(
    wrapper_id: str, kind: WrapperKindId, balance: Decimal, contribution: Decimal | None
) -> Wrapper:
    """One generated wrapper; pension contributions use relief at source."""
    contributions = None
    if contribution is not None:
        contributions = ContributionSchedule(
            employee_amount=Decision(value=Money(contribution), recorded_on=RECORDED),
            relief_mechanic=(
                ReliefMechanic.RELIEF_AT_SOURCE if kind == SIPP_KIND else None
            ),
        )
    return Wrapper(
        id=EntityId(wrapper_id),
        kind=kind,
        balance=money_fact(balance),
        contributions=contributions,
    )


@st.composite
def modest_households(
    draw: st.DrawFn,
    *,
    kinds_pool: tuple[WrapperKindId, ...] = KINDS,
    min_wrappers: int = 1,
) -> Household:
    """A one-person household of a few distinct-kind wrappers.

    Retired and accumulating people are both generated: retirement
    ages span 55-68, ages 25-88. Accumulating people may earn and
    contribute; anyone may hold a spending plan and one planned
    outflow. Kinds are drawn without repetition so per-kind caps and
    the withdrawal order stay single-membered — the golden scenarios
    cover multi-wrapper cap splitting.
    """
    retirement_age = draw(st.integers(min_value=55, max_value=68))
    retired = draw(st.booleans())
    if retired:
        age = draw(st.integers(min_value=retirement_age, max_value=88))
    else:
        age = draw(st.integers(min_value=25, max_value=retirement_age - 1))
    kinds = draw(
        st.lists(
            st.sampled_from(kinds_pool),
            min_size=min_wrappers,
            max_size=3,
            unique=True,
        )
    )
    wrappers = []
    for index, kind in enumerate(kinds):
        contribution = None
        if not retired and kind in (ISA_KIND, SIPP_KIND):
            contribution = draw(st.one_of(st.none(), contribution_amounts))
        wrappers.append(
            wrapper_of(f"wrapper-{index}", kind, draw(balances), contribution)
        )
    employment = None
    if not retired:
        employment = draw(st.one_of(st.none(), income_amounts))
    person = Person(
        id=EntityId("person-1"),
        date_of_birth=Fact(
            value=date(TODAY.year - age, 1, 1), as_of=AS_OF, recorded_on=RECORDED
        ),
        target_retirement_age=Decision(value=retirement_age, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        employment_income=None if employment is None else money_fact(employment),
        wrappers=tuple(wrappers),
    )
    spending = draw(st.one_of(st.none(), spending_amounts))
    plan = None
    if spending is not None:
        plan = SpendingPlan(annual_spending_real=money_fact(spending))
    outflow = draw(st.one_of(st.none(), outflow_amounts))
    outflows: tuple[PlannedOutflow, ...] = ()
    if outflow is not None:
        outflows = (
            PlannedOutflow(
                id=EntityId("outflow-1"),
                label="One-off",
                amount_real=Decision(value=Money(outflow), recorded_on=RECORDED),
                at_age_of=(person.id, age + 1),
            ),
        )
    return Household(persons=(person,), spending=plan, planned_outflows=outflows)


def _draw_db_pension(draw: st.DrawFn, tag: str) -> DBPension:
    """One deferred DB entitlement taken at its normal pension age.

    The taken-at default needs no early/late factor (scheme facts stay
    minimal); commutation, when drawn, trades a quarter of the pension
    at a factor of 12 — the common scheme shape.
    """
    commuted = draw(st.sampled_from(COMMUTED_FRACTIONS))
    factor = None
    if commuted > Decimal(0):
        factor = Fact(value=Decimal(12), as_of=AS_OF, recorded_on=RECORDED)
    return DBPension(
        id=EntityId(f"{tag}-db"),
        accrued_annual_pension=money_fact(draw(db_pension_amounts)),
        statement_date=AS_OF,
        normal_pension_age=Fact(
            value=draw(st.integers(min_value=60, max_value=68)),
            as_of=AS_OF,
            recorded_on=RECORDED,
        ),
        revaluation_basis=draw(st.sampled_from(REVALUATION_BASES)),
        early_late_factors=FactorTable(factors={}),
        commuted_fraction=Decision(value=commuted, recorded_on=RECORDED),
        commutation_factor=factor,
    )


def _draw_state_pension(draw: st.DrawFn) -> StatePensionRecord:
    """One state pension record: a DWP forecast, no deferral.

    Any drawn protected payment is capped at the forecast (the schema
    invariant: it is a slice of the forecast).
    """
    forecast = draw(weekly_amounts)
    protected = draw(st.one_of(st.none(), weekly_amounts))
    return StatePensionRecord(
        forecast_weekly_amount=money_fact(forecast),
        protected_payment=(
            None if protected is None else money_fact(min(protected, forecast))
        ),
        deferral_years=Decision(value=Decimal(0), recorded_on=RECORDED),
    )


def _draw_annuity_purchase(
    draw: st.DrawFn, tag: str, *, youngest_age: int
) -> AnnuityPurchase:
    """One planned annuity purchase of any shipped type and basis.

    ``youngest_age`` keeps the purchase in the person's future — a
    purchase already attained before the run's today cannot be priced
    from the modelled pot (roadmap 5.5). The 58 floor keeps every
    firing purchase past the pension access gate by period start, and
    75 is the shipped rate table's last knot.
    """
    survivor = None
    basis = AnnuityBasis.SINGLE
    if draw(st.booleans()):
        basis = AnnuityBasis.JOINT
        survivor = Decision(
            value=draw(st.sampled_from(JOINT_SURVIVOR_FRACTIONS)), recorded_on=RECORDED
        )
    return AnnuityPurchase(
        id=EntityId(f"{tag}-annuity"),
        at_age=Decision(
            value=draw(
                st.integers(
                    min_value=max(58, youngest_age),
                    max_value=MAX_ANNUITY_PURCHASE_AGE,
                )
            ),
            recorded_on=RECORDED,
        ),
        fraction_of_pot=Decision(
            value=draw(st.sampled_from(POT_FRACTIONS)), recorded_on=RECORDED
        ),
        annuity_type=draw(st.sampled_from(AnnuityType)),
        basis=basis,
        survivor_fraction=survivor,
    )


def _draw_couple_person(draw: st.DrawFn, tag: str) -> Person:
    """One person for a couple household (issue #201).

    Unlike ``modest_households``, wrapper kinds repeat — so per-kind
    caps and §5.2 treatment groups become multi-membered — residency
    may be Scottish, and DB pensions, a state pension record, and an
    annuity purchase are each optionally present.
    """
    retirement_age = draw(st.integers(min_value=55, max_value=68))
    retired = draw(st.booleans())
    if retired:
        age = draw(st.integers(min_value=retirement_age, max_value=88))
    else:
        age = draw(st.integers(min_value=25, max_value=retirement_age - 1))
    kinds = draw(st.lists(st.sampled_from(KINDS), min_size=1, max_size=3))
    wrappers = []
    for index, kind in enumerate(kinds):
        contribution = None
        if not retired and kind in (ISA_KIND, SIPP_KIND):
            contribution = draw(st.one_of(st.none(), contribution_amounts))
        wrappers.append(
            wrapper_of(f"{tag}-wrapper-{index}", kind, draw(balances), contribution)
        )
    employment = None
    if not retired:
        employment = draw(st.one_of(st.none(), income_amounts))
    db_pensions: tuple[DBPension, ...] = ()
    if draw(st.booleans()):
        db_pensions = (_draw_db_pension(draw, tag),)
    state_pension = None
    if age <= MAX_STATE_PENSION_AGE and draw(st.booleans()):
        state_pension = _draw_state_pension(draw)
    annuity_purchases: tuple[AnnuityPurchase, ...] = ()
    if age < MAX_ANNUITY_PURCHASE_AGE and draw(st.booleans()):
        annuity_purchases = (_draw_annuity_purchase(draw, tag, youngest_age=age + 1),)
    return Person(
        id=EntityId(tag),
        date_of_birth=Fact(
            value=date(TODAY.year - age, 1, 1), as_of=AS_OF, recorded_on=RECORDED
        ),
        target_retirement_age=Decision(value=retirement_age, recorded_on=RECORDED),
        tax_residency=draw(st.sampled_from(RESIDENCIES)),
        employment_income=None if employment is None else money_fact(employment),
        wrappers=tuple(wrappers),
        db_pensions=db_pensions,
        annuity_purchases=annuity_purchases,
        state_pension=state_pension,
    )


@st.composite
def couple_households(draw: st.DrawFn, *, bankable: bool = False) -> Household:
    """A two-person household over the pooled withdrawal step (§4.11).

    The issue #201 widening alongside ``modest_households``: two
    persons with repeated wrapper kinds, mixed residencies, optional
    DB pensions, state pension records, and annuity purchases; the
    marriage allowance is occasionally declined. ``bankable`` appends
    a bare taxable wrapper to the first person so decumulation surplus
    is always swept, never spent (roadmap 9.2) — the cash-conservation
    identity needs every pound to land somewhere visible.
    """
    persons = [_draw_couple_person(draw, f"person-{number}") for number in (1, 2)]
    if bankable:
        bank = wrapper_of(
            "person-1-bank",
            draw(st.sampled_from((GIA_KIND, CASH_KIND))),
            draw(balances),
            None,
        )
        persons[0] = replace(persons[0], wrappers=(*persons[0].wrappers, bank))
    spending = draw(st.one_of(st.none(), spending_amounts))
    plan = None
    if spending is not None:
        plan = SpendingPlan(annual_spending_real=money_fact(spending))
    outflow = draw(st.one_of(st.none(), outflow_amounts))
    outflows: tuple[PlannedOutflow, ...] = ()
    if outflow is not None:
        target = persons[draw(st.integers(min_value=0, max_value=1))]
        target_age = TODAY.year - target.date_of_birth.value.year
        outflows = (
            PlannedOutflow(
                id=EntityId("outflow-1"),
                label="One-off",
                amount_real=Decision(value=Money(outflow), recorded_on=RECORDED),
                at_age_of=(target.id, target_age + 1),
            ),
        )
    claim = None
    if draw(st.booleans()):
        claim = Decision(value=False, recorded_on=RECORDED)
    return Household(
        persons=tuple(persons),
        spending=plan,
        planned_outflows=outflows,
        claim_marriage_allowance=claim,
    )


def taxed_drawdown_household() -> Household:
    """A retiree whose SIPP draws bear marginal tax (#147).

    Spending far beyond the pot's tax-free element forces taxable
    draws above the personal allowance — a regime the generated space
    reaches only rarely, pinned here so every run covers it.
    """
    person = Person(
        id=EntityId("person-1"),
        date_of_birth=Fact(value=date(1958, 1, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        wrappers=(wrapper_of("wrapper-0", SIPP_KIND, Decimal(500_000), None),),
    )
    plan = SpendingPlan(annual_spending_real=money_fact(Decimal(40_000)))
    return Household(persons=(person,), spending=plan)


def penny_taxed_outflow_household() -> Household:
    """Issue #147's minimal counterexample, pinned verbatim.

    A planned outflow a few pence below the whole SIPP balance drains
    the pot: the draw's taxable share clears the personal allowance by
    pennies (11p of tax) and the net delivery lands a penny short
    (shortfall) — the smallest taxed-draw case Hypothesis found.
    """
    person = Person(
        id=EntityId("person-1"),
        date_of_birth=Fact(value=date(1971, 1, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=55, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        wrappers=(
            wrapper_of("wrapper-0", ISA_KIND, Decimal(0), None),
            wrapper_of("wrapper-1", SIPP_KIND, Decimal("16760.74"), None),
        ),
    )
    outflow = PlannedOutflow(
        id=EntityId("outflow-1"),
        label="One-off",
        amount_real=Decision(value=Money(Decimal("16760.64")), recorded_on=RECORDED),
        at_age_of=(person.id, 56),
    )
    return Household(persons=(person,), planned_outflows=(outflow,))


def run_projection(household: Household, years: int) -> ProjectionResult:
    """Project ``household`` deterministically over ``years`` years."""
    config = RunConfig(today=TODAY, horizon_end=date(TODAY.year + years, 8, 1))
    return run(household, ASSUMPTIONS, REGION, config)


def assert_no_negative_closing(result: ProjectionResult) -> None:
    """Every wrapper of every person closes every period at/above zero."""
    assert result.snapshots
    for snapshot in result.snapshots:
        for person in snapshot.persons:
            for entry in person.wrappers:
                assert entry.closing_uncrystallised >= ZERO
                assert entry.closing_crystallised >= ZERO


def assert_ledger_identity(result: ProjectionResult) -> None:
    """Closing = opening + inflows - outflows - fee + growth.

    The golden scenarios' ledger identity, over generated plans: every
    flow the wrapper reports must account for the balance movement, to
    within accumulated per-field rounding.
    """
    for snapshot in result.snapshots:
        for person in snapshot.persons:
            for entry in person.wrappers:
                reconstructed = (
                    entry.opening_balance
                    + entry.employee_contribution
                    + entry.employer_contribution
                    + entry.contribution_bonus
                    + entry.banked_in
                    - entry.withdrawal_tax_free
                    - entry.withdrawal_taxable
                    - entry.annuity_purchase
                    - entry.growth_tax
                    - entry.aa_charge
                    - entry.fee
                    + entry.growth
                )
                gap = (entry.closing_balance - reconstructed).amount.copy_abs()
                assert gap <= IDENTITY_TOLERANCE, (
                    f"{entry.wrapper_id} ledger identity off by {gap}"
                    f" in {snapshot.period.start}"
                )


def _delivered_toward_need(person: PersonPeriodResult) -> Money:
    """One person's gross cash toward the household need (see caller).

    Gross draws (the up-front and annuity-purchase lump sums come off,
    already counted in the income term) plus gross income, net of the
    personal tax assessment — excluding the growth tax and the funded
    annual-allowance charge, which the wrappers fund directly — minus
    the banked surplus, plus the reported shortfall.
    """
    wrapper_funded_tax = sum(
        (entry.growth_tax + entry.aa_charge for entry in person.wrappers),
        start=ZERO,
    )
    gross_withdrawn = (
        sum(
            (
                entry.withdrawal_tax_free + entry.withdrawal_taxable
                for entry in person.wrappers
            ),
            start=ZERO,
        )
        - person.pension_lump_sum
        - person.annuity_lump_sum
    )
    income = (
        person.db_income
        + person.state_pension_income
        + person.annuity_income
        + person.db_lump_sum
        + person.annuity_lump_sum
        + person.pension_lump_sum
    )
    return (
        gross_withdrawn
        + income
        - (person.tax.tax_due - wrapper_funded_tax)
        - person.banked
        + person.shortfall
    )


def assert_retirement_cash_conservation(result: ProjectionResult) -> None:
    """Gross draws + income - tax - banked + shortfall = need + outflows.

    The golden scenarios' need identity, over generated plans: every
    fully retired period's need is met by the gross cash the pooled
    withdrawal step pays out of wrappers plus gross income, net of the
    personal tax assessments, with any surplus banked and any deficit
    reported as shortfall. The gross form matters (#147):
    ``net_withdrawn`` is already net of the marginal tax the gross-up
    prices on draws and ``tax_due`` assesses that same tax, so a
    net-figure identity would count it twice. Summed at household
    level: the household need, delivery, and shortfall each land on a
    defined owner, so the persons' results sum to the household truth
    (§4.11). Periods where anyone still accumulates are skipped —
    working-life spending is outside the model and income in payment
    banks — as are periods where the marriage allowance fired: the
    s55B adjustment is reporting-level, its reduction (and the
    transferor's re-assessment) never feeds the period's cash flows
    (the recorded §4.11 simplification).
    """
    for snapshot in result.snapshots:
        persons = snapshot.persons
        if any(person.years_to_retirement > 0 for person in persons):
            continue
        adjusted = any(
            line.band == MARRIAGE_ALLOWANCE_BAND
            for person in persons
            for line in person.tax.lines
        )
        if adjusted:
            continue
        delivered = sum(
            (_delivered_toward_need(person) for person in persons), start=ZERO
        )
        need = sum(
            (person.spending_need + person.planned_outflows for person in persons),
            start=ZERO,
        )
        gap = (delivered - need).amount.copy_abs()
        assert gap <= IDENTITY_TOLERANCE * len(persons), (
            f"unaccounted retirement cash in {snapshot.period.start}"
        )


def assert_tax_lines_sum(result: ProjectionResult) -> None:
    """Every person's per-band tax lines sum to their tax due."""
    for snapshot in result.snapshots:
        for person in snapshot.persons:
            lines_total = sum((line.tax for line in person.tax.lines), start=ZERO)
            assert person.tax.tax_due == lines_total


class TestEngineInvariants:
    """Structural identities over generated plans (issue #132)."""

    @given(household=modest_households(), years=horizon_years)
    @settings(max_examples=100, deadline=None)
    def test_no_closing_balance_goes_negative(
        self, household: Household, years: int
    ) -> None:
        """Every wrapper closes every period at or above zero."""
        assert_no_negative_closing(run_projection(household, years))

    @given(household=modest_households(), years=horizon_years)
    @settings(max_examples=100, deadline=None)
    def test_wrapper_ledger_identity_every_period(
        self, household: Household, years: int
    ) -> None:
        """Every wrapper's reported flows account for its movement."""
        assert_ledger_identity(run_projection(household, years))

    @given(household=modest_households(), years=horizon_years)
    @settings(max_examples=100, deadline=None)
    @example(household=taxed_drawdown_household(), years=2)
    @example(household=penny_taxed_outflow_household(), years=1)
    def test_retirement_cash_conservation_every_period(
        self, household: Household, years: int
    ) -> None:
        """Every retired period's need is delivered, banked, or reported."""
        assert_retirement_cash_conservation(run_projection(household, years))

    @given(household=modest_households(), years=horizon_years)
    @settings(max_examples=100, deadline=None)
    def test_tax_lines_sum_to_tax_due(self, household: Household, years: int) -> None:
        """Every period's per-band tax lines sum to the tax due."""
        assert_tax_lines_sum(run_projection(household, years))


class TestCoupleHouseholdInvariants:
    """The same identities over pooled two-person households (#201).

    Repeated wrapper kinds make the per-kind caps and §5.2 treatment
    groups multi-membered, so the pooled execution's greedy
    marginal-cost draw engages; DB pensions, state pensions, and
    annuity purchases feed the income offset; mixed residencies and
    the marriage allowance exercise the household tax step.
    """

    @given(household=couple_households(), years=horizon_years)
    @settings(max_examples=75, deadline=None)
    def test_no_closing_balance_goes_negative(
        self, household: Household, years: int
    ) -> None:
        """Every wrapper of either person closes at or above zero."""
        assert_no_negative_closing(run_projection(household, years))

    @given(household=couple_households(), years=horizon_years)
    @settings(max_examples=75, deadline=None)
    def test_wrapper_ledger_identity_every_period(
        self, household: Household, years: int
    ) -> None:
        """Every wrapper's reported flows account for its movement."""
        assert_ledger_identity(run_projection(household, years))

    @given(household=couple_households(bankable=True), years=horizon_years)
    @settings(max_examples=75, deadline=None)
    def test_retirement_cash_conservation_every_period(
        self, household: Household, years: int
    ) -> None:
        """Every fully retired period's household need is accounted for.

        ``bankable`` guarantees a bare taxable wrapper, so income
        beyond the need (a strong DB or state pension) is always swept
        into a visible ``banked`` figure rather than spent (roadmap
        9.2) — the identity is exact, not one-sided.
        """
        assert_retirement_cash_conservation(run_projection(household, years))

    @given(household=couple_households(), years=horizon_years)
    @settings(max_examples=75, deadline=None)
    def test_tax_lines_sum_to_tax_due(self, household: Household, years: int) -> None:
        """Both persons' per-band lines sum to their tax due.

        Marriage-allowance periods included: the s55B reducer is a
        negative line, so the identity must survive it on the
        recipient and the re-assessment on the transferor.
        """
        assert_tax_lines_sum(run_projection(household, years))


class TestOrderAndParallelismInvariance:
    """The projection is a function of the plan, not its encoding."""

    @given(
        household=modest_households(
            kinds_pool=(ISA_KIND, SIPP_KIND, GIA_KIND), min_wrappers=2
        ),
        years=horizon_years,
    )
    @settings(max_examples=50, deadline=None)
    def test_wrapper_listing_order_changes_nothing(
        self, household: Household, years: int
    ) -> None:
        """Reversing the wrapper listing reproduces the projection.

        The kinds pool keeps one wrapper per tax treatment, so the
        §5.2 withdrawal order, the per-kind caps, and the banked-in
        sweep target are all listing-independent — the results must
        agree wrapper for wrapper.
        """
        [person] = household.persons
        mirrored_person = replace(person, wrappers=tuple(reversed(person.wrappers)))
        mirrored_household = replace(household, persons=(mirrored_person,))
        baseline = run_projection(household, years)
        mirrored = run_projection(mirrored_household, years)
        paired = zip(baseline.snapshots, mirrored.snapshots, strict=True)
        for base_snapshot, mirrored_snapshot in paired:
            [base_person] = base_snapshot.persons
            [mirror_person] = mirrored_snapshot.persons
            base_wrappers = sorted(base_person.wrappers, key=lambda w: w.wrapper_id)
            mirror_wrappers = sorted(mirror_person.wrappers, key=lambda w: w.wrapper_id)
            assert mirror_wrappers == base_wrappers
            mirror_rest = replace(mirror_person, wrappers=())
            base_rest = replace(base_person, wrappers=())
            assert mirror_rest == base_rest

    @given(
        household=modest_households(),
        seed=st.integers(min_value=0, max_value=2**32),
        paths=st.integers(min_value=2, max_value=3),
    )
    @settings(max_examples=25, deadline=None)
    def test_parallel_paths_reproduce_the_serial_run(
        self, household: Household, seed: int, paths: int
    ) -> None:
        """Chunked parallel Monte Carlo equals the serial run exactly."""
        config = RunConfig(
            today=TODAY,
            horizon_end=date(TODAY.year + 2, 8, 1),
            mode=RunMode.MONTE_CARLO,
            seed=seed,
        )
        serial = run_paths(household, ASSUMPTIONS, REGION, config, paths=paths)
        with ThreadPoolExecutor(max_workers=2) as executor:
            parallelism = PathParallelism(executor=executor, workers=2)
            parallel = run_paths(
                household,
                ASSUMPTIONS,
                REGION,
                config,
                paths=paths,
                parallelism=parallelism,
            )
        assert parallel == serial
