"""The end-to-end golden scenario (roadmap 4.5; planning §5.2, §8 Phase 4).

The first full-pipeline golden test: a 35-year-old with a workplace DC
and an ISA, retiring at 60, projected deterministically through the
real UK region from a fixed ``today`` (2 August 2026) to the default
planning horizon (age 95). The expected output is hand-reviewed and
checked in at ``tests/golden/dc_isa_retire_60.json``; the test
serializes the run the same way and compares character-for-character.

Any engine change that shifts the output fails this test — that is the
point. To accept a shift, regenerate with

    uv run pytest tests/test_golden_scenario.py --update-golden

then review the diff and explain it in the pull request.

The companion tests pin independently hand-computed figures (first
partial period, the retirement transition, ledger identities), so the
golden file is anchored to arithmetic reviewed by hand, not merely to
whatever the engine emitted when the file was first written.
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from glidepath.core import (
    AssumptionKey,
    ContributionSchedule,
    Decision,
    EntityId,
    Fact,
    Household,
    Money,
    PeriodSnapshot,
    Person,
    ProjectionReport,
    ProjectionResult,
    ReliefMechanic,
    ReportBasis,
    RunConfig,
    SpendingPlan,
    Wrapper,
    WrapperPeriodResult,
    build_report,
    run,
)
from glidepath.regions.uk import (
    ISA_KIND,
    RUK_RESIDENCY,
    WORKPLACE_DC_KIND,
    default_assumption_set,
    future_years_extension,
    uk_region,
)

GOLDEN_PATH = Path(__file__).parent / "golden" / "dc_isa_retire_60.json"

TODAY = date(2026, 8, 2)
RECORDED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2026, 8, 1)

SCENARIO = (
    "35-year-old (born 1 Feb 1991) earning 50,000 with a workplace DC"
    " (balance 40,000; 5,000/yr gross employee via relief at source +"
    " 3,000/yr employer, both earnings-escalated) and an ISA (balance"
    " 20,000; 5,000/yr fixed), retiring at 60 on a 30,000/yr real"
    " spending need; shipped default assumptions, run from 2 Aug 2026"
    " to the default planning horizon (age 95)."
)

ZERO = Money(Decimal(0))
PENNY = Decimal("0.01")
TWELVE = Decimal(12)
MICRO = Decimal("0.000001")
TEN_DP = Decimal("0.0000000001")

RETIREMENT_INDEX = 25
"""Age 60 is attained on 1 Feb 2051 — after the 2050/51 period's first
day, so the §4.1 gate keeps that year accumulating and decumulation
starts with the 2051/52 period, the 26th projected."""


def money_fact(amount: str) -> Fact[Money]:
    """A user-stated monetary fact."""
    return Fact(value=Money(Decimal(amount)), as_of=AS_OF, recorded_on=RECORDED)


def golden_household() -> Household:
    """The golden persona: DC + ISA accumulator retiring at 60."""
    dc = Wrapper(
        id=EntityId("golden-dc"),
        kind=WORKPLACE_DC_KIND,
        balance=money_fact("40000"),
        contributions=ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(5000)), recorded_on=RECORDED),
            employer_amount=money_fact("3000"),
            relief_mechanic=ReliefMechanic.RELIEF_AT_SOURCE,
            escalation=AssumptionKey.EARNINGS_GROWTH_REAL,
        ),
    )
    isa = Wrapper(
        id=EntityId("golden-isa"),
        kind=ISA_KIND,
        balance=money_fact("20000"),
        contributions=ContributionSchedule(
            employee_amount=Decision(value=Money(Decimal(5000)), recorded_on=RECORDED),
        ),
    )
    person = Person(
        id=EntityId("golden-person"),
        date_of_birth=Fact(value=date(1991, 2, 1), as_of=AS_OF, recorded_on=RECORDED),
        target_retirement_age=Decision(value=60, recorded_on=RECORDED),
        tax_residency=RUK_RESIDENCY,
        employment_income=money_fact("50000"),
        wrappers=(dc, isa),
    )
    spending = SpendingPlan(annual_spending_real=money_fact("30000"))
    return Household(persons=(person,), spending=spending)


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
    """One wrapper's golden row: flows and closing sub-balances."""
    return {
        "wrapper": entry.wrapper_id,
        "kind": entry.kind,
        "equity": str(entry.allocation.equity.quantize(MICRO)),
        "employee_contribution": _money_str(entry.employee_contribution),
        "employer_contribution": _money_str(entry.employer_contribution),
        "provider_relief": _money_str(entry.provider_relief),
        "contribution_shortfall": _money_str(entry.contribution_shortfall),
        "withdrawal_tax_free": _money_str(entry.withdrawal_tax_free),
        "withdrawal_taxable": _money_str(entry.withdrawal_taxable),
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
        "net_withdrawn": _money_str(person.net_withdrawn),
        "shortfall": _money_str(person.shortfall),
        "wrappers": [_wrapper_row(entry) for entry in person.wrappers],
        "closing_balance_real": _money_str(real_closing),
    }


def render_golden(result: ProjectionResult, real_report: ProjectionReport) -> str:
    """Serialize the projection to the golden file's canonical text.

    Nominal ledger figures come from the snapshots; each period also
    carries its real (today's money) closing balance from the reporting
    layer (roadmap 4.4), so both bases are pinned. The provenance
    labels and the assumption keys in first-read order document what
    the run rested on without embedding volatile content digests.
    """
    payload: dict[str, object] = {
        "scenario": SCENARIO,
        "today": TODAY.isoformat(),
        "facts": [entry.label for entry in result.provenance.facts],
        "decisions": [entry.label for entry in result.provenance.decisions],
        "assumption_keys_read": [
            entry.key.name for entry in result.provenance.assumptions
        ],
        "periods": [
            _period_row(snapshot, row.closing_balance)
            for snapshot, row in zip(result.snapshots, real_report.rows, strict=True)
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


class TestGoldenOutput:
    """The checked-in expected output (roadmap 4.5's acceptance)."""

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
            "golden output shifted — regenerate with `uv run pytest"
            " tests/test_golden_scenario.py --update-golden`, review the"
            " diff, and explain it in the pull request"
        )


class TestHandCheckedAnchors:
    """Independently hand-computed figures anchoring the golden file."""

    def test_first_period_is_partial_and_hand_checked(
        self, result: ProjectionResult
    ) -> None:
        """The partial 2026/27 year matches hand-worked figures.

        Eight whole months remain from 2 Aug 2026 to 5 Apr 2027
        (roadmap 4.6), scaling the 50,000 salary to 33,333.33 and both
        5,000 contributions to 3,333.33 (DC provider relief 666.67 at
        20%; employer 2,000.00). Tax: 33,333.33 less the 12,570
        personal allowance leaves 20,763.33 in the basic band; 20%
        rounded down per band is 4,152.66.
        """
        first = result.snapshots[0]
        [person] = first.persons
        dc, isa = person.wrappers
        assert first.year_fraction == Decimal(8) / Decimal(12)
        assert person.employment_income == Money(Decimal("33333.33"))
        assert dc.employee_contribution == Money(Decimal("3333.33"))
        assert dc.provider_relief == Money(Decimal("666.67"))
        assert dc.employer_contribution == Money(Decimal("2000.00"))
        assert isa.employee_contribution == Money(Decimal("3333.33"))
        assert person.tax.tax_due == Money(Decimal("4152.66"))

    def test_retirement_transition_at_sixty(self, result: ProjectionResult) -> None:
        """Accumulation stops and drawdown starts with the 2051/52 year.

        Age 60 arrives on 1 Feb 2051 — mid-2050/51, so that whole year
        still accumulates (§4.1 gate) and the next period switches:
        employment and contributions cease, the spending need appears,
        and the tax-free ISA is drawn ahead of the pension (planning
        §5.2's v1 withdrawal order).
        """
        last_accumulating = result.snapshots[RETIREMENT_INDEX - 1]
        [before] = last_accumulating.persons
        assert last_accumulating.period.start == date(2050, 4, 6)
        assert before.age_at_period_start == 59
        assert before.years_to_retirement == 1
        assert before.employment_income > ZERO
        assert before.spending_need == ZERO
        first_retired = result.snapshots[RETIREMENT_INDEX]
        [after] = first_retired.persons
        dc, isa = after.wrappers
        assert first_retired.period.start == date(2051, 4, 6)
        assert after.age_at_period_start == 60
        assert after.years_to_retirement <= 0
        assert after.employment_income == ZERO
        assert after.spending_need > ZERO
        assert dc.employee_contribution == ZERO
        assert isa.employee_contribution == ZERO
        assert isa.withdrawal_tax_free > ZERO

    def test_horizon_runs_to_planning_age(self, result: ProjectionResult) -> None:
        """The default horizon covers 2026/27 through 2085/86 (age 95)."""
        assert len(result.snapshots) == 60
        last = result.snapshots[-1]
        [person] = last.persons
        assert last.period.start == date(2085, 4, 6)
        assert person.age_at_period_start == 94


class TestStructuralInvariants:
    """Whole-run identities the reviewed output must satisfy."""

    def test_wrapper_ledger_identity_every_period(
        self, result: ProjectionResult
    ) -> None:
        """Closing = opening + inflows - outflows - fee + growth.

        Each snapshot field is independently quantized at period close
        (planning §4.6), so the identity holds to within a few pennies
        of accumulated rounding, never more.
        """
        tolerance = Decimal("0.05")
        for snapshot in result.snapshots:
            [person] = snapshot.persons
            for entry in person.wrappers:
                reconstructed = (
                    entry.opening_balance
                    + entry.employee_contribution
                    + entry.employer_contribution
                    - entry.withdrawal_tax_free
                    - entry.withdrawal_taxable
                    - entry.fee
                    + entry.growth
                )
                gap = (entry.closing_balance - reconstructed).amount.copy_abs()
                assert gap <= tolerance, (
                    f"{entry.wrapper_id} ledger identity off by {gap}"
                    f" in {snapshot.period.start}"
                )

    def test_need_is_met_or_shortfall_accounts_for_it(
        self, result: ProjectionResult
    ) -> None:
        """Every decumulation period: delivered + shortfall = need.

        While the pots last the need is met in full; once they run dry
        the unmet remainder is reported as shortfall — nothing is
        silently dropped (the ruin signal of roadmap 7.3).
        """
        tolerance = Decimal("0.02")
        for snapshot in result.snapshots[RETIREMENT_INDEX:]:
            [person] = snapshot.persons
            gap = (
                (person.net_withdrawn + person.shortfall) - person.spending_need
            ).amount.copy_abs()
            assert gap <= tolerance, f"unaccounted need in {snapshot.period.start}"

    def test_real_spending_need_is_flat_thirty_thousand(
        self, real_report: ProjectionReport
    ) -> None:
        """Deflated to today's money, the need is 30,000 x year fraction.

        The engine inflates the real spending decision by its own CPI
        path and the reporting layer deflates by the same path (one
        inflation truth per run, planning §5.2), so the real need must
        come back flat apart from the partial final year.
        """
        tolerance = Decimal("0.01")
        for row in real_report.rows[RETIREMENT_INDEX:]:
            expected = Money(Decimal(30000) * row.year_fraction).quantized()
            gap = (row.spending_need - expected).amount.copy_abs()
            assert gap <= tolerance, f"real need drifted in {row.period.start}"
