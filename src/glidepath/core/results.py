"""Projection results, snapshots, and run provenance (roadmap 4.1; planning §5.2).

The engine emits one :class:`PeriodSnapshot` per period — balances,
flows by category, tax breakdown, ages, stage, and allocation per
person and wrapper (planning §5.2 step 8) — and returns them in a
:class:`ProjectionResult` whose :class:`RunProvenance` lists the facts
used, the assumptions actually read (default vs overridden), the
decision variables in effect, the region data version, and the seed:
exactly the payload the UI's "stated vs assumed" inspector renders
(planning §5.1).

All monetary snapshot fields are quantized ledger writes (planning
§4.6): the engine rounds at period close, so consumers never see
sub-penny amounts.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from glidepath.core.money import Money

if TYPE_CHECKING:
    from glidepath.core.config import RunConfig
    from glidepath.core.entities import EntityId, Household
    from glidepath.core.glide import LifeStage
    from glidepath.core.investments import AssetAllocation
    from glidepath.core.periods import Period
    from glidepath.core.provenance import Assumption, Decision, Fact
    from glidepath.core.returns import PeriodReturns
    from glidepath.core.tax import TaxResult
    from glidepath.core.wrappers import WrapperKindId

_ZERO = Money(Decimal(0))
_ZERO_FACTOR = Decimal(0)
_ONE_FACTOR = Decimal(1)


@dataclass(frozen=True, slots=True)
class WrapperPeriodResult:
    """One wrapper's balances and flows through one period (§5.2 step 8).

    Pension sub-balances are tracked separately: ``uncrystallised``
    funds have not been accessed; ``crystallised`` funds are already in
    drawdown (planning §5.1). Non-pension kinds keep the crystallised
    fields at zero. ``employee_contribution`` is the gross amount that
    landed in the pot (of which ``provider_relief`` arrived from the
    provider's at-source reclaim); ``contribution_shortfall`` is the
    intended amount that could not be contributed (per-kind caps or the
    region's relief limits). ``growth`` may be negative (a down
    period); every other flow is non-negative.
    """

    wrapper_id: EntityId
    kind: WrapperKindId
    allocation: AssetAllocation
    opening_uncrystallised: Money
    opening_crystallised: Money
    employee_contribution: Money
    employer_contribution: Money
    provider_relief: Money
    contribution_shortfall: Money
    withdrawal_tax_free: Money
    withdrawal_taxable: Money
    fee: Money
    growth: Money
    closing_uncrystallised: Money
    closing_crystallised: Money

    def __post_init__(self) -> None:
        """Reject negative amounts in the non-negative fields."""
        non_negative = (
            self.opening_uncrystallised,
            self.opening_crystallised,
            self.employee_contribution,
            self.employer_contribution,
            self.provider_relief,
            self.contribution_shortfall,
            self.withdrawal_tax_free,
            self.withdrawal_taxable,
            self.fee,
            self.closing_uncrystallised,
            self.closing_crystallised,
        )
        if any(amount < _ZERO for amount in non_negative):
            msg = "WrapperPeriodResult amounts (except growth) must be non-negative"
            raise ValueError(msg)

    @property
    def opening_balance(self) -> Money:
        """Both sub-balances at period open."""
        return self.opening_uncrystallised + self.opening_crystallised

    @property
    def closing_balance(self) -> Money:
        """Both sub-balances at period close."""
        return self.closing_uncrystallised + self.closing_crystallised

    @property
    def withdrawal_gross(self) -> Money:
        """The period's total gross withdrawal from this wrapper."""
        return self.withdrawal_tax_free + self.withdrawal_taxable


@dataclass(frozen=True, slots=True)
class PersonPeriodResult:
    """One person's position through one period (§5.2 step 8).

    ``spending_need`` is the period's net (after-tax) spending target in
    nominal money — zero before decumulation; ``net_withdrawn`` is the
    net cash the withdrawal step delivered toward it; ``shortfall`` is
    the unmet remainder once every accessible wrapper was exhausted
    (the ruin signal the success metrics of roadmap 7.3 read).
    """

    person_id: EntityId
    age_at_period_start: int
    years_to_retirement: int
    stage: LifeStage
    employment_income: Money
    tax: TaxResult
    spending_need: Money
    net_withdrawn: Money
    shortfall: Money
    wrappers: tuple[WrapperPeriodResult, ...]

    def __post_init__(self) -> None:
        """Reject negative flows."""
        amounts = (
            self.employment_income,
            self.spending_need,
            self.net_withdrawn,
            self.shortfall,
        )
        if any(amount < _ZERO for amount in amounts):
            msg = "PersonPeriodResult amounts must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PeriodSnapshot:
    """The full ledger record of one projected period (§5.2 step 8).

    ``inflation_factor`` is the cumulative factor from the run's first
    period to this one — the CPI path the engine inflated nominal
    figures by, which the reporting layer deflates by (roadmap 4.4:
    one inflation truth per run). The first period's factor is 1.

    ``year_fraction`` is the whole-month fraction of the period inside
    the run window (roadmap 4.6, planning §5.2): 1 for a whole period;
    less when ``today`` or the horizon end falls mid-period, in which
    case the period's flows, fees, and growth were scaled by it.
    """

    period: Period
    returns: PeriodReturns
    inflation_factor: Decimal
    persons: tuple[PersonPeriodResult, ...]
    year_fraction: Decimal = _ONE_FACTOR

    def __post_init__(self) -> None:
        """Require a positive inflation factor and a fraction in [0, 1]."""
        if self.inflation_factor <= _ZERO_FACTOR:
            msg = "PeriodSnapshot.inflation_factor must be positive"
            raise ValueError(msg)
        if not _ZERO_FACTOR <= self.year_fraction <= _ONE_FACTOR:
            msg = "PeriodSnapshot.year_fraction must lie between 0 and 1"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class LabelledFact:
    """One user-stated fact the run used, at a stable plan path."""

    label: str
    fact: Fact[Any]


@dataclass(frozen=True, slots=True)
class LabelledDecision:
    """One user choice in effect during the run, at a stable plan path."""

    label: str
    decision: Decision[Any]


@dataclass(frozen=True, slots=True)
class RunProvenance:
    """What a run's numbers rest on (planning §5.1, §4.6).

    ``assumptions`` lists every assumption actually read, in first-read
    order, each carrying its own default-vs-overridden provenance —
    the engine-side read tracking makes this exhaustive with no UI
    bookkeeping (planning §5.1).
    """

    facts: tuple[LabelledFact, ...]
    decisions: tuple[LabelledDecision, ...]
    assumptions: tuple[Assumption[Any], ...]
    region_data_version: str
    seed: int | None


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    """One deterministic projection: the period ledger plus provenance.

    ``config`` is the exact :class:`~glidepath.core.RunConfig` the run
    received — today, horizon, mode, seed — so a result carries the
    configuration part of its §4.6 manifest (the full persisted
    manifest is Phase 6 work).
    """

    snapshots: tuple[PeriodSnapshot, ...]
    provenance: RunProvenance
    config: RunConfig


def collect_plan_facts(household: Household) -> tuple[LabelledFact, ...]:
    """Every user-stated fact in the plan, at stable entity-id paths.

    The engine's inputs are total — a projection reads the whole plan —
    so the facts used are the facts present (planning §5.1).
    """
    facts: list[LabelledFact] = []

    def note(label: str, fact: Fact[Any] | None) -> None:
        """Record ``fact`` under ``label`` when present."""
        if fact is not None:
            facts.append(LabelledFact(label=label, fact=fact))

    if household.spending is not None:
        note(
            "household.spending.annual_spending_real",
            household.spending.annual_spending_real,
        )
    for person in household.persons:
        prefix = f"person[{person.id}]"
        note(f"{prefix}.date_of_birth", person.date_of_birth)
        note(f"{prefix}.sex_for_longevity", person.sex_for_longevity)
        note(f"{prefix}.employment_income", person.employment_income)
        note(f"{prefix}.mpaa_triggered_on", person.mpaa_triggered_on)
        for wrapper in person.wrappers:
            wrapper_prefix = f"wrapper[{wrapper.id}]"
            note(f"{wrapper_prefix}.balance", wrapper.balance)
            note(f"{wrapper_prefix}.crystallised_balance", wrapper.crystallised_balance)
            if wrapper.contributions is not None:
                note(
                    f"{wrapper_prefix}.contributions.employer_amount",
                    wrapper.contributions.employer_amount,
                )
    return tuple(facts)


def collect_plan_decisions(household: Household) -> tuple[LabelledDecision, ...]:
    """Every decision variable in effect, at stable entity-id paths.

    Decisions are exactly the scenario what-if whitelist (planning
    §4.3): retirement ages and contribution choices today; withdrawal
    and annuity choices as later phases add them.
    """
    decisions: list[LabelledDecision] = []
    for person in household.persons:
        decisions.append(
            LabelledDecision(
                label=f"person[{person.id}].target_retirement_age",
                decision=person.target_retirement_age,
            )
        )
        decisions.extend(
            LabelledDecision(
                label=f"wrapper[{wrapper.id}].contributions.employee_amount",
                decision=wrapper.contributions.employee_amount,
            )
            for wrapper in person.wrappers
            if wrapper.contributions is not None
        )
    return tuple(decisions)
