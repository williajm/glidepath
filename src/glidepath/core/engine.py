"""The deterministic projection engine (roadmap 4.1; planning §4.6, §5.2).

``run(plan, assumptions, region, config)`` is a pure function: no I/O,
no clock reads (``config.today`` is an input), no global state
(planning §4.6). Within each period the operation order is part of the
spec (planning §5.2, tested):

1. **Open** — resolve ages, stage, glide-path allocation (§4.1 gate
   convention: retirement is attained only if reached by the period's
   first day).
2. **Income** — employment income while accumulating, escalated by the
   earnings-growth assumption (DB and state pension income land in
   roadmap 4.2/4.3).
3. **Contributions** — employee + employer per schedule, escalation,
   per-kind caps, then the region's relief mechanics.
4. **Withdrawals** — in decumulation, the household's net (after-tax)
   spending need is met from wrappers; net-defined draws gross up
   against the region tax system by fixed-point iteration (capped,
   residual settled at ledger precision).
5. **Tax** — one final assessment per person over the period's full
   categorised income; the gross-up called the same function, so the
   final assessment is consistent by construction.
6. **Fees** — platform + fund on average balances.
7. **Growth** — the period's returns on each wrapper's allocation
   (fees before growth is enforced by ``apply_fees_and_growth``).
8. **Close** — quantize the ledger, emit the period snapshot.

v1 engine conventions, superseded as later phases land:

- Accumulation and decumulation switch together at the target
  retirement age (§4.1 convention): employment income and
  contributions run while years-to-retirement is positive; spending
  withdrawals start once it is not.
- Withdrawal order is tax-aware and fixed (planning §5.2's default,
  pending the strategy protocol of roadmap 5.1): tax-free wrappers
  first, then funds already in drawdown (no fresh tax-free cash), then
  new pension access where the region's gate is open.
- Wrapper balance facts are taken as at the run start; annual-allowance
  measurement joins the loop when the AA charge is modelled
  (roadmap 5.x/9.5).
- The first and last periods are currently applied whole: a mid-period
  ``today`` gets a full year of flows despite balances being as-of the
  run start, and the final period runs past the horizon end. Partial-
  period pro-rating per §4.1 is roadmap 4.6 (issue #57), sequenced
  before the 4.5 golden scenario.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from glidepath.core.config import EngineError, RunConfig
from glidepath.core.contributions import MemberContributionRequest
from glidepath.core.entities import validate_household_v1
from glidepath.core.glide import glide_path_from_shape, years_to_target_retirement
from glidepath.core.investments import FeeSchedule, period_fee
from glidepath.core.money import Money, Rate
from glidepath.core.periods import age_on, date_age_attained
from glidepath.core.provenance import (
    AssumptionKey,
    AssumptionReadRecorder,
    TrackedAssumptions,
    decimal_assumption_value,
    int_assumption_value,
    mapping_assumption_value,
)
from glidepath.core.results import (
    PeriodSnapshot,
    PersonPeriodResult,
    ProjectionResult,
    RunProvenance,
    WrapperPeriodResult,
    collect_plan_decisions,
    collect_plan_facts,
)
from glidepath.core.returns import DeterministicReturnModel
from glidepath.core.tax import TaxInput
from glidepath.core.wrappers import WithdrawalTaxTreatment

if TYPE_CHECKING:
    from datetime import date

    from glidepath.core.contributions import ContributionSchedule
    from glidepath.core.entities import Household, Person, SpendingPlan
    from glidepath.core.glide import GlidePathConfig, LifeStage
    from glidepath.core.investments import AssetAllocation
    from glidepath.core.periods import Period
    from glidepath.core.provenance import AssumptionSet
    from glidepath.core.region import Region
    from glidepath.core.returns import PeriodReturns
    from glidepath.core.wrappers import Wrapper, WrapperTaxTreatment

_ZERO = Money(Decimal(0))
_ONE = Decimal(1)
_GROSS_UP_ITERATION_CAP = 48
"""Fixed-point iteration cap for the net-need gross-up (§5.2 step 4)."""
_NET_TOLERANCE = Money(Decimal("0.005"))
"""Half a penny: residuals below ledger precision are settled, not chased."""


@dataclass(slots=True)
class _WrapperLedger:
    """One wrapper's mutable working ledger for a single period.

    ``uncrystallised``/``crystallised`` are the running balances as the
    period's flows apply; the ``opening_*`` fields keep the step-1
    values for the snapshot and the average-balance fee base.
    """

    wrapper: Wrapper
    allocation: AssetAllocation
    treatment: WrapperTaxTreatment
    uncrystallised: Money
    crystallised: Money
    opening_uncrystallised: Money
    opening_crystallised: Money
    employee_in: Money = _ZERO
    employer_in: Money = _ZERO
    provider_relief: Money = _ZERO
    contribution_shortfall: Money = _ZERO
    withdrawn_uncrystallised: Money = _ZERO
    withdrawn_crystallised: Money = _ZERO
    withdrawal_tax_free: Money = _ZERO
    withdrawal_taxable: Money = _ZERO


@dataclass(slots=True)
class _WithdrawalSource:
    """One drawable sub-balance, with the tax-free fraction of a draw."""

    ledger: _WrapperLedger
    crystallised: bool
    tax_free_fraction: Decimal

    @property
    def available(self) -> Money:
        """What the sub-balance currently holds."""
        if self.crystallised:
            return self.ledger.crystallised
        return self.ledger.uncrystallised

    def draw(self, gross: Money, tax_free: Money, taxable: Money) -> None:
        """Take ``gross`` from the sub-balance and record its tax split."""
        ledger = self.ledger
        if self.crystallised:
            ledger.crystallised = ledger.crystallised - gross
            ledger.withdrawn_crystallised = ledger.withdrawn_crystallised + gross
        else:
            ledger.uncrystallised = ledger.uncrystallised - gross
            ledger.withdrawn_uncrystallised = ledger.withdrawn_uncrystallised + gross
        ledger.withdrawal_tax_free = ledger.withdrawal_tax_free + tax_free
        ledger.withdrawal_taxable = ledger.withdrawal_taxable + taxable


class _NominalFactors:
    """Cumulative nominal escalation factors, one per assumption key.

    Each registered key holds a *real* growth-rate assumption; its
    factor compounds ``(1 + real) * (1 + CPI)`` each period after the
    first, so escalated amounts stay nominal like everything else the
    engine computes (planning §5.2).
    """

    __slots__ = ("_factors", "_real_rates")

    def __init__(self, tracked: TrackedAssumptions, keys: set[AssumptionKey]) -> None:
        """Read each key's real rate through the tracked view.

        Keys are read in sorted order so the run's recorded read order
        — and therefore the serialized provenance — is identical across
        processes (set iteration order is hash-salted; planning §4.6
        demands byte-identical results from identical inputs).
        """
        self._real_rates = {
            key: decimal_assumption_value(tracked.get(key)) for key in sorted(keys)
        }
        self._factors = dict.fromkeys(self._real_rates, _ONE)

    def advance(self, cpi: Decimal) -> None:
        """Compound every factor by one period's nominal growth."""
        for key, real in self._real_rates.items():
            self._factors[key] *= (_ONE + real) * (_ONE + cpi)

    def factor(self, key: AssumptionKey) -> Decimal:
        """The cumulative nominal factor for ``key`` (1 in period one)."""
        return self._factors[key]


def run(
    plan: Household,
    assumptions: AssumptionSet,
    region: Region,
    config: RunConfig,
) -> ProjectionResult:
    """Project ``plan`` deterministically over the horizon (planning §5.2).

    Pure and deterministic (planning §4.6): identical inputs produce an
    identical result. Every tunable number is read through the
    assumption set and recorded; the result's provenance lists the
    facts used, assumptions read, decisions in effect, the region data
    version, and the seed.

    Raises:
        EngineError: If the horizon is empty or the plan is not
            projectable (v1: exactly one person).
    """
    try:
        validate_household_v1(plan)
    except ValueError as exc:
        raise EngineError(str(exc)) from exc
    recorder = AssumptionReadRecorder()
    tracked = TrackedAssumptions(assumptions=assumptions, recorder=recorder)
    projection = _Projection(
        plan=plan,
        person=plan.persons[0],
        region=region,
        tracked=tracked,
        config=config,
    )
    snapshots = projection.execute()
    provenance = RunProvenance(
        facts=collect_plan_facts(plan),
        decisions=collect_plan_decisions(plan),
        assumptions=tuple(assumptions.get(key) for key in recorder.keys_read),
        region_data_version=region.data_version,
        seed=config.seed,
    )
    return ProjectionResult(snapshots=snapshots, provenance=provenance, config=config)


@dataclass(slots=True)
class _Projection:
    """One run's working state: the loop of planning §5.2 over the horizon."""

    plan: Household
    person: Person
    region: Region
    tracked: TrackedAssumptions
    config: RunConfig
    _balances: dict[str, tuple[Money, Money]] = field(default_factory=dict)
    _taxable_income: Money = _ZERO
    _relief_at_source: Money = _ZERO

    def execute(self) -> tuple[PeriodSnapshot, ...]:
        """Run the period loop and return the snapshots in order."""
        self._balances = {
            wrapper.id: (
                wrapper.balance.value,
                _ZERO
                if wrapper.crystallised_balance is None
                else wrapper.crystallised_balance.value,
            )
            for wrapper in self.person.wrappers
        }
        model = DeterministicReturnModel(assumptions=self.tracked)
        factors = _NominalFactors(self.tracked, self._escalation_keys())
        inflation = _ONE
        snapshots: list[PeriodSnapshot] = []
        horizon_end = self._horizon_end()
        for period in self.region.calendar.periods(self.config.today, horizon_end):
            returns = model.returns_for(period, 0)
            if snapshots:
                inflation *= returns.cpi.growth_factor
                factors.advance(returns.cpi.value)
            snapshots.append(self._project_period(period, returns, inflation, factors))
        return tuple(snapshots)

    def _horizon_end(self) -> date:
        """The configured horizon end, or the planning-age default (§5.2)."""
        if self.config.horizon_end is not None:
            return self.config.horizon_end
        planning_age = int_assumption_value(
            self.tracked.get(AssumptionKey.HORIZON_PLANNING_AGE)
        )
        horizon_end = date_age_attained(self.person.date_of_birth.value, planning_age)
        if horizon_end < self.config.today:
            msg = (
                f"planning age {planning_age} was attained before today"
                f" {self.config.today}; set RunConfig.horizon_end explicitly"
            )
            raise EngineError(msg)
        return horizon_end

    def _escalation_keys(self) -> set[AssumptionKey]:
        """The real-growth assumption keys this plan escalates by."""
        keys: set[AssumptionKey] = set()
        if self.person.employment_income is not None:
            keys.add(AssumptionKey.EARNINGS_GROWTH_REAL)
        keys.update(
            wrapper.contributions.escalation
            for wrapper in self.person.wrappers
            if wrapper.contributions is not None
            and wrapper.contributions.escalation is not None
        )
        return keys

    def _glide(self) -> GlidePathConfig:
        """The person's glide path, or the default-shape assumption's."""
        if self.person.glide_path is not None:
            return self.person.glide_path
        shape = mapping_assumption_value(
            self.tracked.get(AssumptionKey.GLIDEPATH_DEFAULT_SHAPE)
        )
        return glide_path_from_shape(shape)

    def _fees_for(self, wrapper: Wrapper) -> FeeSchedule:
        """The wrapper's fee schedule, or the shipped fee assumptions."""
        if wrapper.fees is not None:
            return wrapper.fees
        return FeeSchedule(
            platform=Rate(
                decimal_assumption_value(self.tracked.get(AssumptionKey.FEES_PLATFORM))
            ),
            fund=Rate(
                decimal_assumption_value(self.tracked.get(AssumptionKey.FEES_FUND))
            ),
        )

    def _project_period(
        self,
        period: Period,
        returns: PeriodReturns,
        inflation: Decimal,
        factors: _NominalFactors,
    ) -> PeriodSnapshot:
        """Run the eight steps of planning §5.2 for one period."""
        person = self.person
        # Step 1 — open.
        age = age_on(person.date_of_birth.value, period.start)
        ytr = years_to_target_retirement(
            person.date_of_birth.value, person.target_retirement_age.value, period
        )
        glide = self._glide()
        stage = glide.stage_at(ytr)
        retired = ytr <= 0
        ledgers = [
            self._open_ledger(wrapper, period, glide, ytr)
            for wrapper in person.wrappers
        ]
        # Step 2 — income.
        employment = _ZERO
        if not retired and person.employment_income is not None:
            employment = person.employment_income.value * factors.factor(
                AssumptionKey.EARNINGS_GROWTH_REAL
            )
        # Steps 3-4 — contributions, then withdrawals.
        self._taxable_income = employment
        self._relief_at_source = _ZERO
        if not retired:
            self._contribution_step(ledgers, period, employment, factors)
        need = _ZERO
        delivered = _ZERO
        if retired and self.plan.spending is not None:
            need = _spending_need(self.plan.spending, stage, inflation)
            delivered = self._withdrawal_step(ledgers, period, need)
        # Step 5 — final tax assessment on the full income picture.
        tax = self.region.tax.assess(period, self._tax_input())
        # Steps 6-8 — fees, growth, close.
        wrapper_results = tuple(
            self._close_wrapper(ledger, returns) for ledger in ledgers
        )
        shortfall = max(need - delivered, _ZERO)
        person_result = PersonPeriodResult(
            person_id=person.id,
            age_at_period_start=age,
            years_to_retirement=ytr,
            stage=stage,
            employment_income=employment.quantized(),
            tax=tax,
            spending_need=need.quantized(),
            net_withdrawn=delivered.quantized(),
            shortfall=shortfall.quantized(),
            wrappers=wrapper_results,
        )
        return PeriodSnapshot(
            period=period,
            returns=returns,
            inflation_factor=inflation,
            persons=(person_result,),
        )

    def _open_ledger(
        self, wrapper: Wrapper, period: Period, glide: GlidePathConfig, ytr: int
    ) -> _WrapperLedger:
        """Step 1 for one wrapper: allocation and opening balances."""
        uncrystallised, crystallised = self._balances[wrapper.id]
        allocation = (
            wrapper.allocation
            if wrapper.allocation is not None
            else glide.allocation_at(ytr)
        )
        return _WrapperLedger(
            wrapper=wrapper,
            allocation=allocation,
            treatment=self.region.wrappers.tax_treatment(wrapper.kind, period),
            uncrystallised=uncrystallised,
            crystallised=crystallised,
            opening_uncrystallised=uncrystallised,
            opening_crystallised=crystallised,
        )

    def _contribution_step(
        self,
        ledgers: list[_WrapperLedger],
        period: Period,
        employment: Money,
        factors: _NominalFactors,
    ) -> None:
        """Step 3: scheduled contributions through caps and relief rules.

        Employer amounts (employment terms, outside the member's
        control) consume any per-kind cap first; the employee amount
        fills what remains. Amounts a cap or the region's relief limit
        keeps out of the pot are recorded as the wrapper's contribution
        shortfall — v1 does not reroute them (roadmap 9.2).
        """
        used_by_kind: dict[str, Money] = {}
        relieved_so_far = _ZERO
        for ledger in ledgers:
            schedule = ledger.wrapper.contributions
            if schedule is None:
                continue
            self._require_permitted_mechanic(ledger.wrapper, schedule)
            escalation = _ONE
            if schedule.escalation is not None:
                escalation = factors.factor(schedule.escalation)
            employee_intended = schedule.employee_amount.value * escalation
            employer = _ZERO
            if schedule.employer_amount is not None:
                employer = schedule.employer_amount.value * escalation
            kind = ledger.wrapper.kind
            cap = self.region.wrappers.annual_contribution_limit(kind, period)
            employee = employee_intended
            if cap is not None:
                used = used_by_kind.get(kind, _ZERO)
                headroom = max(cap - used, _ZERO)
                employer = min(employer, headroom)
                employee = min(employee, max(headroom - employer, _ZERO))
                used_by_kind[kind] = used + employer + employee
            outcome = self.region.contributions.member_contribution(
                MemberContributionRequest(
                    gross=employee,
                    relevant_earnings=employment,
                    date_of_birth=self.person.date_of_birth.value,
                    mechanic=schedule.relief_mechanic,
                    already_relieved_gross=relieved_so_far,
                ),
                period,
            )
            if schedule.relief_mechanic is not None:
                relieved_so_far = relieved_so_far + outcome.gross_to_pot
            self._taxable_income = max(
                self._taxable_income - outcome.taxable_pay_deduction, _ZERO
            )
            self._relief_at_source = (
                self._relief_at_source + outcome.assessment_relief_gross
            )
            ledger.employee_in = outcome.gross_to_pot
            ledger.employer_in = employer
            ledger.provider_relief = outcome.provider_relief
            ledger.contribution_shortfall = (
                employee_intended - employee
            ) + outcome.unrelieved_excess
            ledger.uncrystallised = (
                ledger.uncrystallised + outcome.gross_to_pot + employer
            )

    def _require_permitted_mechanic(
        self, wrapper: Wrapper, schedule: ContributionSchedule
    ) -> None:
        """Reject a schedule whose relief mechanic the region forbids.

        The region's permitted-mechanics set is the authority (planning
        §4.2): a mechanic outside it would fabricate relief (e.g.
        relief at source into an ISA), and a missing mechanic on a
        kind that operates one would bypass the relief limits entirely.
        """
        permitted = self.region.wrappers.permitted_relief_mechanics(wrapper.kind)
        mechanic = schedule.relief_mechanic
        if mechanic is None and permitted:
            names = ", ".join(sorted(entry.name for entry in permitted))
            msg = (
                f"wrapper {wrapper.id}: contributions to kind {wrapper.kind!r}"
                f" require a relief mechanic (one of: {names})"
            )
            raise EngineError(msg)
        if mechanic is not None and mechanic not in permitted:
            msg = (
                f"wrapper {wrapper.id}: relief mechanic {mechanic.name} is not"
                f" permitted for kind {wrapper.kind!r}"
            )
            raise EngineError(msg)

    def _withdrawal_step(
        self, ledgers: list[_WrapperLedger], period: Period, need: Money
    ) -> Money:
        """Step 4: meet the net need from wrappers, grossing up for tax.

        Sources are ordered tax-aware (module docstring): tax-free
        wrappers, then crystallised funds (already in drawdown), then
        uncrystallised pension funds where the access gate is open.
        Returns the net cash delivered toward the need.
        """
        delivered = _ZERO
        for source in self._withdrawal_sources(ledgers, period):
            remaining = need - delivered
            if remaining <= _NET_TOLERANCE:
                break
            if source.available <= _ZERO:
                continue
            delivered = delivered + self._draw_from(source, period, remaining)
        return delivered

    def _withdrawal_sources(
        self, ledgers: list[_WrapperLedger], period: Period
    ) -> list[_WithdrawalSource]:
        """The drawable sub-balances, in the v1 tax-aware order."""
        free: list[_WithdrawalSource] = []
        crystallised: list[_WithdrawalSource] = []
        uncrystallised: list[_WithdrawalSource] = []
        for ledger in ledgers:
            treatment = ledger.treatment
            if treatment.withdrawals is WithdrawalTaxTreatment.TAX_FREE:
                free.append(
                    _WithdrawalSource(
                        ledger=ledger, crystallised=False, tax_free_fraction=_ONE
                    )
                )
                free.append(
                    _WithdrawalSource(
                        ledger=ledger, crystallised=True, tax_free_fraction=_ONE
                    )
                )
                continue
            crystallised.append(
                _WithdrawalSource(
                    ledger=ledger, crystallised=True, tax_free_fraction=Decimal(0)
                )
            )
            fraction = Decimal(0)
            if (
                treatment.withdrawals is WithdrawalTaxTreatment.PARTIALLY_TAX_FREE
                and treatment.tax_free_fraction is not None
            ):
                fraction = treatment.tax_free_fraction.value
            if self.region.wrappers.is_access_open(
                ledger.wrapper.kind, self.person.date_of_birth.value, period
            ):
                uncrystallised.append(
                    _WithdrawalSource(
                        ledger=ledger, crystallised=False, tax_free_fraction=fraction
                    )
                )
        return [*free, *crystallised, *uncrystallised]

    def _draw_from(
        self, source: _WithdrawalSource, period: Period, need: Money
    ) -> Money:
        """Draw up to ``need`` net from one source, grossing up for tax.

        The fixed point of planning §5.2 step 4: iterate gross →
        assess → net until the net matches the need (piecewise-constant
        marginal rates converge in a few rounds), capped at
        ``_GROSS_UP_ITERATION_CAP`` with any sub-penny residual settled
        rather than chased. A draw the balance cannot cover takes the
        whole balance instead.
        """
        taxable_share = _ONE - source.tax_free_fraction
        gross = min(need, source.available)
        for _ in range(_GROSS_UP_ITERATION_CAP):
            extra_tax = self._incremental_tax(period, gross * taxable_share)
            target = need + extra_tax
            if target >= source.available:
                gross = source.available
                break
            if (target - gross) < _NET_TOLERANCE and (gross - target) < _NET_TOLERANCE:
                break
            gross = target
        taxable = gross * taxable_share
        net = gross - self._incremental_tax(period, taxable)
        source.draw(gross, tax_free=gross * source.tax_free_fraction, taxable=taxable)
        self._taxable_income = self._taxable_income + taxable
        return net

    def _incremental_tax(self, period: Period, taxable: Money) -> Money:
        """The extra tax ``taxable`` adds on top of the period's income.

        Both calls go through the region's one ``assess`` function —
        the same one the final step-5 assessment uses — so the gross-up
        and the final tax picture cannot disagree (planning §5.2).
        """
        if taxable <= _ZERO:
            return _ZERO
        base = self.region.tax.assess(period, self._tax_input())
        with_draw = self.region.tax.assess(
            period, self._tax_input(extra_income=taxable)
        )
        return with_draw.tax_due - base.tax_due

    def _tax_input(self, extra_income: Money = _ZERO) -> TaxInput:
        """The person's categorised income picture for assessment."""
        return TaxInput(
            residency=self.person.tax_residency,
            non_savings_income=self._taxable_income + extra_income,
            relief_at_source_contributions=self._relief_at_source,
        )

    def _close_wrapper(
        self, ledger: _WrapperLedger, returns: PeriodReturns
    ) -> WrapperPeriodResult:
        """Steps 6-8 for one wrapper: fees, growth, quantize, snapshot.

        The fee (step 6) is charged on the wrapper's aggregate average
        balance — a provider charges the account, not its sub-balances,
        and the cannot-exceed-the-holdings cap binds at account level —
        then allocated across the sub-balances pro rata to their
        post-flow values. Growth (step 7) applies to each post-fee
        sub-balance; fees before growth per the §5.2 order.
        """
        fees = self._fees_for(ledger.wrapper)
        opening_total = ledger.opening_uncrystallised + ledger.opening_crystallised
        after_total = ledger.uncrystallised + ledger.crystallised
        fee_total = period_fee(opening_total, after_total, fees)
        fee_uncrystallised = _ZERO
        if after_total > _ZERO:
            fee_uncrystallised = Money(
                fee_total.amount * ledger.uncrystallised.amount / after_total.amount
            )
        fee_crystallised = fee_total - fee_uncrystallised
        growth_rate = returns.assets.portfolio_growth_factor(ledger.allocation) - _ONE
        post_fee_uncrystallised = ledger.uncrystallised - fee_uncrystallised
        post_fee_crystallised = ledger.crystallised - fee_crystallised
        growth_uncrystallised = Money(post_fee_uncrystallised.amount * growth_rate)
        growth_crystallised = Money(post_fee_crystallised.amount * growth_rate)
        closing_uncrystallised = (
            post_fee_uncrystallised + growth_uncrystallised
        ).quantized()
        closing_crystallised = (post_fee_crystallised + growth_crystallised).quantized()
        self._balances[ledger.wrapper.id] = (
            closing_uncrystallised,
            closing_crystallised,
        )
        return WrapperPeriodResult(
            wrapper_id=ledger.wrapper.id,
            kind=ledger.wrapper.kind,
            allocation=ledger.allocation,
            opening_uncrystallised=ledger.opening_uncrystallised.quantized(),
            opening_crystallised=ledger.opening_crystallised.quantized(),
            employee_contribution=ledger.employee_in.quantized(),
            employer_contribution=ledger.employer_in.quantized(),
            provider_relief=ledger.provider_relief.quantized(),
            contribution_shortfall=ledger.contribution_shortfall.quantized(),
            withdrawal_tax_free=ledger.withdrawal_tax_free.quantized(),
            withdrawal_taxable=ledger.withdrawal_taxable.quantized(),
            fee=fee_total.quantized(),
            growth=(growth_uncrystallised + growth_crystallised).quantized(),
            closing_uncrystallised=closing_uncrystallised,
            closing_crystallised=closing_crystallised,
        )


def _spending_need(
    spending: SpendingPlan, stage: LifeStage, inflation: Decimal
) -> Money:
    """The period's net spending target in nominal money (§5.2 step 4).

    The real (today's money) need is scaled by the stage multiplier
    when one is configured, then inflated by the run's cumulative CPI
    factor — the same single inflation truth the returns carry.
    """
    multiplier = _ONE
    if spending.stage_multipliers is not None:
        multiplier = spending.stage_multipliers.get(stage, _ONE)
    return spending.annual_spending_real.value * multiplier * inflation
