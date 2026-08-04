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
    from collections.abc import Callable
    from datetime import date

    from glidepath.core.config import RunConfig
    from glidepath.core.entities import EntityId, Household
    from glidepath.core.glide import LifeStage
    from glidepath.core.investments import AssetAllocation
    from glidepath.core.pensions import DBPension
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
    provider's at-source reclaim); ``contribution_bonus`` is a
    government bonus credited on top of it (the UK LISA's 25%, roadmap
    9.2); ``contribution_shortfall`` is the intended amount that could
    not be contributed (per-kind caps or the region's relief limits).
    ``annuity_purchase`` is capital that left the wrapper to buy
    annuity income this period (roadmap 5.5) — the purchase's tax-free
    cash element is paid out through ``withdrawal_tax_free`` instead,
    exactly like an up-front lump sum.

    Taxable-growth wrappers (roadmap 9.2): ``taxable_interest`` and
    ``taxable_dividends`` are the period's portfolio income entering
    the tax assessment's savings and dividend layers; ``growth_tax``
    is the tax attributable to that income actually charged to the
    wrapper at period close — capped at what the account then holds,
    with any unfunded remainder joining the person's shortfall
    (planning §5.2). ``banked_in`` is decumulation surplus swept into
    this wrapper — income or gross draws beyond the period's need
    (planning §5.2). ``growth`` may be negative (a down period); every
    other flow is non-negative.
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
    annuity_purchase: Money = _ZERO
    contribution_bonus: Money = _ZERO
    taxable_interest: Money = _ZERO
    taxable_dividends: Money = _ZERO
    growth_tax: Money = _ZERO
    banked_in: Money = _ZERO

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
            self.annuity_purchase,
            self.contribution_bonus,
            self.taxable_interest,
            self.taxable_dividends,
            self.growth_tax,
            self.banked_in,
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
    the need left unmet after the configured withdrawal strategy's plan
    executed, plus any portfolio-income tax a drained taxable wrapper
    could not fund (roadmap 9.2) — the ruin signal the success metrics
    of roadmap 7.3 read.
    Under the default net-defined strategy a shortfall means every
    accessible wrapper was exhausted; a gross-defined strategy (e.g.
    fixed-%) may report one with balances still standing, because its
    draw follows the pot, not the need (planning §5.2).

    ``db_income`` and ``state_pension_income`` are the period's DB and
    state pension income actually in payment (revalued/uprated,
    pro-rated from their exact start dates, §4.1); ``db_lump_sum`` is
    the gross commutation cash received when a DB pension starts this
    period (roadmap 4.2/4.3) — tax-free up to the remaining lump-sum
    allowance, the excess taxed as income (roadmap 5.2).
    ``annuity_income`` is purchased annuity income in payment
    (escalated per its type, pro-rated from its exact start date), and
    ``annuity_lump_sum`` is the tax-free cash delivered alongside an
    annuity purchase's crystallisation of uncrystallised funds this
    period, capped by the remaining lump-sum allowance headroom
    (roadmap 5.5).
    ``planned_outflows`` is the nominal total
    of the household's dated one-offs landing this period (roadmap
    5.4) — a net need on top of ``spending_need``, so the shortfall
    accounting covers both.

    Tax-free cash tracking (roadmap 5.2): ``pension_lump_sum`` is
    up-front tax-free cash delivered by a whole-pot crystallisation
    this period (the ``UP_FRONT_LUMP_SUM`` event — phased tax-free
    elements appear in the wrappers' ``withdrawal_tax_free`` instead);
    ``lsa_used`` is the person's cumulative tax-free cash at period
    end, including the pre-plan ``lsa_used`` fact;
    ``mpaa_triggered_on`` is the flexible-access trigger date in
    effect at period end — the pre-plan fact or the in-run first
    taxable pension draw — or ``None`` if never triggered.

    ``banked`` is the period's decumulation surplus swept into a
    taxable wrapper (roadmap 9.2): income and gross draws beyond the
    net need land in the first uncapped taxable account rather than
    being spent — zero when the person holds none (planning §5.2).
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
    db_income: Money = _ZERO
    db_lump_sum: Money = _ZERO
    state_pension_income: Money = _ZERO
    annuity_income: Money = _ZERO
    annuity_lump_sum: Money = _ZERO
    planned_outflows: Money = _ZERO
    pension_lump_sum: Money = _ZERO
    lsa_used: Money = _ZERO
    mpaa_triggered_on: date | None = None
    banked: Money = _ZERO

    def __post_init__(self) -> None:
        """Reject negative flows."""
        amounts = (
            self.employment_income,
            self.spending_need,
            self.net_withdrawn,
            self.shortfall,
            self.db_income,
            self.db_lump_sum,
            self.state_pension_income,
            self.annuity_income,
            self.annuity_lump_sum,
            self.planned_outflows,
            self.pension_lump_sum,
            self.lsa_used,
            self.banked,
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
class BalanceRollForward:
    """One stale wrapper balance rolled forward to the run start (§4.8).

    A wrapper balance fact is dated ``as_of`` (its statement date); the
    engine seeds the opening ledger with ``stated`` compounded over the
    ``months`` whole months from ``as_of`` to the run's ``today`` at
    the wrapper's expected nominal return — ``factor`` is the exact
    multiplier applied and ``opening`` the quantized result. The stated
    fact itself is never altered: this record is how the estimate
    layered on it stays visible rather than silent (planning §4.8).
    ``label`` addresses the fact at its stable plan path, exactly like
    :class:`LabelledFact`.
    """

    label: str
    stated: Money
    as_of: date
    months: int
    factor: Decimal
    opening: Money

    def __post_init__(self) -> None:
        """Reject records that could not describe a §4.8 roll-forward."""
        if self.months <= 0:
            msg = "BalanceRollForward.months must be positive"
            raise ValueError(msg)
        if self.factor <= _ZERO_FACTOR:
            msg = "BalanceRollForward.factor must be positive"
            raise ValueError(msg)


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

    ``balance_roll_forwards`` lists every wrapper balance fact the run
    rolled forward from its statement date to ``today`` (planning
    §4.8) — empty when every balance was stated within a whole month
    of the run start.
    """

    facts: tuple[LabelledFact, ...]
    decisions: tuple[LabelledDecision, ...]
    assumptions: tuple[Assumption[Any], ...]
    region_data_version: str
    seed: int | None
    balance_roll_forwards: tuple[BalanceRollForward, ...] = ()


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


def _note_db_pension_facts(
    pension: DBPension, note: Callable[[str, Fact[Any] | None], None]
) -> None:
    """One DB pension's facts under its stable entity-id prefix (§5.1)."""
    prefix = f"db_pension[{pension.id}]"
    note(f"{prefix}.accrued_annual_pension", pension.accrued_annual_pension)
    note(f"{prefix}.normal_pension_age", pension.normal_pension_age)
    note(f"{prefix}.commutation_factor", pension.commutation_factor)
    membership = pension.active_membership
    if membership is not None:
        note(f"{prefix}.active_membership.accrual_rate", membership.accrual_rate)
        note(
            f"{prefix}.active_membership.pensionable_salary",
            membership.pensionable_salary,
        )


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
        note(f"{prefix}.lsa_used", person.lsa_used)
        for pension in person.db_pensions:
            _note_db_pension_facts(pension, note)
        if person.state_pension is not None:
            record = person.state_pension
            record_prefix = f"{prefix}.state_pension"
            note(
                f"{record_prefix}.forecast_weekly_amount",
                record.forecast_weekly_amount,
            )
            note(f"{record_prefix}.protected_payment", record.protected_payment)
            note(f"{record_prefix}.ni_record_start", record.ni_record_start)
            note(f"{record_prefix}.qualifying_years", record.qualifying_years)
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
    decisions.extend(
        LabelledDecision(
            label=f"planned_outflow[{outflow.id}].amount_real",
            decision=outflow.amount_real,
        )
        for outflow in household.planned_outflows
    )
    for person in household.persons:
        decisions.append(
            LabelledDecision(
                label=f"person[{person.id}].target_retirement_age",
                decision=person.target_retirement_age,
            )
        )
        for pension in person.db_pensions:
            pension_prefix = f"db_pension[{pension.id}]"
            if pension.taken_at_age is not None:
                decisions.append(
                    LabelledDecision(
                        label=f"{pension_prefix}.taken_at_age",
                        decision=pension.taken_at_age,
                    )
                )
            decisions.append(
                LabelledDecision(
                    label=f"{pension_prefix}.commuted_fraction",
                    decision=pension.commuted_fraction,
                )
            )
            if (
                pension.active_membership is not None
                and pension.active_membership.active_until_age is not None
            ):
                decisions.append(
                    LabelledDecision(
                        label=f"{pension_prefix}.active_membership.active_until_age",
                        decision=pension.active_membership.active_until_age,
                    )
                )
        for purchase in person.annuity_purchases:
            purchase_prefix = f"annuity_purchase[{purchase.id}]"
            decisions.append(
                LabelledDecision(
                    label=f"{purchase_prefix}.at_age", decision=purchase.at_age
                )
            )
            decisions.append(
                LabelledDecision(
                    label=f"{purchase_prefix}.fraction_of_pot",
                    decision=purchase.fraction_of_pot,
                )
            )
        if person.state_pension is not None:
            record_prefix = f"person[{person.id}].state_pension"
            decisions.append(
                LabelledDecision(
                    label=f"{record_prefix}.planned_extra_years",
                    decision=person.state_pension.planned_extra_years,
                )
            )
            decisions.append(
                LabelledDecision(
                    label=f"{record_prefix}.deferral_years",
                    decision=person.state_pension.deferral_years,
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
