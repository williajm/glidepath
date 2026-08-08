"""The decumulation golden scenario (test gap analysis, 2026-08).

The second full-pipeline golden test complements the accumulation-heavy
``test_golden_scenario``: a 63-year-old two years from retirement whose
plan exercises the flows the first golden never produces — a SIPP with
funds already crystallised, a taxable GIA charged growth tax and
receiving banked surplus, a commuted DB pension starting before
retirement, a deferred state pension rolled forward from a stale
forecast, a staged annuity purchase with its tax-free cash, a planned
outflow, and go-go/slow-go/no-go spending stages. The expected output
is hand-reviewed and checked in at
``tests/golden/decumulation_mixed_income.json``; the test serializes
the run the same way and compares character-for-character.

Any engine change that shifts the output fails this test — that is the
point. To accept a shift, regenerate with

    uv run --locked pytest --no-cov tests/test_golden_decumulation.py --update-golden

(``--no-cov``: the repository-wide coverage gate would otherwise fail a
single-module run) then review the diff and explain it in the pull
request.

The companion tests pin independently hand-computed figures (the
partial first year, the DB commutation arithmetic, the annuity's exact
40%-of-pot capital split) and whole-run identities over the flow
fields the first golden holds at zero (annuity purchases, growth tax,
banked surplus), so the golden file is anchored to arithmetic reviewed
by hand, not merely to whatever the engine emitted when the file was
first written.
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from factories import money_fact
from glidepath.core import (
    AnnuityPurchase,
    AnnuityType,
    AssumptionKey,
    ContributionSchedule,
    DBPension,
    Decision,
    EntityId,
    Fact,
    FactorTable,
    Household,
    LifeStage,
    Money,
    PeriodSnapshot,
    Person,
    PlannedOutflow,
    ProjectionReport,
    ProjectionResult,
    Rate,
    ReliefMechanic,
    ReportBasis,
    RevaluationBasis,
    RevaluationReference,
    RunConfig,
    SpendingPlan,
    StatePensionRecord,
    Wrapper,
    WrapperPeriodResult,
    build_report,
    run,
)
from glidepath.regions.uk import (
    GIA_KIND,
    ISA_KIND,
    RUK_RESIDENCY,
    SIPP_KIND,
    default_assumption_set,
    future_years_extension,
    uk_region,
)

GOLDEN_PATH = Path(__file__).parent / "golden" / "decumulation_mixed_income.json"

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)
FORECAST_AS_OF = date(2026, 4, 10)
"""Almost four whole months before the run start, so the state pension
forecast (and its protected slice) is rolled forward per §4.8 — the
first golden states every fact within a month of ``today`` and never
produces a roll-forward record."""

SCENARIO = (
    "63-year-old (born 1 May 1963) earning 48,000 with a SIPP (250,000"
    " uncrystallised + 50,000 already in drawdown; 8,000/yr gross"
    " employee via relief at source, earnings-escalated), an ISA"
    " (40,000; 4,000/yr fixed) and a GIA (100,000), retiring at 65 on a"
    " 30,000/yr real spending need staged 1.2/0.9/0.75 across"
    " go-go/slow-go/no-go; a DB pension (7,500/yr accrued, NPA 65, CPI"
    " revaluation capped at 5%, 25% commuted at factor 12), a state"
    " pension (230.25/wk forecast incl. 12.34/wk protected, deferred 3"
    " months), an annuity purchase of 40% of the pot at 72, and a"
    " 25,000 real planned outflow at 66; shipped default assumptions,"
    " run from 2 Aug 2026 to the default planning horizon (age 95)."
)

ZERO = Money(Decimal(0))
PENNY = Decimal("0.01")
TWELVE = Decimal(12)
MICRO = Decimal("0.000001")
TEN_DP = Decimal("0.0000000001")

STAGE_MULTIPLIERS = {
    LifeStage.GO_GO: Decimal("1.2"),
    LifeStage.SLOW_GO: Decimal("0.9"),
    LifeStage.NO_GO: Decimal("0.75"),
}

DB_START_INDEX = 2
"""The DB pension's NPA (65) arrives on 1 May 2028, mid-2028/29 — its
income and commutation lump sum land while still accumulating, and the
surplus is banked into the GIA (planning §5.2)."""

RETIREMENT_INDEX = 3
"""Age 65 is attained on 1 May 2028 — after the 2028/29 period's first
day, so the §4.1 gate keeps that year accumulating and decumulation
starts with the 2029/30 period, the 4th projected. The planned outflow
(age 66, 1 May 2029) lands in the same period."""

STATE_PENSION_INDEX = 4
"""State pension age (67) arrives on 1 May 2030; the 3-month deferral
decision moves the start to 1 Aug 2030, 8 whole months into 2030/31."""

ANNUITY_INDEX = 9
"""Age 72 arrives on 1 May 2035: 40% of the SIPP converts to level
annuity income, its uncrystallised share paying tax-free cash."""


def golden_household() -> Household:
    """The golden persona: mixed-income decumulator retiring at 65."""
    sipp = Wrapper(
        id=EntityId("golden-sipp"),
        kind=SIPP_KIND,
        balance=money_fact("250000"),
        crystallised_balance=money_fact("50000"),
        contributions=ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(8000)), recorded_on=RECORDED),
            relief_mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
            escalation=AssumptionKey.EARNINGS_GROWTH_REAL,
        ),
    )
    isa = Wrapper(
        id=EntityId("golden-isa"),
        kind=ISA_KIND,
        balance=money_fact("40000"),
        contributions=ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(4000)), recorded_on=RECORDED),
        ),
    )
    gia = Wrapper(
        id=EntityId("golden-gia"),
        kind=GIA_KIND,
        balance=money_fact("100000"),
    )
    db_pension = DBPension(
        id=EntityId("golden-db"),
        accrued_annual_pension=Fact(
            value=Money(Decimal(7500)), as_of=date(2025, 4, 6), recorded_on=RECORDED
        ),
        statement_date=date(2025, 4, 6),
        normal_pension_age=Fact(value=65, as_of=AS_OF, recorded_on=RECORDED),
        revaluation_basis=RevaluationBasis(
            reference=RevaluationReference.CPI, cap=Rate(Decimal("0.05"))
        ),
        early_late_factors=FactorTable(factors={}),
        commuted_fraction=Decision(value=Decimal("0.25"), recorded_on=RECORDED),
        commutation_factor=Fact(value=Decimal(12), as_of=AS_OF, recorded_on=RECORDED),
    )
    state_pension = StatePensionRecord(
        forecast_weekly_amount=Fact(
            value=Money(Decimal("230.25")), as_of=FORECAST_AS_OF, recorded_on=RECORDED
        ),
        protected_payment=Fact(
            value=Money(Decimal("12.34")), as_of=FORECAST_AS_OF, recorded_on=RECORDED
        ),
        deferral_years=Decision(value=Decimal("0.25"), recorded_on=RECORDED),
    )
    annuity = AnnuityPurchase(
        id=EntityId("golden-annuity"),
        at_age=Decision(value=72, recorded_on=RECORDED),
        fraction_of_pot=Decision(value=Decimal("0.4"), recorded_on=RECORDED),
        annuity_type=AnnuityType.LEVEL,
    )
    person = Person(
        id=EntityId("golden-person"),
        date_of_birth=Fact(value=date(1963, 5, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=65, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        employment_income=money_fact("48000"),
        wrappers=(sipp, isa, gia),
        db_pensions=(db_pension,),
        annuity_purchases=(annuity,),
        state_pension=state_pension,
    )
    outflow = PlannedOutflow(
        id=EntityId("golden-outflow"),
        label="motorhome purchase",
        amount_real=Decision(value=Money(Decimal(25000)), recorded_on=RECORDED),
        at_age_of=(person.id, 66),
    )
    spending = SpendingPlan(
        annual_spending_real=money_fact("30000"),
        stage_multipliers=STAGE_MULTIPLIERS,
    )
    return Household(persons=(person,), spending=spending, planned_outflows=(outflow,))


@pytest.fixture(scope="module", name="result")
def result_fixture() -> ProjectionResult:
    """The golden projection, run once for the module."""
    assumptions = default_assumption_set()
    region = uk_region(future_years_extension(assumptions))
    return run(golden_household(), assumptions, region, RunConfig(today=TODAY))


@pytest.fixture(scope="module", name="real_report")
def real_report_fixture(result: ProjectionResult) -> ProjectionReport:
    """The projection presented in real (today's money) terms."""
    return build_report(result, ReportBasis.REAL)


def _money_str(amount: Money) -> str:
    """A money amount as a two-decimal string.

    Snapshot fields are already-quantized ledger writes, so this is a
    no-op for them; derived figures that can arrive unquantized (a zero
    tax assessment) are display-quantized for a uniform file.
    """
    return str(amount.quantized().amount)


def _wrapper_row(entry: WrapperPeriodResult) -> dict[str, object]:
    """One wrapper's golden row: every flow and closing sub-balance.

    A superset of the first golden's row: this scenario drives the
    annuity-purchase, growth-tax, and banked-surplus flows the first
    holds at zero, so they are pinned here.
    """
    return {
        "wrapper": entry.wrapper_id,
        "kind": entry.kind,
        "equity": str(entry.allocation.equity.quantize(MICRO)),
        "employee_contribution": _money_str(entry.employee_contribution),
        "employer_contribution": _money_str(entry.employer_contribution),
        "provider_relief": _money_str(entry.provider_relief),
        "contribution_bonus": _money_str(entry.contribution_bonus),
        "contribution_shortfall": _money_str(entry.contribution_shortfall),
        "withdrawal_tax_free": _money_str(entry.withdrawal_tax_free),
        "withdrawal_taxable": _money_str(entry.withdrawal_taxable),
        "annuity_purchase": _money_str(entry.annuity_purchase),
        "taxable_interest": _money_str(entry.taxable_interest),
        "taxable_dividends": _money_str(entry.taxable_dividends),
        "growth_tax": _money_str(entry.growth_tax),
        "banked_in": _money_str(entry.banked_in),
        "fee": _money_str(entry.fee),
        "growth": _money_str(entry.growth),
        "closing_uncrystallised": _money_str(entry.closing_uncrystallised),
        "closing_crystallised": _money_str(entry.closing_crystallised),
    }


def _period_row(snapshot: PeriodSnapshot, real_closing: Money) -> dict[str, object]:
    """One period's golden row from its snapshot and real closing total."""
    [person] = snapshot.persons
    start_year = snapshot.period.start.year
    return {
        "tax_year": f"{start_year}/{(start_year + 1) % 100:02d}",
        "start": snapshot.period.start.isoformat(),
        "end": snapshot.period.end.isoformat(),
        "active_months": int((snapshot.year_fraction * TWELVE).to_integral_value()),
        "inflation_factor": str(snapshot.inflation_factor.quantize(TEN_DP)),
        "age": person.age_at_period_start,
        "years_to_retirement": person.years_to_retirement,
        "stage": person.stage.name,
        "employment_income": _money_str(person.employment_income),
        "tax_due": _money_str(person.tax.tax_due),
        "spending_need": _money_str(person.spending_need),
        "planned_outflows": _money_str(person.planned_outflows),
        "net_withdrawn": _money_str(person.net_withdrawn),
        "shortfall": _money_str(person.shortfall),
        "db_income": _money_str(person.db_income),
        "db_lump_sum": _money_str(person.db_lump_sum),
        "state_pension_income": _money_str(person.state_pension_income),
        "annuity_income": _money_str(person.annuity_income),
        "annuity_lump_sum": _money_str(person.annuity_lump_sum),
        "pension_lump_sum": _money_str(person.pension_lump_sum),
        "lsa_used": _money_str(person.lsa_used),
        "banked": _money_str(person.banked),
        "wrappers": [_wrapper_row(entry) for entry in person.wrappers],
        "closing_balance_real": _money_str(real_closing),
    }


def render_golden(result: ProjectionResult, real_report: ProjectionReport) -> str:
    """Serialize the projection to the golden file's canonical text.

    Nominal ledger figures come from the snapshots; each period also
    carries its real (today's money) closing balance from the reporting
    layer, so both bases are pinned. The §4.8 roll-forward records are
    pinned too — this scenario's stale state pension forecast is the
    one golden input that arrives at the run start via an estimate
    layered on a fact.
    """
    payload: dict[str, object] = {
        "scenario": SCENARIO,
        "today": TODAY.isoformat(),
        "facts": [entry.label for entry in result.provenance.facts],
        "decisions": [entry.label for entry in result.provenance.decisions],
        "assumption_keys_read": [
            entry.key.name for entry in result.provenance.assumptions
        ],
        "balance_roll_forwards": [
            {
                "label": entry.label,
                "stated": _money_str(entry.stated),
                "as_of": entry.as_of.isoformat(),
                "months": entry.months,
                "factor": str(entry.factor.quantize(TEN_DP)),
                "opening": _money_str(entry.opening),
            }
            for entry in result.provenance.balance_roll_forwards
        ],
        "periods": [
            _period_row(snapshot, row.closing_balance)
            for snapshot, row in zip(result.snapshots, real_report.rows, strict=True)
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


class TestGoldenOutput:
    """The checked-in expected output."""

    def test_matches_checked_in_golden(
        self,
        result: ProjectionResult,
        real_report: ProjectionReport,
        request: pytest.FixtureRequest,
    ) -> None:
        """The run reproduces the reviewed golden file exactly."""
        rendered = render_golden(result, real_report)
        if request.config.getoption("--update-golden"):
            GOLDEN_PATH.parent.mkdir(exist_ok=True)
            GOLDEN_PATH.write_text(rendered, encoding="utf-8", newline="\n")
        stored = GOLDEN_PATH.read_text(encoding="utf-8")
        assert stored == rendered, (
            "golden output shifted — regenerate with `uv run --locked pytest"
            " --no-cov tests/test_golden_decumulation.py --update-golden`,"
            " review the diff, and explain it in the pull request"
        )


class TestHandCheckedAnchors:
    """Independently hand-computed figures anchoring the golden file."""

    def test_first_period_is_partial_and_hand_checked(
        self, result: ProjectionResult
    ) -> None:
        """The partial 2026/27 year matches hand-worked figures.

        Eight whole months remain from 2 Aug 2026 to 5 Apr 2027,
        scaling the 48,000 salary to 32,000.00, the 8,000 SIPP
        contribution to 5,333.33 (provider relief 20% of that gross:
        1,066.67), and the 4,000 ISA contribution to 2,666.67.
        Employment tax: 32,000.00 less the 12,570 personal allowance
        leaves 19,430.00 in the basic band; 20% is 3,886.00.
        The GIA's portfolio income adds its own marginal charge, which
        the ledger books against the wrapper as growth tax — so total
        tax due less the growth tax must land exactly on the
        hand-worked employment figure.
        """
        first = result.snapshots[0]
        [person] = first.persons
        sipp, isa, gia = person.wrappers
        assert first.year_fraction == Decimal(8) / Decimal(12)
        assert person.employment_income == Money(Decimal("32000.00"))
        assert sipp.employee_contribution == Money(Decimal("5333.33"))
        assert sipp.provider_relief == Money(Decimal("1066.67"))
        assert isa.employee_contribution == Money(Decimal("2666.67"))
        assert gia.taxable_interest > ZERO
        assert gia.taxable_dividends > ZERO
        assert gia.growth_tax > ZERO
        employment_tax = person.tax.tax_due - gia.growth_tax
        assert employment_tax == Money(Decimal("3886.00"))

    def test_db_pension_starts_before_retirement(
        self, result: ProjectionResult
    ) -> None:
        """The commuted DB pension lands mid-accumulation and is banked.

        NPA 65 arrives on 1 May 2028, a year before retirement. The
        commutation arithmetic ties the ledger together: the lump sum
        is 25% of the revalued pension x factor 12 = 3 x revalued, so
        the residual income in payment is 75% of (lump sum / 3),
        pro-rated over the 11 whole months from 1 May 2028 to the tax
        year end. The lump sum consumes lump-sum allowance, and the
        surplus income the accumulating year cannot spend is banked
        into the GIA (planning §5.2) — flows the first golden never
        produces.
        """
        snapshot = result.snapshots[DB_START_INDEX]
        [person] = snapshot.persons
        gia = person.wrappers[2]
        assert snapshot.period.start == date(2028, 4, 6)
        assert person.employment_income > ZERO
        assert person.spending_need == ZERO
        assert person.db_lump_sum > ZERO
        assert person.lsa_used == person.db_lump_sum
        revalued = person.db_lump_sum.amount / Decimal(3)
        eleven_twelfths = Decimal(11) / Decimal(12)
        expected_income = Money(
            revalued * Decimal("0.75") * eleven_twelfths
        ).quantized()
        assert person.db_income == expected_income
        assert person.banked > ZERO
        assert gia.banked_in == person.banked

    def test_retirement_transition_at_sixty_five(
        self, result: ProjectionResult
    ) -> None:
        """Decumulation starts with 2029/30, outflow and stages included.

        Age 65 arrives on 1 May 2028 — mid-2028/29, so that whole year
        still accumulates (§4.1 gate) and the next period switches:
        employment ceases, the staged spending need appears (go-go
        multiplier 1.2), and the age-66 planned outflow lands in the
        same period, funded through the withdrawal machinery.
        """
        last_accumulating = result.snapshots[RETIREMENT_INDEX - 1]
        [before] = last_accumulating.persons
        assert before.employment_income > ZERO
        assert before.spending_need == ZERO
        assert before.planned_outflows == ZERO
        first_retired = result.snapshots[RETIREMENT_INDEX]
        [after] = first_retired.persons
        assert first_retired.period.start == date(2029, 4, 6)
        assert after.stage is LifeStage.GO_GO
        assert after.employment_income == ZERO
        assert after.spending_need > ZERO
        assert after.planned_outflows > ZERO
        assert after.net_withdrawn > ZERO
        assert after.shortfall == ZERO

    def test_state_pension_starts_after_deferral(
        self, result: ProjectionResult
    ) -> None:
        """The deferred state pension first pays in 2030/31.

        State pension age (67) arrives on 1 May 2030; the 3-month
        deferral decision moves the start to 1 Aug 2030, so 2029/30
        pays nothing and 2030/31 pays 8 of 12 months. The stale
        forecast reached the run start through a §4.8 roll-forward —
        both the main and the protected slice carry a record.
        """
        [before] = result.snapshots[STATE_PENSION_INDEX - 1].persons
        assert before.state_pension_income == ZERO
        snapshot = result.snapshots[STATE_PENSION_INDEX]
        [person] = snapshot.persons
        assert snapshot.period.start == date(2030, 4, 6)
        assert person.state_pension_income > ZERO
        labels = [entry.label for entry in result.provenance.balance_roll_forwards]
        assert labels == [
            "person[golden-person].state_pension.forecast_weekly_amount",
            "person[golden-person].state_pension.protected_payment",
        ]

    def test_annuity_purchase_converts_forty_percent_at_seventy_two(
        self, result: ProjectionResult
    ) -> None:
        """The age-72 purchase converts exactly 40% of the SIPP.

        The purchase fires on 1 May 2035 (period 2035/36): the capital
        leaving the wrapper plus the tax-free cash paid alongside must
        equal exactly 40% of the SIPP's opening balance, the tax-free
        cash is the period's whole tax-free withdrawal, level annuity
        income joins the income step pro-rated over 11 months, and the
        lump-sum allowance ledger records the cash.
        """
        [before] = result.snapshots[ANNUITY_INDEX - 1].persons
        assert before.annuity_income == ZERO
        snapshot = result.snapshots[ANNUITY_INDEX]
        [person] = snapshot.persons
        sipp = person.wrappers[0]
        assert snapshot.period.start == date(2035, 4, 6)
        assert sipp.annuity_purchase > ZERO
        assert person.annuity_lump_sum == sipp.withdrawal_tax_free
        converted = sipp.annuity_purchase + sipp.withdrawal_tax_free
        forty_percent = Money(sipp.opening_balance.amount * Decimal("0.4")).quantized()
        assert converted == forty_percent
        assert person.annuity_income > ZERO
        assert person.lsa_used > before.lsa_used

    def test_horizon_runs_to_planning_age(self, result: ProjectionResult) -> None:
        """The default horizon covers 2026/27 through 2058/59 (age 95).

        The final period opens on 6 Apr 2058 and the horizon end (age
        95, 1 May 2058) falls before its first whole month completes,
        so the last row is a zero-fraction stub: present in the ledger,
        with every flow scaled to nothing.
        """
        assert len(result.snapshots) == 33
        last = result.snapshots[-1]
        [person] = last.persons
        assert last.period.start == date(2058, 4, 6)
        assert person.age_at_period_start == 94
        assert last.year_fraction == Decimal(0)


class TestStructuralInvariants:
    """Whole-run identities the reviewed output must satisfy."""

    def test_wrapper_ledger_identity_every_period(
        self, result: ProjectionResult
    ) -> None:
        """Closing = opening + inflows - outflows - fee + growth.

        The full-flow version of the first golden's identity: this
        scenario drives the banked-in, annuity-purchase, and growth-tax
        flows, so they must reconcile too. Each snapshot field is
        independently quantized at period close (planning §4.6), so the
        identity holds to within a few pennies of accumulated rounding,
        never more.
        """
        tolerance = Decimal("0.05")
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
                    - entry.fee
                    + entry.growth
                )
                gap = (entry.closing_balance - reconstructed).amount.copy_abs()
                assert gap <= tolerance, (
                    f"{entry.wrapper_id} ledger identity off by {gap}"
                    f" in {snapshot.period.start}"
                )

    def test_retirement_cash_conservation_every_period(
        self, result: ProjectionResult
    ) -> None:
        """Gross draws + income - tax - banked + shortfall = need + outflows.

        The mixed-income version of the first golden's need identity:
        every retired period's net need (spending plus planned
        outflows) is met by the gross cash the withdrawal step pays
        out of wrappers plus pension income and tax-free lump sums (a
        DB commutation, an annuity purchase's tax-free cash) net of
        the personal tax assessment — excluding the growth tax, which
        the wrappers fund directly — with any surplus banked and any
        deficit reported as shortfall. Nothing is silently dropped.
        The gross form matters (#147): ``net_withdrawn`` is already
        net of the marginal tax the gross-up prices on draws and
        ``tax_due`` assesses that same tax, so a net-figure identity
        would count it twice. The wrapper withdrawal fields also carry
        the up-front and annuity-purchase tax-free lump sums, already
        counted in the income term, so those come off the gross
        figure.
        """
        tolerance = Decimal("0.05")
        for snapshot in result.snapshots[RETIREMENT_INDEX:]:
            [person] = snapshot.persons
            growth_tax = sum(
                (entry.growth_tax for entry in person.wrappers), start=ZERO
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
                - (person.tax.tax_due - growth_tax)
                - person.banked
                + person.shortfall
            )
            need = person.spending_need + person.planned_outflows
            gap = (delivered - need).amount.copy_abs()
            assert gap <= tolerance, (
                f"unaccounted retirement cash in {snapshot.period.start}"
            )

    def test_real_spending_need_follows_stage_multipliers(
        self, real_report: ProjectionReport, result: ProjectionResult
    ) -> None:
        """Deflated to today's money, the need is 30,000 x stage x fraction.

        The engine inflates the staged real spending decision by its
        own CPI path and the reporting layer deflates by the same path
        (one inflation truth per run), so the real need must come back
        as the flat 30,000 scaled by each period's go-go/slow-go/no-go
        multiplier and year fraction.
        """
        tolerance = Decimal("0.01")
        rows_and_snapshots = zip(
            real_report.rows[RETIREMENT_INDEX:],
            result.snapshots[RETIREMENT_INDEX:],
            strict=True,
        )
        for row, snapshot in rows_and_snapshots:
            [person] = snapshot.persons
            multiplier = STAGE_MULTIPLIERS[person.stage]
            expected = Money(
                Decimal(30000) * multiplier * row.year_fraction
            ).quantized()
            gap = (row.spending_need - expected).amount.copy_abs()
            assert gap <= tolerance, f"real need drifted in {row.period.start}"

    def test_lump_sum_allowance_ledger_is_monotonic(
        self, result: ProjectionResult
    ) -> None:
        """Cumulative tax-free cash never decreases across the run."""
        used = Decimal(0)
        for snapshot in result.snapshots:
            [person] = snapshot.persons
            assert person.lsa_used.amount >= used, (
                f"lsa_used regressed in {snapshot.period.start}"
            )
            used = person.lsa_used.amount
