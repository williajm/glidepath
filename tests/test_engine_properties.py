"""Property-based tests over the engine (issue #132; planning §5.2).

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

from glidepath.core import (
    ContributionSchedule,
    Decision,
    EntityId,
    Fact,
    Household,
    Money,
    PathParallelism,
    Person,
    PlannedOutflow,
    ProjectionResult,
    ReliefMechanic,
    RunConfig,
    RunMode,
    SpendingPlan,
    Wrapper,
    WrapperKindId,
    run,
    run_paths,
)
from glidepath.regions.uk import (
    CASH_KIND,
    GIA_KIND,
    ISA_KIND,
    RUK_RESIDENCY,
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


def money_fact(amount: Decimal) -> Fact[Money]:
    """A user-stated monetary fact dated just before the run start."""
    return Fact(value=Money(amount), as_of=AS_OF, recorded_on=RECORDED)


def wrapper_of(
    index: int, kind: WrapperKindId, balance: Decimal, contribution: Decimal | None
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
        id=EntityId(f"wrapper-{index}"),
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
        wrappers.append(wrapper_of(index, kind, draw(balances), contribution))
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
        wrappers=(wrapper_of(0, SIPP_KIND, Decimal(500_000), None),),
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
            wrapper_of(0, ISA_KIND, Decimal(0), None),
            wrapper_of(1, SIPP_KIND, Decimal("16760.74"), None),
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


class TestEngineInvariants:
    """Structural identities over generated plans (issue #132)."""

    @given(household=modest_households(), years=horizon_years)
    @settings(max_examples=100, deadline=None)
    def test_no_closing_balance_goes_negative(
        self, household: Household, years: int
    ) -> None:
        """Every wrapper closes every period at or above zero."""
        result = run_projection(household, years)
        assert result.snapshots
        for snapshot in result.snapshots:
            [person] = snapshot.persons
            for entry in person.wrappers:
                assert entry.closing_uncrystallised >= ZERO
                assert entry.closing_crystallised >= ZERO

    @given(household=modest_households(), years=horizon_years)
    @settings(max_examples=100, deadline=None)
    def test_wrapper_ledger_identity_every_period(
        self, household: Household, years: int
    ) -> None:
        """Closing = opening + inflows - outflows - fee + growth.

        The golden scenarios' ledger identity, over generated plans:
        every flow the wrapper reports must account for the balance
        movement, to within accumulated per-field rounding.
        """
        result = run_projection(household, years)
        for snapshot in result.snapshots:
            [person] = snapshot.persons
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

    @given(household=modest_households(), years=horizon_years)
    @settings(max_examples=100, deadline=None)
    @example(household=taxed_drawdown_household(), years=2)
    @example(household=penny_taxed_outflow_household(), years=1)
    def test_retirement_cash_conservation_every_period(
        self, household: Household, years: int
    ) -> None:
        """Gross draws + income - tax - banked + shortfall = need + outflows.

        The golden scenarios' need identity, over generated plans:
        every retired period's need is met by the gross cash the
        withdrawal step pays out of wrappers plus gross income, net of
        the personal tax assessment — excluding the growth tax and the
        funded annual-allowance charge, which the wrappers fund
        directly — with any surplus banked and any deficit reported as
        shortfall. The gross form matters (#147): ``net_withdrawn`` is
        already net of the marginal tax the gross-up prices on draws
        and ``tax_due`` assesses that same tax, so a net-figure
        identity would count it twice. The wrapper withdrawal fields
        also carry the up-front and annuity-purchase tax-free lump
        sums, already counted in the income term, so those come off
        the gross figure.
        """
        result = run_projection(household, years)
        for snapshot in result.snapshots:
            [person] = snapshot.persons
            if person.years_to_retirement > 0:
                continue
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
            delivered = (
                gross_withdrawn
                + income
                - (person.tax.tax_due - wrapper_funded_tax)
                - person.banked
                + person.shortfall
            )
            need = person.spending_need + person.planned_outflows
            gap = (delivered - need).amount.copy_abs()
            assert gap <= IDENTITY_TOLERANCE, (
                f"unaccounted retirement cash in {snapshot.period.start}"
            )

    @given(household=modest_households(), years=horizon_years)
    @settings(max_examples=100, deadline=None)
    def test_tax_lines_sum_to_tax_due(self, household: Household, years: int) -> None:
        """Every period's per-band tax lines sum to the tax due."""
        result = run_projection(household, years)
        for snapshot in result.snapshots:
            [person] = snapshot.persons
            lines_total = sum((line.tax for line in person.tax.lines), start=ZERO)
            assert person.tax.tax_due == lines_total


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
